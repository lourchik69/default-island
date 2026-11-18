"""Interface languages of Default Island.

The source text of the interface is Russian. `tr("Русский текст")` returns
the text in the chosen language, or the Russian text itself when there is no
translation yet, so a missing entry is never an error.

Each module registers its own table with `register()`:

    register({
        "Ничего не играет": ("Nothing is playing", "Нічого не грає"),
    })

The tuple is (English, Ukrainian).
"""
import ctypes

LANGUAGES = (("ru", "Русский"), ("uk", "Українська"), ("en", "English"))
LANGUAGE_CODES = tuple(code for code, _name in LANGUAGES)

_language = "ru"
_tables = {"en": {}, "uk": {}}


def register(table):
    """Add translations: {russian: (english, ukrainian)}."""
    for source, (english, ukrainian) in table.items():
        _tables["en"][source] = english
        _tables["uk"][source] = ukrainian


def set_language(code):
    global _language
    _language = code if code in LANGUAGE_CODES else "ru"


def language():
    return _language


def tr(text):
    """`text` (Russian) in the current interface language."""
    if _language == "ru":
        return text
    return _tables[_language].get(text, text)


def system_language():
    """The interface language that matches the Windows display language."""
    try:
        primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
    except (AttributeError, OSError):
        return "en"
    return {0x19: "ru", 0x22: "uk"}.get(primary, "en")
