from __future__ import annotations

import importlib
import string as _string

import pytest

from voice2text import strings

V01_CS = {'WINDOW_TITLE': 'Přepis řeči',
 'DROP_HINT': 'Přetáhni sem nahrávku',
 'DROP_SUB': 'nebo klikni a vyber soubor',
 'DROP_NEXT': 'Další nahrávku přetáhni sem',
 'SAVE_AS': 'Uložit jako:',
 'FMT_MD': 'Markdown',
 'FMT_TXT': 'TXT',
 'SPEAKERS': 'Rozlišit mluvčí',
 'SPEAKER_COUNT': 'Počet mluvčích:',
 'SPEAKERS_NOTE': '(pomalejší)',
 'SPEAKERS_UNAVAILABLE': '(zatím nedostupné – chybí model)',
 'SPEAKER_LABEL': 'Mluvčí {n}:',
 'PREPARING': 'Připravuji…',
 'TRANSCRIBING': 'Přepisuji… {pct} %',
 'DIARIZING': 'Rozlišuji mluvčí…',
 'DIARIZING_PCT': 'Rozlišuji mluvčí… {pct} %',
 'SAVING': 'Ukládám…',
 'CANCEL': 'Zrušit',
 'DONE': '✓ Hotovo za {elapsed}',
 'SAVED_NEXT_TO': 'Uloženo: {name} (vedle nahrávky)',
 'SAVED_FALLBACK': 'Uloženo do složky Dokumenty/Přepisy: {name}',
 'OPEN': 'Otevřít přepis',
 'SHOW': 'Ukázat ve složce',
 'SPEAKERS_FAILED': 'Mluvčí se nepodařilo rozlišit, přepis je bez nich.',
 'FILE_DIALOG_TITLE': 'Vyber nahrávku',
 'ERR_UNREADABLE': 'Tento soubor neumím přečíst.',
 'ERR_NOT_FILE': 'Nahrávku nejdřív ulož do složky a pak ji přetáhni sem.',
 'ERR_NUM_SPEAKERS': 'Počet mluvčích musí být 2 až 10.',
 'ERR_NO_SPEECH': 'V nahrávce nebyla rozpoznána žádná řeč.',
 'ERR_BUSY': 'Počkej, až doběhne aktuální přepis.',
 'ERR_GENERIC': 'Něco se nepovedlo. Podrobnosti jsou ve složce {log}.',
 'CONFIRM_QUIT_TITLE': 'Přepis ještě běží',
 'CONFIRM_QUIT': 'Přepis ještě běží. Opravdu ukončit?',
 'QUIT_CONFIRM_YES': 'Ukončit',
 'QUIT_CONFIRM_NO': 'Pokračovat v přepisu',
 'MODEL_NEEDED': 'Před prvním přepisem je potřeba stáhnout model řeči (asi 3 GB). Potom už aplikace funguje '
                 'bez internetu.',
 'MODEL_DOWNLOADING': 'Stahuji model… {pct} % ({done} z {total})',
 'MODEL_RETRY': 'Zkusit znovu',
 'MODEL_IMPORT': 'Nahrát model ručně…',
 'MODEL_IMPORT_TITLE': 'Vyber složku s modelem',
 'MODEL_IMPORTING': 'Kopíruji model…',
 'ERR_DOWNLOAD_NETWORK': 'Model se nepodařilo stáhnout. Zkontroluj připojení k internetu, nebo model nahraj '
                         'ručně.',
 'ERR_DOWNLOAD_SPACE': 'Na disku není dost místa pro model (potřeba asi 3,5 GB).',
 'ERR_DOWNLOAD_BUSY': 'Model se právě stahuje v jiném okně aplikace. Zavři ho, nebo počkej, a pak to zkus '
                      'znovu.',
 'ERR_DOWNLOAD_CORRUPT': 'Stažený model byl poškozený. Zkus to prosím znovu.',
 'ERR_IMPORT_INVALID': 'Ve vybrané složce není model řeči.',
 'SEPARATOR': ' · '}

NEW_CONSTANTS = [
    "LANG", "AUDIO_LANGUAGE", "LANG_AUTO", "DETECTED_LANGUAGE", "ERR_LANGUAGE",
    "FILE_FILTER", "FILE_FILTER_ALL", "LANGUAGE_NAMES",
]
TOP = ["cs", "sk", "en", "de", "pl", "uk", "ru", "fr", "es", "it"]
EXTENSIONS = "*.m4a *.mp3 *.wav *.aac *.mp4 *.flac *.ogg *.opus *.webm *.aiff *.aif *.wma *.mov *.mkv"


def _load(lang: str, monkeypatch):
    monkeypatch.setenv("VOICE2TEXT_UI_LANG", lang)
    return importlib.reload(strings)


@pytest.fixture(autouse=True)
def _restore_cs(monkeypatch):
    yield
    monkeypatch.setenv("VOICE2TEXT_UI_LANG", "cs")
    importlib.reload(strings)


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in _string.Formatter().parse(text) if name}


