"""
Data-access helpers — thin wrappers around SQL queries.
v4.0: Per-user reads, channels, and settings.
"""

from __future__ import annotations

import logging
from database import get_db

logger = logging.getLogger(__name__)


# ═══════════════════════════  NEWS (shared)  ═══════════════════════════════

async def add_news(
    channel: str,
    message_id: int,
    text: str,
    summary: str,
    media_path: str | None = None,
    post_date: str | None = None,
) -> bool:
    """Add a new news item. Returns True if inserted, False if it already existed."""
    db = await get_db()
    try:
        if post_date:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO news (channel, message_id, text, summary, media_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (channel, message_id, text, summary, media_path, post_date),
            )
        else:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO news (channel, message_id, text, summary, media_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (channel, message_id, text, summary, media_path),
            )

        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def news_exists(channel: str, message_id: int) -> bool:
    """Check if a news item already exists in the database."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM news WHERE channel = ? AND message_id = ?",
            (channel, message_id),
        )
        row = await cursor.fetchone()
        return row is not None
    finally:
        await db.close()


async def check_similar_summary(summary: str, hours: int = 12) -> bool:
    """
    Check if a post with a SIMILAR AI summary exists in the database
    within the last N hours. Uses fuzzy matching to catch rephrased duplicates.
    """
    if not summary or summary == "—":
        return False

    from difflib import SequenceMatcher

    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT summary FROM news
            WHERE created_at >= datetime('now', ? || ' hours')
            """,
            (f"-{hours}",),
        )
        rows = await cursor.fetchall()

        summary_lower = summary.lower().strip()

        for row in rows:
            existing = (row[0] or "").lower().strip()
            if not existing:
                continue

            # 1. Exact match
            if summary_lower == existing:
                return True

            # 2. Fuzzy match (>55% similarity = likely same news)
            ratio = SequenceMatcher(None, summary_lower, existing).ratio()
            if ratio > 0.55:
                logger.debug("Fuzzy dedup: %.0f%% match\n  NEW: %s\n  OLD: %s",
                             ratio * 100, summary, row[0])
                return True

            # 3. Keyword overlap: extract significant words (>3 chars), check overlap
            new_words = {w for w in summary_lower.split() if len(w) > 3}
            old_words = {w for w in existing.split() if len(w) > 3}
            if new_words and old_words:
                overlap = len(new_words & old_words)
                total = min(len(new_words), len(old_words))
                if total > 0 and overlap / total >= 0.6:
                    logger.debug("Keyword dedup: %d/%d overlap\n  NEW: %s\n  OLD: %s",
                                 overlap, total, summary, row[0])
                    return True

        return False
    finally:
        await db.close()


