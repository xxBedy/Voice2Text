from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from voice2text import app, job, models, strings
from voice2text.job import Done, Failed, Progress

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_app(tmp_path, monkeypatch):
    """Keep logs/models out of the real app folder and undo main()'s global logging/stdio setup."""
    monkeypatch.setenv("VOICE2TEXT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("VOICE2TEXT_CLI_SPAWN", raising=False)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    stdout, stderr = sys.stdout, sys.stderr
    yield
    for h in root.handlers:
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    root.setLevel(level)
    sys.stdout, sys.stderr = stdout, stderr


def readable(tmp_path: Path, name: str = "memo.m4a") -> Path:
    audio = tmp_path / name
    shutil.copy(FIX / "cs_sample.m4a", audio)
    return audio


def no_network(*a, **k):
    raise AssertionError("download must not be attempted")


@pytest.mark.model
def test_cli_transcribe_writes_file(tmp_path, whisper_dir, monkeypatch):
    monkeypatch.setenv("VOICE2TEXT_HOME", str(whisper_dir.parent.parent))
    monkeypatch.setenv("VOICE2TEXT_WHISPER_MODEL", whisper_dir.name.removeprefix("whisper-"))
    audio = tmp_path / "ukázka.m4a"
    shutil.copy(FIX / "cs_sample.m4a", audio)
    assert app.main(["--transcribe", str(audio), "--format", "txt"]) == 0
    assert (tmp_path / "ukázka.txt").read_text(encoding="utf-8").strip()


@pytest.mark.model
def test_cli_silence_returns_3(tmp_path, whisper_dir, monkeypatch):
    monkeypatch.setenv("VOICE2TEXT_HOME", str(whisper_dir.parent.parent))
    monkeypatch.setenv("VOICE2TEXT_WHISPER_MODEL", whisper_dir.name.removeprefix("whisper-"))
    audio = tmp_path / "ticho.m4a"
    shutil.copy(FIX / "silence.m4a", audio)
    assert app.main(["--transcribe", str(audio), "--no-download"]) == 3
    assert not list(tmp_path.glob("ticho*.md"))


def test_cli_unreadable_returns_2(tmp_path):
    p = tmp_path / "x.m4a"
    p.write_bytes(b"nope")
    assert app.main(["--transcribe", str(p), "--no-download"]) == 2


def test_cli_unreadable_is_detected_before_any_download(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "download", no_network)
    p = tmp_path / "x.m4a"
    p.write_bytes(b"nope")
    assert app.main(["--transcribe", str(p)]) == 2


def test_cli_missing_model_with_no_download_returns_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(models, "download", no_network)
    assert app.main(["--transcribe", str(readable(tmp_path)), "--no-download"]) == 1
    assert "model" in capsys.readouterr().err


def fake_download(log: list):
    def download(spec, models_dir, on_progress, should_cancel=lambda: False):
        log.append(spec.key)
        on_progress(50, 100)
        on_progress(100, 100)
        folder = models.model_path(spec, models_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / models.COMPLETE_MARKER).touch()

    return download


def fake_execute(final, seen: list | None = None):
    def execute(req, emit, *a, **k):
        if seen is not None:
            seen.append(req)
        emit(Progress("loading"))
        emit(Progress("transcribing", 0.5, None))
        emit(final(req) if callable(final) else final)

    return execute


@pytest.mark.parametrize(("speakers", "expected"), [(False, ["whisper"]), (True, ["whisper", "diar-segmentation", "diar-embedding"])])
def test_cli_downloads_only_what_it_needs(tmp_path, monkeypatch, capsys, speakers, expected):
    downloaded, seen = [], []
    monkeypatch.setattr(models, "download", fake_download(downloaded))
    monkeypatch.setattr(job, "execute", fake_execute(lambda req: Done(req.audio.with_suffix(".md"), False, 1.0), seen))
    audio = readable(tmp_path)
    args = ["--transcribe", str(audio)] + (["--speakers"] if speakers else [])
    assert app.main(args) == 0
    assert downloaded == expected
    req = seen[0]
    assert req.audio == audio and req.fmt == "md" and req.speakers is speakers
    assert req.whisper_dir == models.model_path(models.whisper_spec(), tmp_path / "home" / "models")
    assert str(audio.with_suffix(".md")) in capsys.readouterr().out


def _run_with_num_speakers(tmp_path, monkeypatch, extra):
    seen = []
    monkeypatch.setattr(models, "download", fake_download([]))
    monkeypatch.setattr(job, "execute", fake_execute(lambda req: Done(req.audio.with_suffix(".md"), False, 1.0), seen))
    code = app.main(["--transcribe", str(readable(tmp_path))] + extra)
    return code, seen


def test_cli_num_speakers_reaches_job(tmp_path, monkeypatch):
    code, seen = _run_with_num_speakers(tmp_path, monkeypatch, ["--speakers", "--num-speakers", "3"])
    assert code == 0 and seen[0].num_speakers == 3


def test_cli_num_speakers_default_is_2(tmp_path, monkeypatch):
    code, seen = _run_with_num_speakers(tmp_path, monkeypatch, ["--speakers"])
    assert code == 0 and seen[0].num_speakers == 2


@pytest.mark.parametrize("bad", ["1", "11", "0"])
def test_cli_num_speakers_out_of_range_exits_1(tmp_path, monkeypatch, capsys, bad):
    code, seen = _run_with_num_speakers(tmp_path, monkeypatch, ["--speakers", "--num-speakers", bad])
    assert code == 1 and seen == []
    assert strings.ERR_NUM_SPEAKERS in capsys.readouterr().err


def test_cli_num_speakers_ignored_without_speakers(tmp_path, monkeypatch):
    code, seen = _run_with_num_speakers(tmp_path, monkeypatch, ["--num-speakers", "1"])
    assert code == 0 and len(seen) == 1


def test_cli_download_failure_returns_1_with_czech_message(tmp_path, monkeypatch, capsys):
    def failing(*a, **k):
        raise models.DownloadError("network")

    monkeypatch.setattr(models, "download", failing)
    assert app.main(["--transcribe", str(readable(tmp_path))]) == 1
    assert "Model se nepodařilo stáhnout" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("code", "text"),
    [("busy", "právě stahuje v jiném okně"), ("corrupt", "Stažený model byl poškozený")],
)
def test_cli_busy_and_corrupt_download_return_1_with_czech_message(tmp_path, monkeypatch, capsys, code, text):
    def failing(*a, **k):
        raise models.DownloadError(code)

    monkeypatch.setattr(models, "download", failing)
    assert app.main(["--transcribe", str(readable(tmp_path))]) == 1
    assert text in capsys.readouterr().err


