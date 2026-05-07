"""
Database initialization and connection helpers (aiosqlite).
"""

import logging
import aiosqlite
from config import DATABASE_PATH

logger = logging.getLogger(__name__)

# ── SQL for table creation ────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT    NOT NULL,              -- @username канала-источника
    message_id  INTEGER NOT NULL,              -- ID сообщения в канале
    text        TEXT,                          -- Полный текст поста
    summary     TEXT,                          -- Краткий заголовок (от Gemini)
    is_read     BOOLEAN NOT NULL DEFAULT 0,    -- DEPRECATED: kept for migration
    priority    INTEGER NOT NULL DEFAULT 2,    -- 1 = 🔥 высокий, 2 = обычный, 3 = низкий
    media_path  TEXT,                          -- Путь к скачанной картинке/видео
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel, message_id)                -- Защита от дубликатов
);

-- Per-user read status (replaces news.is_read)
CREATE TABLE IF NOT EXISTS user_reads (
    user_id   INTEGER NOT NULL,
    news_id   INTEGER NOT NULL,
    read_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, news_id),
    FOREIGN KEY(news_id) REFERENCES news(id)
);

-- Per-user channel subscriptions
CREATE TABLE IF NOT EXISTS user_channels (
    user_id  INTEGER NOT NULL,
    channel  TEXT    NOT NULL,
    enabled  BOOLEAN NOT NULL DEFAULT 1,
    PRIMARY KEY(user_id, channel)
);

-- Per-user settings (digest_enabled, digest_interval, etc.)
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL,
    key     TEXT    NOT NULL,
    value   TEXT,
    PRIMARY KEY(user_id, key)
);

-- Global settings (active_model, parsing_start_time)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_news_priority ON news(priority, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_channel ON news(channel);
CREATE INDEX IF NOT EXISTS idx_user_reads_user ON user_reads(user_id);
CREATE INDEX IF NOT EXISTS idx_user_channels_user ON user_channels(user_id);
"""


async def get_db() -> aiosqlite.Connection:
    """Open a new database connection with WAL mode and FK support."""
    db = await aiosqlite.connect(str(DATABASE_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db() -> None:
    """Create all tables and indexes if they don't exist."""
    logger.info("Initializing database at %s", DATABASE_PATH)
    db = await get_db()
    try:
        await db.executescript(_SCHEMA)
        await db.commit()
        logger.info("Database schema ready (v4.0 multi-user).")
    finally:
        await db.close()
