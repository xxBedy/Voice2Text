from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tests import fakes
from voice2text.gui.runner import DownloadRunner, ImportRunner, JobRunner
from voice2text.job import Done, Failed, JobRequest, Progress
from voice2text.models import COMPLETE_MARKER, DownloadError, ImportFailed, InvalidModelFolder, ModelSpec

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


def test_job_runner_delivers_progress_then_done(qtbot, req):
    runner = JobRunner(target_args=("tests.fakes:fake_transcribe",))
    seen = []
    runner.progress.connect(seen.append)
    with qtbot.waitSignal(runner.finished, timeout=30_000) as blocker:
        runner.start(req)
        assert runner.is_running()
    done = blocker.args[0]
    assert isinstance(done, Done) and done.output.exists()
    assert isinstance(seen[0], Progress) and seen[0].stage == "loading"
    assert not runner.is_running()


def test_job_runner_reports_failed_when_worker_exits_without_terminal_event(qtbot, req):
    runner = JobRunner(target=fakes.silent_worker)
    with qtbot.waitSignal(runner.finished, timeout=30_000) as blocker:
        runner.start(req)
    event = blocker.args[0]
    assert isinstance(event, Failed) and event.code == "error"
    assert not runner.is_running()


def test_job_runner_cancel_kills_worker_and_removes_only_its_part_files(qtbot, req):
    runner = JobRunner(target_args=("tests.fakes:slow_transcribe",))
    finished = []
    runner.finished.connect(finished.append)
    with qtbot.waitSignal(runner.progress, timeout=30_000):
        runner.start(req)
    process = runner._process
    req.fallback_dir.mkdir()
    ours = [req.audio.parent / "memo.md.part", req.fallback_dir / "memo (2).md.part"]
    for p in ours:
        p.write_text("partial", encoding="utf-8")
    foreign = req.audio.parent / "browser-download.zip.part"
    foreign.write_text("x", encoding="utf-8")

    runner.cancel()

    assert not runner.is_running() and not process.is_alive()
    assert not any(p.exists() for p in ours)
    assert foreign.exists()
    qtbot.wait(300)
    assert finished == []


def _spec(key: str) -> ModelSpec:
    return ModelSpec(key=key, subdir=key, files=())


def test_download_runner_aggregates_progress_and_skips_installed(qtbot, tmp_path):
    (tmp_path / "ready").mkdir()
    (tmp_path / "ready" / COMPLETE_MARKER).touch()
    calls = []

    def fake_download(spec, models_dir, on_progress, should_cancel):
        calls.append(spec.key)
        on_progress(0, 3_000_000_000)
        on_progress(3_000_000_000, 3_000_000_000)
        (models_dir / spec.subdir).mkdir()
        (models_dir / spec.subdir / COMPLETE_MARKER).touch()

    dl = DownloadRunner([_spec("a"), _spec("ready"), _spec("b")], tmp_path, download_fn=fake_download)
    seen = []
    dl.progress.connect(lambda done, total: seen.append((done, total)))
    with qtbot.waitSignal(dl.succeeded, timeout=5_000):
        dl.start()
    assert dl.wait(5_000)
    assert calls == ["a", "b"]
    assert seen[-1] == (6_000_000_000, 6_000_000_000)  # beyond 32-bit int


def test_download_runner_reports_download_error_code(qtbot, tmp_path):
    def failing(spec, models_dir, on_progress, should_cancel):
        raise DownloadError("space")

    dl = DownloadRunner([_spec("a")], tmp_path, download_fn=failing)
    with qtbot.waitSignal(dl.failed, timeout=5_000) as blocker:
        dl.start()
    assert dl.wait(5_000)
    assert blocker.args == ["space"]


def test_download_runner_unexpected_exception_reports_error(qtbot, tmp_path):
    def boom(spec, models_dir, on_progress, should_cancel):
        raise RuntimeError("boom")

    dl = DownloadRunner([_spec("a")], tmp_path, download_fn=boom)
    with qtbot.waitSignal(dl.failed, timeout=5_000) as blocker:
        dl.start()
    assert dl.wait(5_000)
    assert blocker.args == ["error"]