def _constants(mod) -> dict:
    return {k: v for k, v in vars(mod).items() if k.isupper()}


def test_czech_values_equal_v0_1(monkeypatch):
    cs = _load("cs", monkeypatch)
    for name, value in V01_CS.items():
        assert getattr(cs, name) == value, name
    assert cs.LANG == "cs"
    assert cs.FILE_FILTER == f"Nahrávky ({EXTENSIONS})"
    assert cs.FILE_FILTER_ALL == "Všechny soubory (*)"
    assert cs.AUDIO_LANGUAGE == "Jazyk nahrávky:"
    assert cs.LANG_AUTO == "Automaticky"
    assert cs.DETECTED_LANGUAGE == "Rozpoznaný jazyk: {name}"
    assert cs.ERR_LANGUAGE == "Neznámý jazyk: {code}"


def test_both_languages_define_every_constant_with_same_placeholders(monkeypatch):
    cs = _constants(_load("cs", monkeypatch))
    en = _constants(_load("en", monkeypatch))
    assert set(cs) == set(en)
    assert set(V01_CS) | set(NEW_CONSTANTS) <= set(cs)
    for name, value in cs.items():
        if isinstance(value, str) and name != "LANG":
            assert _fields(value) == _fields(en[name]), name
            assert en[name].strip(), name
    assert list(cs["LANGUAGE_NAMES"]) == TOP and list(en["LANGUAGE_NAMES"]) == TOP


def test_english_copy(monkeypatch):
    en = _load("en", monkeypatch)
    assert en.LANG == "en"
    assert en.WINDOW_TITLE == "Speech to Text"
    assert en.SPEAKER_LABEL == "Speaker {n}:"
    assert en.AUDIO_LANGUAGE == "Recording language:"
    assert en.LANG_AUTO == "Automatic"
    assert en.DETECTED_LANGUAGE == "Detected language: {name}"
    assert en.ERR_LANGUAGE == "Unknown language: {code}"
    assert en.FILE_FILTER == f"Recordings ({EXTENSIONS})"
    assert en.FILE_FILTER_ALL == "All files (*)"
    assert en.LANGUAGE_NAMES["cs"] == "Czech" and en.LANGUAGE_NAMES["de"] == "German"
    assert en.DONE.format(elapsed="11 min") == "✓ Done in 11 min"


def test_czech_language_names(monkeypatch):
    cs = _load("cs", monkeypatch)
    assert cs.LANGUAGE_NAMES["cs"] == "Čeština" and cs.LANGUAGE_NAMES["de"] == "Němčina"


def test_import_follows_env(monkeypatch):
    assert _load("en", monkeypatch).LANG == "en"
    assert _load("cs", monkeypatch).LANG == "cs"


def test_format_minutes(monkeypatch):
    for lang in ("cs", "en"):
        m = _load(lang, monkeypatch)
        assert m.format_minutes(40) == "40 s"
        assert m.format_minutes(61) == "2 min"
        assert m.format_minutes(660) == "11 min"
        assert m.format_minutes(59.5) == "1 min"


def test_format_eta(monkeypatch):
    cs = _load("cs", monkeypatch)
    assert cs.format_eta(None) == ""
    assert cs.format_eta(30) == "zbývá méně než minuta"
    assert cs.format_eta(181) == "zbývá cca 4 min"
    en = _load("en", monkeypatch)
    assert en.format_eta(None) == ""
    assert en.format_eta(30) == "less than a minute left"
    assert en.format_eta(181) == "about 4 min left"


def test_format_bytes(monkeypatch):
    cs = _load("cs", monkeypatch)
    assert cs.format_bytes(1_234_000_000) == "1,2 GB"
    assert cs.format_bytes(75_000_000) == "75 MB"
    assert cs.format_bytes(0) == "0 MB"
    assert cs.format_bytes(999_400_000) == "999 MB"
    assert cs.format_bytes(999_600_000) == "1,0 GB"
    en = _load("en", monkeypatch)
    assert en.format_bytes(1_234_000_000) == "1.2 GB"
    assert en.format_bytes(75_000_000) == "75 MB"
    assert en.format_bytes(999_600_000) == "1.0 GB"


def test_language_follows_system_when_env_unset(monkeypatch):
    """Without the override, an English system language gives English copy (CI runners)."""
    from voice2text import locale_info

    monkeypatch.delenv("VOICE2TEXT_UI_LANG", raising=False)
    monkeypatch.setattr(locale_info.sys, "platform", "linux")  # win32 asks the display language
    monkeypatch.setattr(locale_info, "system_language_tag", lambda: "en-US")
    en = importlib.reload(strings)
    assert en.LANG == "en"
    assert en.LANGUAGE_NAMES["cs"] == "Czech"
    monkeypatch.setattr(locale_info, "system_language_tag", lambda: "cs-CZ")
    assert importlib.reload(strings).LANG == "cs"
    # Leave the module in Czech whatever the host language is.
    monkeypatch.setenv("VOICE2TEXT_UI_LANG", "cs")
    assert importlib.reload(strings).LANG == "cs"
