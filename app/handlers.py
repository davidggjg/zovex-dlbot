from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from . import config, db, keyboards as kb, state, ytdl
from .jobs import Job, Queue
from .util import esc, find_url, human_duration, human_size

log = logging.getLogger(__name__)
router = Router()

_last_request: dict[int, float] = {}
QUEUE: Queue | None = None


def bind_queue(queue: Queue) -> None:
    global QUEUE
    QUEUE = queue


WELCOME = (
    "<b>שלום 👋</b>\n\n"
    "שלח לי קישור לסרטון מ‑YouTube, TikTok, Instagram, Facebook, X, Reddit, "
    "Twitch ועוד מאות אתרים — ואני אוריד אותו בשבילך.\n\n"
    "מה אפשר לקבל:\n"
    "• 🎬 וידאו בכל איכות שזמינה (עד 4K)\n"
    "• 🎵 אודיו בלבד — MP3 או M4A\n"
    "• 💬 כתוביות — בחירת שפה, כקובץ נפרד / משולב / צרוב בוידאו\n\n"
    f"מגבלות: עד {config.MAX_FILESIZE_MB}MB לקובץ, "
    f"{config.DAILY_QUOTA_MB}MB ו‑{config.DAILY_JOBS} הורדות ביום.\n\n"
    "פקודות: /help · /quota · /cancel"
)

HELP = (
    "<b>איך זה עובד</b>\n\n"
    "1️⃣ שלח קישור (אפשר גם בתוך הודעת טקסט).\n"
    "2️⃣ הבוט מנתח ומציג כפתורי איכות.\n"
    "3️⃣ לוחץ — ומקבל שורת התקדמות עד שהקובץ מגיע.\n\n"
    "<b>כתוביות</b>\n"
    "• <b>מקוריות</b> — מה שהיוצר העלה (מדויק).\n"
    "• <b>אוטומטיות</b> — תמלול של YouTube (פחות מדויק, הרבה שפות).\n"
    "• <b>SRT / VTT</b> — קובץ טקסט נפרד.\n"
    "• <b>משולב</b> — כתוביות בתוך קובץ ה‑MP4, אפשר לכבות בנגן. מהיר.\n"
    "• <b>צרוב</b> — הכתוביות חלק מהתמונה. דורש קידוד מחדש, איטי.\n\n"
    "<b>הורדה נכשלת?</b>\n"
    "אתרים כמו אינסטגרם דורשים לפעמים התחברות. אדמין יכול להעלות קובץ "
    "cookies עם /cookies.\n\n"
    "⚠️ הורד רק תוכן שמותר לך להוריד — הבוט לא אחראי לשימוש בזכויות יוצרים."
)


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await db.touch_user(message.from_user.id, message.from_user.username)
    await message.answer(WELCOME, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("quota"))
