"""Render the idle window offscreen into docs/images/window-{en,cs}.png.

Usage: uv run python scripts/render_screenshots.py
The UI language is fixed at import time, so each language runs in its own process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGS = ("en", "cs")


def render(lang: str, target: Path) -> None:
    os.environ["VOICE2TEXT_UI_LANG"] = lang
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtCore import QObject, QSettings, Signal
    from PySide6.QtWidgets import QApplication

    from voice2text.app import _force_light_scheme
    from voice2text.gui.main_window import MainWindow
    from voice2text.models import COMPLETE_MARKER, model_path, required_specs

    class FakeRunner(QObject):
        progress = Signal(object)
        finished = Signal(object)

        def start(self, req): ...
        def cancel(self): ...
        def is_running(self):
            return False

    class FakeDownloader(QObject):
        progress = Signal("qint64", "qint64")
        failed = Signal(str)
        succeeded = Signal()

        def start(self): ...
        def cancel(self): ...
        def isRunning(self):
            return False

        def wait(self, *a):
            return True

    app = QApplication.instance() or QApplication(sys.argv[:1])
    _force_light_scheme(app)
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = Path(tmp) / "models"
        for spec in required_specs(True):  # pre-made markers: the window starts in the idle state
            folder = model_path(spec, models_dir)
            folder.mkdir(parents=True)
            (folder / COMPLETE_MARKER).touch()
        settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("speakers", True)
        settings.setValue("audio_language", "auto")  # deterministic, independent of this machine
        window = MainWindow(FakeRunner(), FakeDownloader, settings, models_dir, True)
        window.show()
        app.processEvents()
        window.grab().save(str(target), "PNG")
        window.close()


def strip_metadata(path: Path) -> None:
    from PIL import Image

    with Image.open(path) as im:
        clean = im.convert("RGBA").copy()
        clean.info.clear()
    clean.save(path, "PNG", optimize=True)  # no text/ICC/dpi chunks carried over


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        lang = sys.argv[2]
        render(lang, ROOT / "docs" / "images" / f"window-{lang}.png")
        return 0
    (ROOT / "docs" / "images").mkdir(parents=True, exist_ok=True)
    for lang in LANGS:
        subprocess.run([sys.executable, __file__, "--child", lang], check=True)
        strip_metadata(ROOT / "docs" / "images" / f"window-{lang}.png")
        print(f"docs/images/window-{lang}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
