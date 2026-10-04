from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import config
from .util import human_size, lang_label

PAGE_SIZE = 10


def main_menu(token: str, info) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for h in info.heights[:6]:
        size = info.size_by_height.get(h)
        label = f"🎬 {h}p" + (f" · {human_size(size)}" if size else "")
        kb.button(text=label, callback_data=f"v:{token}:{h}")
    kb.adjust(2)

    extra = InlineKeyboardBuilder()
    if not info.heights:
        extra.button(text="🎬 הורד וידאו (איכות מקסימלית)", callback_data=f"v:{token}:best")
    extra.button(text="🎵 MP3", callback_data=f"a:{token}:mp3")
    extra.button(text="🎧 M4A (איכות גבוהה)", callback_data=f"a:{token}:m4a")
    extra.adjust(2)
    kb.attach(extra)

    if info.manual_subs or info.auto_subs:
        subs = InlineKeyboardBuilder()
        subs.button(text="💬 כתוביות", callback_data=f"sm:{token}")
        subs.adjust(1)
        kb.attach(subs)

    tail = InlineKeyboardBuilder()
    tail.button(text="🔗 פתח מקור", url=info.webpage_url)
    tail.button(text="✖️ ביטול", callback_data=f"x:{token}")
    tail.adjust(2)
    kb.attach(tail)
    return kb.as_markup()


def subs_menu(token: str, info) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if info.manual_subs:
        kb.button(
            text=f"✍️ כתוביות מקוריות ({len(info.manual_subs)} שפות)",
            callback_data=f"sl:{token}:m:0",
        )
    if info.auto_subs:
        kb.button(
            text=f"🤖 כתוביות אוטומטיות ({len(info.auto_subs)} שפות)",
            callback_data=f"sl:{token}:a:0",
        )
    kb.button(text="⬅️ חזרה", callback_data=f"bk:{token}")
    kb.adjust(1)
    return kb.as_markup()


def _ordered(langs: list[str]) -> list[str]:
    preferred = [l for code in config.DEFAULT_SUB_LANGS for l in langs if l.split("-")[0] == code]
    rest = [l for l in langs if l not in preferred]
    return preferred + rest


def lang_menu(token: str, langs: list[str], kind: str, page: int) -> InlineKeyboardMarkup:
    langs = _ordered(langs)
    pages = max(1, -(-len(langs) // PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = langs[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    kb = InlineKeyboardBuilder()
    for code in chunk:
        kb.button(text=lang_label(code), callback_data=f"sp:{token}:{kind}:{code}")
    kb.adjust(2)

    nav = InlineKeyboardBuilder()
    if pages > 1:
        nav.button(text="◀️", callback_data=f"sl:{token}:{kind}:{page - 1}")
        nav.button(text=f"{page + 1}/{pages}", callback_data="noop")
        nav.button(text="▶️", callback_data=f"sl:{token}:{kind}:{page + 1}")
        nav.adjust(3)
    kb.attach(nav)

    tail = InlineKeyboardBuilder()
    tail.button(text="🌍 כל השפות בקובץ ZIP", callback_data=f"sp:{token}:{kind}:__all__")
    tail.button(text="⬅️ חזרה", callback_data=f"sm:{token}")
    tail.adjust(1)
    kb.attach(tail)
    return kb.as_markup()


def sub_delivery_menu(token: str, kind: str, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 קובץ SRT", callback_data=f"sd:{token}:{kind}:{lang}:srt")
    kb.button(text="📄 קובץ VTT", callback_data=f"sd:{token}:{kind}:{lang}:vtt")
    if lang != "__all__":
        kb.button(
            text="🎬 וידאו + כתוביות נבחרות (מהיר)",
            callback_data=f"sd:{token}:{kind}:{lang}:soft",
        )
        kb.button(
            text="🔥 וידאו עם כתוביות צרובות (איטי)",
            callback_data=f"sd:{token}:{kind}:{lang}:hard",
        )
    kb.button(text="⬅️ חזרה", callback_data=f"sm:{token}")
    kb.adjust(2, 1, 1, 1)
    return kb.as_markup()


def quality_for_subbed(token: str, kind: str, lang: str, mode: str, info) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    heights = info.heights[:6] or [720]
    for h in heights:
        kb.button(text=f"{h}p", callback_data=f"sv:{token}:{kind}:{lang}:{mode}:{h}")
    kb.adjust(3)
    tail = InlineKeyboardBuilder()
    tail.button(text="⬅️ חזרה", callback_data=f"sm:{token}")
    tail.adjust(1)
    kb.attach(tail)
    return kb.as_markup()


def cancel_only(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🛑 בטל", callback_data=f"kill:{job_id}")]]
    )
