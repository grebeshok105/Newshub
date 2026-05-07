"""
Telegram bot handlers — commands, buttons, callbacks, FSM states.
v4.0: Multi-user support with per-user channels, reads, and settings.
"""

from __future__ import annotations

import logging
import math
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import models
from bot.keyboards import (
    main_menu_keyboard,
    news_detail_keyboard,
    pagination_keyboard,
    model_selection_keyboard,
    digest_settings_keyboard,
    settings_menu_keyboard,
    channels_keyboard,
    remove_channel_keyboard,
)
from config import PAGE_SIZE, FIREWORKS_MODEL
from utils.text import truncate, scrub_markdown, smart_truncate
from datetime import datetime, timezone, timedelta

# Moscow = UTC+3
_MSK = timezone(timedelta(hours=3))

# Bot version (update on every release)
BOT_VERSION = "v.3.0-MULTI"
BOT_UPDATE_DATE = "17.04 — 16:30"

MAX_CHANNELS_PER_USER = 10


def _format_time_msk(created_at_str: str) -> str:
    """Parse DB timestamp (UTC) and return HH:MM in Moscow time."""
    try:
        dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)  # Mark as UTC
        msk = dt.astimezone(_MSK)             # Convert to Moscow
        return msk.strftime("%H:%M")
    except Exception:
        return "—"


def _get_user_id(message_or_callback) -> int:
    """Extract user ID from Message or CallbackQuery."""
    if hasattr(message_or_callback, 'from_user') and message_or_callback.from_user:
        return message_or_callback.from_user.id
    return 0


logger = logging.getLogger(__name__)
router = Router()


# ═══════════════════════════  FSM STATES  ══════════════════════════════════

class DetailState(StatesGroup):
    """FSM state for 'Детальный разбор' — waiting for news number."""
    waiting_for_number = State()


class AddChannelState(StatesGroup):
    """FSM state for adding a new channel."""
    waiting_for_name = State()


