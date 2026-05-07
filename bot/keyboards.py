"""
Keyboard builders — reply and inline keyboards for the NewsHub bot.
v4.0: Added settings menu, channel management keyboards.
"""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


# ═══════════════════════════  REPLY KEYBOARDS  ═════════════════════════════

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard with main action buttons."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📋 Список новостей"),
                KeyboardButton(text="🔄 Обновить"),
            ],
            [
                KeyboardButton(text="📖 Детальный разбор"),
                KeyboardButton(text="🔥 Дайджест"),
            ],
            [
                KeyboardButton(text="📚 Архив"),
                KeyboardButton(text="⚙️ Настройки"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ═══════════════════════════  SETTINGS MENU  ═══════════════════════════════

def settings_menu_keyboard() -> InlineKeyboardMarkup:
    """Inline menu for the ⚙️ Настройки button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📡 Каналы", callback_data="settings_channels"),
                InlineKeyboardButton(text="🤖 Модель ИИ", callback_data="settings_model"),
            ],
            [
                InlineKeyboardButton(text="⏰ Авто-дайджест", callback_data="settings_digest"),
                InlineKeyboardButton(text="🔧 Тест парсинг", callback_data="settings_test_parse"),
            ],
        ]
    )


# ═══════════════════════════  CHANNEL MANAGEMENT  ══════════════════════════

def channels_keyboard(channels: list[dict]) -> InlineKeyboardMarkup:
    """
    Inline keyboard showing user's channels with toggle buttons.
    channels: list of {"channel": str, "enabled": bool}
    """
    rows: list[list[InlineKeyboardButton]] = []

    # 2 channels per row
    row: list[InlineKeyboardButton] = []
    for ch_info in channels:
        ch = ch_info["channel"]
        enabled = ch_info["enabled"]
        icon = "🟢" if enabled else "🔴"
        row.append(
            InlineKeyboardButton(
                text=f"{icon} @{ch}",
                callback_data=f"toggle_ch:{ch}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Action buttons
    rows.append([
        InlineKeyboardButton(text="➕ Добавить канал", callback_data="add_channel"),
        InlineKeyboardButton(text="🗑 Удалить канал", callback_data="remove_channel_menu"),
    ])
    rows.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="settings_back"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def remove_channel_keyboard(channels: list[dict]) -> InlineKeyboardMarkup:
    """Keyboard for selecting a channel to remove."""
    rows: list[list[InlineKeyboardButton]] = []
    for ch_info in channels:
        ch = ch_info["channel"]
        rows.append([
            InlineKeyboardButton(text=f"❌ @{ch}", callback_data=f"remove_ch:{ch}")
        ])
    rows.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="settings_channels"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ═══════════════════════════  INLINE KEYBOARDS  ════════════════════════════

def pagination_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """
    Inline keyboard for navigating news pages.
    Shows: ◀️ | Page X/Y | ▶️
    """
    buttons: list[InlineKeyboardButton] = []

    # Previous button
    if page > 1:
        buttons.append(
            InlineKeyboardButton(text="◀️", callback_data=f"page:{page - 1}")
        )
    else:
        buttons.append(
            InlineKeyboardButton(text="·", callback_data="noop")
        )

    # Page counter
    buttons.append(
        InlineKeyboardButton(
            text=f"📄 {page}/{total_pages}",
            callback_data="noop",
        )
    )

    # Next button
    if page < total_pages:
        buttons.append(
            InlineKeyboardButton(text="▶️", callback_data=f"page:{page + 1}")
        )
    else:
        buttons.append(
            InlineKeyboardButton(text="·", callback_data="noop")
        )

    # Second row: mark all as read
    mark_all_btn = InlineKeyboardButton(
        text="✅ Прочитать всё",
        callback_data="mark_all_read",
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons,
            [mark_all_btn],
        ]
    )


def news_detail_keyboard(news_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard shown after viewing a news detail."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Назад к списку",
                    callback_data="page:1",
                ),
            ],
        ]
    )


def model_selection_keyboard(active_model: str) -> InlineKeyboardMarkup:
    """Inline menu with all supported AI models, marking the active one."""
    from config import SUPPORTED_MODELS

    rows: list[list[InlineKeyboardButton]] = []

    # 2 models per row
    row: list[InlineKeyboardButton] = []
    for model in SUPPORTED_MODELS:
        label = f"✅ {model}" if model == active_model else model
        row.append(InlineKeyboardButton(text=label, callback_data=f"set_model:{model}"))

        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    # Back button
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="settings_back")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def digest_settings_keyboard(enabled: bool, interval: str) -> InlineKeyboardMarkup:
    """Inline menu for configuring auto-digest."""
    rows = []

    # Toggle button
    toggle_text = "🟢 Авто-дайджест: ВКЛ" if enabled else "🔴 Авто-дайджест: ВЫКЛ"
    rows.append([InlineKeyboardButton(text=toggle_text, callback_data="toggle_digest")])

    # Intervals (only show if enabled)
    if enabled:
        intervals = [
            ("Каждые 3ч", "3h"),
            ("Каждые 6ч", "6h"),
            ("2 раза в день", "12h"),
            ("1 раз в день (09:00)", "24h"),
        ]

        # Split into rows of 2
        for i in range(0, len(intervals), 2):
            row = []
            for label, val in intervals[i:i+2]:
                btn_text = f"✅ {label}" if interval == val else label
                row.append(InlineKeyboardButton(text=btn_text, callback_data=f"set_digest_interval:{val}"))
            rows.append(row)

    # Back button
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="settings_back")])

    return InlineKeyboardMarkup(inline_keyboard=rows)
