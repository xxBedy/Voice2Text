"""Job orchestration: decode -> transcribe -> (speakers) -> paragraphs -> save."""

from __future__ import annotations

import importlib
import logging
import multiprocessing
import multiprocessing.connection
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from voice2text import paths
from voice2text.diarization import assign_speakers, diarize
from voice2text.osutils import keep_awake
from voice2text.output import Format, render, save
from voice2text.paragraphs import build_paragraphs, drop_repeats
from voice2text.transcriber import UnreadableAudioError, transcribe

log = logging.getLogger(__name__)

WORKER_LOG_NAME = "worker.log"
WORKER_LOG_MAX_BYTES = 1_000_000


@dataclass(frozen=True)
class JobRequest:
    audio: Path
    fmt: Format
    speakers: bool
    whisper_dir: Path
    models_dir: Path
    fallback_dir: Path
    num_speakers: int = 2
    language: str = "auto"  # Whisper code, or "auto" to detect


@dataclass(frozen=True)
class Progress:
    stage: Literal["loading", "transcribing", "diarizing", "saving"]
    fraction: float | None = None
    eta_s: float | None = None


@dataclass(frozen=True)
class Done:
    output: Path
    fell_back: bool
    elapsed_s: float
    speakers_failed: bool = False  # transcript saved, but without speaker labels
    detected_language: str | None = None  # only when the request asked for "auto"


@dataclass(frozen=True)
class Failed:
    code: Literal["unreadable", "no_speech", "error"]
    detail: str = ""


def estimate_remaining(elapsed_s: float, fraction: float) -> float | None:
    if fraction < 0.02 or elapsed_s < 5:
        return None
    return elapsed_s * (1 - fraction) / fraction


def _progress_reporter(
    stage: Literal["transcribing", "diarizing"], started: float, emit: Callable[[object], None]
) -> Callable[[float], None]:
    """Forward a stage's progress fraction with an ETA, at most once per second."""
    last_emit = float("-inf")

    def report(fraction: float) -> None:
        nonlocal last_emit
        now = time.monotonic()
        if now - last_emit < 1.0:
            return
        last_emit = now
        emit(Progress(stage, fraction, estimate_remaining(now - started, fraction)))

    return report


def execute(
    req: JobRequest,
    emit: Callable[[object], None],
    transcribe_fn=transcribe,
    diarize_fn=diarize,
) -> None:
    started = time.monotonic()
    try:
        emit(Progress("loading"))
        on_transcribe = _progress_reporter("transcribing", started, emit)
        auto = req.language == "auto"
        detected: list[str] = []
        segments = drop_repeats(
            transcribe_fn(
                req.audio,
                req.whisper_dir,
                on_transcribe,
                None if auto else req.language,
                lambda code, probability: detected.append(code),
            )
        )
        if not segments:
            emit(Failed("no_speech"))
            return
        speakers_failed = False
        if req.speakers:
            emit(Progress("diarizing"))
            on_diarize = _progress_reporter("diarizing", time.monotonic(), emit)
            try:
                turns = diarize_fn(req.audio, req.models_dir, on_diarize, req.num_speakers)
                segments = assign_speakers(segments, turns)
            except Exception:
                # Labels are a bonus: an hour of transcription must never be lost to them.
                log.exception("speaker labelling failed; saving the transcript without speakers")
                speakers_failed = True
        paragraphs = build_paragraphs(segments)
        emit(Progress("saving"))
        text = render(paragraphs, title=req.audio.stem, fmt=req.fmt)
        output, fell_back = save(text, req.audio, req.fmt, req.fallback_dir)
        emit(Done(output, fell_back, time.monotonic() - started, speakers_failed, detected[0] if auto and detected else None))
    except UnreadableAudioError:
        emit(Failed("unreadable"))
    except Exception as e:
        log.exception("job failed")
        emit(Failed("error", str(e)))


def _resolve(path: str):
    module, _, attr = path.partition(":")
    return getattr(importlib.import_module(module), attr)


def setup_worker_logging() -> None:
    """Log the worker process to `logs/worker.log` (plain append, reset when over 1 MB).

    The spawned worker never runs the app's logging setup, and in windowed builds its
    stdout/stderr are None; both would silently lose the traceback of a failed job.
    """
    try:
        folder = paths.logs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / WORKER_LOG_NAME
        if path.exists() and path.stat().st_size > WORKER_LOG_MAX_BYTES:
            path.write_bytes(b"")
        handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    if sys.stdout is None:
        sys.stdout = handler.stream
    if sys.stderr is None:
        sys.stderr = handler.stream


def _exit_with_parent() -> None:
    """End this worker when the app that spawned it dies (crash, force quit): an orphan
    would keep burning CPU and holding off sleep with nobody to see or cancel it."""
    parent = multiprocessing.parent_process()
    if parent is None:  # not a spawned worker
        return
    sentinel = parent.sentinel

    def watch() -> None:
        multiprocessing.connection.wait([sentinel])
        os._exit(1)

    threading.Thread(target=watch, name="voice2text-parent-watch", daemon=True).start()


def _disable_onnx_telemetry() -> None:
    """onnxruntime (Silero VAD) can emit telemetry events on Windows; switch them off."""
    if sys.platform != "win32":
        return
    try:
        import onnxruntime

        disable = getattr(onnxruntime, "disable_telemetry_events", None)
        if disable is not None:
            disable()
    except Exception:
        log.warning("could not disable onnxruntime telemetry", exc_info=True)


def worker_main(
    req: JobRequest,
    queue,
    transcribe_path: str = "voice2text.transcriber:transcribe",
    diarize_path: str = "voice2text.diarization:diarize",
) -> None:
    try:
        setup_worker_logging()
    except Exception:
        pass  # logging must never cost the user the transcript
    try:
        _exit_with_parent()
        _disable_onnx_telemetry()
    except Exception:
        log.exception("worker setup failed; continuing")
    try:
        transcribe_fn = _resolve(transcribe_path)
        diarize_fn = _resolve(diarize_path)
        with keep_awake():
            execute(req, queue.put, transcribe_fn, diarize_fn)
    except Exception as e:
        log.exception("worker failed")
        queue.put(Failed("error", str(e)))