def test_download_runner_cancel_reaches_download(qtbot, tmp_path):
    started = threading.Event()

    def blocking(spec, models_dir, on_progress, should_cancel):
        started.set()
        while not should_cancel():
            time.sleep(0.01)
        raise DownloadError("cancelled")

    dl = DownloadRunner([_spec("a")], tmp_path, download_fn=blocking)
    with qtbot.waitSignal(dl.failed, timeout=5_000) as blocker:
        dl.start()
        assert started.wait(5)
        dl.cancel()
    assert dl.wait(5_000)
    assert blocker.args == ["cancelled"]


def test_job_runner_failed_start_does_not_leave_it_running(req):
    runner = JobRunner(target=lambda req, queue: None)  # lambdas cannot be pickled for spawn
    with pytest.raises(Exception):
        runner.start(req)
    assert not runner.is_running()


def test_job_runner_cancel_delivers_an_already_queued_done(qtbot, req):
    runner = JobRunner(target=fakes.done_then_linger)
    finished = []
    runner.finished.connect(finished.append)
    runner.start(req)
    flag = req.audio.parent / "queued.flag"
    deadline = time.monotonic() + 30
    while not flag.exists() and time.monotonic() < deadline:
        time.sleep(0.05)  # no Qt events: the poll timer has not seen the Done yet
    assert flag.exists()

    runner.cancel()

    assert len(finished) == 1 and isinstance(finished[0], Done)
    assert finished[0].output.read_text(encoding="utf-8") == "hotovo\n"
    assert not runner.is_running()


@pytest.mark.parametrize(
    ("exc", "code"),
    [(InvalidModelFolder("x"), "invalid"), (ImportFailed("space"), "space"), (ImportFailed("io"), "io"), (RuntimeError("x"), "error")],
)
def test_import_runner_reports_error_codes(qtbot, tmp_path, exc, code):
    def failing(folder, models_dir):
        raise exc

    imp = ImportRunner(tmp_path / "src", tmp_path / "models", import_fn=failing)
    with qtbot.waitSignal(imp.failed, timeout=5_000) as blocker:
        imp.start()
    assert imp.wait(5_000)
    assert blocker.args == [code]


def test_import_runner_runs_off_the_calling_thread(qtbot, tmp_path):
    seen = {}

    def fake_import(folder, models_dir):
        seen["thread"] = threading.current_thread()
        return ["whisper"]

    imp = ImportRunner(tmp_path / "src", tmp_path / "models", import_fn=fake_import)
    with qtbot.waitSignal(imp.succeeded, timeout=5_000) as blocker:
        imp.start()
    assert blocker.args == [["whisper"]]
    assert seen["thread"] is not threading.main_thread()


STALLED_DOWNLOAD_QUIT = r"""
import sys, threading, time
from pathlib import Path
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication
from voice2text.gui.main_window import MainWindow
from voice2text.gui.runner import DownloadRunner, JobRunner
from voice2text.models import required_specs

def stalled(spec, models_dir, on_progress, should_cancel):
    on_progress(0, 3_000_000_000)
    threading.Event().wait()  # a socket read that never returns and never checks should_cancel

home = Path(sys.argv[1])
app = QApplication([])
factory = lambda: DownloadRunner(required_specs(True), home / "models", download_fn=stalled)
w = MainWindow(JobRunner(), factory, QSettings(str(home / "s.ini"), QSettings.Format.IniFormat), home / "models", True)
w.show()
def quit_now():
    print(f"CLOSING_AT={time.time()}", flush=True)
    w.close()
QTimer.singleShot(500, quit_now)
sys.exit(app.exec())
"""


def test_quit_during_stalled_download_neither_crashes_nor_hangs(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", STALLED_DOWNLOAD_QUIT, str(tmp_path)], env=env, capture_output=True, text=True, timeout=60
    )
    ended = time.time()
    assert proc.returncode == 0, proc.stderr
    assert "Destroyed while thread" not in proc.stderr
    closing = float(proc.stdout.split("CLOSING_AT=")[1].split()[0])
    assert ended - closing < 5.5
