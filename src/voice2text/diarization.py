"""Optional speaker diarization via sherpa-onnx."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voice2text.models import diarization_specs, model_path
from voice2text.segments import Segment
from voice2text.transcriber import decode_audio, default_cpu_threads

CLUSTER_THRESHOLD = 0.5


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: int


def load_audio_16k_mono(audio: Path) -> np.ndarray:
    return decode_audio(audio)


def assign_speakers(segments: list[Segment], turns: list[SpeakerTurn]) -> list[Segment]:
    if not turns:
        return list(segments)
    renumber: dict[int, int] = {}
    out: list[Segment] = []
    for seg in segments:
        overlap: dict[int, float] = {}
        for t in turns:
            o = min(seg.end, t.end) - max(seg.start, t.start)
            if o > 0:
                overlap[t.speaker] = overlap.get(t.speaker, 0.0) + o
        if overlap:
            raw = max(overlap, key=lambda k: overlap[k])
        else:
            def gap(t: SpeakerTurn) -> float:
                return max(t.start - seg.end, seg.start - t.end, 0.0)

            raw = min(turns, key=gap).speaker
        label = renumber.setdefault(raw, len(renumber) + 1)
        out.append(dataclasses.replace(seg, speaker=label))
    return out


def _build_config(seg_model: Path, emb_model: Path, threads: int, num_speakers: int):
    if num_speakers < 1:
        raise ValueError(f"num_speakers must be >= 1, got {num_speakers}")
    import sherpa_onnx

    return sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg_model)),
            num_threads=threads,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers, threshold=CLUSTER_THRESHOLD),
    )


def diarize(
    audio: Path, models_dir: Path, on_progress: Callable[[float], None], num_speakers: int
) -> list[SpeakerTurn]:
    if num_speakers < 1:
        raise ValueError(f"num_speakers must be >= 1, got {num_speakers}")
    seg_spec, emb_spec = diarization_specs()
    seg_model = model_path(seg_spec, models_dir) / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
    emb_model = model_path(emb_spec, models_dir) / "wespeaker_en_voxceleb_resnet34.onnx"
    config = _build_config(seg_model, emb_model, default_cpu_threads(), num_speakers)
    import sherpa_onnx

    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    samples = load_audio_16k_mono(audio)

    def callback(done: int, total: int) -> int:
        on_progress(done / total if total else 1.0)
        return 0

    result = sd.process(samples, callback=callback).sort_by_start_time()
    on_progress(1.0)
    return [SpeakerTurn(float(r.start), float(r.end), int(r.speaker)) for r in result]