@pytest.mark.parametrize(("event", "code"), [(Failed("no_speech"), 3), (Failed("unreadable"), 2), (Failed("error", "x"), 1)])
def test_cli_exit_codes(tmp_path, monkeypatch, event, code):
    monkeypatch.setattr(models, "download", fake_download([]))
    monkeypatch.setattr(job, "execute", fake_execute(event))
    assert app.main(["--transcribe", str(readable(tmp_path)), "--format", "txt"]) == code


def test_main_writes_log_file(tmp_path):
    p = tmp_path / "x.m4a"
    p.write_bytes(b"nope")
    app.main(["--transcribe", str(p), "--no-download"])
    log = tmp_path / "home" / "logs" / "voice2text.log"
    assert log.exists() and "Voice2Text" in log.read_text(encoding="utf-8")


def test_missing_stdio_is_redirected_to_log(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    p = tmp_path / "x.m4a"
    p.write_bytes(b"nope")
    assert app.main(["--transcribe", str(p), "--no-download"]) == 2
    assert sys.stdout is not None and sys.stderr is not None
    print("hello from stdout")
    sys.stderr.write("hello from stderr\n")
    text = (tmp_path / "home" / "logs" / "voice2text.log").read_text(encoding="utf-8")
    assert "hello from stdout" in text and "hello from stderr" in text


def test_gui_launches_and_quits_offscreen(tmp_path):
    """Smoke: `python -m voice2text` opens the window (models present) and exits cleanly."""
    home = tmp_path / "smoke-home"
    for spec in models.required_specs(True):
        folder = models.model_path(spec, home / "models")
        folder.mkdir(parents=True)
        (folder / models.COMPLETE_MARKER).touch()
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", VOICE2TEXT_HOME=str(home), VOICE2TEXT_SMOKE_QUIT_MS="1500")
    proc = subprocess.run([sys.executable, "-m", "voice2text"], env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    log = (home / "logs" / "voice2text.log").read_text(encoding="utf-8")
    assert "gui" in log and "download" not in log.lower()


def test_unknown_argument_is_rejected_without_launching_the_gui(monkeypatch, capsys):
    monkeypatch.setattr(app, "_run_gui", lambda: pytest.fail("the window must not open"))
    assert app.main(["--transcibe", "x.m4a"]) == 1
    assert "--transcibe" in capsys.readouterr().err


def test_macos_process_serial_number_argument_is_ignored(monkeypatch):
    monkeypatch.setattr(app, "_run_gui", lambda: 0)
    assert app.main(["-psn_0_12345"]) == 0


def test_cli_speaker_failure_prints_note_and_exits_0(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(models, "download", fake_download([]))
    monkeypatch.setattr(
        job, "execute", fake_execute(lambda req: Done(req.audio.with_suffix(".md"), False, 1.0, speakers_failed=True))
    )
    audio = readable(tmp_path)
    assert app.main(["--transcribe", str(audio), "--speakers"]) == 0
    out, err = capsys.readouterr()
    assert strings.SPEAKERS_FAILED in err
    assert out.strip() == str(audio.with_suffix(".md"))


def test_cli_prints_diarizing_progress_in_10_percent_steps(tmp_path, monkeypatch, capsys):
    def execute(req, emit, *a, **k):
        emit(Progress("loading"))
        emit(Progress("transcribing", 1.0, None))
        emit(Progress("diarizing"))
        for f in (0.05, 0.1, 0.5, 0.55, 1.0):
            emit(Progress("diarizing", f, None))
        emit(Done(req.audio.with_suffix(".md"), False, 1.0))

    monkeypatch.setattr(models, "download", fake_download([]))
    monkeypatch.setattr(job, "execute", execute)
    assert app.main(["--transcribe", str(readable(tmp_path)), "--speakers"]) == 0
    lines = capsys.readouterr().err.splitlines()
    assert [l for l in lines if l.startswith(strings.DIARIZING)] == [
        strings.DIARIZING,
        "Rozlišuji mluvčí… 5 %",
        "Rozlišuji mluvčí… 50 %",
        "Rozlišuji mluvčí… 100 %",
    ]


class _Hints:
    def __init__(self):
        self.schemes = []

    def setColorScheme(self, scheme):
        self.schemes.append(scheme)


class _App:
    def __init__(self, hints):
        self._hints = hints

    def styleHints(self):
        return self._hints


def test_force_light_scheme_sets_light():
    from PySide6.QtCore import Qt

    hints = _Hints()
    app._force_light_scheme(_App(hints))
    assert hints.schemes == [Qt.ColorScheme.Light]


def test_force_light_scheme_tolerates_qt_without_it():
    app._force_light_scheme(_App(object()))  # older Qt: no setColorScheme, no error


def test_run_gui_forces_light_scheme(monkeypatch, tmp_path, qapp):
    home = tmp_path / "home"
    for spec in models.required_specs(True):
        folder = models.model_path(spec, home / "models")
        folder.mkdir(parents=True)
        (folder / models.COMPLETE_MARKER).touch()
    seen = []
    monkeypatch.setattr(app, "_force_light_scheme", seen.append)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # _run_gui installs its own
    monkeypatch.setenv("VOICE2TEXT_SMOKE_QUIT_MS", "50")
    assert app._run_gui() == 0
    assert seen == [qapp]


# --- VOICE2TEXT_CLI_SPAWN=1: the window's process path, driven from the CLI (CI smoke) ------


def spawned_with(target_args=(), target=None):
    """_execute_spawned with a fake transcriber; the worker saves next to tmp_path recordings."""
    from tests import fakes

    real = app._execute_spawned

    def run(req, emit):
        real(req, emit, target=target or fakes.worker_outside_temp_rule, target_args=target_args)

    return run


def test_cli_spawn_env_runs_the_job_in_a_spawned_worker(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VOICE2TEXT_CLI_SPAWN", "1")
    monkeypatch.setattr(models, "download", fake_download([]))
    monkeypatch.setattr(job, "execute", lambda *a, **k: pytest.fail("must not run in-process"))
    monkeypatch.setattr(app.paths, "fallback_output_dir", lambda: tmp_path / "fb")
    monkeypatch.setattr(app, "_execute_spawned", spawned_with(("tests.fakes:fake_transcribe",)))
    audio = readable(tmp_path, "ukázka porady.m4a")
    assert app.main(["--transcribe", str(audio), "--format", "txt"]) == 0
    out, err = capsys.readouterr()
    output = tmp_path / "ukázka porady.txt"
    assert out.strip() == str(output) and "Dobrý den" in output.read_text(encoding="utf-8")
    assert strings.PREPARING in err and "Přepisuji… 50 %" in err


@pytest.mark.parametrize(
    ("fake", "code"),
    [("tests.fakes:unreadable_transcribe", 2), ("tests.fakes:empty_transcribe", 3), ("tests.fakes:does_not_exist", 1)],
)
def test_cli_spawn_keeps_exit_codes(tmp_path, monkeypatch, fake, code):
    monkeypatch.setenv("VOICE2TEXT_CLI_SPAWN", "1")
    monkeypatch.setattr(models, "download", fake_download([]))
    monkeypatch.setattr(app.paths, "fallback_output_dir", lambda: tmp_path / "fb")
    monkeypatch.setattr(app, "_execute_spawned", spawned_with((fake,)))
    assert app.main(["--transcribe", str(readable(tmp_path))]) == code


def test_cli_spawn_worker_dying_without_result_exits_1(tmp_path, monkeypatch, capsys):
    from tests import fakes

    monkeypatch.setenv("VOICE2TEXT_CLI_SPAWN", "1")
    monkeypatch.setattr(models, "download", fake_download([]))
    monkeypatch.setattr(app, "_execute_spawned", spawned_with(target=fakes.silent_worker))
    assert app.main(["--transcribe", str(readable(tmp_path))]) == 1
    assert "Něco se nepovedlo" in capsys.readouterr().err


def test_execute_spawned_uses_the_real_worker_main(tmp_path):
    audio = readable(tmp_path)
    req = job.JobRequest(
        audio=audio, fmt="md", speakers=False, whisper_dir=tmp_path / "w",
        models_dir=tmp_path / "m", fallback_dir=tmp_path / "fb",
    )
    events = []
    app._execute_spawned(req, events.append, target_args=("tests.fakes:fake_transcribe",))
    assert isinstance(events[0], Progress) and events[0].stage == "loading"
    assert isinstance(events[-1], Done) and events[-1].output.exists()
    assert sum(isinstance(e, (Done, Failed)) for e in events) == 1


def test_cli_generic_error_points_to_the_logs_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(models, "download", fake_download([]))
    monkeypatch.setattr(job, "execute", fake_execute(Failed("error", "x")))
    assert app.main(["--transcribe", str(readable(tmp_path))]) == 1
    assert strings.ERR_GENERIC.format(log=tmp_path / "home" / "logs") in capsys.readouterr().err


def test_version_is_the_same_in_pyproject_and_package():
    """uv_build cannot read the version dynamically: a release bumps both (see docs/development.md)."""
    import tomllib

    import voice2text

    pyproject = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == voice2text.__version__


def test_main_does_not_detect_language_when_env_already_set(monkeypatch):
    monkeypatch.setenv("VOICE2TEXT_UI_LANG", "cs")

    def boom():
        raise AssertionError("ui_language() must not run when the env var is set")

    monkeypatch.setattr(app.locale_info, "ui_language", boom)
    assert app.main(["--bogus-flag"]) == 1


def test_main_exports_detected_language_when_env_unset(monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_UI_LANG", raising=False)
    monkeypatch.setattr(app.locale_info, "ui_language", lambda: "en")
    app.main(["--bogus-flag"])
    assert os.environ["VOICE2TEXT_UI_LANG"] == "en"


def test_cli_language_defaults_to_auto(tmp_path, monkeypatch):
    code, seen = _run_with_num_speakers(tmp_path, monkeypatch, [])
    assert code == 0 and seen[0].language == "auto"


def test_cli_language_reaches_job(tmp_path, monkeypatch):
    code, seen = _run_with_num_speakers(tmp_path, monkeypatch, ["--language", "cs"])
    assert code == 0 and seen[0].language == "cs"


def test_cli_unknown_language_exits_1_before_any_work(tmp_path, monkeypatch, capsys):
    code, seen = _run_with_num_speakers(tmp_path, monkeypatch, ["--language", "xx"])
    assert code == 1 and seen == []
    assert strings.ERR_LANGUAGE.format(code="xx") in capsys.readouterr().err


def test_cli_prints_detected_language_for_auto(tmp_path, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(models, "download", fake_download([]))
    monkeypatch.setattr(
        job, "execute", fake_execute(lambda req: Done(req.audio.with_suffix(".md"), False, 1.0, detected_language="de"), seen)
    )
    assert app.main(["--transcribe", str(readable(tmp_path))]) == 0
    assert strings.DETECTED_LANGUAGE.format(name="Němčina") in capsys.readouterr().err


def test_cli_no_detected_line_for_explicit_language(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(models, "download", fake_download([]))
    monkeypatch.setattr(job, "execute", fake_execute(lambda req: Done(req.audio.with_suffix(".md"), False, 1.0), []))
    assert app.main(["--transcribe", str(readable(tmp_path)), "--language", "cs"]) == 0
    assert "Rozpoznaný jazyk" not in capsys.readouterr().err
