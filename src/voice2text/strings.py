"""User-visible strings (Czech and English) and number formatting. All UI copy lives here.

The language is chosen once at import by `locale_info.ui_language()` (env `VOICE2TEXT_UI_LANG`
wins), so every module sees the same copy and spawned workers match the parent process.
"""

from __future__ import annotations

import math

from voice2text import locale_info

_CS = {
    "WINDOW_TITLE": "Přepis řeči",
    "DROP_HINT": "Přetáhni sem nahrávku",
    "DROP_SUB": "nebo klikni a vyber soubor",
    "DROP_NEXT": "Další nahrávku přetáhni sem",
    "SAVE_AS": "Uložit jako:",
    "FMT_MD": "Markdown",
    "FMT_TXT": "TXT",
    "SPEAKERS": "Rozlišit mluvčí",
    "SPEAKER_COUNT": "Počet mluvčích:",
    "SPEAKERS_NOTE": "(pomalejší)",
    "SPEAKERS_UNAVAILABLE": "(zatím nedostupné – chybí model)",
    "SPEAKER_LABEL": "Mluvčí {n}:",
    "PREPARING": "Připravuji…",
    "TRANSCRIBING": "Přepisuji… {pct} %",
    "DIARIZING": "Rozlišuji mluvčí…",
    "DIARIZING_PCT": "Rozlišuji mluvčí… {pct} %",
    "SAVING": "Ukládám…",
    "CANCEL": "Zrušit",
    "DONE": "✓ Hotovo za {elapsed}",
    "SAVED_NEXT_TO": "Uloženo: {name} (vedle nahrávky)",
    "SAVED_FALLBACK": "Uloženo do složky Dokumenty/Přepisy: {name}",
    "OPEN": "Otevřít přepis",
    "SHOW": "Ukázat ve složce",
    "SPEAKERS_FAILED": "Mluvčí se nepodařilo rozlišit, přepis je bez nich.",
    "FILE_DIALOG_TITLE": "Vyber nahrávku",
    "ERR_UNREADABLE": "Tento soubor neumím přečíst.",
    "ERR_NOT_FILE": "Nahrávku nejdřív ulož do složky a pak ji přetáhni sem.",
    "ERR_NUM_SPEAKERS": "Počet mluvčích musí být 2 až 10.",
    "ERR_NO_SPEECH": "V nahrávce nebyla rozpoznána žádná řeč.",
    "ERR_BUSY": "Počkej, až doběhne aktuální přepis.",
    "ERR_GENERIC": "Něco se nepovedlo. Podrobnosti jsou ve složce {log}.",
    "CONFIRM_QUIT_TITLE": "Přepis ještě běží",
    "CONFIRM_QUIT": "Přepis ještě běží. Opravdu ukončit?",
    "QUIT_CONFIRM_YES": "Ukončit",
    "QUIT_CONFIRM_NO": "Pokračovat v přepisu",
    "MODEL_NEEDED": "Před prvním přepisem je potřeba stáhnout model řeči (asi 3 GB). Potom už aplikace funguje bez internetu.",
    "MODEL_DOWNLOADING": "Stahuji model… {pct} % ({done} z {total})",
    "MODEL_RETRY": "Zkusit znovu",
    "MODEL_IMPORT": "Nahrát model ručně…",
    "MODEL_IMPORT_TITLE": "Vyber složku s modelem",
    "MODEL_IMPORTING": "Kopíruji model…",
    "ERR_DOWNLOAD_NETWORK": "Model se nepodařilo stáhnout. Zkontroluj připojení k internetu, nebo model nahraj ručně.",
    "ERR_DOWNLOAD_SPACE": "Na disku není dost místa pro model (potřeba asi 3,5 GB).",
    "ERR_DOWNLOAD_BUSY": "Model se právě stahuje v jiném okně aplikace. Zavři ho, nebo počkej, a pak to zkus znovu.",
    "ERR_DOWNLOAD_CORRUPT": "Stažený model byl poškozený. Zkus to prosím znovu.",
    "ERR_IMPORT_INVALID": "Ve vybrané složce není model řeči.",
    "FILE_FILTER": "Nahrávky (*.m4a *.mp3 *.wav *.aac *.mp4 *.flac *.ogg *.opus *.webm *.aiff *.aif *.wma *.mov *.mkv)",
    "AUDIO_LANGUAGE": "Jazyk nahrávky:",
    "LANG_AUTO": "Automaticky",
    "DETECTED_LANGUAGE": "Rozpoznaný jazyk: {name}",
    "ERR_LANGUAGE": "Neznámý jazyk: {code}",
    "FILE_FILTER_ALL": "Všechny soubory (*)",
}

