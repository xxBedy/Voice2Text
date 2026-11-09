from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from voice2text import transcriber
from voice2text.transcriber import (
    UnreadableAudioError,
    default_cpu_threads,
    probe_duration,
    transcribe,
)

FIX = Path(__file__).parent / "fixtures"


def test_probe_duration_of_sample():
    assert 4 < probe_duration(FIX / "cs_sample.m4a") < 15


@pytest.mark.parametrize("name", ["not_audio.m4a", "empty.m4a"])
def test_probe_unreadable(tmp_path, name):
    p = FIX / name if name != "empty.m4a" else tmp_path / name
    if name == "empty.m4a":
        p.write_bytes(b"")
    with pytest.raises(UnreadableAudioError):
        probe_duration(p)


@pytest.mark.model
def test_transcribe_sample_reports_progress(whisper_dir):
    fractions = []
    segs = transcribe(FIX / "cs_sample.m4a", whisper_dir, fractions.append)
    text = " ".join(s.text for s in segs).lower()
    assert segs and fractions and 0 < fractions[-1] <= 1.0
    if os.environ.get("VOICE2TEXT_WHISPER_MODEL") == "large-v3":
        assert "zkušební" in text and "rozpočet" in text


@pytest.mark.model
def test_transcribe_silence_returns_no_segments(whisper_dir):
    assert transcribe(FIX / "silence.m4a", whisper_dir, lambda f: None) == []


@pytest.mark.model
def test_transcribe_unreadable_raises(whisper_dir):
    with pytest.raises(UnreadableAudioError):
        transcribe(FIX / "not_audio.m4a", whisper_dir, lambda f: None)


class _Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _fake_sysctl(outputs):
    """outputs: sysctl key -> stdout string, or None to fail."""

    def run(cmd, **kwargs):
        assert isinstance(cmd, list) and kwargs.get("timeout") == 2
        assert cmd[:2] == ["/usr/sbin/sysctl", "-n"]  # absolute: never a sysctl found on PATH
        out = outputs.get(cmd[-1])
        if out is None:
            return _Result(returncode=1)
        return _Result(stdout=out + "\n")

    return run


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_CPU_THREADS", raising=False)


def test_threads_env_override_wins(monkeypatch):
    monkeypatch.setenv("VOICE2TEXT_CPU_THREADS", "3")
    assert default_cpu_threads() == 3
    monkeypatch.setenv("VOICE2TEXT_CPU_THREADS", "32")
    assert default_cpu_threads() == 32  # no cap


@pytest.mark.parametrize("bad", ["abc", "0", "-2", ""])
def test_threads_env_invalid_ignored(monkeypatch, bad):
    monkeypatch.setenv("VOICE2TEXT_CPU_THREADS", bad)
    monkeypatch.setattr(transcriber.sys, "platform", "linux")
    monkeypatch.setattr(transcriber.os, "cpu_count", lambda: 8)
    assert default_cpu_threads() == 4


def test_threads_darwin_caps_performance_cores(monkeypatch, clean_env):
    monkeypatch.setattr(transcriber.sys, "platform", "darwin")
    monkeypatch.setattr(
        transcriber.subprocess, "run", _fake_sysctl({"hw.perflevel0.physicalcpu": "8"})
    )
    assert default_cpu_threads() == 6


def test_threads_darwin_falls_back_to_physicalcpu(monkeypatch, clean_env):
    monkeypatch.setattr(transcriber.sys, "platform", "darwin")
    monkeypatch.setattr(
        transcriber.subprocess,
        "run",
        _fake_sysctl({"hw.perflevel0.physicalcpu": None, "hw.physicalcpu": "4"}),
    )
    assert default_cpu_threads() == 4


def test_threads_darwin_all_sysctl_fail_uses_cpu_count(monkeypatch, clean_env):
    def boom(cmd, **kwargs):
        raise OSError("no sysctl")

    monkeypatch.setattr(transcriber.sys, "platform", "darwin")
    monkeypatch.setattr(transcriber.subprocess, "run", boom)
    monkeypatch.setattr(transcriber.os, "cpu_count", lambda: 10)
    assert default_cpu_threads() == 6


