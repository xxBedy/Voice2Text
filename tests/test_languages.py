from __future__ import annotations

import importlib

import pytest

from voice2text import languages, locale_info, strings


def test_top_order_is_fixed():
    assert languages.TOP == ("cs", "sk", "en", "de", "pl", "uk", "ru", "fr", "es", "it")


def test_all_codes_cover_top_and_many_more():
    codes = languages.all_codes()
    assert set(languages.TOP) <= set(codes)
    assert len(codes) >= 90 and len(set(codes)) == len(codes)


def test_choices_start_with_auto_then_top_localised_then_rest_sorted():
    ch = languages.choices()
    assert ch[0] == ("auto", strings.LANG_AUTO)
    assert [c for c, _ in ch[1:11]] == list(languages.TOP)
    assert ch[1] == ("cs", "Čeština") and ch[3] == ("en", "Angličtina")
    rest = ch[11:]
    assert {c for c, _ in rest} == set(languages.all_codes()) - set(languages.TOP)
    names = [n for _, n in rest]
    assert names == sorted(names, key=str.casefold)
    assert len(ch) == len(languages.all_codes()) + 1
    assert all(n for _, n in ch)


def test_choices_are_english_in_english_ui(monkeypatch):
    monkeypatch.setenv("VOICE2TEXT_UI_LANG", "en")
    try:
        importlib.reload(strings)
        ch = languages.choices()
        assert ch[0] == ("auto", "Automatic")
        assert ch[1] == ("cs", "Czech") and ch[4] == ("de", "German")
    finally:
        monkeypatch.setenv("VOICE2TEXT_UI_LANG", "cs")
        importlib.reload(strings)


def test_display_name():
    assert languages.display_name("cs") == "Čeština"
    assert languages.display_name("haw") == "Hawaiian"
    assert languages.display_name("auto") == strings.LANG_AUTO
    assert languages.display_name("zz") == "zz"


@pytest.mark.parametrize(
    "tag,expected",
    [("de-DE", "de"), ("cs-CZ", "cs"), ("xx-XX", "auto"), (None, "auto"), ("nb-NO", "no")],
)
def test_default_choice(monkeypatch, tag, expected):
    monkeypatch.setattr(locale_info, "system_language_tag", lambda: tag)
    assert languages.default_choice() == expected


def test_all_codes_falls_back_to_vendored_copy(monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("faster_whisper"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    codes = languages.all_codes()
    assert set(languages.TOP) <= set(codes) and len(codes) >= 90
