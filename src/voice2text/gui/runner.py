"""Background work for the window: transcription in a spawned process, model download and
import on daemon threads."""

from __future__ import annotations

import logging
import multiprocessing
import queue as queue_mod
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from voice2text import models
from voice2text.job import Done, Failed, JobRequest, worker_main
from voice2text.models import DownloadError, ImportFailed, InvalidModelFolder, ModelSpec

log = logging.getLogger(__name__)

POLL_MS = 100
_NUMBERED = re.compile(r" \(\d+\)")


class JobRunner(QObject):
    """Runs one job at a time in a `spawn` process and relays its events as signals.

    `finished` fires exactly once per started job (Done or Failed), unless the job is
    cancelled, in which case nothing fires.
    """

    progress = Signal(object)
    finished = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        target: Callable[..., None] = worker_main,
        target_args: tuple = (),
    ):
        super().__init__(parent)
        self._target = target
        self._target_args = tuple(target_args)
        self._ctx = multiprocessing.get_context("spawn")
        self._process = None
        self._queue = None
        self._req: JobRequest | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self, req: JobRequest) -> None:
        if self.is_running():
            raise RuntimeError("a job is already running")
        self._req = req
        self._queue = self._ctx.Queue()
        self._process = self._ctx.Process(
            target=self._target,
            args=(req, self._queue, *self._target_args),
            name="voice2text-job",
            daemon=True,
        )
        try:
            self._process.start()
        except BaseException:
            self._reset()
            raise
        log.info("job started: %s (fmt=%s, speakers=%s)", req.audio.name, req.fmt, req.speakers)
        self._timer.start()

    def is_running(self) -> bool:
        return self._process is not None

    def cancel(self) -> None:
        process, req = self._process, self._req
        if process is None:
            return
        # The worker may have saved and reported Done just before the click: keep that result.
        terminal = self._drain_terminal()
        if isinstance(terminal, Done):
            log.info("cancel came after the job finished; keeping the transcript")
            self._finish(terminal)
            return
        self._reset()
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        if req is not None:
            _remove_partial_outputs(req)
        log.info("job cancelled")

    def _drain_terminal(self) -> object | None:
        """Read what the worker already sent; return its Done/Failed if it is there."""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue_mod.Empty:
                return None
            except Exception:
                log.exception("could not read job event")
                return None
            if isinstance(event, (Done, Failed)):
                return event

    def _poll(self) -> None:
        if self._process is None:
            return
        # Sample liveness before draining: a worker that is already dead has flushed
        # everything it ever sent, so an empty queue then really means "no result".
        alive = self._process.is_alive()
        while True:
            try:
                event = self._queue.get_nowait()
            except queue_mod.Empty:
                break
            except Exception:
                log.exception("could not read job event")
                self._finish(Failed("error", "event queue broken"))
                return
            if isinstance(event, (Done, Failed)):
                self._finish(event)
                return
            self.progress.emit(event)
        if not alive:
            self._finish(Failed("error", f"worker exited with code {self._process.exitcode}"))

    def _finish(self, event: object) -> None:
        if isinstance(event, Failed):
            log.error("job failed: %s %s", event.code, event.detail)
        else:
            log.info("job done: %s", event)
        self._reset()
        self.finished.emit(event)

    def _reset(self) -> None:
        self._timer.stop()
        if self._queue is not None:
            self._queue.close()
        self._process = None
        self._queue = None
        self._req = None


def _remove_partial_outputs(req: JobRequest) -> None:
    """Delete `<stem>.<fmt>.part` / `<stem> (N).<fmt>.part` left by a killed save.

    Only our own names are touched: the audio often sits in Downloads, where other
    programs keep their own `*.part` files.
    """
    stem = req.audio.stem
    suffix = f".{req.fmt}.part"
    for folder in {req.audio.parent, req.fallback_dir}:
        for path in folder.glob("*.part"):
            name = path.name
            if not (name.startswith(stem) and name.endswith(suffix)):
                continue
            middle = name[len(stem) : len(name) - len(suffix)]
            if middle == "" or _NUMBERED.fullmatch(middle):
                try:
                    path.unlink()
                except OSError:
                    log.warning("could not remove %s", path)