_EN = {
    "WINDOW_TITLE": "Speech to Text",
    "DROP_HINT": "Drop a recording here",
    "DROP_SUB": "or click to choose a file",
    "DROP_NEXT": "Drop the next recording here",
    "SAVE_AS": "Save as:",
    "FMT_MD": "Markdown",
    "FMT_TXT": "TXT",
    "SPEAKERS": "Identify speakers",
    "SPEAKER_COUNT": "Number of speakers:",
    "SPEAKERS_NOTE": "(slower)",
    "SPEAKERS_UNAVAILABLE": "(not available yet – model missing)",
    "SPEAKER_LABEL": "Speaker {n}:",
    "PREPARING": "Preparing…",
    "TRANSCRIBING": "Transcribing… {pct}%",
    "DIARIZING": "Identifying speakers…",
    "DIARIZING_PCT": "Identifying speakers… {pct}%",
    "SAVING": "Saving…",
    "CANCEL": "Cancel",
    "DONE": "✓ Done in {elapsed}",
    "SAVED_NEXT_TO": "Saved: {name} (next to the recording)",
    "SAVED_FALLBACK": "Saved to Documents/Transcripts: {name}",
    "OPEN": "Open transcript",
    "SHOW": "Show in folder",
    "SPEAKERS_FAILED": "Couldn't identify the speakers, so the transcript has no speaker labels.",
    "FILE_DIALOG_TITLE": "Choose a recording",
    "FILE_FILTER": "Recordings (*.m4a *.mp3 *.wav *.aac *.mp4 *.flac *.ogg *.opus *.webm *.aiff *.aif *.wma *.mov *.mkv)",
    "ERR_UNREADABLE": "This file can't be read.",
    "ERR_NOT_FILE": "Save the recording to a folder first, then drop it here.",
    "ERR_NUM_SPEAKERS": "The number of speakers must be between 2 and 10.",
    "ERR_NO_SPEECH": "No speech was found in the recording.",
    "ERR_BUSY": "Wait until the current transcription finishes.",
    "ERR_GENERIC": "Something went wrong. Details are in {log}.",
    "CONFIRM_QUIT_TITLE": "Transcription in progress",
    "CONFIRM_QUIT": "A transcription is still running. Quit anyway?",
    "QUIT_CONFIRM_YES": "Quit",
    "QUIT_CONFIRM_NO": "Keep transcribing",
    "MODEL_NEEDED": "The speech model (about 3 GB) must be downloaded before the first transcription. After that, the app works offline.",
    "MODEL_DOWNLOADING": "Downloading model… {pct}% ({done} of {total})",
    "MODEL_RETRY": "Try again",
    "MODEL_IMPORT": "Import model manually…",
    "MODEL_IMPORT_TITLE": "Choose the model folder",
    "MODEL_IMPORTING": "Copying model…",
    "ERR_DOWNLOAD_NETWORK": "Couldn't download the model. Check your internet connection, or import the model manually.",
    "ERR_DOWNLOAD_SPACE": "Not enough disk space for the model (about 3.5 GB needed).",
    "ERR_DOWNLOAD_BUSY": "The model is being downloaded in another app window. Close it or wait, then try again.",
    "ERR_DOWNLOAD_CORRUPT": "The downloaded model was damaged. Please try again.",
    "ERR_IMPORT_INVALID": "The selected folder doesn't contain a speech model.",
    "AUDIO_LANGUAGE": "Recording language:",
    "LANG_AUTO": "Automatic",
    "DETECTED_LANGUAGE": "Detected language: {name}",
    "ERR_LANGUAGE": "Unknown language: {code}",
    "FILE_FILTER_ALL": "All files (*)",
}

_NAMES_CS = {
    "cs": "Čeština",
    "sk": "Slovenština",
    "en": "Angličtina",
    "de": "Němčina",
    "pl": "Polština",
    "uk": "Ukrajinština",
    "ru": "Ruština",
    "fr": "Francouzština",
    "es": "Španělština",
    "it": "Italština",
}
_NAMES_EN = {
    "cs": "Czech",
    "sk": "Slovak",
    "en": "English",
    "de": "German",
    "pl": "Polish",
    "uk": "Ukrainian",
    "ru": "Russian",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
}

LANG = locale_info.ui_language()
_T = _CS if LANG == "cs" else _EN

