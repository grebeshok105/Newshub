"""
NewsHub Telegram Bot — Entry point.

Starts Telethon (UserBot), aiogram (Bot), and APScheduler together.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

import config
from bot.handlers import router
from database import init_db
from services.scheduler import create_scheduler, set_bot, set_scheduler, reconfigure_digest_job
from services.telethon_parser import start_client, stop_client

# ── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("newshub")


async def main() -> None:
    # 1. Initialize database
    await init_db()
    logger.info("Database initialized.")

    # 2. Start Telethon client (UserBot for channel parsing)
    logger.info("Starting Telethon client…")
    try:
        telethon_client = await start_client()
        logger.info("✅ Telethon client ready.")
    except Exception:
        logger.warning(
            "⚠️ Telethon failed to connect (VPN/proxy may be needed). "
            "Bot will start without parsing capability."
        )

    # 3. Create aiogram bot and dispatcher
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # 4. Set bot reference for scheduler (to send digest messages)
    set_bot(bot)

    # 5. Start scheduler (auto-fetch + daily digest)
    scheduler = create_scheduler()
    set_scheduler(scheduler)
    scheduler.start()
    await reconfigure_digest_job()
    logger.info("Scheduler started.")

    # 6. Start polling
    logger.info("📰 NewsHub Bot is starting…")
    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down…")
        scheduler.shutdown(wait=False)
        await stop_client()
        await bot.session.close()
        logger.info("Goodbye! 👋")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
