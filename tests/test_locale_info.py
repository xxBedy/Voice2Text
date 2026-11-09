from __future__ import annotations

import subprocess
import sys
import types

import pytest

from voice2text import locale_info

MAC_CS = '(\n    "cs-CZ",\n    "en-CZ"\n)\n'
MAC_DE = '(\n    "de-DE"\n)\n'


def _fake_run(stdout="", returncode=0, exc=None):
    def run(*args, **kwargs):
        if exc:
            raise exc
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    return run


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("VOICE2TEXT_UI_LANG", "en")
    monkeypatch.setattr(locale_info, "system_language_tag", lambda: "cs-CZ")
    assert locale_info.ui_language() == "en"
    monkeypatch.setenv("VOICE2TEXT_UI_LANG", "cs")
    monkeypatch.setattr(locale_info, "system_language_tag", lambda: "de-DE")
    assert locale_info.ui_language() == "cs"


def test_invalid_env_is_ignored(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # win32 asks the display language instead
    monkeypatch.setenv("VOICE2TEXT_UI_LANG", "fr")
    monkeypatch.setattr(locale_info, "system_language_tag", lambda: "cs-CZ")
    assert locale_info.ui_language() == "cs"


@pytest.mark.parametrize("tag,expected", [("cs-CZ", "cs"), ("cs", "cs"), ("de-DE", "en"), (None, "en")])
def test_ui_language_from_system(monkeypatch, tag, expected):
    monkeypatch.setattr(sys, "platform", "linux")  # win32 asks the display language instead
    monkeypatch.delenv("VOICE2TEXT_UI_LANG", raising=False)
    monkeypatch.setattr(locale_info, "system_language_tag", lambda: tag)
    assert locale_info.ui_language() == expected


def test_macos_first_language(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", _fake_run(MAC_CS))
    assert locale_info.system_language_tag() == "cs-CZ"
    monkeypatch.setattr(subprocess, "run", _fake_run(MAC_DE))
    tag = locale_info.system_language_tag()
    assert tag == "de-DE"
    monkeypatch.delenv("VOICE2TEXT_UI_LANG", raising=False)
    assert locale_info.ui_language() == "en"


@pytest.mark.parametrize(
    "run",
    [_fake_run(exc=OSError("nope")), _fake_run("", returncode=1), _fake_run("garbage"), _fake_run("(\n)\n")],
)
def test_macos_failure_falls_back_to_locale(monkeypatch, run):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(locale_info.locale, "getlocale", lambda *a, **k: ("cs_CZ", "UTF-8"))
    assert locale_info.system_language_tag() == "cs_CZ"


def test_windows_uses_user_default_locale_name(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    class Kernel32:
        def GetUserDefaultLocaleName(self, buf, size):
            buf.value = "cs-CZ"
            return 6

    fake = types.SimpleNamespace(
        create_unicode_buffer=lambda n: types.SimpleNamespace(value=""),
        windll=types.SimpleNamespace(kernel32=Kernel32()),
    )
    monkeypatch.setattr(locale_info, "_ctypes", lambda: fake, raising=False)
    assert locale_info.system_language_tag() == "cs-CZ"


def test_never_raises(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def boom(*a, **k):
        raise ValueError("x")

    monkeypatch.setattr(locale_info.locale, "getlocale", boom)
    assert locale_info.system_language_tag() is None


SUPPORTED = {"cs", "de", "zh", "no", "he", "en", "jw"}


@pytest.mark.parametrize(
    "tag,expected",
    [("cs-CZ", "cs"), ("de", "de"), ("zh-Hans-CN", "zh"), ("nb-NO", "no"), ("nn_NO", "no"), ("xx", None), (None, None), ("", None), ("he-IL", "he"), ("jv-ID", "jw")],
)
def test_whisper_code_for(tag, expected):
    assert locale_info.whisper_code_for(tag, SUPPORTED) == expected


def test_whisper_code_for_old_hebrew_and_javanese():
    assert locale_info.whisper_code_for("he", {"iw", "jw"}) == "iw"
    assert locale_info.whisper_code_for("jv", {"jv"}) == "jv"
    assert locale_info.whisper_code_for("jv", {"jw"}) == "jw"


def test_windows_style_english_names_for_czech_count_as_czech(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(locale_info.locale, "getlocale", lambda *a, **k: ("Czech_Czechia", "1250"))
    monkeypatch.delenv("VOICE2TEXT_UI_LANG", raising=False)
    assert locale_info.ui_language() == "cs"


def test_other_long_locale_names_stay_english(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(locale_info.locale, "getlocale", lambda *a, **k: ("German_Germany", "1252"))
    monkeypatch.delenv("VOICE2TEXT_UI_LANG", raising=False)
    assert locale_info.ui_language() == "en"


def _fake_win_ctypes(ui_langid, locale_name="en-US"):
    class Kernel32:
        def GetUserDefaultUILanguage(self):
            return ui_langid

        def GetUserDefaultLocaleName(self, buf, size):
            buf.value = locale_name
            return len(locale_name) + 1

    return types.SimpleNamespace(
        create_unicode_buffer=lambda n: types.SimpleNamespace(value=""),
        windll=types.SimpleNamespace(kernel32=Kernel32()),
    )


@pytest.mark.parametrize(
    "langid,locale_name,expected",
    [
        (0x0405, "en-US", "cs"),  # Czech display language, US regional format
        (0x0409, "cs-CZ", "en"),  # English display language, Czech regional format
        (0x0805, "en-GB", "cs"),  # any sublanguage of Czech
        (0x0407, "de-DE", "en"),
    ],
)
def test_windows_ui_language_follows_display_language(monkeypatch, langid, locale_name, expected):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("VOICE2TEXT_UI_LANG", raising=False)
    monkeypatch.setattr(locale_info, "_ctypes", lambda: _fake_win_ctypes(langid, locale_name), raising=False)
    assert locale_info.ui_language() == expected
    # the recording-language default still follows the regional format
    assert locale_info.system_language_tag() == locale_name


def test_windows_ui_language_failure_falls_back_to_english(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("VOICE2TEXT_UI_LANG", raising=False)

    def boom():
        raise OSError("no ctypes")

    monkeypatch.setattr(locale_info, "_ctypes", boom, raising=False)
    assert locale_info.ui_language() == "en"


def test_windows_ui_language_env_override_still_wins(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("VOICE2TEXT_UI_LANG", "cs")
    monkeypatch.setattr(locale_info, "_ctypes", lambda: _fake_win_ctypes(0x0409), raising=False)
    assert locale_info.ui_language() == "cs"
