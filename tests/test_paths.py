import sys
from pathlib import Path

import pytest

from voice2text import paths


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE2TEXT_HOME", str(tmp_path))
    assert paths.app_home() == tmp_path
    assert paths.models_dir() == tmp_path / "models"
    assert paths.logs_dir() == tmp_path / "logs"
    assert paths.fallback_output_dir().name == "Přepisy"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS default location")
def test_macos_default(monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_HOME", raising=False)
    assert paths.app_home() == Path.home() / "Library/Application Support/Voice2Text"


def test_log_file_lives_in_logs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE2TEXT_HOME", str(tmp_path))
    assert paths.log_file() == tmp_path / "logs" / "voice2text.log"


def test_fallback_dir_is_czech_in_czech_ui(monkeypatch):
    monkeypatch.setattr(paths, "documents_dir", lambda: Path("/docs"))
    monkeypatch.setattr(paths.strings, "LANG", "cs")
    assert paths.fallback_output_dir() == Path("/docs/Přepisy")


def test_fallback_dir_is_english_in_english_ui(monkeypatch):
    monkeypatch.setattr(paths, "documents_dir", lambda: Path("/docs"))
    monkeypatch.setattr(paths.strings, "LANG", "en")
    assert paths.fallback_output_dir() == Path("/docs/Transcripts")


def test_english_saved_fallback_names_the_real_folder(monkeypatch):
    import importlib

    from voice2text import strings

    monkeypatch.setenv("VOICE2TEXT_UI_LANG", "en")
    try:
        importlib.reload(strings)
        assert "Documents/Transcripts" in strings.SAVED_FALLBACK
    finally:
        monkeypatch.setenv("VOICE2TEXT_UI_LANG", "cs")
        importlib.reload(strings)
