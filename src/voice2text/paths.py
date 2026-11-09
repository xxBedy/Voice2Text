import os
import sys
from pathlib import Path

from voice2text import strings


def app_home() -> Path:
    override = os.environ.get("VOICE2TEXT_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "Voice2Text"
    return Path.home() / "Library" / "Application Support" / "Voice2Text"


def models_dir() -> Path:
    return app_home() / "models"


def logs_dir() -> Path:
    return app_home() / "logs"


def log_file() -> Path:
    return logs_dir() / "voice2text.log"


def documents_dir() -> Path:
    from PySide6.QtCore import QStandardPaths  # lazy: the worker process imports this module

    loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(loc) if loc else Path.home() / "Documents"


def fallback_output_dir() -> Path:
    return documents_dir() / ("Přepisy" if strings.LANG == "cs" else "Transcripts")