@pytest.mark.parametrize("cpus,expected", [(12, 6), (4, 2), (None, 2), (1, 1)])
def test_threads_other_platforms(monkeypatch, clean_env, cpus, expected):
    monkeypatch.setattr(transcriber.sys, "platform", "win32")
    # Never touch the real API: on a real Windows host it would answer for that machine.
    monkeypatch.setattr(transcriber, "_windows_performance_cores", lambda: None)
    monkeypatch.setattr(transcriber.os, "cpu_count", lambda: cpus)
    assert default_cpu_threads() == expected


# --- recording language (fake WhisperModel) ---------------------------------------


class _FakeInfo:
    duration = 10.0
    language = "de"
    language_probability = 0.87


class _FakeSeg:
    start, end, text = 0.0, 5.0, " hallo "


def _patch_whisper(monkeypatch, calls):
    import sys
    import types

    import numpy as np

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, samples, **kw):
            calls.append(kw)
            return iter([_FakeSeg()]), _FakeInfo()

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(transcriber, "decode_audio", lambda a: np.zeros(16000, dtype=np.float32))
    monkeypatch.setattr(transcriber, "probe_duration", lambda a: 10.0)


def test_transcribe_passes_explicit_language_and_skips_on_language(monkeypatch):
    calls, detected = [], []
    _patch_whisper(monkeypatch, calls)
    segs = transcribe(Path("x.m4a"), Path("m"), lambda f: None, language="cs", on_language=lambda c, p: detected.append((c, p)))
    assert calls[0]["language"] == "cs" and detected == [] and segs[0].text == "hallo"


def test_transcribe_auto_passes_none_and_reports_detected(monkeypatch, caplog):
    import logging

    calls, detected = [], []
    _patch_whisper(monkeypatch, calls)
    with caplog.at_level(logging.INFO, logger="voice2text.transcriber"):
        transcribe(Path("x.m4a"), Path("m"), lambda f: None, language=None, on_language=lambda c, p: detected.append((c, p)))
    assert calls[0]["language"] is None and detected == [("de", 0.87)]
    assert "de" in caplog.text


def test_transcribe_default_language_is_auto_and_callback_optional(monkeypatch):
    calls = []
    _patch_whisper(monkeypatch, calls)
    transcribe(Path("x.m4a"), Path("m"), lambda f: None)
    assert calls[0]["language"] is None


@pytest.mark.model
@pytest.mark.parametrize("language", ["cs", None])
def test_transcribe_sample_with_language(whisper_dir, language):
    detected = []
    segs = transcribe(FIX / "cs_sample.m4a", whisper_dir, lambda f: None, language=language, on_language=lambda c, p: detected.append(c))
    assert segs and " ".join(s.text for s in segs).strip()
    assert detected == [] if language else len(detected) == 1 and detected[0]


# --- Windows performance cores -----------------------------------------------------

import struct


def _core_record(efficiency_class: int, relationship: int = 0, size: int = 48) -> bytes:
    """SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX: Relationship, Size, then PROCESSOR_RELATIONSHIP
    (Flags, EfficiencyClass, Reserved[20], GroupCount, GroupMask...)."""
    head = struct.pack("<II", relationship, size)
    body = struct.pack("<BB20sH", 1, efficiency_class, b"\0" * 20, 1)
    return head + body.ljust(size - 8, b"\0")


def test_count_performance_cores_hybrid_cpu():
    buf = b"".join(_core_record(0) for _ in range(8)) + b"".join(_core_record(1) for _ in range(2))
    assert transcriber._count_performance_cores(buf) == 2


def test_count_performance_cores_uniform_cpu():
    assert transcriber._count_performance_cores(b"".join(_core_record(0) for _ in range(6))) == 6


def test_count_performance_cores_skips_other_relationships():
    buf = _core_record(0, relationship=2, size=64) + _core_record(0) + _core_record(0)
    assert transcriber._count_performance_cores(buf) == 2


def test_count_performance_cores_empty_is_none():
    assert transcriber._count_performance_cores(b"") is None
    assert transcriber._count_performance_cores(_core_record(0, relationship=3)) is None


@pytest.mark.parametrize("cut", [3, 8, 9, 20, 47])
def test_count_performance_cores_truncated_is_none(cut):
    assert transcriber._count_performance_cores(_core_record(0)[:cut]) is None


def test_count_performance_cores_bad_sizes_are_none():
    assert transcriber._count_performance_cores(struct.pack("<II", 0, 0) + b"\0" * 40) is None
    assert transcriber._count_performance_cores(_core_record(0) + _core_record(1)[:30]) is None


