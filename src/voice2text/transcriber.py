"""Offline transcription with faster-whisper."""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import av
import numpy as np

from voice2text.segments import Segment

DEVICE = "cpu"
COMPUTE_TYPE = "int8"
MAX_CPU_THREADS = 6
THREADS_ENV = "VOICE2TEXT_CPU_THREADS"

log = logging.getLogger(__name__)


def _sysctl_int(key: str) -> int | None:
    try:
        proc = subprocess.run(
            ["/usr/sbin/sysctl", "-n", key], capture_output=True, text=True, timeout=2
        )
        if proc.returncode != 0:
            return None
        value = int(proc.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return value if value > 0 else None


def _ctypes():
    import ctypes

    return ctypes


_RELATION_PROCESSOR_CORE = 0
_RECORD_HEADER = struct.Struct("<II")  # Relationship, Size
_EFFICIENCY_CLASS_OFFSET = 9  # header (8) + PROCESSOR_RELATIONSHIP.Flags (1)


def _count_performance_cores(buf: bytes) -> int | None:
    """Count the cores of the highest efficiency class in a buffer of
    SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX records; None if there are none or the
    buffer is malformed. On a hybrid CPU that is the performance cores; elsewhere all cores."""
    classes: list[int] = []
    offset = 0
    while offset < len(buf):
        if len(buf) - offset < _RECORD_HEADER.size:
            return None
        relationship, size = _RECORD_HEADER.unpack_from(buf, offset)
        if size < _RECORD_HEADER.size or offset + size > len(buf):
            return None
        if relationship == _RELATION_PROCESSOR_CORE:
            if size <= _EFFICIENCY_CLASS_OFFSET:
                return None
            classes.append(buf[offset + _EFFICIENCY_CLASS_OFFSET])
        offset += size
    if not classes:
        return None
    best = max(classes)
    return sum(1 for c in classes if c == best)


def _windows_performance_cores() -> int | None:
    """Performance-core count via GetLogicalProcessorInformationEx; None on any problem."""
    try:
        ctypes = _ctypes()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        length = ctypes.c_ulong(0)
        kernel32.GetLogicalProcessorInformationEx(_RELATION_PROCESSOR_CORE, None, ctypes.byref(length))
        if length.value <= 0:
            return None
        buf = ctypes.create_string_buffer(length.value)
        if not kernel32.GetLogicalProcessorInformationEx(_RELATION_PROCESSOR_CORE, buf, ctypes.byref(length)):
            return None
        count = _count_performance_cores(buf.raw[: length.value])
    except Exception:
        log.warning("could not read the Windows processor topology", exc_info=True)
        return None
    if count:
        log.info("cores in top efficiency class: %d", count)
    return count


def default_cpu_threads() -> int:
    """Thread count for CTranslate2.

    Using all logical cores (efficiency cores, hyperthreads) stalls its parallel
    work, so use roughly the physical/performance cores, capped at MAX_CPU_THREADS.
    VOICE2TEXT_CPU_THREADS overrides (uncapped).
    """
    raw = os.environ.get(THREADS_ENV)
    if raw is not None:
        try:
            override = int(raw.strip())
        except ValueError:
            override = 0
        if override > 0:
            return override
    if sys.platform == "darwin":
        n = (
            _sysctl_int("hw.perflevel0.physicalcpu")
            or _sysctl_int("hw.physicalcpu")
            or os.cpu_count()
            or 4
        )
    elif sys.platform == "win32":
        n = _windows_performance_cores() or max(1, (os.cpu_count() or 4) // 2)
    else:
        n = max(1, (os.cpu_count() or 4) // 2)
    return max(1, min(n, MAX_CPU_THREADS))


class UnreadableAudioError(Exception):
    pass


def probe_duration(audio: Path) -> float:
    """Duration in seconds; raises UnreadableAudioError if not usable audio."""
    try:
        with av.open(str(audio)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise UnreadableAudioError("no audio stream")
            duration: float | None = None
            if container.duration:
                duration = container.duration / av.time_base
            elif stream.duration and stream.time_base:
                duration = float(stream.duration * stream.time_base)
    except UnreadableAudioError:
        raise
    except (av.error.FFmpegError, OSError, ValueError) as exc:
        raise UnreadableAudioError(str(exc)) from exc
    if duration is None or duration <= 0:
        raise UnreadableAudioError("unknown or zero duration")
    return duration


def decode_audio(audio: Path, rate: int = 16000) -> np.ndarray:
    """Decode to 16 kHz mono float32.

    faster-whisper's own decoder passes `metadata_errors`, which av 19 rejects,
    so we decode here and hand it the array.
    """
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=rate)
    chunks: list[np.ndarray] = []
    with av.open(str(audio)) as container:
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def transcribe(
    audio: Path,
    model_dir: Path,
    on_progress: Callable[[float], None],
    language: str | None = None,
    on_language: Callable[[str, float], None] | None = None,
) -> list[Segment]:
    """`language` is a Whisper code; None detects it (then `on_language(code, probability)`)."""
    probe_duration(audio)
    try:
        samples = decode_audio(audio)
    except (av.error.FFmpegError, OSError, ValueError) as exc:
        raise UnreadableAudioError(str(exc)) from exc
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from faster_whisper import WhisperModel

    threads = default_cpu_threads()
    log.info("cpu_threads=%d", threads)
    model = WhisperModel(
        str(model_dir),
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
        cpu_threads=threads,
    )
    raw_segments, info = model.transcribe(
        samples,
        language=language,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    if language is None:
        detected = getattr(info, "language", None)
        if detected:
            probability = float(getattr(info, "language_probability", 0.0) or 0.0)
            log.info("detected language %s (p=%.2f)", detected, probability)
            if on_language is not None:
                on_language(detected, probability)
    segments: list[Segment] = []
    for seg in raw_segments:
        segments.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip()))
        if info.duration > 0:
            on_progress(min(1.0, seg.end / info.duration))
    return segments