WINDOW_TITLE = _T["WINDOW_TITLE"]
DROP_HINT = _T["DROP_HINT"]
DROP_SUB = _T["DROP_SUB"]
DROP_NEXT = _T["DROP_NEXT"]
SAVE_AS = _T["SAVE_AS"]
FMT_MD = _T["FMT_MD"]
FMT_TXT = _T["FMT_TXT"]
SPEAKERS = _T["SPEAKERS"]
SPEAKER_COUNT = _T["SPEAKER_COUNT"]
SPEAKERS_NOTE = _T["SPEAKERS_NOTE"]
SPEAKERS_UNAVAILABLE = _T["SPEAKERS_UNAVAILABLE"]
SPEAKER_LABEL = _T["SPEAKER_LABEL"]
PREPARING = _T["PREPARING"]
TRANSCRIBING = _T["TRANSCRIBING"]
DIARIZING = _T["DIARIZING"]
DIARIZING_PCT = _T["DIARIZING_PCT"]
SAVING = _T["SAVING"]
CANCEL = _T["CANCEL"]
DONE = _T["DONE"]
SAVED_NEXT_TO = _T["SAVED_NEXT_TO"]
SAVED_FALLBACK = _T["SAVED_FALLBACK"]
OPEN = _T["OPEN"]
SHOW = _T["SHOW"]
SPEAKERS_FAILED = _T["SPEAKERS_FAILED"]
FILE_DIALOG_TITLE = _T["FILE_DIALOG_TITLE"]
ERR_UNREADABLE = _T["ERR_UNREADABLE"]
ERR_NOT_FILE = _T["ERR_NOT_FILE"]
ERR_NUM_SPEAKERS = _T["ERR_NUM_SPEAKERS"]
ERR_NO_SPEECH = _T["ERR_NO_SPEECH"]
ERR_BUSY = _T["ERR_BUSY"]
ERR_GENERIC = _T["ERR_GENERIC"]
CONFIRM_QUIT_TITLE = _T["CONFIRM_QUIT_TITLE"]
CONFIRM_QUIT = _T["CONFIRM_QUIT"]
QUIT_CONFIRM_YES = _T["QUIT_CONFIRM_YES"]
QUIT_CONFIRM_NO = _T["QUIT_CONFIRM_NO"]
MODEL_NEEDED = _T["MODEL_NEEDED"]
MODEL_DOWNLOADING = _T["MODEL_DOWNLOADING"]
MODEL_RETRY = _T["MODEL_RETRY"]
MODEL_IMPORT = _T["MODEL_IMPORT"]
MODEL_IMPORT_TITLE = _T["MODEL_IMPORT_TITLE"]
MODEL_IMPORTING = _T["MODEL_IMPORTING"]
ERR_DOWNLOAD_NETWORK = _T["ERR_DOWNLOAD_NETWORK"]
ERR_DOWNLOAD_SPACE = _T["ERR_DOWNLOAD_SPACE"]
ERR_DOWNLOAD_BUSY = _T["ERR_DOWNLOAD_BUSY"]
ERR_DOWNLOAD_CORRUPT = _T["ERR_DOWNLOAD_CORRUPT"]
ERR_IMPORT_INVALID = _T["ERR_IMPORT_INVALID"]
FILE_FILTER = _T["FILE_FILTER"]
AUDIO_LANGUAGE = _T["AUDIO_LANGUAGE"]
LANG_AUTO = _T["LANG_AUTO"]
DETECTED_LANGUAGE = _T["DETECTED_LANGUAGE"]
ERR_LANGUAGE = _T["ERR_LANGUAGE"]
FILE_FILTER_ALL = _T["FILE_FILTER_ALL"]
LANGUAGE_NAMES: dict[str, str] = _NAMES_CS if LANG == "cs" else _NAMES_EN

SEPARATOR = " · "


def format_minutes(seconds: float) -> str:
    """`40 s` under a minute, otherwise whole minutes rounded up (`2 min`)."""
    whole = math.ceil(seconds)
    if whole < 60:
        return f"{max(whole, 0)} s"
    return f"{math.ceil(seconds / 60)} min"


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if LANG == "cs":
        if seconds < 60:
            return "zbývá méně než minuta"
        return f"zbývá cca {math.ceil(seconds / 60)} min"
    if seconds < 60:
        return "less than a minute left"
    return f"about {math.ceil(seconds / 60)} min left"


def format_bytes(n: int) -> str:
    """Decimal units; Czech decimal comma (`1,2 GB`) or English point (`1.2 GB`); `75 MB`."""
    megabytes = round(n / 1_000_000)
    if megabytes < 1000:
        return f"{megabytes} MB"
    text = f"{n / 1_000_000_000:.1f}"
    return (text.replace(".", ",") if LANG == "cs" else text) + " GB"