def test_threads_win32_uses_performance_cores(monkeypatch, clean_env):
    monkeypatch.setattr(transcriber.sys, "platform", "win32")
    monkeypatch.setattr(transcriber.os, "cpu_count", lambda: 20)
    monkeypatch.setattr(transcriber, "_windows_performance_cores", lambda: 2)
    assert default_cpu_threads() == 2
    monkeypatch.setattr(transcriber, "_windows_performance_cores", lambda: 10)
    assert default_cpu_threads() == 6  # cap


@pytest.mark.parametrize("detected", [None, 0])
def test_threads_win32_api_failure_falls_back(monkeypatch, clean_env, detected):
    monkeypatch.setattr(transcriber.sys, "platform", "win32")
    monkeypatch.setattr(transcriber.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(transcriber, "_windows_performance_cores", lambda: detected)
    assert default_cpu_threads() == 6
    monkeypatch.setattr(transcriber.os, "cpu_count", lambda: 1)
    assert default_cpu_threads() == 1


def test_windows_performance_cores_two_call_pattern(monkeypatch):
    import ctypes
    import types

    data = b"".join(_core_record(1) for _ in range(4)) + _core_record(0)
    calls = []

    class Kernel32:
        def GetLogicalProcessorInformationEx(self, relationship, buf, length):
            calls.append((relationship, buf is None))
            if buf is None:
                length._obj.value = len(data)
                return 0
            ctypes.memmove(buf, data, len(data))
            return 1

        def GetLastError(self):
            return 122

    fake = types.SimpleNamespace(
        byref=lambda v: types.SimpleNamespace(_obj=v),
        c_ulong=lambda v=0: types.SimpleNamespace(value=v),
        create_string_buffer=ctypes.create_string_buffer,
        WinDLL=lambda *a, **k: Kernel32(),
    )
    monkeypatch.setattr(transcriber, "_ctypes", lambda: fake, raising=False)
    assert transcriber._windows_performance_cores() == 4
    assert calls == [(0, True), (0, False)]


def test_windows_performance_cores_any_error_is_none(monkeypatch):
    def boom():
        raise OSError("no kernel32")

    monkeypatch.setattr(transcriber, "_ctypes", boom, raising=False)
    assert transcriber._windows_performance_cores() is None


def _dump_processor_records(buf: bytes) -> list[tuple[int, int, int, int, int, int]]:
    """(Relationship, Size, Flags, EfficiencyClass, GroupCount, logical processors) per record.
    GroupMask[i] is a GROUP_AFFINITY (KAFFINITY Mask, WORD Group, WORD Reserved[3]) at 32 + 16*i."""
    out = []
    offset = 0
    while offset + 32 <= len(buf):
        relationship, size = struct.unpack_from("<II", buf, offset)
        flags, efficiency = buf[offset + 8], buf[offset + 9]
        (group_count,) = struct.unpack_from("<H", buf, offset + 30)
        logical = sum(
            bin(struct.unpack_from("<Q", buf, offset + 32 + 16 * i)[0]).count("1")
            for i in range(group_count)
            if offset + 32 + 16 * i + 8 <= offset + size
        )
        out.append((relationship, size, flags, efficiency, group_count, logical))
        if size <= 0:
            break
        offset += size
    return out


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows API only")
def test_windows_performance_cores_on_real_windows():
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    length = ctypes.c_ulong(0)
    kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(length))
    buf = ctypes.create_string_buffer(length.value)
    assert kernel32.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(length))
    records = _dump_processor_records(buf.raw[: length.value])
    cpus = os.cpu_count() or 1
    print(f"cpu_count={cpus} records (rel, size, flags, eff, groups, logical)={records}")
    cores = [r for r in records if r[0] == 0]
    # The record walk must cover every logical processor exactly once.
    assert sum(r[5] for r in cores) == cpus

    detected = transcriber._windows_performance_cores()
    print(f"detected={detected}")
    assert isinstance(detected, int) and 1 <= detected <= cpus
    assert detected <= len(cores)
    if len({r[3] for r in cores}) <= 1:  # non-hybrid: every core counts, at most 2 threads each
        assert detected == len(cores)
        assert detected >= cpus // 2
