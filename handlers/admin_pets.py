"""
handlers/admin_pets.py — Команды !петы и !пет для администраторов.

!петы          — список всех петов в системе, отсортированных по доходу (убывание).
!пет <число>   — поиск пета по значению дохода (точный или ближайший).

Доступ только для администраторов.
"""

import logging
from typing import Optional

from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from database import Database
from handlers.admin_common import is_admin

logger = logging.getLogger(__name__)
router = Router()
db = Database()

PETS_PER_PAGE = 10


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def _parse_income(pet: dict) -> int:
    """Парсит pet_income в целое число."""
    raw = (pet.get('pet_income') or '').replace(' ', '').replace(',', '')
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return 0


def _format_income(pet: dict) -> str:
    """Форматирует доход с пробелами-разделителями тысяч: 1 201 044."""
    val = _parse_income(pet)
    if val == 0:
        return pet.get('pet_income') or '?'
    return f"{val:,}".replace(',', ' ')


def _pet_line(num: int, pet: dict, highlight: bool = False) -> str:
    """Одна строка пета в списке.
    name уже содержит полное имя вида 'Дракон - $1 222 /сек ❄️ Снежная (х1.00)',
    поэтому доход отдельно не добавляем."""
    name = pet.get('name') or '?'
    user_id = pet.get('user_id', 0)
    roblox = db.get_roblox_nick(user_id) if user_id else None

    user_link = f'<a href="tg://user?id={user_id}">{user_id}</a>'
    roblox_part = roblox if roblox else "нет"

    prefix = "➡ " if highlight else ""
    return f"{prefix}{num}. {name} — {user_link} — {roblox_part}"


def _build_pets_keyboard(page: int, total: int, search_value: Optional[int] = None) -> InlineKeyboardMarkup:
    """Клавиатура пагинации + кнопка поиска."""
    total_pages = max(1, (total + PETS_PER_PAGE - 1) // PETS_PER_PAGE)
    rows = []

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admpets_page_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="admpets_noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admpets_page_{page + 1}"))
    if len(nav) > 1 or total_pages > 1:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔍 Найти пета", callback_data="admpets_search_hint")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pets_page_text(
    all_pets: list,
    page: int,
    header: str,
    highlight_idx: Optional[int] = None,
) -> str:
    """Формирует текст страницы списка петов.
    highlight_idx — 1-based глобальный индекс пета для выделения стрелкой."""
    total = len(all_pets)
    total_pages = max(1, (total + PETS_PER_PAGE - 1) // PETS_PER_PAGE)
    start = page * PETS_PER_PAGE
    page_items = all_pets[start: start + PETS_PER_PAGE]

    lines = [
        header,
        f"Страница: {page + 1}/{total_pages}",
        "",
    ]
    for i, pet in enumerate(page_items):
        global_num = start + i + 1
        highlight = (highlight_idx is not None and global_num == highlight_idx)
        lines.append(_pet_line(global_num, pet, highlight=highlight))

    return "\n".join(lines)


# ─── Команда !петы ────────────────────────────────────────────────────────────

@router.message(F.text == "!петы", F.chat.type == "private")
async def cmd_all_pets(message: Message):
    """!петы — список всех петов, отсортированных по доходу."""
    if not is_admin(message.from_user.id):
        return

    all_pets = db.get_all_pets_sorted()
    if not all_pets:
        await message.answer("🐾 Петов в системе пока нет.")
        return

    text = _pets_page_text(all_pets, page=0, header="🐾 <b>Все петы в системе</b>")
    keyboard = _build_pets_keyboard(page=0, total=len(all_pets))
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data.startswith("admpets_page_"))
async def pets_page_turn(callback: CallbackQuery):
    """Листание страниц списка петов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    page = int(callback.data.split("_")[2])
    all_pets = db.get_all_pets_sorted()
    total = len(all_pets)
    total_pages = max(1, (total + PETS_PER_PAGE - 1) // PETS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    # При перелистывании — без выделения (стрелка убирается)
    text = _pets_page_text(all_pets, page=page, header="🐾 <b>Все петы в системе</b>")
    keyboard = _build_pets_keyboard(page=page, total=total)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "admpets_search_hint")
async def pets_search_hint(callback: CallbackQuery):
    """Подсказка как использовать поиск."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await callback.answer(
        "Используйте команду:\n!пет <число>\nПример: !пет 141685",
        show_alert=True
    )


@router.callback_query(F.data == "admpets_noop")
async def pets_noop(callback: CallbackQuery):
    await callback.answer()


# ─── Команда !пет <число> ─────────────────────────────────────────────────────

@router.message(F.text.startswith("!пет "), F.chat.type == "private")
async def cmd_find_pet(message: Message):
    """!пет <число> — найти пета по значению дохода."""
    if not is_admin(message.from_user.id):
        return

    # Извлекаем число — убираем пробелы внутри числа
    raw = message.text.strip()[5:].strip()
    income_str = raw.replace(' ', '').replace(',', '')
    try:
        income_value = int(income_str)
    except ValueError:
        await message.answer("❌ Укажите число. Пример: <code>!пет 141685</code>", parse_mode="HTML")
        return

    result = db.search_pet_by_income(income_value)
    all_pets = db.get_all_pets_sorted()

    if not all_pets:
        await message.answer("🐾 Петов в системе пока нет.")
        return

    if result['exact']:
        pet = result['exact']
        idx = result['exact_index']
        page = (idx - 1) // PETS_PER_PAGE
        header = f"🐾 <b>Найден точный пет ✅</b>\nСтраница: {page + 1}"
        text = _pets_page_text(all_pets, page=page, header=header, highlight_idx=idx)
        keyboard = _build_pets_keyboard(page=page, total=len(all_pets))
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    elif result['nearest']:
        pet = result['nearest']
        idx = result['nearest_index']
        page = (idx - 1) // PETS_PER_PAGE
        nearest_income = _format_income(pet)
        header = (
            f"🔎 <b>Ближайший по значению пет</b>\n"
            f"Запрос: ${income_value:,}".replace(',', ' ') +
            f" | Найдено: ${nearest_income} /сек\n"
            f"Страница: {page + 1}"
        )
        text = _pets_page_text(all_pets, page=page, header=header, highlight_idx=idx)
        keyboard = _build_pets_keyboard(page=page, total=len(all_pets))
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer("🐾 Петов в системе пока нет.")


# ─── Вход из кнопки админ-панели ──────────────────────────────────────────────

@router.callback_query(F.data == "admin_pets_list")
async def admin_pets_list_callback(callback: CallbackQuery):
    """Открыть список петов из кнопки админ-панели."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    all_pets = db.get_all_pets_sorted()
    if not all_pets:
        await callback.answer("🐾 Петов в системе пока нет.", show_alert=True)
        return

    text = _pets_page_text(all_pets, page=0, header="🐾 <b>Все петы в системе</b>")
    keyboard = _build_pets_keyboard(page=0, total=len(all_pets))
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()