async def get_recent_summaries(limit: int = 50) -> list[str]:
    """Get recent news summaries for AI deduplication context."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT summary FROM news ORDER BY id DESC LIMIT ?", 
            (limit,)
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows if row[0]]
    finally:
        await db.close()




async def get_news_by_id(news_id: int) -> dict | None:
    """Get a single news item by its database ID."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM news WHERE id = ?", (news_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ═══════════════════════════  PER-USER NEWS  ═══════════════════════════════

async def get_unread_news(user_id: int, page: int = 1, page_size: int = 5) -> list[dict]:
    """
    Get unread news for a specific user, filtered by their enabled channels.
    """
    offset = (page - 1) * page_size
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT n.id, n.channel, n.text, n.summary, n.media_path, n.created_at
            FROM news n
            JOIN user_channels uc ON n.channel = uc.channel
            LEFT JOIN user_reads ur ON n.id = ur.news_id AND ur.user_id = ?
            WHERE uc.user_id = ? 
              AND uc.enabled = 1
              AND ur.news_id IS NULL
            ORDER BY n.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, user_id, page_size, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_unread_count(user_id: int) -> int:
    """Count of unread news items for a specific user."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT COUNT(*) FROM news n
            INNER JOIN user_channels uc
                ON uc.channel = n.channel AND uc.user_id = ? AND uc.enabled = 1
            LEFT JOIN user_reads ur
                ON ur.news_id = n.id AND ur.user_id = ?
            WHERE ur.news_id IS NULL
            """,
            (user_id, user_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
    finally:
        await db.close()


async def mark_as_read(user_id: int, news_id: int) -> None:
    """Mark a news item as read for a specific user."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO user_reads (user_id, news_id) VALUES (?, ?)",
            (user_id, news_id),
        )
        await db.commit()
    finally:
        await db.close()


async def mark_all_read(user_id: int) -> int:
    """Mark all unread news as read for a specific user. Returns count."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO user_reads (user_id, news_id)
            SELECT ?, n.id FROM news n
            INNER JOIN user_channels uc
                ON uc.channel = n.channel AND uc.user_id = ? AND uc.enabled = 1
            LEFT JOIN user_reads ur
                ON ur.news_id = n.id AND ur.user_id = ?
            WHERE ur.news_id IS NULL
            """,
            (user_id, user_id, user_id),
        )
        await db.commit()
        return cursor.rowcount
    finally:
        await db.close()


async def get_recent_unread_for_digest(user_id: int, hours: int = 24) -> list[dict]:
    """Get unread news from the last N hours for a user's digest."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT n.id, n.channel, n.text, n.summary, n.media_path, n.created_at
            FROM news n
            INNER JOIN user_channels uc
                ON uc.channel = n.channel AND uc.user_id = ? AND uc.enabled = 1
            LEFT JOIN user_reads ur
                ON ur.news_id = n.id AND ur.user_id = ?
            WHERE ur.news_id IS NULL
              AND n.created_at >= datetime('now', ? || ' hours')
            ORDER BY n.created_at DESC
            """,
            (user_id, user_id, f"-{hours}"),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_recently_read(user_id: int, limit: int = 5) -> list[dict]:
    """Get the last N read news items for a user (Archive)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT n.id, n.channel, n.text, n.summary, n.media_path, n.created_at
            FROM news n
            INNER JOIN user_reads ur ON ur.news_id = n.id AND ur.user_id = ?
            INNER JOIN user_channels uc
                ON uc.channel = n.channel AND uc.user_id = ? AND uc.enabled = 1
            ORDER BY ur.read_at DESC
            LIMIT ?
            """,
            (user_id, user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


# ═══════════════════════════  USER CHANNELS  ═══════════════════════════════

async def get_user_channels(user_id: int) -> list[dict]:
    """Get all channels for a user (enabled and disabled)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT channel, enabled FROM user_channels WHERE user_id = ? ORDER BY channel",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def add_user_channel(user_id: int, channel: str) -> bool:
    """Add a channel for a user. Returns True if added, False if already exists."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO user_channels (user_id, channel, enabled) VALUES (?, ?, 1)",
            (user_id, channel),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def remove_user_channel(user_id: int, channel: str) -> None:
    """Remove a channel from a user's list."""
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM user_channels WHERE user_id = ? AND channel = ?",
            (user_id, channel),
        )
        await db.commit()
    finally:
        await db.close()


async def toggle_user_channel(user_id: int, channel: str) -> bool:
    """Toggle a channel on/off for a user. Returns new enabled state."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT enabled FROM user_channels WHERE user_id = ? AND channel = ?",
            (user_id, channel),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        new_state = 0 if row[0] else 1
        await db.execute(
            "UPDATE user_channels SET enabled = ? WHERE user_id = ? AND channel = ?",
            (new_state, user_id, channel),
        )
        await db.commit()
        return bool(new_state)
    finally:
        await db.close()


async def get_user_channel_count(user_id: int) -> int:
    """Count channels for a user."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM user_channels WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
    finally:
        await db.close()


async def seed_default_channels(user_id: int) -> None:
    """Copy default channels from config to a new user. Skips if user already has channels."""
    from config import CHANNELS
    existing = await get_user_channel_count(user_id)
    if existing > 0:
        return  # Already seeded

    db = await get_db()
    try:
        for ch in CHANNELS:
            await db.execute(
                "INSERT OR IGNORE INTO user_channels (user_id, channel, enabled) VALUES (?, ?, 1)",
                (user_id, ch),
            )
        await db.commit()
        logger.info("Seeded %d default channels for user %d", len(CHANNELS), user_id)
    finally:
        await db.close()


async def get_all_active_channels() -> list[str]:
    """Get the union of all enabled channels across all users (for parser)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT DISTINCT channel FROM user_channels WHERE enabled = 1"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]
    finally:
        await db.close()


# ═══════════════════════════  USER SETTINGS  ═══════════════════════════════

async def get_user_setting(user_id: int, key: str) -> str | None:
    """Get a per-user setting value."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        row = await cursor.fetchone()
        return row[0] if row else None
    finally:
        await db.close()


async def set_user_setting(user_id: int, key: str, value: str) -> None:
    """Set a per-user setting value (upsert)."""
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
            """,
            (user_id, key, value),
        )
        await db.commit()
    finally:
        await db.close()


async def get_all_digest_users() -> list[int]:
    """Get all user IDs that have digest enabled (or haven't explicitly disabled it)."""
    db = await get_db()
    try:
        # Get all unique users from user_channels
        cursor = await db.execute(
            """
            SELECT DISTINCT uc.user_id FROM user_channels uc
            LEFT JOIN user_settings us
                ON us.user_id = uc.user_id AND us.key = 'digest_enabled'
            WHERE us.value IS NULL OR us.value != '0'
            """
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]
    finally:
        await db.close()


# ═══════════════════════════  GLOBAL SETTINGS  ═════════════════════════════

async def get_setting(key: str) -> str | None:
    """Get a global setting value by key."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None
    finally:
        await db.close()


async def set_setting(key: str, value: str) -> None:
    """Set a global setting value (upsert)."""
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        await db.commit()
    finally:
        await db.close()
