from __future__ import annotations

import asyncio
import logging
import shutil
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import BotCommand

from . import config, db, handlers
from .jobs import Queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("dlbot")


def build_bot() -> Bot:
    if config.USE_LOCAL_API:
        api = TelegramAPIServer.from_base(config.LOCAL_API_URL, is_local=True)
        session = AiohttpSession(api=api)
        log.info("using local Bot API server at %s (2000MB uploads)", config.LOCAL_API_URL)
    else:
        session = AiohttpSession()
        log.warning("using cloud Bot API — uploads are capped at 50MB")
    return Bot(
        token=config.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode="HTML"),
    )


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="התחלה"),
            BotCommand(command="help", description="הסבר על הכפתורים והכתוביות"),
            BotCommand(command="quota", description="כמה נשאר לי היום"),
            BotCommand(command="cancel", description="איך לבטל הורדה"),
        ]
    )


async def main() -> None:
    if not config.BOT_TOKEN:
        log.error("BOT_TOKEN חסר — ערוך את קובץ .env")
        raise SystemExit(1)
    if not shutil.which("ffmpeg"):
        log.error("ffmpeg לא מותקן — הרץ מחדש את install.sh")
        raise SystemExit(1)

    await db.init()
    bot = build_bot()

    me = await bot.get_me()
    log.info("logged in as @%s (%s)", me.username, me.id)

    queue = Queue(bot)
    handlers.bind_queue(queue)
    queue.start()

    dp = Dispatcher()
    dp.include_router(handlers.router)

    await bot.delete_webhook(drop_pending_updates=True)
    await set_commands(bot)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await queue.stop()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
