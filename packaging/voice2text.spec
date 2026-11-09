# PyInstaller spec for Voice2Text (macOS .app and Windows folder build).
# Build: uv run pyinstaller packaging/voice2text.spec --noconfirm
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from voice2text import __version__ as version  # noqa: E402

datas, binaries, hiddenimports = [], [], []
for pkg in ("faster_whisper", "ctranslate2", "av", "sherpa_onnx", "onnxruntime"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

is_mac = sys.platform == "darwin"
icon = str(ROOT / "packaging" / "icon.png")

a = Analysis(
    [str(ROOT / "src" / "voice2text" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Voice2Text",
    console=False,  # windowed
    icon=icon,
    target_arch="arm64" if is_mac else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Voice2Text",
)
if is_mac:
    app = BUNDLE(
        coll,
        name="Voice2Text.app",
        icon=icon,
        bundle_identifier="cz.xxbedy.voice2text",
        version=version,
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "14.0",
        },
    )
