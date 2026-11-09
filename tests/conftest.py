from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

# The suite asserts Czech copy and `voice2text.strings` picks its language at import time, so pin it
# before anything imports `voice2text` -- otherwise the host locale (e.g. an English CI runner) leaks in.
os.environ["VOICE2TEXT_UI_LANG"] = "cs"

from voice2text import models  # noqa: E402

# GUI tests run headless unless the developer asks for a real platform.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

CACHE_DIR = Path.home() / ".cache" / "voice2text-tests" / "models"


@pytest.fixture(scope="session")
def whisper_dir() -> Path:
    env = {} if os.environ.get("VOICE2TEXT_WHISPER_MODEL") else {"VOICE2TEXT_WHISPER_MODEL": "tiny"}
    with mock.patch.dict(os.environ, env):
        spec = models.whisper_spec()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not models.is_installed(spec, CACHE_DIR):
        models.download(spec, CACHE_DIR, lambda done, total: None)
    return models.model_path(spec, CACHE_DIR)


@pytest.fixture(scope="session")
def diar_models_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for spec in models.diarization_specs():
        if not models.is_installed(spec, CACHE_DIR):
            models.download(spec, CACHE_DIR, lambda done, total: None)
    return CACHE_DIR


@pytest.fixture(autouse=True)
def _temp_app_home(tmp_path_factory, monkeypatch):
    """Spawned workers log into the app folder; keep tests out of the real one."""
    monkeypatch.setenv("VOICE2TEXT_HOME", str(tmp_path_factory.mktemp("app-home")))


@pytest.fixture(autouse=True)
def _hermetic_system_temp(tmp_path_factory, monkeypatch):
    """pytest's tmp_path lies inside the real system temp folder, where the app saves to the
    fallback folder by design. In-process tests see an empty dedicated folder as "the system
    temp" instead; tests/test_output.py checks the real rule."""
    from voice2text import output

    fake = tmp_path_factory.mktemp("system-temp")
    monkeypatch.setattr(output, "temp_roots", lambda: [fake])


@pytest.fixture(autouse=True)
def _czech_ui(monkeypatch):
    """The suite asserts Czech copy; tests of English copy reload `strings` themselves."""
    monkeypatch.setenv("VOICE2TEXT_UI_LANG", "cs")