class _DaemonWorker(QObject):
    """Runs `_work()` on a daemon thread and reports through Qt signals (queued to the window).

    A daemon thread, not a QThread: a download stuck in a socket read cannot be interrupted,
    and a QThread still running at exit aborts the process. A daemon thread simply dies with it.
    """

    def __init__(self, name: str, parent: QObject | None = None):
        super().__init__(parent)
        self._name = name
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.isRunning():
            return
        self._thread = threading.Thread(target=self._work, name=self._name, daemon=True)
        self._thread.start()

    def isRunning(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, msecs: int | None = None) -> bool:
        """Join for at most `msecs`; True when the thread has finished."""
        if self._thread is None:
            return True
        self._thread.join(None if msecs is None else msecs / 1000)
        return not self._thread.is_alive()

    def _work(self) -> None:
        raise NotImplementedError


class DownloadRunner(_DaemonWorker):
    """Downloads every not-yet-installed spec; progress is aggregated over all of them."""

    progress = Signal("qint64", "qint64")  # bytes done, bytes total (exceeds 32-bit int)
    failed = Signal(str)  # DownloadError code, or "error" for anything unexpected
    succeeded = Signal()

    def __init__(
        self,
        specs: list[ModelSpec],
        models_dir: Path,
        parent: QObject | None = None,
        *,
        download_fn: Callable[..., None] = models.download,
    ):
        super().__init__("voice2text-download", parent)
        self._specs = list(specs)
        self._models_dir = Path(models_dir)
        self._download = download_fn
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _work(self) -> None:
        finished_bytes = 0
        last_emit = 0.0
        try:
            for spec in self._specs:
                if models.is_installed(spec, self._models_dir):
                    continue
                spec_total = 0

                def on_progress(done: int, total: int, base: int = finished_bytes) -> None:
                    nonlocal spec_total, last_emit
                    spec_total = total
                    now = time.monotonic()
                    if done >= total or now - last_emit >= 0.1:
                        last_emit = now
                        self.progress.emit(base + done, base + total)

                log.info("downloading model %s", spec.key)
                self._download(spec, self._models_dir, on_progress, self._cancel.is_set)
                finished_bytes += spec_total
        except DownloadError as e:
            log.warning("model download failed: %s", e.code)
            self.failed.emit(e.code)
            return
        except Exception:
            log.exception("model download failed unexpectedly")
            self.failed.emit("error")
            return
        log.info("models ready")
        self.succeeded.emit()


class ImportRunner(_DaemonWorker):
    """Copies a model folder chosen by the user (gigabytes from a USB stick) off the UI thread."""

    succeeded = Signal(object)  # list of imported spec keys
    failed = Signal(str)  # "invalid", "space", "io", or "error" for anything unexpected

    def __init__(
        self,
        folder: Path,
        models_dir: Path,
        parent: QObject | None = None,
        *,
        import_fn: Callable[[Path, Path], list[str]] = models.import_models_from,
    ):
        super().__init__("voice2text-import", parent)
        self._folder = Path(folder)
        self._models_dir = Path(models_dir)
        self._import = import_fn

    def _work(self) -> None:
        try:
            keys = self._import(self._folder, self._models_dir)
        except InvalidModelFolder:
            log.info("no model in %s", self._folder)
            self.failed.emit("invalid")
            return
        except ImportFailed as e:
            log.error("model import failed: %s", e.code)
            self.failed.emit(e.code)
            return
        except Exception:
            log.exception("model import failed unexpectedly")
            self.failed.emit("error")
            return
        log.info("imported models %s from %s", keys, self._folder)
        self.succeeded.emit(keys)
