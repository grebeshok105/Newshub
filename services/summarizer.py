"""
Summarization service — Fireworks AI REST API for post summaries and digests.

Uses aiohttp to call the Fireworks AI Chat Completions API directly.
"""

from __future__ import annotations

import logging
from datetime import datetime

import aiohttp

import models
from utils.text import make_summary_fallback, truncate, scrub_markdown

logger = logging.getLogger(__name__)

# ── Fireworks AI REST API endpoint ───────────────────────────────────────────
_FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"


async def _call_fireworks(prompt: str, model_name: str, max_tokens: int = 500) -> str | None:
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

    payload = {
        "model": model_name,
        "max_tokens": max_tokens,
        "top_p": 1,
        "top_k": 40,
        "presence_penalty": 0,
        "frequency_penalty": 0,
        "temperature": 0.3,
        "messages": [
            {"role": "user", "content": prompt},
        ],
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
                choices = data.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    content = message.get("content", "").strip()
                    if content:
                        return content

                logger.warning("Fireworks [%s] returned empty response.", model_name)
                return None

    except Exception:
        logger.exception("Fireworks API call failed [%s]", model_name)
        return None


async def call_ai_with_failover(prompt: str, max_tokens: int = 500) -> str | None:
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
    result = await _call_fireworks(prompt, active_model, max_tokens)
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
    result = await _call_fireworks(prompt, next_model, max_tokens)
    if result:
        return scrub_markdown(result)

    return None


# ═══════════════════════════  SINGLE POST SUMMARY  ═════════════════════════

_SUMMARY_PROMPT = """\
Прочитай этот новый пост из Telegram-канала.
Вот список из недавних заголовков (для контекста):
{context}

Твоя задача:
1. ОЧЕНЬ ВАЖНО: Проверь, не является ли новый пост очевидным ДУБЛИКАТОМ одной из этих недавних новостей (т.е. описывает то же самое событие, результат матча или трансфер).
2. Если это точный смысловой дубликат новости из списка, напиши в ответ ровно одно слово: DUPLICATE_POST. Ничего больше.
3. Если это НОВАЯ новость, напиши КРАТКИЙ заголовок на русском языке. Максимум 80 символов. Суть новости (кто, что, счет). Текст должен быть сухим, без кавычек, спецсимволов и "Заголовок:". Только plain text и эмодзи.

Пост:
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
    prompt = _SUMMARY_PROMPT.format(context=context_str, text=truncate(text, 2000))

    result = await call_ai_with_failover(prompt, max_tokens=80)

    if result:
        # Clean up common LLM artifacts
        summary = result.strip('"\'')
        if summary.lower().startswith("заголовок:"):
            summary = summary[10:].strip()
        return summary

    # ── Fallback ──
    logger.warning("AI summarization failed completely. Stripping text.")
    return make_summary_fallback(text)


# ═══════════════════════════  DAILY DIGEST  ════════════════════════════════

_DIGEST_PROMPT = """\
Ты — AI-редактор киберспортивного дайджеста. Твоя задача — сделать СУХУЮ и ЧЕТКУЮ выжимку.

Правила форматирования (КРИТИЧЕСКИ ВАЖНО):
1. ЗАПРЕЩЕНО использовать любое Markdown-форматирование: никаких звездочек (*), решеток (#), квадратных скобок ([]) или нижних подчеркиваний (_).
2. Только обычный текст и ЭМОДЗИ.
3. Текст должен быть плотным, без лишних слов.

Логика контента:
1. СГРУППИРУЙ новости: если 3 канала написали об одной победе команды — сделай из этого ОДИН пункт.
2. Дай краткое резюме (1-2 коротких предложения) на каждый пункт.
3. Формат вывода:
   📰 Дайджест новостей — {date}
   
   🔹 Название события — суть кратко.
   🔹 Название события — суть кратко.
   
   📌 Коротко о другом:
   · Факт 1
   · Факт 2

Новости для обработки:
{news_block}
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

    prompt = _DIGEST_PROMPT.format(date=date_str, news_block=news_block)
    result = await call_ai_with_failover(prompt, max_tokens=2000)

    if result:
        logger.info("Fireworks digest generated (%d chars).", len(result))
        return result

    return _fallback_digest(posts, date_str)


def _fallback_digest(posts: list[dict], date_str: str) -> str:
    """Simple formatted listing when LLM is unavailable."""
    lines = [f"📰 <b>Дайджест новостей — {date_str}</b>\n"]

    for i, post in enumerate(posts[:15], 1):
        summary = post.get("summary", "")
        channel = post.get("channel", "?")
        emoji = "🔹"
        lines.append(f"{emoji} <b>{i}.</b> {summary}\n   <i>@{channel}</i>")

    return "\n".join(lines)
