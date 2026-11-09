from __future__ import annotations

import logging
import multiprocessing
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from tests import fakes
from voice2text import job, osutils
from voice2text.job import Done, Failed, JobRequest, Progress, estimate_remaining, execute, worker_main

FIXTURE = Path(__file__).parent / "fixtures" / "cs_sample.m4a"


@pytest.fixture
def req(tmp_path: Path) -> JobRequest:
    audio = tmp_path / "memo.m4a"
    shutil.copy(FIXTURE, audio)
    return JobRequest(
        audio=audio,
        fmt="md",
        speakers=False,
        whisper_dir=tmp_path / "w",
        models_dir=tmp_path / "m",
        fallback_dir=tmp_path / "fb",
    )


def run(req, **kw):
    events = []
    execute(req, events.append, **kw)
    return events


def test_execute_happy_path_saves_md_next_to_audio(req):
    ev = run(req, transcribe_fn=fakes.fake_transcribe)
    assert isinstance(ev[0], Progress) and ev[0].stage == "loading"
    done = ev[-1]
    assert isinstance(done, Done) and not done.fell_back
    assert done.output == req.audio.with_suffix(".md")
    assert done.output.read_text(encoding="utf-8").startswith("# ")


def test_execute_with_speakers_labels_paragraphs(req):
    ev = run(replace(req, speakers=True), transcribe_fn=fakes.fake_transcribe, diarize_fn=fakes.fake_diarize)
    assert "**Mluvčí 1:**" in ev[-1].output.read_text(encoding="utf-8")
    assert any(isinstance(e, Progress) and e.stage == "diarizing" for e in ev)


def test_no_speech_saves_nothing(req):
    ev = run(req, transcribe_fn=fakes.empty_transcribe)
    assert ev[-1] == Failed("no_speech") and not req.audio.with_suffix(".md").exists()


def test_unreadable_is_reported(req):
    assert run(req, transcribe_fn=fakes.unreadable_transcribe)[-1].code == "unreadable"


def test_unexpected_exception_becomes_failed_error(req):
    def boom(*a):
        raise RuntimeError("x")

    last = run(req, transcribe_fn=boom)[-1]
    assert last.code == "error" and last.detail == "x"


def test_exactly_one_terminal_event(req):
    ev = run(req, transcribe_fn=fakes.fake_transcribe)
    assert sum(isinstance(e, (Done, Failed)) for e in ev) == 1


def test_estimate_remaining():
    assert estimate_remaining(3, 0.5) is None
    assert estimate_remaining(60, 0.01) is None
    assert estimate_remaining(60, 0.25) == pytest.approx(180)


def test_worker_process_terminate_stops_it_and_leaves_no_file(req):
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=worker_main, args=(req, q, "tests.fakes:slow_transcribe"))
    p.start()
    first = q.get(timeout=30)
    assert isinstance(first, Progress) and first.stage == "loading"
    p.terminate()
    p.join(5)
    assert not p.is_alive()
    assert not list(req.audio.parent.glob("*.md")) and not list(req.audio.parent.glob("*.part"))


def test_worker_bad_dotted_path_emits_single_failed(req):
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=worker_main, args=(req, q, "tests.fakes:does_not_exist"))
    p.start()
    ev = q.get(timeout=30)
    p.join(10)
    assert isinstance(ev, Failed) and ev.code == "error"
    assert q.empty() or not isinstance(q.get(timeout=1), (Done, Failed))


def test_worker_process_happy_path(req):
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=worker_main, args=(req, q, "tests.fakes:fake_transcribe"))
    p.start()
    events = []
    while not events or not isinstance(events[-1], (Done, Failed)):
        events.append(q.get(timeout=30))
    p.join(10)
    assert isinstance(events[-1], Done) and events[-1].output.exists()


def test_osutils_does_not_import_qt_at_module_level():
    import importlib

    src = Path(importlib.import_module("voice2text.osutils").__file__).read_text(encoding="utf-8")
    top = src.split("def open_file")[0]
    assert "PySide6" not in top


