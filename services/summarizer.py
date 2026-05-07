"""
Summarization service — Fireworks AI REST API for post summaries and digests.

Uses aiohttp to call the Fireworks AI Chat Completions API directly.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import aiohttp

import models
from utils.text import make_summary_fallback, truncate, scrub_markdown

logger = logging.getLogger(__name__)

# ── Fireworks AI REST API endpoint ───────────────────────────────────────────
_FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

# Reasoning models (e.g. deepseek-v4-pro) emit chain-of-thought wrapped in
# <think>…</think> tags inside `content`. We want only the final answer, so we
# strip these blocks before returning.
_THINK_BLOCK_RE = re.compile(
    r"<\s*(think|thinking|reasoning)\s*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_OPEN_THINK_RE = re.compile(
    r"<\s*(think|thinking|reasoning)\s*>.*?(?=<\s*/\s*\1\s*>|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_CLOSE_THINK_RE = re.compile(
    r"<\s*/\s*(think|thinking|reasoning)\s*>",
    re.IGNORECASE,
)


def _strip_thinking(text: str) -> str:
    """Remove reasoning-model chain-of-thought blocks from `text`."""
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text)
    # Tolerate unclosed/malformed think blocks (truncated, missing close tag).
    cleaned = _OPEN_THINK_RE.sub("", cleaned)
    cleaned = _CLOSE_THINK_RE.sub("", cleaned)
    return cleaned.strip()


async def _call_fireworks(
    prompt: str,
    model_name: str,
    max_tokens: int = 500,
    system_prompt: str | None = None,
) -> str | None:
    """
    Call the Fireworks AI Chat Completions API with the specified model.
    Returns the generated text, or None on any error.
    """
    from config import FIREWORKS_API_KEY

    if not FIREWORKS_API_KEY:
        logger.warning("FIREWORKS_API_KEY not set — skipping AI call.")
        return None

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {FIREWORKS_API_KEY}",
    }

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model_name,
        "max_tokens": max_tokens,
        "top_p": 1,
        "top_k": 40,
        "presence_penalty": 0,
        "frequency_penalty": 0,
        "temperature": 0.3,
        "messages": messages,
    }

    logger.debug("Requesting Fireworks AI (%s)...", model_name)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _FIREWORKS_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(
                        "Fireworks API error %d [%s]: %s",
                        resp.status,
                        model_name,
                        error_text[:300],
                    )
                    return None

                data = await resp.json()
                choices = data.get("choices", []) or []
                if choices:
                    message = choices[0].get("message") or {}
                    raw_content = message.get("content") or ""
                    # Some reasoning models emit CoT in `reasoning_content`
                    # instead of inline <think> blocks. We always discard it.
                    content = _strip_thinking(raw_content).strip()
                    if content:
                        return content
                    finish_reason = choices[0].get("finish_reason")
                    logger.warning(
                        "Fireworks [%s] returned empty content (finish_reason=%s). "
                        "Reasoning model may have run out of tokens.",
                        model_name,
                        finish_reason,
                    )
                    return None

                logger.warning("Fireworks [%s] returned no choices.", model_name)
                return None

    except Exception:
        logger.exception("Fireworks API call failed [%s]", model_name)
        return None


async def call_ai_with_failover(
    prompt: str,
    max_tokens: int = 500,
    system_prompt: str | None = None,
) -> str | None:
    """
    Try the active model from settings. If it fails, cycle to the next
    supported model and retry once.
    """
    from config import SUPPORTED_MODELS, FIREWORKS_MODEL

    # 1. Get active model
    active_model = await models.get_setting("active_model")
    if not active_model:
        active_model = FIREWORKS_MODEL

    # 2. Try active model
    result = await _call_fireworks(prompt, active_model, max_tokens, system_prompt)
    if result:
        return scrub_markdown(result)

    # 3. Failover: cycle to the next model in SUPPORTED_MODELS
    logger.warning("Failover triggered! Model %s failed.", active_model)

    try:
        current_idx = SUPPORTED_MODELS.index(active_model)
    except ValueError:
        current_idx = 0

    next_idx = (current_idx + 1) % len(SUPPORTED_MODELS)
    next_model = SUPPORTED_MODELS[next_idx]

    logger.info("Switching to fallback model: %s", next_model)
    await models.set_setting("active_model", next_model)

    # Retry once with the new model
    result = await _call_fireworks(prompt, next_model, max_tokens, system_prompt)
    if result:
        return scrub_markdown(result)

    return None


# ═══════════════════════════  SINGLE POST SUMMARY  ═════════════════════════

_SUMMARY_SYSTEM_PROMPT = """\
Ты — редактор заголовков для киберспортивного новостного канала.
Тебе пришлют один пост и список недавних заголовков для проверки на дубликат.

Правила ответа (СТРОГО):
- Если новый пост — точный смысловой дубликат одной из недавних новостей (то же событие, результат матча, трансфер), ответь ровно одним словом: DUPLICATE_POST. Без точки. Без кавычек. Ничего больше.
- Иначе ответь КРАТКИМ заголовком на русском. Максимум 80 символов. Только суть: кто, что, счёт. Без кавычек, без markdown, без "Заголовок:", без вступлений и пояснений. Эмодзи допустимы.
- Не пересказывай задачу. Не объясняй ход мыслей. Не пиши ничего, кроме заголовка или DUPLICATE_POST.
"""

_SUMMARY_USER_PROMPT = """\
Недавние заголовки:
{context}

