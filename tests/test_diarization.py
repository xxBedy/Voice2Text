from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voice2text import features
from voice2text.diarization import SpeakerTurn, assign_speakers, diarize, load_audio_16k_mono
from voice2text.models import diarization_specs
from voice2text.segments import Segment
from voice2text.transcriber import probe_duration

FIX = Path(__file__).parent / "fixtures"
S, T = Segment, SpeakerTurn


def test_assign_by_largest_overlap_and_renumber_by_first_appearance():
    segs = [S(0, 4, "a"), S(4, 8, "b"), S(8, 9.4, "c")]
    turns = [T(0, 3, 7), T(3, 9, 2), T(9, 10, 7)]
    assert [s.speaker for s in assign_speakers(segs, turns)] == [1, 2, 2]


def test_segment_without_overlap_takes_nearest_turn():
    segs = [S(0, 1, "a"), S(20, 21, "b")]
    turns = [T(0, 1, 5), T(25, 30, 9)]
    assert [s.speaker for s in assign_speakers(segs, turns)] == [1, 2]


def test_no_turns_leaves_speaker_none():
    assert assign_speakers([S(0, 1, "a")], [])[0].speaker is None


def test_speakers_enabled_flag():
    assert features.SPEAKERS_ENABLED is True


def test_diarization_specs_layout():
    specs = diarization_specs()
    assert [s.subdir for s in specs] == ["diar-segmentation", "diar-embedding"]
    assert specs[0].files[0].archive == "tar.bz2"


@pytest.mark.model
def test_load_audio_16k_mono():
    x = load_audio_16k_mono(FIX / "cs_sample.m4a")
    assert x.dtype == np.float32
    assert abs(len(x) - probe_duration(FIX / "cs_sample.m4a") * 16000) <= 0.03 * len(x)  # container duration includes AAC priming/padding (~1.9% here)


@pytest.mark.model
def test_diarize_returns_turns(diar_models_dir):
    progress: list[float] = []
    turns = diarize(FIX / "cs_sample.m4a", diar_models_dir, progress.append, 2)
    assert len(turns) >= 1
    assert all(t.start < t.end for t in turns)
    assert [t.start for t in turns] == sorted(t.start for t in turns)


def test_build_config_passes_threads_to_both_models(monkeypatch):
    import sherpa_onnx

    from voice2text import diarization

    captured: dict[str, dict] = {}

    def fake(name):
        def _f(**kw):
            captured[name] = kw
            return kw

        return _f

    monkeypatch.setattr(sherpa_onnx, "OfflineSpeakerSegmentationPyannoteModelConfig", fake("pyannote"))
    monkeypatch.setattr(sherpa_onnx, "OfflineSpeakerSegmentationModelConfig", fake("seg"))
    monkeypatch.setattr(sherpa_onnx, "SpeakerEmbeddingExtractorConfig", fake("emb"))
    monkeypatch.setattr(sherpa_onnx, "FastClusteringConfig", fake("clu"))
    monkeypatch.setattr(sherpa_onnx, "OfflineSpeakerDiarizationConfig", fake("top"))
    monkeypatch.setattr(diarization, "default_cpu_threads", lambda: 3)

    diarization._build_config(Path("s.onnx"), Path("e.onnx"), diarization.default_cpu_threads(), 2)

    assert captured["seg"]["num_threads"] == 3
    assert captured["emb"]["num_threads"] == 3


def _patch_sherpa(monkeypatch):
    import sherpa_onnx

    captured: dict[str, dict] = {}

    def fake(name):
        def _f(**kw):
            captured[name] = kw
            return kw

        return _f

    for attr, name in [
        ("OfflineSpeakerSegmentationPyannoteModelConfig", "pyannote"),
        ("OfflineSpeakerSegmentationModelConfig", "seg"),
        ("SpeakerEmbeddingExtractorConfig", "emb"),
        ("FastClusteringConfig", "clu"),
        ("OfflineSpeakerDiarizationConfig", "top"),
    ]:
        monkeypatch.setattr(sherpa_onnx, attr, fake(name))
    return captured


def test_build_config_passes_num_speakers_as_num_clusters(monkeypatch):
    from voice2text import diarization

    captured = _patch_sherpa(monkeypatch)
    diarization._build_config(Path("s.onnx"), Path("e.onnx"), 2, 3)
    assert captured["clu"]["num_clusters"] == 3
    assert captured["clu"]["threshold"] == diarization.CLUSTER_THRESHOLD


def test_build_config_rejects_less_than_one_speaker(monkeypatch):
    from voice2text import diarization

    _patch_sherpa(monkeypatch)
    with pytest.raises(ValueError):
        diarization._build_config(Path("s.onnx"), Path("e.onnx"), 2, 0)


def test_diarize_rejects_less_than_one_speaker():
    from voice2text import diarization

    with pytest.raises(ValueError):
        diarization.diarize(Path("x.m4a"), Path("m"), lambda f: None, 0)
