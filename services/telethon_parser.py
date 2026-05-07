"""
Telethon-based channel parser — reads messages from Telegram channels
using the UserBot API (requires phone login once).
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.tl.types import Channel
from telethon.network import connection

import models
from config import (
    CHANNELS,
    TELETHON_API_HASH,
    TELETHON_API_ID,
    TELETHON_SESSION_NAME,
    TELETHON_PHONE,
    PROXY_IP,
    PROXY_PORT,
    PROXY_SECRET,
    BASE_DIR,
)
from services.blacklist import is_blacklisted
from services.summarizer import generate_digest, summarize_post
from utils.text import normalize_text

logger = logging.getLogger(__name__)

# ── Telethon client (singleton) ──────────────────────────────────────────
_client: TelegramClient | None = None


def get_client() -> TelegramClient:
    """Get or create the Telethon client (not yet started)."""
    global _client
    if _client is None:
        session_path = str(BASE_DIR / TELETHON_SESSION_NAME)
        
        # Configure MTProxy if available
        if PROXY_IP and PROXY_PORT and PROXY_SECRET:
            logger.info("Using MTProxy: %s:%s", PROXY_IP, PROXY_PORT)
            _client = TelegramClient(
                session_path,
                TELETHON_API_ID,
                TELETHON_API_HASH,
                connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
                proxy=(PROXY_IP, int(PROXY_PORT), PROXY_SECRET)
            )
        else:
            _client = TelegramClient(
                session_path,
                TELETHON_API_ID,
                TELETHON_API_HASH,
            )
            
    return _client


async def start_client() -> TelegramClient:
    """
    Start the Telethon client.
    On first run, will prompt for phone number and auth code in terminal.
    After that, the session is saved to a file.
    """
    client = get_client()
    if not client.is_connected():
        logger.info("Starting Telethon client…")
        if TELETHON_PHONE:
            await client.start(phone=TELETHON_PHONE)
        else:
            await client.start()
        me = await client.get_me()
        logger.info(
            "Telethon authorized as: %s (ID: %s)",
            me.first_name if me else "?",
            me.id if me else "?",
        )
    return client


async def stop_client() -> None:
    """Gracefully disconnect the Telethon client."""
    global _client
    if _client and _client.is_connected():
        await _client.disconnect()
        logger.info("Telethon client disconnected.")
    _client = None


# ═══════════════════════════  CHANNEL PARSING  ═════════════════════════════

async def fetch_channel_posts(
    client: TelegramClient,
    channel_username: str,
    limit: int = 5,
) -> int:
    """
    Fetch the latest posts from a single channel and save new ones to DB.
    Applies blacklist filtering and Gemini summarization.

    Returns the number of new posts added.
    """
    new_count = 0

    try:
        # Resolve the channel entity
        entity = await client.get_entity(channel_username)
        logger.info("Fetching from @%s…", channel_username)

        # ── Get the baseline date for "Fresh Only" logic ────────────
        # Skip everything older than the bot's first launch/start
        raw_start_time = await models.get_setting("parsing_start_time")
        if not raw_start_time:
            # First time ever? Set current time as baseline
            # Telegram uses UTC for message dates.
            base_date = datetime.now(timezone.utc)
            await models.set_setting("parsing_start_time", base_date.isoformat())
            logger.info("First run: set parsing baseline to %s", base_date)
        else:
            base_date = datetime.fromisoformat(raw_start_time)

        # Iterate over the latest messages
        async for message in client.iter_messages(entity, limit=limit):
            try:
                # ── Freshness check ──────────────────────────────────
                # If post date is older than baseline, stop (messages are sorted desc)
                if message.date < base_date:
                    logger.debug("Reached historical messages at @%s, stopping.", channel_username)
                    break 

                # ── Existence check ──────────────────────────────────
                # Skip if post is already in the database
                if await models.news_exists(channel_username, message_id=message.id):
                    continue

                # Skip messages without text
                if not message.text:
                    continue

                raw_text = normalize_text(message.text)
                if not raw_text.strip():
                    continue

                # ── Blacklist filter ─────────────────────────────────
                if is_blacklisted(raw_text):
                    logger.debug("Blocked by blacklist: @%s #%s", channel_username, message.id)
                    continue

                # ── Existence check ──────────────────────────────────
                # Skip if post is already in the database
                if await models.news_exists(channel_username, message_id=message.id):
                    continue

                # ── Layer 1: AI semantic dedup check ──────────────────
                # Feed the last 50 summaries into the LLM prompt
                recent_summaries = await models.get_recent_summaries(50)
                summary = await summarize_post(raw_text, recent_summaries)
                
                if not summary or "DUPLICATE_POST" in summary:
                    logger.info("[DEDUP-AI] Skipping via AI check.")
                    continue

                # ── Layer 2: Fuzzy + keyword fallback ─────────────────
                if await models.check_similar_summary(summary):
                    logger.info("[DEDUP-FUZZY] Skipping via fuzzy match: %s", summary)
                    continue

                # ── Save to DB ───────────────────────────────────────
                # Use message.date (UTC by default in telethon)
                post_date_str = message.date.strftime("%Y-%m-%d %H:%M:%S")
                
                inserted = await models.add_news(
                    channel=channel_username,
                    message_id=message.id,
                    text=raw_text,
                    summary=summary,
                    media_path=None,
                    post_date=post_date_str,
                )

                if inserted:
                    new_count += 1
                    logger.info(
                        "[NEW] @%s #%s — %s",
                        channel_username, message.id, summary[:50],
                    )

            except Exception:
                logger.exception(
                    "Error processing message #%s from @%s",
                    message.id, channel_username,
                )

    except Exception:
        logger.exception("Failed to fetch channel @%s", channel_username)

    return new_count


async def fetch_all_channels(limit: int = 20) -> int:
    """
    Fetch posts from all channels subscribed by any user.
    Returns total number of new posts added.
    """
    channels = await models.get_all_active_channels()
    if not channels:
        logger.warning("No active channels across any user.")
        return 0

    client = get_client()
    if not client.is_connected():
        await start_client()

    total_new = 0
    for username in channels:
        try:
            n = await fetch_channel_posts(client, username, limit=limit)
            total_new += n
            logger.info("@%s — %d new posts", username, n)
        except Exception:
            logger.exception("Failed to process channel @%s", username)
        # Small delay between channels
        await asyncio.sleep(1)

    logger.info("Channel fetch complete: %d new posts total.", total_new)
    return total_new


def _preview_line(text: str, max_chars: int = 100) -> str:
    """Cheap one-line preview from raw post text — no AI calls."""
    one_line = " ".join(text.split())
    if len(one_line) <= max_chars:
        return one_line
    return one_line[: max_chars - 1].rstrip() + "…"


async def test_fetch_random_channel(
    user_id: int | None = None,
    count: int = 10,
) -> tuple[str | None, list[dict], str | None]:
    """
    DEBUG: Pick a random channel (from the user's channels if given, otherwise
    any active channel across all users), fetch the latest `count` posts and
    build a digest from them.

    To stay well below Fireworks free-tier rate limits, this function makes a
    *single* AI call (the digest); per-post previews are derived from the raw
    text. Nothing is written to the database. Returns
    (channel_name, items, digest) where each item has keys
    ``message_id``, ``channel``, ``summary``, ``text``.
    """
    if user_id is not None:
        user_channels = await models.get_user_channels(user_id)
        channels = [c["channel"] for c in user_channels if c.get("enabled")]
    else:
        channels = await models.get_all_active_channels()

    if not channels:
        return None, [], None

    username = random.choice(channels)
    client = get_client()
    if not client.is_connected():
        await start_client()

    items: list[dict] = []
    try:
        entity = await client.get_entity(username)
        logger.info(
            "DEBUG: Test fetching last %d posts from @%s…", count, username
        )

        async for message in client.iter_messages(entity, limit=count * 3):
            if len(items) >= count:
                break
            try:
                if not message.text:
                    continue

                raw_text = normalize_text(message.text)
                if not raw_text.strip():
                    continue

                if is_blacklisted(raw_text):
                    continue

                items.append(
                    {
                        "message_id": message.id,
                        "channel": username,
                        "summary": _preview_line(raw_text),
                        "text": raw_text,
                    }
                )
            except Exception:
                logger.exception(
                    "Test parse: error on message #%s @%s",
                    getattr(message, "id", "?"), username,
                )

    except Exception:
        logger.exception("Failed to DEBUG fetch channel @%s", username)
        return username, items, None

    if not items:
        return username, items, None

    digest = await generate_digest(items)
    return username, items, digest
