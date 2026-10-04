import html
import re

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def find_url(text: str | None) -> str | None:
    if not text:
        return None
    m = _URL_RE.search(text)
    return m.group(0).rstrip(').,;"\'') if m else None


def human_size(n: int | float | None) -> str:
    if not n:
        return "לא ידוע"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit in ("B", "KB") else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def human_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(fraction: float, width: int = 12) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    return "█" * filled + "░" * (width - filled)


def esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def safe_filename(name: str, limit: int = 80) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", name or "video").strip(" ._")
    return (name[:limit] or "video")


LANG_NAMES = {
    "he": "עברית", "iw": "עברית", "en": "אנגלית", "ar": "ערבית", "ru": "רוסית",
    "es": "ספרדית", "fr": "צרפתית", "de": "גרמנית", "it": "איטלקית",
    "pt": "פורטוגזית", "tr": "טורקית", "hi": "הינדי", "ja": "יפנית",
    "ko": "קוריאנית", "zh": "סינית", "zh-Hans": "סינית פשוטה",
    "zh-Hant": "סינית מסורתית", "pl": "פולנית", "nl": "הולנדית",
    "uk": "אוקראינית", "ro": "רומנית", "fa": "פרסית", "id": "אינדונזית",
    "vi": "וייטנאמית", "th": "תאית", "sv": "שוודית", "el": "יוונית",
    "cs": "צ'כית", "hu": "הונגרית", "am": "אמהרית", "yi": "יידיש",
}


def lang_label(code: str) -> str:
    base = code.split("-")[0]
    name = LANG_NAMES.get(code) or LANG_NAMES.get(base)
    return f"{name} ({code})" if name else code
