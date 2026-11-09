"""Entry point: the window by default, `--transcribe PATH` for headless runs (tests, CI smoke)."""

from __future__ import annotations

import argparse
import io
import logging
import logging.handlers
import multiprocessing
import os
import sys
from pathlib import Path

from voice2text import __version__, features, languages, locale_info, paths, strings

log = logging.getLogger("voice2text")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNREADABLE = 2
EXIT_NO_SPEECH = 3

_OUR_HANDLER = "_voice2text_handler"
CLI_SPAWN_ENV = "VOICE2TEXT_CLI_SPAWN"  # hidden: CLI runs the job like the window (CI smoke)


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    if "VOICE2TEXT_UI_LANG" not in os.environ:
        os.environ["VOICE2TEXT_UI_LANG"] = locale_info.ui_language()
    _setup_logging()
    args = _parse_args(argv)
    if args is None:
        return EXIT_ERROR
    log.info("Voice2Text %s starting (%s)", __version__, "cli" if args.transcribe else "gui")
    if args.transcribe is not None:
        return _run_cli(args)
    return _run_gui()


# --- logging ------------------------------------------------------------------


class _HandlerStream(io.TextIOBase):
    """stdout/stderr replacement for windowed builds: writes into the log handler's current file."""

    def __init__(self, handler: logging.FileHandler):
        self._handler = handler

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        h = self._handler
        h.acquire()
        try:
            if h.stream is None:
                h.stream = h._open()
            h.stream.write(text)
            h.stream.flush()
        finally:
            h.release()
        return len(text)


def _setup_logging() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _OUR_HANDLER, False):
            root.removeHandler(h)
            h.close()
    handler: logging.FileHandler | None = None
    try:
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            paths.log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        pass  # unwritable app folder: keep running, log to stderr only
    if handler is not None:
        handler.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s"))
        setattr(handler, _OUR_HANDLER, True)
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    if sys.stdout is None or sys.stderr is None:  # windowed PyInstaller build
        stream = _HandlerStream(handler) if handler is not None else open(os.devnull, "w", encoding="utf-8")
        sys.stdout = stream
        sys.stderr = stream


def _parse_args(argv: list[str] | None) -> argparse.Namespace | None:
    """Parsed arguments, or None (after printing why) when an argument is not known."""
    parser = argparse.ArgumentParser(prog="voice2text", description="Offline speech-to-text.")
    parser.add_argument("--transcribe", metavar="PATH", type=Path, help="transcribe PATH without a window")
    parser.add_argument("--format", choices=("md", "txt"), default="md")
    parser.add_argument("--speakers", action="store_true", help="label speakers (downloads extra models)")
    parser.add_argument("--num-speakers", type=int, default=2, metavar="N", help="speakers in the recording, 2-10 (with --speakers)")
    parser.add_argument("--language", default="auto", metavar="CODE|auto", help="recording language (Whisper code) or auto")
    parser.add_argument("--no-download", action="store_true", help="fail instead of downloading missing models")
    args, unknown = parser.parse_known_args(argv)
    unknown = [a for a in unknown if not a.startswith("-psn_")]  # macOS Finder launches may add -psn_…
    if unknown:
        log.error("unknown arguments: %s", unknown)
        print(f"voice2text: error: unrecognized arguments: {' '.join(unknown)}", file=sys.stderr, flush=True)
        return None
    return args


# --- headless -----------------------------------------------------------------


