"""System / UI language detection. Qt-free; must not import `strings`."""

from __future__ import annotations

import locale
import os
import re
import subprocess
import sys
from collections.abc import Collection
from typing import Literal

UI_LANG_ENV = "VOICE2TEXT_UI_LANG"

_ALIASES = {"nb": "no", "nn": "no"}


def _ctypes():
    import ctypes

    return ctypes


def _macos_tag() -> str | None:
    try:
        out = subprocess.run(
            ["/usr/bin/defaults", "read", "-g", "AppleLanguages"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    match = re.search(r'["\']?([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*)["\']?\s*[,)]', out.stdout)
    return match.group(1) if match else None


def _windows_tag() -> str | None:
    try:
        ctypes = _ctypes()
        buf = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
            return buf.value or None
    except Exception:
        return None
    return None


_LANG_CZECH = 0x05  # primary language ID of Czech in a Windows LANGID


def _windows_ui_is_czech() -> bool:
    """True if the Windows display language is Czech (GetUserDefaultUILanguage)."""
    try:
        langid = int(_ctypes().windll.kernel32.GetUserDefaultUILanguage())
    except Exception:
        return False
    return langid & 0x3FF == _LANG_CZECH


def _locale_tag() -> str | None:
    try:
        return locale.getlocale()[0] or None
    except Exception:
        return None


def system_language_tag() -> str | None:
    """BCP-47-ish tag of the user's preferred language, e.g. `cs-CZ`. Never raises."""
    try:
        tag = None
        if sys.platform == "darwin":
            tag = _macos_tag()
        elif sys.platform == "win32":
            tag = _windows_tag()
        return tag or _locale_tag()
    except Exception:
        return None


def ui_language() -> Literal["cs", "en"]:
    forced = os.environ.get(UI_LANG_ENV, "").strip().lower()
    if forced in ("cs", "en"):
        return forced  # type: ignore[return-value]
    if sys.platform == "win32":
        # The UI follows the Windows display language, not the regional format
        # (which still picks the default recording language).
        return "cs" if _windows_ui_is_czech() else "en"
    tag = system_language_tag()
    # Windows' C runtime reports English names such as "Czech_Czechia".
    return "cs" if tag and tag.lower().startswith(("cs", "czech")) else "en"


def whisper_code_for(tag: str | None, supported: Collection[str]) -> str | None:
    """Map a system language tag to a Whisper language code, or None if unsupported."""
    if not tag:
        return None
    primary = re.split(r"[-_.@]", tag.strip().lower())[0]
    if not primary:
        return None
    primary = _ALIASES.get(primary, primary)
    if primary == "he":
        primary = "iw" if "iw" in supported else "he"
    elif primary == "jv":
        primary = "jv" if "jv" in supported else "jw"
    return primary if primary in supported else None
