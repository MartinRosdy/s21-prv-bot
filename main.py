"""
S21 Peer-Review Notifier — Telegram bot entry point.

Starts aiogram polling, opens SQLite, and launches APScheduler:
interval calendar sync (diff) + one-shot reminders before peer-reviews.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.core.config import get_settings
from bot.database.db import Database
from bot.handlers.auth import get_auth_router
from bot.handlers.start import get_start_router
from bot.routers.menu import get_menu_router
from bot.routers.reviews import get_reviews_router
from bot.routers.settings import bot_commands_for, get_settings_router
from bot.services.crypto import CryptoService
from bot.services.s21_api import S21ApiClient
from bot.services.scheduler import PeerReviewScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("s21_reviewer")


async def set_bot_commands(bot: Bot) -> None:
    """
    Register Telegram command menu.

    Default (no language_code) + explicit Russian — Telegram clients that
    do not send language_code still see Russian descriptions. Per-user
    sync happens on /lang and /login via BotCommandScopeChat.
    """
    ru_commands = bot_commands_for("ru")
    await bot.set_my_commands(ru_commands)
    await bot.set_my_commands(ru_commands, language_code="ru")
    for lang_code, lang in (("en", "en"), ("uz", "uz")):
        try:
            await bot.set_my_commands(
                bot_commands_for(lang),
                language_code=lang_code,
            )
        except Exception:
            logger.debug(
                "set_my_commands(language_code=%s) skipped",
                lang_code,
                exc_info=True,
            )
    logger.info("Bot commands registered (default=ru)")


async def main() -> None:
    settings = get_settings()

    db = Database(settings.db_path)
    await db.connect()

    crypto = CryptoService(settings.encryption_key)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher(storage=MemoryStorage())

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        api = S21ApiClient(
            session,
            auth_url=settings.auth_url,
            graphql_url=settings.graphql_url,
            school_id=settings.school_id,
        )

        dp.include_router(get_start_router(db))
        dp.include_router(get_auth_router(db, crypto, api))
        dp.include_router(get_menu_router(db))
        dp.include_router(get_settings_router(db, bot=bot))

        scheduler = PeerReviewScheduler(
            bot=bot,
            db=db,
            crypto=crypto,
            api=api,
            poll_interval_seconds=settings.poll_interval_seconds,
        )

        dp.include_router(
            get_reviews_router(
                db,
                crypto,
                api,
                force_poll=scheduler.poll_user,
            )
        )

        scheduler.start()

        await set_bot_commands(bot)

        logger.info("Bot is starting…")
        try:
            # Drop pending updates so old /login commands with passwords
            # are not re-processed after a restart.
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        finally:
            scheduler.shutdown()
            await db.close()
            await bot.session.close()
            logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