def _err(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _tolerant_stdio() -> None:
    """Czech text/paths must never crash on a legacy-codepage console (Windows CI)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass


def _run_cli(args: argparse.Namespace) -> int:
    from voice2text import job, models
    from voice2text.osutils import keep_awake
    from voice2text.transcriber import UnreadableAudioError, probe_duration

    _tolerant_stdio()
    if args.speakers and not 2 <= args.num_speakers <= 10:
        _err(strings.ERR_NUM_SPEAKERS)
        return EXIT_ERROR
    language = args.language.strip().lower()
    if language != "auto" and language not in languages.all_codes():
        _err(strings.ERR_LANGUAGE.format(code=args.language))
        return EXIT_ERROR
    audio = args.transcribe.expanduser().absolute()
    try:
        probe_duration(audio)
    except UnreadableAudioError as e:
        log.info("unreadable input %s: %s", audio, e)
        _err(strings.ERR_UNREADABLE)
        return EXIT_UNREADABLE

    models_dir = paths.models_dir()
    try:
        specs = [models.whisper_spec()] + (models.diarization_specs() if args.speakers else [])
    except ValueError as e:
        _err(str(e))
        return EXIT_ERROR
    missing = [s for s in specs if not models.is_installed(s, models_dir)]
    if missing and args.no_download:
        _err(strings.MODEL_NEEDED)
        return EXIT_ERROR
    for spec in missing:
        try:
            models.download(spec, models_dir, _download_printer())
        except models.DownloadError as e:
            log.error("model download failed: %s", e.code)
            _err(
                {
                    "space": strings.ERR_DOWNLOAD_SPACE,
                    "busy": strings.ERR_DOWNLOAD_BUSY,
                    "corrupt": strings.ERR_DOWNLOAD_CORRUPT,
                }.get(e.code, strings.ERR_DOWNLOAD_NETWORK)
            )
            return EXIT_ERROR

    req = job.JobRequest(
        audio=audio,
        fmt=args.format,
        speakers=args.speakers,
        num_speakers=args.num_speakers,
        whisper_dir=models.model_path(specs[0], models_dir),
        models_dir=models_dir,
        fallback_dir=paths.fallback_output_dir(),
        language=language,
    )
    results: list[object] = []
    printer = _progress_printer()

    def emit(event: object) -> None:
        if isinstance(event, (job.Done, job.Failed)):
            results.append(event)
        else:
            printer(event)

    if os.environ.get(CLI_SPAWN_ENV) == "1":
        log.info("running the job in a spawned worker (%s=1)", CLI_SPAWN_ENV)
        _execute_spawned(req, emit)
    else:
        with keep_awake():
            job.execute(req, emit)
    result = results[-1] if results else job.Failed("error", "no result")
    if isinstance(result, job.Done):
        log.info("done in %.1f s: %s", result.elapsed_s, result.output)
        if result.speakers_failed:
            _err(strings.SPEAKERS_FAILED)
        if result.detected_language:
            _err(strings.DETECTED_LANGUAGE.format(name=languages.display_name(result.detected_language)))
        print(result.output, flush=True)
        return EXIT_OK
    log.error("job failed: %s %s", result.code, result.detail)
    if result.code == "unreadable":
        _err(strings.ERR_UNREADABLE)
        return EXIT_UNREADABLE
    if result.code == "no_speech":
        _err(strings.ERR_NO_SPEECH)
        return EXIT_NO_SPEECH
    _err(strings.ERR_GENERIC.format(log=paths.logs_dir()))
    return EXIT_ERROR


def _execute_spawned(req, emit, target=None, target_args: tuple = ()) -> None:
    """Run the job the way the window does: `worker_main` in a `spawn` process, events
    over a queue. Emits exactly one Done/Failed (Failed when the worker dies silently)."""
    import queue as queue_mod

    from voice2text import job

    ctx = multiprocessing.get_context("spawn")
    events = ctx.Queue()
    process = ctx.Process(
        target=target or job.worker_main,
        args=(req, events, *target_args),
        name="voice2text-job",
        daemon=True,
    )
    process.start()
    finished = False
    try:
        while not finished:
            # Sample liveness before reading: a dead worker has flushed all it ever sent.
            alive = process.is_alive()
            try:
                event = events.get(timeout=0.5)
            except queue_mod.Empty:
                if alive:
                    continue
                event = job.Failed("error", f"worker exited with code {process.exitcode}")
            finished = isinstance(event, (job.Done, job.Failed))
            emit(event)
    finally:
        if finished:
            process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        events.close()


def _download_printer():
    last = -10

    def on_progress(done: int, total: int) -> None:
        nonlocal last
        pct = done * 100 // total if total else 0
        if pct >= last + 10 or (done == total and pct != last):
            last = pct
            _err(strings.MODEL_DOWNLOADING.format(pct=pct, done=strings.format_bytes(done), total=strings.format_bytes(total)))

    return on_progress


def _progress_printer():
    from voice2text.job import Progress

    last: dict = {"stage": None, "pct": None}

    def show(event: object) -> None:
        if not isinstance(event, Progress):
            return
        if event.stage == "transcribing" or (event.stage == "diarizing" and event.fraction is not None):
            pct = int((event.fraction or 0.0) * 100)
            if last["stage"] == event.stage and last["pct"] is not None and pct < last["pct"] + 10:
                return
            last["pct"] = pct
            template = strings.TRANSCRIBING if event.stage == "transcribing" else strings.DIARIZING_PCT
            line = template.format(pct=pct)
        elif event.stage == last["stage"]:
            return
        else:
            last["pct"] = None
            line = {"loading": strings.PREPARING, "diarizing": strings.DIARIZING, "saving": strings.SAVING}.get(
                event.stage, strings.PREPARING
            )
        last["stage"] = event.stage
        _err(line)

    return show


# --- window -------------------------------------------------------------------


def _log_unhandled(exc_type, exc, tb) -> None:
    log.critical("unhandled exception", exc_info=(exc_type, exc, tb))
    sys.__excepthook__(exc_type, exc, tb)


def _force_light_scheme(qt_app) -> None:
    """The window is designed light; dark mode would leave dialogs dark-on-dark."""
    from PySide6.QtCore import Qt

    hints = qt_app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+
        hints.setColorScheme(Qt.ColorScheme.Light)


def _run_gui() -> int:
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication

    from voice2text.gui.main_window import MainWindow
    from voice2text.gui.runner import DownloadRunner, JobRunner
    from voice2text.models import required_specs

    sys.excepthook = _log_unhandled
    qt_app = QApplication.instance() or QApplication(sys.argv[:1])
    qt_app.setApplicationName("Voice2Text")
    qt_app.setApplicationVersion(__version__)
    _force_light_scheme(qt_app)

    speakers = features.SPEAKERS_ENABLED
    models_dir = paths.models_dir()
    settings = QSettings("Voice2Text", "Voice2Text")
    window = MainWindow(
        JobRunner(),
        lambda: DownloadRunner(required_specs(speakers), models_dir),
        settings,
        models_dir,
        speakers,
    )
    window.show()

    smoke_ms = os.environ.get("VOICE2TEXT_SMOKE_QUIT_MS", "")
    if smoke_ms.isdigit():  # hidden: launch smoke test closes the window by itself
        QTimer.singleShot(int(smoke_ms), window.close)

    code = qt_app.exec()
    log.info("exiting with %s", code)
    return code
