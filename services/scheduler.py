"""
Scheduler — APScheduler-based auto-fetching and daily digest.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import models
from config import CHANNEL_FETCH_INTERVAL, DIGEST_HOUR, TIMEZONE

logger = logging.getLogger(__name__)

# Will be set by main.py after the bot is initialized
_bot_instance = None
_scheduler_instance: AsyncIOScheduler | None = None

def set_bot(bot) -> None:
    """Store a reference to the aiogram Bot for sending messages."""
    global _bot_instance
    _bot_instance = bot

def set_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Store a reference to the APScheduler."""
    global _scheduler_instance
    _scheduler_instance = scheduler

async def reconfigure_digest_job() -> None:
    """
    Read digest settings from DB and update the scheduler job.
    Called on startup and when user changes settings.
    """
    if not _scheduler_instance:
        return
        
    enabled_str = await models.get_setting("digest_enabled")
    interval = await models.get_setting("digest_interval") or "24h"
    enabled = enabled_str != "0" # Default is True if not set
    
    if not enabled:
        # Pause or remove job
        if _scheduler_instance.get_job("daily_digest"):
            _scheduler_instance.pause_job("daily_digest")
            logger.info("Auto-digest is disabled. Job paused.")
        return
        
    # Resume if paused
    if _scheduler_instance.get_job("daily_digest"):
        _scheduler_instance.resume_job("daily_digest")
        
    # Reconfigure trigger based on interval
    if interval == "3h":
        trigger = IntervalTrigger(hours=3)
    elif interval == "6h":
        trigger = IntervalTrigger(hours=6)
    elif interval == "12h":
        # 09:00 and 21:00
        trigger = CronTrigger(hour="9,21", minute=0, timezone=TIMEZONE)
    else: # 24h
        trigger = CronTrigger(hour=DIGEST_HOUR, minute=0, timezone=TIMEZONE)
        
    _scheduler_instance.reschedule_job("daily_digest", trigger=trigger)
    logger.info("Auto-digest reconfigured: enabled=True, interval=%s", interval)


# ═══════════════════════════  JOBS  ════════════════════════════════════════

async def _channel_scrape_job() -> None:
    """Scrape all configured channels for new posts."""
    from services.telethon_parser import fetch_all_channels

    logger.info("⏰ Running scheduled channel scrape…")
    try:
        new_count = await fetch_all_channels()
        logger.info("Scheduled scrape done: %d new posts.", new_count)
    except Exception:
        logger.exception("Scheduled channel scrape failed")


async def _daily_digest_job() -> None:
    """Generate and send a digest to all users who have it enabled."""
    from services.summarizer import generate_digest
    from services.telethon_parser import fetch_all_channels

    logger.info("📰 Running daily digest job…")
    try:
        # Fetch fresh posts first
        await fetch_all_channels()

        # Get all users with digest enabled
        user_ids = await models.get_all_digest_users()
        if not user_ids:
            logger.info("No users with digest enabled — skipping.")
            return

        if not _bot_instance:
            logger.warning("Bot not available — skipping digest send.")
            return

        for user_id in user_ids:
            try:
                # Get this user's unread news
                posts = await models.get_recent_unread_for_digest(user_id, hours=24)
                if not posts:
                    logger.debug("No unread posts for user %d — skipping.", user_id)
                    continue

                # Generate digest
                digest_text = await generate_digest(posts)

                # Send digest
                await _bot_instance.send_message(
                    chat_id=user_id,
                    text=digest_text,
                    parse_mode="HTML",
                )

                unread_count = await models.get_unread_count(user_id)
                await _bot_instance.send_message(
                    chat_id=user_id,
                    text=f"\n📊 Всего непрочитанных: <b>{unread_count}</b>",
                    parse_mode="HTML",
                )

                logger.info("Digest sent to user %d.", user_id)

            except Exception:
                logger.exception("Failed to send digest to user %d", user_id)

    except Exception:
        logger.exception("Daily digest job failed")


# ═══════════════════════════  SETUP  ═══════════════════════════════════════

def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the scheduler (but don't start it yet)."""
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    # Channel scraping every N minutes
    scheduler.add_job(
        _channel_scrape_job,
        trigger=IntervalTrigger(minutes=CHANNEL_FETCH_INTERVAL),
        id="channel_scrape",
        name="Channel Scrape",
        replace_existing=True,
    )

    # Daily digest at configured hour (e.g., 09:00 Moscow time)
    scheduler.add_job(
        _daily_digest_job,
        trigger=CronTrigger(hour=DIGEST_HOUR, minute=0, timezone=TIMEZONE),
        id="daily_digest",
        name="Daily News Digest",
        replace_existing=True,
    )

    logger.info(
        "Scheduler configured: channels every %d min, digest at %02d:00 (%s).",
        CHANNEL_FETCH_INTERVAL,
        DIGEST_HOUR,
        TIMEZONE,
    )
    return scheduler
