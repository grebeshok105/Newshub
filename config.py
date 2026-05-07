"""
Central configuration — loads values from .env and exposes typed constants.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Paths ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / os.getenv("DATABASE_PATH", "newshub.db")

# ── Telegram Bot ─────────────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

# ── Telethon (UserBot) ───────────────────────────────────────────────────
TELETHON_API_ID: int = int(os.getenv("TELETHON_API_ID", "0"))
TELETHON_API_HASH: str = os.getenv("TELETHON_API_HASH", "")
TELETHON_SESSION_NAME: str = os.getenv("TELETHON_SESSION_NAME", "newshub_session")
TELETHON_PHONE: str = os.getenv("TELETHON_PHONE", "").strip()
if TELETHON_PHONE.lower() == "none" or TELETHON_PHONE == "+7XXXXXXXXXX":
    TELETHON_PHONE = ""

PROXY_IP: str = os.getenv("PROXY_IP", "")
PROXY_PORT: str = os.getenv("PROXY_PORT", "")
PROXY_SECRET: str = os.getenv("PROXY_SECRET", "")

# ── Fireworks AI / LLM ────────────────────────────────────────────────────
FIREWORKS_API_KEY: str = os.getenv("FIREWORKS_API_KEY", "")
FIREWORKS_MODEL: str = os.getenv(
    "FIREWORKS_MODEL", "accounts/fireworks/models/glm-5p1"
)

# Order is by empirical instruction-following on our digest task:
# - GLM and MiniMax respect the system prompt cleanly.
# - Kimi-k2 ignores system role and dumps chain-of-thought into content.
# - DeepSeek-v4-pro is a reasoning model: slow, token-hungry, last resort.
SUPPORTED_MODELS: list[str] = [
    "accounts/fireworks/models/glm-5p1",
    "accounts/fireworks/models/glm-5",
    "accounts/fireworks/models/minimax-m2p7",
    "accounts/fireworks/models/kimi-k2p6",
    "accounts/fireworks/models/kimi-k2p5",
    "accounts/fireworks/models/deepseek-v4-pro",
]

# ── Channels ─────────────────────────────────────────────────────────────
CHANNELS: list[str] = [
    ch.strip()
    for ch in os.getenv("CHANNELS", "").split(",")
    if ch.strip()
]

# ── Blacklist ────────────────────────────────────────────────────────────
BLACKLIST_WORDS: list[str] = [
    w.strip().lower()
    for w in os.getenv("BLACKLIST_WORDS", "").split(",")
    if w.strip()
]

# ── Schedule & Timezone ──────────────────────────────────────────────────
DIGEST_HOUR: int = int(os.getenv("DIGEST_HOUR", "9"))
TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow")
CHANNEL_FETCH_INTERVAL: int = int(os.getenv("CHANNEL_FETCH_INTERVAL", "15"))

# ── Pagination ───────────────────────────────────────────────────────────
PAGE_SIZE: int = int(os.getenv("PAGE_SIZE", "5"))

# ── Logging ──────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
