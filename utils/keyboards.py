"""
keyboards.py - Постоянные клавиатуры для бота
"""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_main_keyboard(language: str = "RUS") -> ReplyKeyboardMarkup:
    """
    Основная клавиатура с кнопками:
    - Уведомления / Отключить / Помощь
    - Инвентарь / Обмен
    """
    builder = ReplyKeyboardBuilder()

    if language == "RUS":
        main_buttons = [
            KeyboardButton(text="🔔 Уведомления"),
            KeyboardButton(text="🔕 Отключить"),
            KeyboardButton(text="❓ Помощь"),
        ]
        bottom_buttons = [
            KeyboardButton(text="🎒 Инвентарь"),
            KeyboardButton(text="🔄 Обмен"),
        ]
    else:
        main_buttons = [
            KeyboardButton(text="🔔 Notifications"),
            KeyboardButton(text="🔕 Disable"),
            KeyboardButton(text="❓ Help"),
        ]
        bottom_buttons = [
            KeyboardButton(text="🎒 Inventory"),
            KeyboardButton(text="🔄 Trade"),
        ]

    builder.row(*main_buttons, width=3)
    builder.row(*bottom_buttons, width=2)

    return builder.as_markup(resize_keyboard=True, one_time_keyboard=False)

def remove_keyboard() -> ReplyKeyboardRemove:
    """Удаление клавиатуры"""
    return ReplyKeyboardRemove()