async def cmd_quota(message: Message) -> None:
    used, jobs = await db.get_usage(message.from_user.id)
    await message.answer(
        f"<b>המצב שלך היום</b>\n"
        f"נפח: {human_size(used)} מתוך {config.DAILY_QUOTA_MB}MB\n"
        f"הורדות: {jobs} מתוך {config.DAILY_JOBS}\n"
        f"בתור כרגע בשרת: {QUEUE.pending()} · רצות: {QUEUE.running()}",
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    await message.answer("לביטול הורדה פעילה — לחץ על 🛑 בהודעת ההתקדמות שלה.")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    s = await db.stats()
    await message.answer(
        f"<b>סטטיסטיקה</b>\n"
        f"משתמשים: {s['users']}\n"
        f"הורדות היום: {s['jobs_today']} · {human_size(s['bytes_today'])}\n"
        f"סך הכל: {s['jobs_total']} (נכשלו: {s['failed_total']})\n"
        f"תור: {QUEUE.pending()} · רצות: {QUEUE.running()}",
        parse_mode="HTML",
    )


@router.message(Command("cookies"))
async def cmd_cookies(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    await message.answer(
        "שלח לי עכשיו קובץ <code>cookies.txt</code> (פורמט Netscape).\n"
        "שם הקובץ קובע לאיזה אתר הוא משויך:\n"
        "<code>instagram.txt</code>, <code>tiktok.txt</code>, <code>youtube.txt</code>, "
        "<code>facebook.txt</code>, <code>twitter.txt</code>, <code>reddit.txt</code>\n"
        "או <code>cookies.txt</code> כגנרי לכל האתרים.",
        parse_mode="HTML",
    )


@router.message(Command("block"))
async def cmd_block(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("שימוש: <code>/block 123456789</code>", parse_mode="HTML")
        return
    await db.set_blocked(int(parts[1]), True)
    await message.answer(f"✅ {parts[1]} נחסם.")


@router.message(Command("unblock"))
async def cmd_unblock(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("שימוש: <code>/unblock 123456789</code>", parse_mode="HTML")
        return
    await db.set_blocked(int(parts[1]), False)
    await message.answer(f"✅ {parts[1]} שוחרר.")


@router.message(F.document)
async def on_document(message: Message) -> None:
    """Admins upload cookies.txt files this way."""
    if not _is_admin(message.from_user.id):
        return
    name = (message.document.file_name or "cookies.txt").lower()
    if not name.endswith(".txt"):
        await message.answer("רק קבצי .txt מתקבלים כאן.")
        return
    if message.document.file_size > 2 * 1024 * 1024:
        await message.answer("הקובץ גדול מדי לקובץ cookies.")
        return
    target = config.COOKIES_DIR / name.replace("/", "_")
    file = await message.bot.get_file(message.document.file_id)
    await message.bot.download_file(file.file_path, destination=target)
    target.chmod(0o600)
    await message.answer(f"✅ נשמר כ‑<code>{esc(target.name)}</code>.", parse_mode="HTML")


async def _gate(message: Message) -> str | None:
    """Returns an error string if the user may not start a job right now."""
    uid = message.from_user.id
    if await db.is_blocked(uid):
        return "אין לך הרשאה להשתמש בבוט."

    now = time.monotonic()
    if now - _last_request.get(uid, 0) < config.COOLDOWN_SECONDS:
        return f"רגע 😊 המתן {config.COOLDOWN_SECONDS} שניות בין בקשות."
    _last_request[uid] = now

    if QUEUE.user_active(uid) >= config.USER_CONCURRENCY:
        return "יש לך כבר הורדה פעילה. חכה שהיא תסתיים."

    used, jobs = await db.get_usage(uid)
    if jobs >= config.DAILY_JOBS:
        return f"הגעת למגבלה של {config.DAILY_JOBS} הורדות ביום. נסה מחר."
    if used >= config.DAILY_QUOTA:
        return f"הגעת למגבלת הנפח היומית ({config.DAILY_QUOTA_MB}MB). נסה מחר."
    return None


@router.message(F.text)
async def on_text(message: Message) -> None:
    url = find_url(message.text)
    if not url:
        await message.answer("שלח לי קישור לסרטון 🙂 (/help להסבר)")
        return

    await db.touch_user(message.from_user.id, message.from_user.username)
    error = await _gate(message)
    if error:
        await message.answer(error)
        return

    status = await message.answer("🔍 מנתח את הקישור…")
    try:
        info = await asyncio.to_thread(ytdl.extract_info, url)
    except ytdl.DownloadError as exc:
        await status.edit_text(f"❌ {esc(str(exc))}", parse_mode="HTML")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("extract failed")
        await status.edit_text(f"❌ לא הצלחתי לקרוא את הקישור: {esc(str(exc)[:200])}",
                               parse_mode="HTML")
        return

    if info.is_live:
        await status.edit_text("❌ זה שידור חי — אי אפשר להוריד אותו כקובץ.")
        return
    if info.duration and info.duration > config.MAX_DURATION_MINUTES * 60:
        await status.edit_text(
            f"❌ הסרטון ארוך מהמותר ({config.MAX_DURATION_MINUTES} דקות)."
        )
        return

    token = state.put({"info": info, "url": url, "owner": message.from_user.id})
    await status.edit_text(
        _summary(info), parse_mode="HTML", reply_markup=kb.main_menu(token, info),
        disable_web_page_preview=True,
    )


def _summary(info) -> str:
    lines = [f"<b>{esc(info.title[:200])}</b>", ""]
    meta = []
    if info.uploader:
        meta.append(f"👤 {esc(info.uploader[:60])}")
    if info.duration:
        meta.append(f"⏱ {human_duration(info.duration)}")
    meta.append(f"🌐 {esc(info.extractor)}")
    lines.append("  ·  ".join(meta))
    if info.manual_subs or info.auto_subs:
        parts = []
        if info.manual_subs:
            parts.append(f"{len(info.manual_subs)} מקוריות")
        if info.auto_subs:
            parts.append(f"{len(info.auto_subs)} אוטומטיות")
        lines.append(f"💬 כתוביות זמינות: {', '.join(parts)}")
    lines += ["", "בחר מה להוריד:"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# callbacks
# --------------------------------------------------------------------------


async def _resolve(call: CallbackQuery, token: str):
    item = state.get(token)
    if item is None:
        await call.answer("הבקשה פגה. שלח את הקישור מחדש.", show_alert=True)
        return None
    if item["owner"] != call.from_user.id:
        await call.answer("זו לא הבקשה שלך.", show_alert=True)
        return None
    return item


async def _enqueue(call: CallbackQuery, job: Job) -> None:
    used, jobs = await db.get_usage(job.user_id)
    if jobs >= config.DAILY_JOBS:
        await call.answer("הגעת למגבלה היומית.", show_alert=True)
        return
    if QUEUE.user_active(job.user_id) >= config.USER_CONCURRENCY:
        await call.answer("יש לך כבר הורדה פעילה.", show_alert=True)
        return

    await db.add_usage(job.user_id, 0, jobs=1)
    position = await QUEUE.submit(job)
    if position and position >= config.GLOBAL_CONCURRENCY:
        await call.message.edit_text(
            f"🕐 <b>בתור</b> (מקום {position - config.GLOBAL_CONCURRENCY + 1})\n"
            f"{esc(job.title[:70])}",
            parse_mode="HTML",
        )
    await call.answer("נוסף לתור ✅")


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data.startswith("x:"))
async def cb_close(call: CallbackQuery) -> None:
    _, token = call.data.split(":", 1)
    state.drop(token)
    await call.message.edit_text("בוטל.")
    await call.answer()


@router.callback_query(F.data.startswith("kill:"))
async def cb_kill(call: CallbackQuery) -> None:
    _, job_id = call.data.split(":", 1)
    if QUEUE.cancel(job_id):
        await call.answer("מבטל…")
    else:
        await call.answer("ההורדה כבר הסתיימה.", show_alert=True)


@router.callback_query(F.data.startswith("bk:"))
async def cb_back(call: CallbackQuery) -> None:
    _, token = call.data.split(":", 1)
    item = await _resolve(call, token)
    if not item:
        return
    info = item["info"]
    await call.message.edit_text(
        _summary(info), parse_mode="HTML", reply_markup=kb.main_menu(token, info),
        disable_web_page_preview=True,
    )
    await call.answer()


@router.callback_query(F.data.startswith("v:"))
async def cb_video(call: CallbackQuery) -> None:
    _, token, height = call.data.split(":", 2)
    item = await _resolve(call, token)
    if not item:
        return
    info = item["info"]
    await _enqueue(
        call,
        Job(
            user_id=call.from_user.id,
            chat_id=call.message.chat.id,
            status_message_id=call.message.message_id,
            url=item["url"],
            title=info.title,
            kind="video",
            height=None if height == "best" else int(height),
        ),
    )


@router.callback_query(F.data.startswith("a:"))
async def cb_audio(call: CallbackQuery) -> None:
    _, token, fmt = call.data.split(":", 2)
    item = await _resolve(call, token)
    if not item:
        return
    info = item["info"]
    await _enqueue(
        call,
        Job(
            user_id=call.from_user.id,
            chat_id=call.message.chat.id,
            status_message_id=call.message.message_id,
            url=item["url"],
            title=info.title,
            kind="audio",
            audio_fmt=fmt,
        ),
    )


@router.callback_query(F.data.startswith("sm:"))
async def cb_subs_menu(call: CallbackQuery) -> None:
    _, token = call.data.split(":", 1)
    item = await _resolve(call, token)
    if not item:
        return
    info = item["info"]
    await call.message.edit_text(
        f"💬 <b>כתוביות</b>\n{esc(info.title[:120])}\n\n"
        "<b>מקוריות</b> — מה שהיוצר העלה, מדויק.\n"
        "<b>אוטומטיות</b> — תמלול מכונה, הרבה שפות אבל פחות מדויק.",
        parse_mode="HTML",
        reply_markup=kb.subs_menu(token, info),
    )
    await call.answer()


@router.callback_query(F.data.startswith("sl:"))
async def cb_sub_langs(call: CallbackQuery) -> None:
    _, token, kind, page = call.data.split(":", 3)
    item = await _resolve(call, token)
    if not item:
        return
    info = item["info"]
    langs = info.auto_subs if kind == "a" else info.manual_subs
    title = "🤖 אוטומטיות" if kind == "a" else "✍️ מקוריות"
    await call.message.edit_text(
        f"{title} — בחר שפה:\n<b>{esc(info.title[:100])}</b>",
        parse_mode="HTML",
        reply_markup=kb.lang_menu(token, langs, kind, int(page)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("sp:"))
async def cb_sub_pick(call: CallbackQuery) -> None:
    _, token, kind, lang = call.data.split(":", 3)
    item = await _resolve(call, token)
    if not item:
        return
    label = "כל השפות" if lang == "__all__" else lang
    await call.message.edit_text(
        f"💬 שפה: <b>{esc(label)}</b>\n\nאיך לקבל את הכתוביות?",
        parse_mode="HTML",
        reply_markup=kb.sub_delivery_menu(token, kind, lang),
    )
    await call.answer()


@router.callback_query(F.data.startswith("sd:"))
async def cb_sub_delivery(call: CallbackQuery) -> None:
    _, token, kind, lang, mode = call.data.split(":", 4)
    item = await _resolve(call, token)
    if not item:
        return
    info = item["info"]
    langs = info.auto_subs if kind == "a" else info.manual_subs
    chosen = langs if lang == "__all__" else [lang]

    if mode in ("srt", "vtt"):
        await _enqueue(
            call,
            Job(
                user_id=call.from_user.id,
                chat_id=call.message.chat.id,
                status_message_id=call.message.message_id,
                url=item["url"],
                title=info.title,
                kind="subs",
                sub_kind=kind,
                sub_langs=chosen,
                sub_fmt=mode,
            ),
        )
        return

    note = (
        "🔥 צריבה דורשת קידוד מחדש — על שרת של 2 ליבות זה יכול לקחת "
        "כמה דקות עד חצי שעה.\n\n"
        if mode == "hard"
        else "🧩 שילוב מהיר, בלי קידוד מחדש.\n\n"
    )
    await call.message.edit_text(
        note + "באיזו איכות להוריד את הוידאו?",
        parse_mode="HTML",
        reply_markup=kb.quality_for_subbed(token, kind, lang, mode, info),
    )
    await call.answer()


@router.callback_query(F.data.startswith("sv:"))
async def cb_subbed_video(call: CallbackQuery) -> None:
    _, token, kind, lang, mode, height = call.data.split(":", 5)
    item = await _resolve(call, token)
    if not item:
        return
    info = item["info"]
    langs = info.auto_subs if kind == "a" else info.manual_subs
    chosen = langs if lang == "__all__" else [lang]
    await _enqueue(
        call,
        Job(
            user_id=call.from_user.id,
            chat_id=call.message.chat.id,
            status_message_id=call.message.message_id,
            url=item["url"],
            title=info.title,
            kind="subbed",
            height=int(height),
            sub_kind=kind,
            sub_langs=chosen,
            sub_fmt=mode,
        ),
    )