# ═══════════════════════════  /start  ══════════════════════════════════════

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user = message.from_user
    if not user:
        return

    user_id = user.id

    # Seed default channels for new users
    await models.seed_default_channels(user_id)

    # Get this user's channels for display
    user_channels = await models.get_user_channels(user_id)
    enabled_channels = [ch for ch in user_channels if ch["enabled"]]
    channels_list = "\n".join([f"· <i>@{ch['channel']}</i>" for ch in enabled_channels])
    if not channels_list:
        channels_list = "<i>Нет каналов. Добавь через ⚙️ Настройки.</i>"

    welcome = (
        f"📰 <b>NewsHub Bot</b> [{BOT_VERSION}]\n"
        f"<i>Последнее обновление: {BOT_UPDATE_DATE}</i>\n\n"
        "Я агрегирую новости из Telegram-каналов, "
        "фильтрую мусор и делаю AI-суммаризацию.\n\n"
        f"📡 <b>Твои каналы:</b>\n{channels_list}\n\n"
        "🔹 <b>📋 Список новостей</b> — непрочитанные с пагинацией\n"
        "🔹 <b>🔄 Обновить</b> — парсинг каналов прямо сейчас\n"
        "🔹 <b>🔥 Дайджест</b> — краткая выжимка всего важного\n"
        "🔹 <b>⚙️ Настройки</b> — каналы, модель ИИ, авто-дайджест\n"
    )
    await message.answer(
        welcome,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    logger.info("User %s (%s) started the bot.", user_id, user.username)


# ═══════════════════════════  📋 NEWS LIST  ════════════════════════════════

async def _send_news_page(message: Message, user_id: int, page: int, edit: bool = False) -> None:
    """Build and send/edit a paginated news list message."""
    total = await models.get_unread_count(user_id)
    if total == 0:
        text = "📭 Нет непрочитанных новостей.\nНажми 🔄 <b>Обновить</b> для парсинга."
        if edit:
            await message.edit_text(text, parse_mode="HTML")
        else:
            await message.answer(text, parse_mode="HTML")
        return

    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(1, min(page, total_pages))

    # Get this page of news
    news_items = await models.get_unread_news(user_id, page=page, page_size=PAGE_SIZE)

    # Build message
    lines = [f"📋 <b>Непрочитанные новости</b> ({total} шт.)\n"]

    for item in news_items:
        news_id = item.get("id", 0)

        # Extract time and convert to MSK
        created_at_raw = item.get("created_at", "")
        time_str = _format_time_msk(created_at_raw)

        # Scrub summary during display to clean up any leftover markdown
        summary = scrub_markdown(item.get("summary", "—"))
        channel = item.get("channel", "?")

        # Simplify format: no extra emojis, clean alignment
        lines.append(f"<b>#{news_id}</b>  {time_str}  {summary}\n  <i>@{channel}</i>")

    text = "\n".join(lines)

    kb = pagination_keyboard(page, total_pages)

    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(F.text == "📋 Список новостей")
async def btn_news_list(message: Message, state: FSMContext) -> None:
    """Show the first page of unread news."""
    await state.clear()
    user_id = _get_user_id(message)
    await state.update_data(user_id=user_id)
    await _send_news_page(message, user_id=user_id, page=1)


# ═══════════════════════════  ⚙️ SETTINGS  ════════════════════════════════

@router.message(F.text == "⚙️ Настройки")
async def btn_settings(message: Message, state: FSMContext) -> None:
    """Show the settings inline menu."""
    await state.clear()
    await message.answer(
        "⚙️ <b>Настройки</b>\n\nВыбери раздел:",
        parse_mode="HTML",
        reply_markup=settings_menu_keyboard(),
    )


@router.callback_query(F.data == "settings_back")
async def cb_settings_back(callback: CallbackQuery) -> None:
    """Return to the main settings menu."""
    try:
        await callback.message.edit_text(  # type: ignore
            "⚙️ <b>Настройки</b>\n\nВыбери раздел:",
            parse_mode="HTML",
            reply_markup=settings_menu_keyboard(),
        )
    except Exception:
        pass
    await callback.answer()


# ═══════════════════════════  📡 CHANNELS  ═════════════════════════════════

@router.callback_query(F.data == "settings_channels")
async def cb_channels_menu(callback: CallbackQuery) -> None:
    """Show the channel management menu."""
    user_id = _get_user_id(callback)
    channels = await models.get_user_channels(user_id)

    count = len(channels)
    text = (
        f"📡 <b>Твои каналы</b> ({count}/{MAX_CHANNELS_PER_USER})\n\n"
        "🟢 = включен, 🔴 = выключен\n"
        "Нажми на канал чтобы переключить."
    )
    try:
        await callback.message.edit_text(  # type: ignore
            text,
            parse_mode="HTML",
            reply_markup=channels_keyboard(channels),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_ch:"))
async def cb_toggle_channel(callback: CallbackQuery) -> None:
    """Toggle a channel on/off."""
    user_id = _get_user_id(callback)
    channel = callback.data.split(":", 1)[1]  # type: ignore

    new_state = await models.toggle_user_channel(user_id, channel)
    status = "включен" if new_state else "выключен"

    # Refresh the list
    channels = await models.get_user_channels(user_id)
    count = len(channels)
    text = (
        f"📡 <b>Твои каналы</b> ({count}/{MAX_CHANNELS_PER_USER})\n\n"
        "🟢 = включен, 🔴 = выключен\n"
        "Нажми на канал чтобы переключить."
    )
    try:
        await callback.message.edit_text(  # type: ignore
            text,
            parse_mode="HTML",
            reply_markup=channels_keyboard(channels),
        )
    except Exception:
        pass
    await callback.answer(f"@{channel} {status}")


@router.callback_query(F.data == "add_channel")
async def cb_add_channel(callback: CallbackQuery, state: FSMContext) -> None:
    """Start the add-channel flow."""
    user_id = _get_user_id(callback)
    count = await models.get_user_channel_count(user_id)

    if count >= MAX_CHANNELS_PER_USER:
        await callback.answer(f"❌ Максимум {MAX_CHANNELS_PER_USER} каналов!", show_alert=True)
        return

    await state.set_state(AddChannelState.waiting_for_name)
    await state.update_data(user_id=user_id)

    try:
        await callback.message.edit_text(  # type: ignore
            "📡 <b>Добавление канала</b>\n\n"
            "Отправь имя канала (без @).\n"
            "Например: <code>newcsgo</code>",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.message(AddChannelState.waiting_for_name)
async def handle_new_channel_name(message: Message, state: FSMContext) -> None:
    """Validate and add a new channel."""
    user_id = _get_user_id(message)
    raw_name = (message.text or "").strip().lstrip("@").lower()

    if not raw_name or len(raw_name) < 3:
        await message.answer("❌ Некорректное имя канала. Попробуй ещё раз.")
        return

    await state.clear()

    # Check limit
    count = await models.get_user_channel_count(user_id)
    if count >= MAX_CHANNELS_PER_USER:
        await message.answer(f"❌ У тебя уже {MAX_CHANNELS_PER_USER} каналов (максимум).")
        return

    # Validate channel exists via Telethon
    wait_msg = await message.answer(f"🔍 Проверяю канал @{raw_name}...")
    try:
        from services.telethon_parser import get_client, start_client
        client = get_client()
        if not client.is_connected():
            await start_client()

        entity = await client.get_entity(raw_name)
        # Check it's actually a channel/group
        from telethon.tl.types import Channel as TelethonChannel
        if not isinstance(entity, TelethonChannel):
            await wait_msg.edit_text(f"❌ @{raw_name} — это не канал.")
            return

    except Exception:
        await wait_msg.edit_text(f"❌ Канал @{raw_name} не найден или недоступен.")
        return

    # Add to user's channels
    added = await models.add_user_channel(user_id, raw_name)
    if added:
        await wait_msg.edit_text(f"✅ Канал <b>@{raw_name}</b> добавлен!", parse_mode="HTML")
    else:
        await wait_msg.edit_text(f"ℹ️ Канал @{raw_name} уже есть в твоём списке.")


@router.callback_query(F.data == "remove_channel_menu")
async def cb_remove_channel_menu(callback: CallbackQuery) -> None:
    """Show remove channel selection."""
    user_id = _get_user_id(callback)
    channels = await models.get_user_channels(user_id)

    if not channels:
        await callback.answer("У тебя нет каналов.", show_alert=True)
        return

    try:
        await callback.message.edit_text(  # type: ignore
            "🗑 <b>Удаление канала</b>\n\nВыбери канал для удаления:",
            parse_mode="HTML",
            reply_markup=remove_channel_keyboard(channels),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("remove_ch:"))
async def cb_remove_channel(callback: CallbackQuery) -> None:
    """Remove a channel from user's list."""
    user_id = _get_user_id(callback)
    channel = callback.data.split(":", 1)[1]  # type: ignore

    await models.remove_user_channel(user_id, channel)

    # Refresh with updated list
    channels = await models.get_user_channels(user_id)
    if channels:
        try:
            await callback.message.edit_text(  # type: ignore
                "🗑 <b>Удаление канала</b>\n\nВыбери канал для удаления:",
                parse_mode="HTML",
                reply_markup=remove_channel_keyboard(channels),
            )
        except Exception:
            pass
    else:
        try:
            await callback.message.edit_text(  # type: ignore
                "📡 Все каналы удалены. Добавь новые через ⚙️ Настройки.",
                parse_mode="HTML",
                reply_markup=settings_menu_keyboard(),
            )
        except Exception:
            pass

    await callback.answer(f"@{channel} удалён")


# ═══════════════════════════  🤖 MODEL SELECTION  ══════════════════════════

@router.callback_query(F.data == "settings_model")
async def cb_model_menu(callback: CallbackQuery) -> None:
    """Show AI model selection menu (from settings)."""
    active = await models.get_setting("active_model") or FIREWORKS_MODEL
    kb = model_selection_keyboard(active)
    try:
        await callback.message.edit_text(  # type: ignore
            f"🤖 <b>Настройка нейросети</b>\n\n"
            f"Текущая модель: <code>{active}</code>\n"
            f"Выбери нужную модель из списка ниже:",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        pass
    await callback.answer()


@router.message(Command("model"))
async def cmd_model(message: Message) -> None:
    """Show AI model selection menu (slash command)."""
    active = await models.get_setting("active_model") or FIREWORKS_MODEL
    kb = model_selection_keyboard(active)
    await message.answer(
        f"🤖 <b>Настройка нейросети</b>\n\n"
        f"Текущая модель: <code>{active}</code>\n"
        f"Выбери нужную модель из списка ниже:",
        parse_mode="HTML",
        reply_markup=kb
    )


@router.callback_query(F.data.startswith("set_model:"))
async def cb_set_model(callback: CallbackQuery) -> None:
    """Handle model selection from inline menu."""
    model_name = callback.data.split(":", 1)[1]  # type: ignore
    await models.set_setting("active_model", model_name)

    kb = model_selection_keyboard(model_name)
    try:
        await callback.message.edit_text(  # type: ignore
            f"✅ <b>Модель изменена!</b>\n\n"
            f"Текущая модель: <code>{model_name}</code>\n"
            f"Теперь все суммаризации будут идти через неё.",
            parse_mode="HTML",
            reply_markup=kb
        )
    except Exception:
        pass

    await callback.answer(f"Модель: {model_name}")


# ═══════════════════════════  PAGE NAVIGATION  ═════════════════════════════

@router.callback_query(F.data.startswith("page:"))
async def cb_page(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle pagination button clicks."""
    if not callback.message:
        return

    # Store user_id for pagination context
    user_id = _get_user_id(callback)
    await state.update_data(user_id=user_id)

    raw = callback.data.split(":", 1)[1]  # type: ignore[union-attr]
    try:
        page = int(raw)
    except ValueError:
        await callback.answer("❌ Ошибка пагинации")
        return

    await _send_news_page(callback.message, user_id=user_id, page=page, edit=True)
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    """No-op callback for disabled buttons."""
    await callback.answer()


# ═══════════════════════════  MARK ALL READ  ═══════════════════════════════

@router.callback_query(F.data == "mark_all_read")
async def cb_mark_all_read(callback: CallbackQuery) -> None:
    """Mark all news as read for this user."""
    if not callback.message:
        return

    user_id = _get_user_id(callback)
    count = await models.mark_all_read(user_id)
    await callback.message.answer(
        f"✅ Отмечено как прочитанное: <b>{count}</b> новостей.",
        parse_mode="HTML",
    )
    await callback.answer(f"Прочитано: {count}")


# ═══════════════════════════  🔄 UPDATE  ═══════════════════════════════════

@router.message(F.text == "🔄 Обновить")
async def btn_update(message: Message, state: FSMContext) -> None:
    """Fetch latest posts from all channels."""
    await state.clear()
    user_id = _get_user_id(message)
    wait_msg = await message.answer("⏳ Парсинг каналов… Подожди немного.")

    try:
        from services.telethon_parser import fetch_all_channels
        new_count = await fetch_all_channels()

        total_unread = await models.get_unread_count(user_id)
        await wait_msg.edit_text(
            f"✅ Обновление завершено!\n\n"
            f"📥 Новых новостей: <b>{new_count}</b>\n"
            f"📋 Всего непрочитанных: <b>{total_unread}</b>",
            parse_mode="HTML",
        )
        logger.info("Manual update: %d new posts.", new_count)

    except Exception:
        logger.exception("Error during manual update")
        await message.answer("❌ Ошибка при обновлении. Попробуй позже.")


async def _run_test_parse(message: Message, user_id: int) -> None:
    """
    Shared logic for /testparse and the settings menu button:
    pick a random user channel, fetch the last 10 posts, summarize each, build
    a digest from them, and stream the result back to the user.
    """
    wait_msg = await message.answer(
        "⏳ Тестовый парсинг… выбираю случайный канал и тяну последние 10 постов."
    )

    try:
        from services.telethon_parser import test_fetch_random_channel
        channel, items, digest = await test_fetch_random_channel(
            user_id=user_id, count=10
        )

        if not channel:
            await wait_msg.edit_text(
                "❌ Нет активных каналов. Добавь канал через ⚙️ Настройки."
            )
            return
        if not items:
            await wait_msg.edit_text(
                f"❌ Канал @{channel} выбран, но подходящих постов не найдено "
                f"(пусто, чёрный список или ошибка парсинга).",
                parse_mode="HTML",
            )
            return

        await wait_msg.edit_text(
            f"✅ Канал: <b>@{channel}</b>\n"
            f"Получено постов: <b>{len(items)}</b>\n\n"
            f"📝 <b>Заголовки от ИИ:</b>",
            parse_mode="HTML",
        )

        # 1) List of summaries
        lines = []
        for i, it in enumerate(items, 1):
            lines.append(f"{i}. {scrub_markdown(it['summary'])}")
        await message.answer("\n".join(lines))

        # 2) Digest
        if digest:
            await message.answer(
                f"🔥 <b>Дайджест по @{channel}:</b>\n\n{digest}",
                parse_mode="HTML",
            )
        else:
            await message.answer("⚠️ Дайджест не сгенерирован (Fireworks вернул пусто).")

    except Exception:
        logger.exception("Error during test parse")
        try:
            await wait_msg.edit_text("❌ Произошла ошибка во время тестового парсинга.")
        except Exception:
            pass


@router.callback_query(F.data == "settings_test_parse")
async def cb_test_parse(callback: CallbackQuery) -> None:
    """DEBUG: Parse last 10 posts from a random channel (from settings menu)."""
    await callback.answer()
    user_id = _get_user_id(callback)
    if callback.message:
        await _run_test_parse(callback.message, user_id)  # type: ignore[arg-type]


@router.message(Command("testparse"))
@router.message(Command("test_parse"))
async def cmd_test_parse(message: Message, state: FSMContext) -> None:
    """DEBUG: Parse last 10 posts from a random channel + build digest."""
    await state.clear()
    user_id = _get_user_id(message)
    await _run_test_parse(message, user_id)


# ═══════════════════════════  🔥 DIGEST  ═══════════════════════════════════

@router.message(F.text == "🔥 Дайджест")
async def btn_digest(message: Message) -> None:
    """Manually generate a digest of unread news for this user."""
    user_id = _get_user_id(message)
    wait_msg = await message.answer("🤖 Собираю дайджест самых горячих новостей... Подожди (около 20-30 сек).")

    # Get unread news from the last 48 hours for a rich digest
    posts = await models.get_recent_unread_for_digest(user_id, hours=48)

    if not posts:
        await wait_msg.edit_text("📭 У тебя нет новых непрочитанных новостей для дайджеста.")
        return

    try:
        from services.summarizer import generate_digest
        digest = await generate_digest(posts)

        await wait_msg.delete()
        await message.answer(digest, parse_mode="HTML")
    except Exception:
        logger.exception("Error generating manual digest")
        await wait_msg.edit_text("❌ Ошибка при создании дайджеста.")


# ═══════════════════════════  Настройки Дайджеста  ═════════════════════════

@router.callback_query(F.data == "settings_digest")
async def cb_digest_menu(callback: CallbackQuery) -> None:
    """Show digest settings menu (from settings)."""
    user_id = _get_user_id(callback)
    enabled_str = await models.get_user_setting(user_id, "digest_enabled")
    interval = await models.get_user_setting(user_id, "digest_interval") or "24h"
    enabled = enabled_str != "0"  # Default is True

    kb = digest_settings_keyboard(enabled, interval)
    try:
        await callback.message.edit_text(  # type: ignore
            f"⚙️ <b>Настройки авто-дайджеста</b>\n\n"
            f"Здесь можно включить или отключить автоматическую отправку "
            f"сводки новостей, а также выбрать интервал.",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        pass
    await callback.answer()


@router.message(Command("digest_settings"))
async def cmd_digest_settings(message: Message) -> None:
    """Show auto-digest settings menu (slash command)."""
    user_id = _get_user_id(message)
    enabled_str = await models.get_user_setting(user_id, "digest_enabled")
    interval = await models.get_user_setting(user_id, "digest_interval") or "24h"
    enabled = enabled_str != "0"  # Default is True

    kb = digest_settings_keyboard(enabled, interval)
    await message.answer(
        f"⚙️ <b>Настройки авто-дайджеста</b>\n\n"
        f"Здесь можно включить или отключить автоматическую отправку "
        f"сводки новостей, а также выбрать интервал.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "toggle_digest")
async def cb_toggle_digest(callback: CallbackQuery) -> None:
    """Toggle auto-digest on/off for this user."""
    user_id = _get_user_id(callback)
    enabled_str = await models.get_user_setting(user_id, "digest_enabled")
    interval = await models.get_user_setting(user_id, "digest_interval") or "24h"
    enabled = enabled_str != "0"

    new_enabled = not enabled
    await models.set_user_setting(user_id, "digest_enabled", "1" if new_enabled else "0")

    kb = digest_settings_keyboard(new_enabled, interval)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)  # type: ignore
    except Exception:
        pass
    await callback.answer(f"Авто-дайджест {'ВКЛ' if new_enabled else 'ВЫКЛ'}")


@router.callback_query(F.data.startswith("set_digest_interval:"))
async def cb_set_digest_interval(callback: CallbackQuery) -> None:
    """Set interval for auto-digest for this user."""
    user_id = _get_user_id(callback)
    interval_val = callback.data.split(":", 1)[1]  # type: ignore
    await models.set_user_setting(user_id, "digest_interval", interval_val)

    enabled_str = await models.get_user_setting(user_id, "digest_enabled")
    enabled = enabled_str != "0"

    kb = digest_settings_keyboard(enabled, interval_val)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)  # type: ignore
    except Exception:
        pass
    await callback.answer(f"Интервал изменён")


# ═══════════════════════════  📚 ARCHIVE  ══════════════════════════════════

@router.message(F.text == "📚 Архив")
async def btn_archive(message: Message) -> None:
    """Show the last 5 read news items for this user."""
    user_id = _get_user_id(message)
    recent = await models.get_recently_read(user_id, limit=5)

    if not recent:
        await message.answer("📚 Твой архив пока пуст.")
        return

    lines = ["📚 <b>Последние 5 прочитанных новостей:</b>\n"]
    for i, item in enumerate(recent, 1):
        summary = item.get("summary", "—")
        channel = item.get("channel", "?")
        lines.append(f"{i}. <b>{summary}</b>\n   <i>@{channel}</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ═══════════════════════════  📖 DETAIL VIEW  ══════════════════════════════

@router.message(F.text == "📖 Детальный разбор")
async def btn_detail(message: Message, state: FSMContext) -> None:
    """Ask for a news number to show full text."""
    user_id = _get_user_id(message)
    total = await models.get_unread_count(user_id)
    if total == 0:
        await message.answer(
            "📭 Нет непрочитанных новостей.\n"
            "Нажми 🔄 <b>Обновить</b> для парсинга.",
            parse_mode="HTML",
        )
        return

    await state.set_state(DetailState.waiting_for_number)
    await state.update_data(user_id=user_id)
    await message.answer(
        "🔢 Отправь <b>ID новости</b> из списка (например, <code>#42</code> или просто <code>42</code>).",
        parse_mode="HTML",
    )


@router.message(F.text.regexp(r"^\d+$"))
async def handle_plain_number(message: Message, state: FSMContext) -> None:
    """Catch plain numbers at any time and show post details."""
    num_str = message.text.strip()  # type: ignore
    await handle_detail_number_logic(message, state, num_str)


@router.message(DetailState.waiting_for_number)
async def handle_detail_number_fsm(message: Message, state: FSMContext) -> None:
    """Process number when in explicit waiting state."""
    await handle_detail_number_logic(message, state, message.text or "")


async def handle_detail_number_logic(message: Message, state: FSMContext, num_str: str) -> None:
    """Internal logic to show detail using absolute Database ID."""
    user_id = _get_user_id(message)
    await state.clear()

    # Strip # from the beginning if user typed #ID
    num_str = num_str.lstrip("#").strip()

    try:
        num = int(num_str)
    except ValueError:
        return  # Not a number, ignore

    # Look up news directly by its Database ID
    news = await models.get_news_by_id(num)
    if not news:
        await message.answer(f"❌ Новость с ID <b>#{num}</b> не найдена.", parse_mode="HTML")
        return

    news_id = news["id"]

    # Mark as read for THIS user
    await models.mark_as_read(user_id, news_id)

    # Format full text with aggressive scrubbing
    channel = news.get("channel", "?")
    summary = scrub_markdown(news.get("summary", "—"))
    full_text = scrub_markdown(news.get("text", "—"))
    created_raw = news.get("created_at", "")
    created = _format_time_msk(created_raw)
    msg_id = news.get("message_id", 0)

    # Compose response
    response = (
        f"<b>{summary}</b>\n\n"
    )

    footer = (
        f"\n\n"
        f"📝 <b>@{channel}</b> · {created}\n"
        f"🔗 <a href='https://t.me/{channel}/{msg_id}'>Оригинал в Telegram</a>\n"
        f"✅ <i>Отмечено как прочитанное</i>"
    )

    # Send text
    text_content = response + smart_truncate(full_text, 3800) + footer
    await message.answer(
        text_content,
        parse_mode="HTML",
        reply_markup=news_detail_keyboard(news_id),
    )

    logger.info("User %d viewed news #%d", user_id, news_id)
