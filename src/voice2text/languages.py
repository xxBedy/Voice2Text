"""Recording languages offered to the user (Whisper language codes). Qt-free."""

from __future__ import annotations

from voice2text import locale_info, strings

AUTO = "auto"
TOP = ("cs", "sk", "en", "de", "pl", "uk", "ru", "fr", "es", "it")

# English names of the languages Whisper knows; also the vendored fallback for the code list.
_ENGLISH_NAMES = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "as": "Assamese", "az": "Azerbaijani",
    "ba": "Bashkir", "be": "Belarusian", "bg": "Bulgarian", "bn": "Bengali", "bo": "Tibetan",
    "br": "Breton", "bs": "Bosnian", "ca": "Catalan", "cs": "Czech", "cy": "Welsh",
    "da": "Danish", "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish", "fo": "Faroese",
    "fr": "French", "gl": "Galician", "gu": "Gujarati", "ha": "Hausa", "haw": "Hawaiian",
    "he": "Hebrew", "hi": "Hindi", "hr": "Croatian", "ht": "Haitian Creole", "hu": "Hungarian",
    "hy": "Armenian", "id": "Indonesian", "is": "Icelandic", "it": "Italian", "ja": "Japanese",
    "jw": "Javanese", "ka": "Georgian", "kk": "Kazakh", "km": "Khmer", "kn": "Kannada",
    "ko": "Korean", "la": "Latin", "lb": "Luxembourgish", "ln": "Lingala", "lo": "Lao",
    "lt": "Lithuanian", "lv": "Latvian", "mg": "Malagasy", "mi": "Maori", "mk": "Macedonian",
    "ml": "Malayalam", "mn": "Mongolian", "mr": "Marathi", "ms": "Malay", "mt": "Maltese",
    "my": "Myanmar", "ne": "Nepali", "nl": "Dutch", "nn": "Nynorsk", "no": "Norwegian",
    "oc": "Occitan", "pa": "Punjabi", "pl": "Polish", "ps": "Pashto", "pt": "Portuguese",
    "ro": "Romanian", "ru": "Russian", "sa": "Sanskrit", "sd": "Sindhi", "si": "Sinhala",
    "sk": "Slovak", "sl": "Slovenian", "sn": "Shona", "so": "Somali", "sq": "Albanian",
    "sr": "Serbian", "su": "Sundanese", "sv": "Swedish", "sw": "Swahili", "ta": "Tamil",
    "te": "Telugu", "tg": "Tajik", "th": "Thai", "tk": "Turkmen", "tl": "Tagalog",
    "tr": "Turkish", "tt": "Tatar", "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek",
    "vi": "Vietnamese", "yi": "Yiddish", "yo": "Yoruba", "yue": "Cantonese", "zh": "Chinese",
}


def all_codes() -> list[str]:
    """Every Whisper language code (from faster-whisper; a vendored copy if that moves)."""
    try:
        from faster_whisper.tokenizer import _LANGUAGE_CODES

        codes = [str(c) for c in _LANGUAGE_CODES]
        if codes:
            return codes
    except Exception:
        pass
    return list(_ENGLISH_NAMES)


def display_name(code: str) -> str:
    if code == AUTO:
        return strings.LANG_AUTO
    return strings.LANGUAGE_NAMES.get(code) or _ENGLISH_NAMES.get(code) or code


def choices() -> list[tuple[str, str]]:
    codes = all_codes()
    top = [c for c in TOP if c in codes]
    rest = sorted((c for c in codes if c not in TOP), key=lambda c: _ENGLISH_NAMES.get(c, c).casefold())
    return [(AUTO, strings.LANG_AUTO)] + [(c, display_name(c)) for c in top + rest]


def default_choice() -> str:
    return locale_info.whisper_code_for(locale_info.system_language_tag(), all_codes()) or AUTO