def test_keep_awake_is_safe_on_other_platforms(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with osutils.keep_awake():
        pass


@pytest.mark.skipif(sys.platform != "darwin", reason="caffeinate is macOS-only")
def test_keep_awake_terminates_caffeinate(monkeypatch):
    procs = []
    real = subprocess.Popen

    def spy(*a, **kw):
        procs.append(real(*a, **kw))
        return procs[-1]

    monkeypatch.setattr(osutils.subprocess, "Popen", spy)
    with osutils.keep_awake():
        assert len(procs) == 1 and procs[0].poll() is None
    assert procs[0].poll() is not None


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    out, err = sys.stdout, sys.stderr
    yield
    for h in list(root.handlers):
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    root.setLevel(level)
    sys.stdout, sys.stderr = out, err


def test_worker_writes_traceback_to_worker_log(req, tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("VOICE2TEXT_HOME", str(home))
    log = home / "logs" / "worker.log"
    log.parent.mkdir(parents=True)
    log.write_text("OLD\n" * 300_000, encoding="utf-8")  # > 1 MB: truncated when the worker starts
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=worker_main, args=(req, q, "tests.fakes:does_not_exist"))
    p.start()
    assert isinstance(q.get(timeout=30), Failed)
    p.join(10)
    text = log.read_text(encoding="utf-8")
    assert "Traceback" in text and "does_not_exist" in text
    assert "OLD" not in text


def test_worker_logging_appends_and_redirects_missing_stdio(tmp_path, monkeypatch, restore_logging):
    monkeypatch.setenv("VOICE2TEXT_HOME", str(tmp_path))
    log = tmp_path / "logs" / "worker.log"
    log.parent.mkdir()
    log.write_text("PREVIOUS\n", encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    job.setup_worker_logging()
    print("from stdout")
    sys.stderr.write("from stderr\n")
    logging.getLogger("voice2text.test").warning("from logging")
    sys.stdout.flush()
    text = log.read_text(encoding="utf-8")
    assert all(s in text for s in ("PREVIOUS", "from stdout", "from stderr", "from logging"))


def test_worker_import_chain_stays_qt_free():
    code = "import sys, voice2text.job; print([m for m in sys.modules if m.startswith('PySide6')])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_execute_passes_num_speakers_to_diarize(req):
    seen = []

    def spy(audio, models_dir, on_progress, num_speakers):
        seen.append(num_speakers)
        return fakes.fake_diarize(audio, models_dir, on_progress, num_speakers)

    run(replace(req, speakers=True, num_speakers=4), transcribe_fn=fakes.fake_transcribe, diarize_fn=spy)
    assert seen == [4]


def test_job_request_default_num_speakers(req):
    assert req.num_speakers == 2


# --- speaker labelling must never cost the transcript ---------------------------


def test_speaker_failure_saves_transcript_without_labels(req, caplog):
    ev = run(replace(req, speakers=True), transcribe_fn=fakes.fake_transcribe, diarize_fn=fakes.failing_diarize)
    done = ev[-1]
    assert isinstance(done, Done) and done.speakers_failed is True
    text = done.output.read_text(encoding="utf-8")
    assert "Dobrý den" in text and "Mluvčí" not in text
    assert "sherpa exploded" in caplog.text


def test_assign_speakers_failure_also_keeps_transcript(req, monkeypatch):
    def boom(segments, turns):
        raise ValueError("bad turns")

    monkeypatch.setattr(job, "assign_speakers", boom)
    done = run(replace(req, speakers=True), transcribe_fn=fakes.fake_transcribe, diarize_fn=fakes.fake_diarize)[-1]
    assert isinstance(done, Done) and done.speakers_failed is True
    assert "Mluvčí" not in done.output.read_text(encoding="utf-8")


def test_successful_speakers_do_not_report_failure(req):
    done = run(replace(req, speakers=True), transcribe_fn=fakes.fake_transcribe, diarize_fn=fakes.fake_diarize)[-1]
    assert isinstance(done, Done) and done.speakers_failed is False


def test_speaker_failure_still_transcribes_first(req):
    order = []

    def transcribe(audio, model_dir, on_progress, language=None, on_language=None):
        order.append("transcribe")
        return fakes.fake_transcribe(audio, model_dir, on_progress)

    def diarize(audio, models_dir, on_progress, num_speakers):
        order.append("diarize")
        raise RuntimeError("x")

    run(replace(req, speakers=True), transcribe_fn=transcribe, diarize_fn=diarize)
    assert order == ["transcribe", "diarize"]


# --- diarization progress ---------------------------------------------------------


class FakeClock:
    """Stands in for the `time` module inside job.py only."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def test_diarizing_progress_is_forwarded_with_eta_and_rate_limited(req, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(job, "time", clock)

    def diarize(audio, models_dir, on_progress, num_speakers):
        clock.now += 10
        on_progress(0.25)  # 10 s into diarization: 30 s left
        clock.now += 0.5
        on_progress(0.3)  # within a second of the previous one: dropped
        clock.now += 10
        on_progress(0.5)  # 20.5 s in: 20.5 s left
        on_progress(1.0)  # same instant: dropped
        return fakes.fake_diarize(audio, models_dir, lambda f: None, num_speakers)

    ev = run(replace(req, speakers=True), transcribe_fn=fakes.fake_transcribe, diarize_fn=diarize)
    diarizing = [e for e in ev if isinstance(e, Progress) and e.stage == "diarizing"]
    assert diarizing[0] == Progress("diarizing")  # start: no fraction yet (busy indicator)
    assert [(e.fraction, e.eta_s) for e in diarizing[1:]] == [(0.25, 30.0), (0.5, 20.5)]
    assert isinstance(ev[-1], Done)


# --- orphaned worker -------------------------------------------------------------------

ORPHANING_PARENT = r"""
import multiprocessing, os, shutil, sys
from pathlib import Path
from voice2text.job import JobRequest, worker_main

tmp, fixture, pid_file = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
audio = tmp / "memo.m4a"
shutil.copy(fixture, audio)
req = JobRequest(audio=audio, fmt="md", speakers=False, whisper_dir=tmp / "w", models_dir=tmp / "m", fallback_dir=tmp / "fb")
ctx = multiprocessing.get_context("spawn")
q = ctx.Queue()
p = ctx.Process(target=worker_main, args=(req, q, "tests.fakes:slow_transcribe"), daemon=True)
p.start()
q.get(timeout=60)  # "loading": the worker is up and transcribing (slowly)
pid_file.write_text(str(p.pid))
os._exit(0)  # die like a crashed or force-quit app: no cleanup of the child
"""


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)
    except OSError:
        pass


def test_worker_exits_when_its_parent_dies(tmp_path):
    root = Path(__file__).resolve().parent.parent
    pid_file = tmp_path / "worker.pid"
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(root), os.environ.get("PYTHONPATH")])))
    with open(tmp_path / "parent.log", "w") as out:  # not a pipe: the orphan would hold it open
        proc = subprocess.run(
            [sys.executable, "-c", ORPHANING_PARENT, str(tmp_path), str(FIXTURE), str(pid_file)],
            cwd=root, env=env, stdout=out, stderr=out, timeout=120,
        )
    assert proc.returncode == 0, (tmp_path / "parent.log").read_text()
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    alive = _pid_alive(pid)
    if alive:
        _kill(pid)
    assert not alive, "the orphaned worker kept running"


def test_parent_watch_is_a_no_op_outside_a_worker_process():
    before = {t.name for t in threading.enumerate()}
    job._exit_with_parent()  # the test runner has no multiprocessing parent
    assert {t.name for t in threading.enumerate()} == before


# --- onnxruntime telemetry (Windows only) ----------------------------------------------


class _FakeOrt:
    def __init__(self):
        self.disabled = 0

    def disable_telemetry_events(self):
        self.disabled += 1


def test_onnx_telemetry_disabled_on_windows(monkeypatch):
    fake = _FakeOrt()
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    monkeypatch.setattr(job.sys, "platform", "win32")
    job._disable_onnx_telemetry()
    assert fake.disabled == 1


def test_onnx_telemetry_untouched_elsewhere(monkeypatch):
    fake = _FakeOrt()
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    monkeypatch.setattr(job.sys, "platform", "darwin")
    job._disable_onnx_telemetry()
    assert fake.disabled == 0


@pytest.mark.parametrize("module", [object(), None])  # no such function / import fails
def test_onnx_telemetry_guarded(monkeypatch, module):
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    monkeypatch.setattr(job.sys, "platform", "win32")
    job._disable_onnx_telemetry()  # never raises


class _ListQueue(list):
    def put(self, event):
        self.append(event)


def test_worker_main_starts_parent_watch_and_disables_telemetry(req, monkeypatch, restore_logging):
    calls = []
    monkeypatch.setattr(job, "_exit_with_parent", lambda: calls.append("watch"))
    monkeypatch.setattr(job, "_disable_onnx_telemetry", lambda: calls.append("telemetry"))
    q = _ListQueue()
    worker_main(req, q, "tests.fakes:fake_transcribe")
    assert calls == ["watch", "telemetry"] and isinstance(q[-1], Done)


def test_keep_awake_runs_caffeinate_by_absolute_path(monkeypatch):
    commands = []

    class FakeProc:
        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_popen(cmd, *a, **k):
        commands.append(cmd)
        return FakeProc()

    monkeypatch.setattr(osutils.sys, "platform", "darwin")
    monkeypatch.setattr(osutils.subprocess, "Popen", fake_popen)
    with osutils.keep_awake():
        pass
    assert commands == [["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]]


# --- recording language -----------------------------------------------------------


def _spy_transcribe(seen):
    def transcribe(audio, model_dir, on_progress, language=None, on_language=None):
        seen.append(language)
        if language is None and on_language is not None:
            on_language("de", 0.91)
        return fakes.fake_transcribe(audio, model_dir, on_progress)

    return transcribe


def test_job_request_default_language_is_auto(req):
    assert req.language == "auto"


def test_auto_passes_none_and_reports_detected_language(req):
    seen = []
    done = run(req, transcribe_fn=_spy_transcribe(seen))[-1]
    assert seen == [None]
    assert isinstance(done, Done) and done.detected_language == "de"


def test_explicit_language_is_passed_and_not_reported(req):
    seen = []
    done = run(replace(req, language="cs"), transcribe_fn=_spy_transcribe(seen))[-1]
    assert seen == ["cs"]
    assert isinstance(done, Done) and done.detected_language is None


def test_auto_without_detection_info_is_fine(req):
    done = run(req, transcribe_fn=fakes.empty_transcribe)[-1]
    assert done == Failed("no_speech")
    done = run(req, transcribe_fn=lambda a, m, p, language=None, on_language=None: fakes.fake_transcribe(a, m, p))[-1]
    assert isinstance(done, Done) and done.detected_language is None


def test_silence_with_auto_is_no_speech(req):
    assert run(req, transcribe_fn=fakes.empty_transcribe)[-1] == Failed("no_speech")
