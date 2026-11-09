"""Fake transcribe/diarize functions importable from spawned worker processes."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from voice2text.diarization import SpeakerTurn
from voice2text.segments import Segment
from voice2text.transcriber import UnreadableAudioError


def fake_transcribe(audio: Path, model_dir: Path, on_progress: Callable[[float], None], language=None, on_language=None) -> list[Segment]:
    if language is None and on_language is not None:
        on_language("cs", 0.98)
    on_progress(0.5)
    on_progress(1.0)
    return [
        Segment(0.0, 2.0, "Dobrý den, vítejte u nás."),
        Segment(2.5, 5.0, "Děkuji za pozvání, jsem rád."),
    ]


def empty_transcribe(audio: Path, model_dir: Path, on_progress: Callable[[float], None], language=None, on_language=None) -> list[Segment]:
    return []


def unreadable_transcribe(audio: Path, model_dir: Path, on_progress: Callable[[float], None], language=None, on_language=None) -> list[Segment]:
    raise UnreadableAudioError(str(audio))


def slow_transcribe(audio: Path, model_dir: Path, on_progress: Callable[[float], None], language=None, on_language=None) -> list[Segment]:
    time.sleep(30)
    return []


def fake_diarize(
    audio: Path, models_dir: Path, on_progress: Callable[[float], None], num_speakers: int = 2
) -> list[SpeakerTurn]:
    on_progress(1.0)
    return [SpeakerTurn(0.0, 2.2, 4), SpeakerTurn(2.2, 5.0, 9)]


def silent_worker(req, queue, *args) -> None:
    """Worker target that exits without ever delivering a terminal event."""
    return None


def done_then_linger(req, queue, *args) -> None:
    """Worker that saves and queues Done, then is still alive when the user hits cancel."""
    import time as _time

    from voice2text.job import Done

    output = req.audio.with_suffix("." + req.fmt)
    output.write_text("hotovo\n", encoding="utf-8")
    queue.put(Done(output, False, 0.1))
    queue.close()
    queue.join_thread()  # Done is in the pipe now
    (req.audio.parent / "queued.flag").touch()
    _time.sleep(3)


def failing_diarize(
    audio: Path, models_dir: Path, on_progress: Callable[[float], None], num_speakers: int = 2
) -> list[SpeakerTurn]:
    raise RuntimeError("sherpa exploded")


def worker_outside_temp_rule(req, queue, *args) -> None:
    """The real worker, for recordings in pytest's tmp_path (inside the system temp folder):
    save next to them instead of the user's real Documents/Přepisy."""
    from voice2text import output
    from voice2text.job import worker_main

    output.temp_roots = lambda: []
    worker_main(req, queue, *args)