Новый пост:
{text}
"""


async def summarize_post(text: str, recent_summaries: list[str] | None = None) -> str | None:
    """
    Generate a short summary (title) for a single post using LLM.
    If recent_summaries is provided, it checks for semantic duplicates.
    Returns 'DUPLICATE_POST' if it's a semantic duplicate.
    Falls back to first-line extraction on failure.
    """
    if not text or not text.strip():
        return "—"

    context_str = "\n".join(f"- {s}" for s in recent_summaries) if recent_summaries else "Нет недавних новостей."
    user_prompt = _SUMMARY_USER_PROMPT.format(
        context=context_str, text=truncate(text, 2000)
    )

    result = await call_ai_with_failover(
        user_prompt,
        max_tokens=512,
        system_prompt=_SUMMARY_SYSTEM_PROMPT,
    )

    if result:
        summary = _clean_title(result)
        if summary:
            return summary

    # ── Fallback ──
    logger.warning("AI summarization failed completely. Stripping text.")
    return make_summary_fallback(text)


_TITLE_PREFACE_RE = re.compile(
    r"^\s*(?:заголовок|title|ответ|answer|итог|вывод)\s*[:\-—]\s*",
    re.IGNORECASE,
)


def _clean_title(raw: str) -> str:
    """Strip common preface artefacts from a single-title model response."""
    if not raw:
        return ""
    text = raw.strip().strip('"\'').strip()
    text = _TITLE_PREFACE_RE.sub("", text).strip()
    # Some models still emit multi-line analysis — keep only the first
    # non-empty line, which is reliably the actual title.
    for line in text.splitlines():
        line = line.strip().strip('"\'').strip()
        if line:
            return line
    return text


# ═══════════════════════════  DAILY DIGEST  ════════════════════════════════

_DIGEST_SYSTEM_PROMPT = """\
Ты — редактор киберспортивного дайджеста. Тебе пришлют список новостей за день, ты возвращаешь готовый дайджест.

Правила формата (СТРОГО):
- Только обычный текст и эмодзи. Никакого markdown: без *, #, [, ], _, `.
- Дайджест начинается строкой «📰 Дайджест новостей — DD.MM.YYYY» — где дату подставит пользователь.
- Главные пункты — буллеты «🔹 Заголовок — суть в 1–2 коротких предложениях.».
- Внизу блок «📌 Коротко о другом:» со строками «· факт».
- Сгруппируй дубли: если несколько постов об одном событии, делай ОДИН пункт.

Правила вывода (СТРОГО):
- Не пересказывай задачу. Не повторяй правила. Не пиши анализ или рассуждения.
- Не пиши вступлений вроде «Вот дайджест:» или «Анализ новостей».
- Первый символ ответа — эмодзи 📰. Дальше идёт сразу готовый дайджест и больше ничего.
"""

_DIGEST_USER_PROMPT = """\
Дата: {date}

Новости:
{news_block}

Сделай дайджест по правилам.
"""


async def generate_digest(posts: list[dict]) -> str:
    """
    Generate a formatted digest from a list of news posts using Fireworks AI.
    Falls back to a simple listing if AI is unavailable.
    """
    if not posts:
        return "📭 Нет новостей для дайджеста."

    # Build news block for prompt
    news_lines: list[str] = []
    for i, post in enumerate(posts[:20], 1):
        channel = post.get("channel", "?")
        txt = truncate(post.get("text", ""), 600)
        news_lines.append(f"[{i}] @{channel}: {txt}")

    news_block = "\n\n".join(news_lines)
    date_str = datetime.now().strftime("%d.%m.%Y")

    user_prompt = _DIGEST_USER_PROMPT.format(date=date_str, news_block=news_block)
    result = await call_ai_with_failover(
        user_prompt,
        max_tokens=2000,
        system_prompt=_DIGEST_SYSTEM_PROMPT,
    )

    if result:
        cleaned = _clean_digest(result)
        logger.info(
            "Fireworks digest generated (%d chars, cleaned to %d).",
            len(result),
            len(cleaned),
        )
        if cleaned:
            return cleaned

    return _fallback_digest(posts, date_str)


def _clean_digest(raw: str) -> str:
    """
    Trim model preamble before the digest header.

    Models occasionally echo the task or write an analysis step before the
    actual digest. We anchor on the first '📰' (the required first character of
    the answer) and drop everything before it.
    """
    if not raw:
        return ""
    idx = raw.find("📰")
    if idx > 0:
        return raw[idx:].strip()
    return raw.strip()


def _fallback_digest(posts: list[dict], date_str: str) -> str:
    """Simple formatted listing when LLM is unavailable."""
    lines = [f"📰 <b>Дайджест новостей — {date_str}</b>\n"]

    for i, post in enumerate(posts[:15], 1):
        summary = post.get("summary", "")
        channel = post.get("channel", "?")
        emoji = "🔹"
        lines.append(f"{emoji} <b>{i}.</b> {summary}\n   <i>@{channel}</i>")

    return "\n".join(lines)
