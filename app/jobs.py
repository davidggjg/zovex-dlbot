"""Download queue: bounded worker pool so two CPU cores are never oversubscribed."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

from . import config, db, ytdl
from .util import esc, human_duration, human_size, progress_bar, safe_filename

log = logging.getLogger(__name__)


@dataclass
class Job:
    user_id: int
    chat_id: int
    status_message_id: int
    url: str
    title: str
    kind: str                   # video | audio | subs | subbed
    height: int | None = None
    audio_fmt: str = "mp3"
    sub_kind: str = "m"         # m = manual, a = automatic
    sub_langs: list[str] = field(default_factory=list)
    sub_fmt: str = "srt"        # srt | vtt | soft | hard
    id: str = field(default_factory=lambda: secrets.token_urlsafe(5))
    cancelled: bool = False


class Queue:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._q: asyncio.Queue[Job] = asyncio.Queue()
        self._active: dict[str, Job] = {}
        self._per_user: dict[int, int] = {}
        self._workers: list[asyncio.Task] = []

    # ---------- public API ----------

    def user_active(self, user_id: int) -> int:
        return self._per_user.get(user_id, 0)

    def pending(self) -> int:
        return self._q.qsize()

    def running(self) -> int:
        return len(self._active)

    async def submit(self, job: Job) -> int:
        """Returns the queue position (0 = starts immediately)."""
        self._per_user[job.user_id] = self._per_user.get(job.user_id, 0) + 1
        position = self._q.qsize()
        await self._q.put(job)
        return position

    def cancel(self, job_id: str) -> bool:
        job = self._active.get(job_id)
        if job:
            job.cancelled = True
            return True
        return False

    def start(self) -> None:
        for i in range(max(1, config.GLOBAL_CONCURRENCY)):
            self._workers.append(asyncio.create_task(self._worker(i), name=f"dl-worker-{i}"))

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    # ---------- internals ----------

    async def _worker(self, index: int) -> None:
        while True:
            job = await self._q.get()
            try:
                await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("job failed: %s", exc)
                await self._fail(job, f"שגיאה לא צפויה: {exc}")
            finally:
                self._per_user[job.user_id] = max(0, self._per_user.get(job.user_id, 1) - 1)
                self._active.pop(job.id, None)
                self._q.task_done()

    async def _edit(self, job: Job, text: str, keyboard=None) -> None:
        try:
            await self.bot.edit_message_text(
                text,
                chat_id=job.chat_id,
                message_id=job.status_message_id,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramBadRequest:
            pass  # identical text / message gone

    async def _fail(self, job: Job, message: str) -> None:
        await self._edit(job, f"❌ <b>נכשל</b>\n{esc(job.title)}\n\n{esc(message)}")
        await db.log_history(job.user_id, job.url, job.title, 0, False, message[:400])

    async def _run(self, job: Job) -> None:
        from .keyboards import cancel_only

        self._active[job.id] = job
        workdir = config.DOWNLOAD_DIR / f"{job.user_id}-{job.id}"
        loop = asyncio.get_running_loop()
        started = time.monotonic()

        def on_progress(p: dict) -> None:
            if job.cancelled:
                raise ytdl.DownloadError("בוטל על ידי המשתמש.")
            total = p.get("total") or 0
            done = p.get("downloaded") or 0
            frac = (done / total) if total else 0.0
            speed = p.get("speed") or 0
            lines = [
                f"⬇️ <b>מוריד…</b>\n{esc(job.title[:70])}\n",
                f"<code>{progress_bar(frac)}</code> {frac * 100:4.1f}%",
                f"{human_size(done)} / {human_size(total)}"
                + (f"  ·  {human_size(speed)}/s" if speed else "")
                + (f"  ·  {human_duration(p.get('eta'))} נותרו" if p.get("eta") else ""),
            ]
            asyncio.run_coroutine_threadsafe(
                self._edit(job, "\n".join(lines), cancel_only(job.id)), loop
            )

        try:
            await self._edit(job, f"⏳ <b>מתחיל…</b>\n{esc(job.title[:70])}", cancel_only(job.id))

            if job.kind == "video":
                path = await asyncio.to_thread(
                    ytdl.download_video, job.url, workdir, job.height, on_progress
                )
                await self._deliver(job, path, "video")

            elif job.kind == "audio":
                path = await asyncio.to_thread(
                    ytdl.download_audio, job.url, workdir, job.audio_fmt, on_progress
                )
                await self._deliver(job, path, "audio")

            elif job.kind == "subs":
                await self._edit(job, f"💬 <b>מוריד כתוביות…</b>\n{esc(job.title[:70])}")
                subs = await asyncio.to_thread(
                    ytdl.download_subtitles,
                    job.url, workdir, job.sub_langs, job.sub_kind == "a", job.sub_fmt,
                )
                await self._deliver_subs(job, subs)

            elif job.kind == "subbed":
                path = await asyncio.to_thread(
                    ytdl.download_video, job.url, workdir, job.height, on_progress
                )
                await self._edit(job, f"💬 <b>מוריד כתוביות…</b>\n{esc(job.title[:70])}")
                subs = await asyncio.to_thread(
                    ytdl.download_subtitles,
                    job.url, workdir, job.sub_langs, job.sub_kind == "a", "srt",
                )
                burn = job.sub_fmt == "hard"
                await self._edit(
                    job,
                    ("🔥 <b>צורב כתוביות…</b> זה יכול לקחת כמה דקות\n" if burn
                     else "🧩 <b>משלב כתוביות…</b>\n") + esc(job.title[:70]),
                )
                merged = await asyncio.to_thread(ytdl.mux_subtitles, path, subs, burn)
                await self._deliver(job, merged, "video")

            else:
                await self._fail(job, "סוג עבודה לא מוכר.")
                return

            log.info("job %s done in %.0fs", job.id, time.monotonic() - started)

        except ytdl.DownloadError as exc:
            await self._fail(job, str(exc))
        finally:
            ytdl.cleanup(workdir)

    # ---------- sending ----------

    def _file_arg(self, path: Path):
        """With a local Bot API server a plain absolute path avoids re-uploading."""
        if config.USE_LOCAL_API:
            return str(path)
        return FSInputFile(path, filename=path.name)

    async def _deliver(self, job: Job, path: Path, media: str) -> None:
        size = path.stat().st_size
        if size > config.MAX_FILESIZE:
            await self._fail(
                job,
                f"הקובץ שהתקבל הוא {human_size(size)} — מעל המגבלה של "
                f"{config.MAX_FILESIZE_MB}MB. נסה איכות נמוכה יותר.",
            )
            return

        await self._edit(job, f"📤 <b>שולח…</b> ({human_size(size)})\n{esc(job.title[:70])}")
        caption = f"<b>{esc(job.title[:180])}</b>"
        try:
            if media == "video":
                w, h, dur = await asyncio.to_thread(ytdl.probe_dimensions, path)
                await self.bot.send_video(
                    job.chat_id,
                    self._file_arg(path),
                    caption=caption,
                    parse_mode="HTML",
                    supports_streaming=True,
                    width=w or None,
                    height=h or None,
                    duration=dur or None,
                )
            else:
                await self.bot.send_audio(
                    job.chat_id, self._file_arg(path), caption=caption, parse_mode="HTML"
                )
        except TelegramBadRequest as exc:
            # Some containers/codecs Telegram refuses as video — fall back to a document.
            log.warning("send_%s rejected (%s), retrying as document", media, exc)
            await self.bot.send_document(
                job.chat_id, self._file_arg(path), caption=caption, parse_mode="HTML"
            )

        await db.add_usage(job.user_id, size)
        await db.log_history(job.user_id, job.url, job.title, size, True, None)
        await self._edit(job, f"✅ <b>הושלם</b> · {human_size(size)}\n{esc(job.title[:70])}")

    async def _deliver_subs(self, job: Job, subs: list[Path]) -> None:
        if len(subs) == 1:
            path = subs[0]
        else:
            path = subs[0].parent / f"{safe_filename(job.title, 50)}-subs.zip"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for s in subs:
                    zf.write(s, arcname=s.name)

        size = path.stat().st_size
        await self.bot.send_document(
            job.chat_id,
            self._file_arg(path),
            caption=f"💬 <b>{esc(job.title[:150])}</b>\n{len(subs)} קבצי כתוביות",
            parse_mode="HTML",
        )
        await db.add_usage(job.user_id, size)
        await db.log_history(job.user_id, job.url, job.title, size, True, None)
        await self._edit(job, f"✅ <b>כתוביות נשלחו</b> ({len(subs)} קבצים)")
