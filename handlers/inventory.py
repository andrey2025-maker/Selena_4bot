"""
handlers/inventory.py - Система инвентаря
Пользователи просматривают свои предметы и запрашивают выдачу.
Администраторы получают уведомления, подтверждают выдачу и управляют инвентарём.
"""

import asyncio
import json
import logging
import re
from typing import List

from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import Database
from utils.messages import locale_manager
from config import Config
from utils.keyboards import get_main_keyboard
from utils.log_events import (
    log_inventory_add, log_inventory_remove, log_inventory_transfer,
    log_inventory_pickup_request, log_inventory_pickup_done,
)

logger = logging.getLogger(__name__)
router = Router()
db = Database()

from handlers.admin_common import ADMIN_IDS, is_admin


# ========== СПРАВОЧНИКИ ДЛЯ ДОБАВЛЕНИЯ ==========

# Еда: (ключ, RU-название, EN-название, эмодзи)
FOOD_LIST = [
    ("pear",        "Груша",               "Pear",               "🍐"),
    ("pineapple",   "Ананас",              "Pineapple",          "🍍"),
    ("mango",       "Манго",               "Gold Mango",         "🥭"),
    ("dragon",      "Драконий фрукт",      "Dragon Fruit",       "🐲"),
    ("bloodstone",  "Bloodstone Cycad",    "Bloodstone Cycad",   "🩸"),
    ("pinecone",    "Colossal Pinecone",   "Colossal Pinecone",  "❇️"),
    ("kiwi",        "Франкен Киви",        "Franken Kiwi",       "🥝"),
    ("pumpkin",     "Тыква",               "Pumpkin",            "🎃"),
    ("durian",      "Дуриан",              "Durian",             "❄️"),
    ("candy_corn",  "Конфета",             "Candy Corn",         "🍬"),
    ("pearl",       "Ракушка",             "Deepsea Pearl Fruit","🐚"),
    ("volt",        "Volt Ginkgo",         "Volt Ginkgo",        "⚡️🦕"),
    ("cranberry",   "Клюква",              "Cranberry",          "🍇"),
    ("acorn",       "Желудь",              "Acorn",              "🌰"),
    ("gingerbread", "Пряничный человечек", "Gingerbread",        "🍪"),
    ("candycane",   "Конфетная трость",    "Candycane",          "🎄🍭"),
    ("cherry",      "Вишня",               "Cherry",             "🍒"),
]
FOOD_BY_KEY = {f[0]: f for f in FOOD_LIST}

# Мутации пета
PET_MUTATIONS = [
    ("normal",    "⚪️", "Обычная",       "Normal"),
    ("golden",    "🟡", "Золотая",        "Golden"),
    ("diamond",   "💎", "Алмазная",       "Diamond"),
    ("electric",  "⚡️","Электрическая",  "Electric"),
    ("fiery",     "🔥", "Огненная",       "Fiery"),
    ("jurassic",  "🦖", "Юрская",         "Jurassic"),
    ("snowy",     "❄️", "Снежная",        "Snowy"),
    ("halloween", "🎃", "Хэллуин",        "Halloween"),
    ("thanks",    "🦃", "Благодарения",   "Thanksgiving"),
    ("xmas",      "🎄", "Рождество",      "Christmas"),
    ("valentine", "🌸🩷","День Валентина","Valentine's Day"),
]
PET_MUT_BY_KEY = {m[0]: m for m in PET_MUTATIONS}

# Погоды пета
PET_WEATHERS = [
    ("storm",   "💨", "Буря",   "Storm"),
    ("aurora",  "🌀", "Аврора", "Aurora"),
    ("volcano", "🌋", "Вулкан", "Volcano"),
    ("admin",   "🪯", "Админ",  "Admin"),
]
PET_WEATHER_BY_KEY = {w[0]: w for w in PET_WEATHERS}


def _food_display(key: str, lang: str) -> str:
    f = FOOD_BY_KEY.get(key)
    if not f:
        return key
    name = f[2] if lang == "EN" else f[1]
    return f"{f[3]} {name}"


def _pet_mut_display(key: str, lang: str) -> str:
    m = PET_MUT_BY_KEY.get(key)
    if not m:
        return key
    name = m[3] if lang == "EN" else m[2]
    return f"{m[1]} {name}"


def _pet_weather_display(key: str, lang: str) -> str:
    w = PET_WEATHER_BY_KEY.get(key)
    if not w:
        return key
    name = w[3] if lang == "EN" else w[2]
    return f"{w[1]} {name}"


def _pet_full_name(name: str, income: str, mutation_key: str, weather_key: str,
                   coeff: str, lang: str) -> str:
    """Формирует строку пета: Дракон - $35,993 /сек 🦖 Юрская/🪯Админ (х2.04)"""
    sec = "s" if lang == "EN" else "сек"
    mut = _pet_mut_display(mutation_key, lang) if mutation_key else ""
    weather = _pet_weather_display(weather_key, lang) if weather_key else ""
    weather_part = f"/{weather}" if weather else ""
    coeff_part = f" (х{coeff})" if coeff else ""
    return f"{name} - ${income} /{sec} {mut}{weather_part}{coeff_part}"


# ========== FSM ==========

class InventoryStates(StatesGroup):
    selecting_pickup = State()


class UserAddItemStates(StatesGroup):
    waiting_for_pet_photo = State()   # ожидание фото питомца от пользователя


class AdminInventoryStates(StatesGroup):
    adding_item_user = State()        # ввод user_id для просмотра инвентаря
    adding_item_data = State()        # ожидание сообщения с предметом (legacy / item)
    selecting_delete = State()        # выбор предметов для удаления
    delete_qty_input = State()        # ввод количества для удаляемых предметов
    setting_example_photo = State()   # установка примера фото питомца
    # Еда
    food_selecting = State()          # выбор еды из списка (мультиселект)
    food_qty_input = State()          # ввод количества для каждой выбранной еды
    # Пет
    pet_name = State()                # ввод имени пета
    pet_income = State()              # ввод дохода
    pet_mutation = State()            # выбор мутации
    pet_weather = State()             # выбор погоды
    pet_coeff = State()               # ввод коэффициента
    pet_photo = State()               # опциональное фото пета
    # Передача предметов
    transfer_selecting = State()      # выбор предметов для передачи
    transfer_qty_input = State()      # ввод количества для каждого выбранного предмета
    transfer_recipient = State()      # ввод получателя (тэг или ID)


class InventoryPickupStates(StatesGroup):
    pickup_qty_input = State()        # ввод количества для забираемых предметов


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def _user_display(user: dict) -> str:
    """HTML-ссылка на пользователя: @username или ID: uid."""
    if not user:
        return "?"
    uid = user["user_id"]
    label = f"@{user['username']}" if user.get("username") else f"ID: {uid}"
    return f'<a href="tg://user?id={uid}">{label}</a>'


ITEMS_PER_PAGE = 10


def _build_inventory_view_keyboard(lang: str, page: int = 0, total: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура просмотра инвентаря — Забрать, Обмен, Добавить питомца, пагинация"""
    if lang == "RUS":
        pickup_text = "📤 Забрать"
        trade_text  = "🔄 Обмен предметами"
        add_text    = "➕ Добавить питомца"
    else:
        pickup_text = "📤 Pick up"
        trade_text  = "🔄 Trade items"
        add_text    = "➕ Add pet"

    rows = []

    # Пагинация
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"inv_page_{page - 1}"))
        nav.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="inv_page_noop"
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"inv_page_{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=pickup_text, callback_data="inv_pickup")])
    rows.append([InlineKeyboardButton(text=trade_text,  callback_data="inv_item_trade")])
    rows.append([InlineKeyboardButton(text=add_text,    callback_data="inv_add_pet_request")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_pickup_keyboard(items: list, selected_ids: List[int], lang: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора предметов для выдачи"""
    keyboard = []
    for item in items:
        item_id = item["id"]
        checked = item_id in selected_ids
        mark = "✅" if checked else "☑️"
        qty = f" x{item['quantity']}" if item.get("quantity", 1) > 1 else ""
        keyboard.append([
            InlineKeyboardButton(
                text=f"{mark} {item['name']}{qty}",
                callback_data=f"inv_toggle_{item_id}"
            )
        ])

    if lang == "RUS":
        select_all_text = "✔️ Выбрать все"
        pickup_text = "📤 Забрать"
        back_text = "🔙 Назад"
    else:
        select_all_text = "✔️ Select all"
        pickup_text = "📤 Pick up"
        back_text = "🔙 Back"

    keyboard.append([
        InlineKeyboardButton(text=select_all_text, callback_data="inv_select_all"),
        InlineKeyboardButton(text=back_text, callback_data="inv_back"),
    ])
    keyboard.append([
        InlineKeyboardButton(text=pickup_text, callback_data="inv_confirm_pickup")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _build_admin_delete_keyboard(items: list, selected_ids: List[int], target_user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора предметов для удаления (для админа)"""
    keyboard = []
    for item in items:
        item_id = item["id"]
        checked = item_id in selected_ids
        mark = "✅" if checked else "☑️"
        qty = f" x{item['quantity']}" if item.get("quantity", 1) > 1 else ""
        keyboard.append([
            InlineKeyboardButton(
                text=f"{mark} {item['name']}{qty}",
                callback_data=f"inv_adm_toggle_{item_id}_{target_user_id}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton(text="✔️ Выбрать все", callback_data=f"inv_adm_selall_{target_user_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"inv_adm_view_{target_user_id}"),
    ])
    keyboard.append([
        InlineKeyboardButton(text="🗑 Удалить выбранные", callback_data=f"inv_adm_delete_{target_user_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _item_line(item: dict, num: int, lang: str) -> str:
    """Форматирует одну строку предмета инвентаря."""
    qty = f" x{item['quantity']}" if item.get("quantity", 1) > 1 else ""
    line = f"{num}. <b>{item['name']}</b>{qty}"
    if item.get("description"):
        line += f"\n   {item['description']}"
    return line


def _inventory_text(items: list, lang: str, title: str = None,
                    page: int = 0, all_items: list = None) -> str:
    """Текст инвентаря со страницей и секциями Предметы / Еда / Петы."""
    if lang == "RUS":
        empty = "🎒 Инвентарь пуст."
        header = title or "🎒 <b>Ваш инвентарь:</b>"
        sec_item = "📦 <b>Предметы</b>"
        sec_food = "🍎 <b>Еда</b>"
        sec_pet  = "🐾 <b>Петы</b>"
    else:
        empty = "🎒 Inventory is empty."
        header = title or "🎒 <b>Your inventory:</b>"
        sec_item = "📦 <b>Items</b>"
        sec_food = "🍎 <b>Food</b>"
        sec_pet  = "🐾 <b>Pets</b>"

    source = all_items if all_items is not None else items
    if not source:
        return f"{header}\n\n{empty}"

    # Пагинация
    start = page * ITEMS_PER_PAGE
    page_items = source[start: start + ITEMS_PER_PAGE]

    # Группируем по типу (сохраняем глобальную нумерацию)
    groups: dict[str, list[tuple[int, dict]]] = {"item": [], "food": [], "pet": []}
    for i, item in enumerate(page_items, start + 1):
        itype = item.get("item_type", "item")
        groups.setdefault(itype, []).append((i, item))

    lines = [header, ""]
    for itype, sec_header in [("item", sec_item), ("food", sec_food), ("pet", sec_pet)]:
        group = groups.get(itype, [])
        if not group:
            continue
        lines.append(sec_header)
        for num, item in group:
            lines.append(_item_line(item, num, lang))
        lines.append("")

    # Убираем лишний пустой конец
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


def _has_media(item: dict) -> bool:
    """Проверить наличие медиафайла у предмета.
    Если media_type не заполнен (старые записи) — считаем photo по умолчанию."""
    fid = item.get("media_file_id")
    if not fid:
        return False
    mtype = item.get("media_type")
    # NULL/пустой media_type — считаем photo (обратная совместимость)
    return (not mtype) or (mtype in ("photo", "video", "document"))


def _first_media_item(items: list) -> dict | None:
    """Вернуть первый предмет с медиафайлом (фото, видео или документ)."""
    for item in items:
        if _has_media(item):
            return item
    return None


def _all_media_items(items: list) -> list:
    """Вернуть все предметы с медиафайлами со страницы."""
    return [i for i in items if _has_media(i)]


async def _send_inventory_page(
    target,
    user_id: int,
    lang: str,
    page: int = 0,
    *,
    bot: Bot = None,
    edit: bool = False,
    title: str = None,
    show_actions: bool = True,
    album_msg_ids: list = None,
):
    """
    Отправить/обновить страницу инвентаря.

    - Если на странице несколько медиа-предметов — отправляется альбом + текст отдельно.
    - Если одно медиа — текст+кнопки в caption.
    - При edit=True: удаляем старые сообщения (включая альбом) и отправляем новые.
    - show_actions=False — без кнопок Забрать/Добавить (для группы).
    - album_msg_ids — список message_id альбома для удаления при перелистывании.
    """
    items = db.get_user_inventory(user_id)
    total = len(items)
    text = _inventory_text(items, lang, title=title, page=page, all_items=items)

    if show_actions:
        keyboard = _build_inventory_view_keyboard(lang, page=page, total=total)
    else:
        keyboard = _build_group_inventory_keyboard(user_id, lang, page=page, total=total)

    # Медиа берём только из предметов ТЕКУЩЕЙ страницы
    start = page * ITEMS_PER_PAGE
    page_items = items[start: start + ITEMS_PER_PAGE]
    media_items = _all_media_items(page_items)

    msg_target = target if isinstance(target, Message) else target.message

    if edit:
        msg = msg_target
        has_media = bool(msg.photo or msg.video or msg.document)

        if has_media:
            # Одиночное медиа-сообщение — удаляем и пересоздаём
            try:
                await msg.delete()
            except Exception:
                pass
            return await _send_inventory_page(
                target, user_id, lang, page,
                bot=bot, edit=False, title=title, show_actions=show_actions
            )

        # Текстовое сообщение (в т.ч. текст после альбома) — редактируем текст
        # Альбом уже удалён в вызывающем хендлере (inventory_page_turn)
        if media_items:
            # На новой странице есть медиа — удаляем текст и отправляем заново с медиа
            try:
                await msg.delete()
            except Exception:
                pass
            return await _send_inventory_page(
                target, user_id, lang, page,
                bot=bot, edit=False, title=title, show_actions=show_actions
            )

        # Нет медиа на новой странице — просто редактируем текст
        try:
            await msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await _send_inventory_page(
                target, user_id, lang, page,
                bot=bot, edit=False, title=title, show_actions=show_actions
            )
        return

    # Новое сообщение
    if len(media_items) == 1:
        # Одно медиа — отправляем с текстом и кнопками в caption
        try:
            mi = media_items[0]
            mtype = mi.get("media_type", "photo")
            fid = mi["media_file_id"]
            if mtype == "video":
                await msg_target.answer_video(fid, caption=text, reply_markup=keyboard, parse_mode="HTML")
            elif mtype == "document":
                await msg_target.answer_document(fid, caption=text, reply_markup=keyboard, parse_mode="HTML")
            else:
                await msg_target.answer_photo(fid, caption=text, reply_markup=keyboard, parse_mode="HTML")
            return
        except Exception as e:
            logger.warning(f"Failed to send inventory media: {e}")

    elif len(media_items) > 1:
        # Несколько медиа — отправляем альбом, затем текст с кнопками отдельным сообщением
        sent_album_ids = []
        try:
            media_group = []
            for mi in media_items:
                mtype = mi.get("media_type", "photo")
                fid = mi["media_file_id"]
                name = mi.get("name", "")
                if mtype == "video":
                    media_group.append(InputMediaVideo(media=fid, caption=name, parse_mode="HTML"))
                else:
                    media_group.append(InputMediaPhoto(media=fid, caption=name, parse_mode="HTML"))
            sent = await msg_target.answer_media_group(media_group)
            sent_album_ids = [m.message_id for m in sent] if sent else []
        except Exception as e:
            logger.warning(f"Failed to send inventory media group: {e}")
        # Текст с кнопками — отдельным сообщением после альбома
        await msg_target.answer(text, reply_markup=keyboard, parse_mode="HTML")
        # Возвращаем IDs альбома чтобы вызывающий код мог сохранить их в FSM
        return sent_album_ids

    await msg_target.answer(text, reply_markup=keyboard, parse_mode="HTML")


def _build_group_inventory_keyboard(owner_id: int, lang: str, page: int = 0, total: int = 0) -> InlineKeyboardMarkup | None:
    """Клавиатура пагинации для инвентаря в группе — только кнопки листания."""
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    if total_pages <= 1:
        return None
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ginv_page_{owner_id}_{page - 1}"))
    nav.append(InlineKeyboardButton(
        text=f"{page + 1}/{total_pages}",
        callback_data="inv_page_noop"
    ))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ginv_page_{owner_id}_{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[nav])


def _inventory_title(user_id: int, lang: str) -> str:
    """Заголовок инвентаря: 📦 Инвентарь <ссылка на пользователя> (Roblox: ник)"""
    user = db.get_user(user_id)
    roblox_nick = db.get_roblox_nick(user_id)

    label = f"@{user['username']}" if user and user.get("username") else f"ID: {user_id}"
    link = f'<a href="tg://user?id={user_id}">{label}</a>'
    roblox_part = f" (Roblox: {roblox_nick})" if roblox_nick else ""

    if lang == "RUS":
        return f"📦 Инвентарь {link}{roblox_part}"
    else:
        return f"📦 Inventory {link}{roblox_part}"


async def _send_item_media(bot: Bot, chat_id: int, item: dict, caption: str = None):
    """Отправить медиафайл(ы) предмета.
    media_file_id может быть одиночным file_id или JSON-списком вида
    [{"file_id": "...", "media_type": "photo"}, ...]
    """
    file_id = item.get("media_file_id")
    media_type = item.get("media_type")
    cap = caption or f"<b>{item['name']}</b>"
    if not file_id:
        return

    # Пробуем разобрать как JSON-список (несколько медиафайлов)
    media_list = None
    if isinstance(file_id, str) and file_id.startswith("["):
        try:
            media_list = json.loads(file_id)
        except Exception:
            pass

    try:
        if media_list and len(media_list) > 1:
            # Отправляем как медиагруппу (альбом)
            album = []
            for i, m in enumerate(media_list):
                fid = m.get("file_id", "")
                mtype = m.get("media_type", "photo")
                item_cap = cap if i == 0 else None
                if mtype == "photo":
                    album.append(InputMediaPhoto(media=fid, caption=item_cap, parse_mode="HTML"))
                elif mtype == "video":
                    album.append(InputMediaVideo(media=fid, caption=item_cap, parse_mode="HTML"))
                elif mtype == "document":
                    album.append(InputMediaDocument(media=fid, caption=item_cap, parse_mode="HTML"))
            if album:
                await bot.send_media_group(chat_id, album)
        elif media_list and len(media_list) == 1:
            # Один файл из списка
            m = media_list[0]
            fid = m.get("file_id", "")
            mtype = m.get("media_type", "photo")
            if mtype == "photo":
                await bot.send_photo(chat_id, fid, caption=cap, parse_mode="HTML")
            elif mtype == "video":
                await bot.send_video(chat_id, fid, caption=cap, parse_mode="HTML")
            elif mtype == "document":
                await bot.send_document(chat_id, fid, caption=cap, parse_mode="HTML")
        else:
            # Одиночный файл (старый формат)
            if media_type == "photo":
                await bot.send_photo(chat_id, file_id, caption=cap, parse_mode="HTML")
            elif media_type == "video":
                await bot.send_video(chat_id, file_id, caption=cap, parse_mode="HTML")
            elif media_type == "document":
                await bot.send_document(chat_id, file_id, caption=cap, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error sending media for item {item.get('id', '?')}: {e}")


# ========== ХЕНДЛЕРЫ ПОЛЬЗОВАТЕЛЯ ==========

@router.message(F.chat.type == "private", F.text.in_(["🎒 Инвентарь", "🎒 Inventory"]))
async def show_inventory(message: Message, state: FSMContext):
    """Показать инвентарь пользователя"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    title = _inventory_title(user_id, lang)
    result = await _send_inventory_page(message, user_id, lang, page=0, bot=message.bot, title=title)
    # Сохраняем IDs альбома в FSM если была отправлена медиагруппа
    if isinstance(result, list) and result:
        await state.update_data(inv_album_msg_ids=result)


@router.callback_query(F.data.startswith("inv_page_"))
async def inventory_page_turn(callback: CallbackQuery, state: FSMContext):
    """Листание страниц инвентаря в ЛС"""
    if callback.data == "inv_page_noop":
        await callback.answer()
        return

    page = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"
    title = _inventory_title(user_id, lang)

    # Удаляем сообщения альбома из предыдущей страницы (если были)
    fsm_data = await state.get_data()
    prev_album_ids = fsm_data.get("inv_album_msg_ids", [])
    if prev_album_ids:
        for mid in prev_album_ids:
            try:
                await callback.bot.delete_message(callback.message.chat.id, mid)
            except Exception:
                pass
        await state.update_data(inv_album_msg_ids=[])

    result = await _send_inventory_page(callback, user_id, lang, page=page, edit=True, title=title)
    # Сохраняем IDs нового альбома если была отправлена медиагруппа
    if isinstance(result, list) and result:
        await state.update_data(inv_album_msg_ids=result)
    await callback.answer()


@router.callback_query(F.data == "inv_pickup")
async def start_pickup_selection(callback: CallbackQuery, state: FSMContext):
    """Переключиться в режим выбора предметов для выдачи"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    items = db.get_user_inventory(user_id)
    if not items:
        lc = "ru" if lang == "RUS" else "en"
        await callback.answer(locale_manager.get_text(lc, "inventory.empty_alert"), show_alert=True)
        return

    await state.set_state(InventoryStates.selecting_pickup)
    await state.update_data(selected_ids=[], inventory_msg_id=callback.message.message_id)

    lc = "ru" if lang == "RUS" else "en"
    text = locale_manager.get_text(lc, "inventory.select_for_pickup")
    keyboard = _build_pickup_keyboard(items, [], lang)

    # Если сообщение содержит медиа (фото/видео) — нельзя edit_text, удаляем и отправляем новое
    if callback.message.photo or callback.message.video or callback.message.document:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("inv_toggle_"), InventoryStates.selecting_pickup)
async def toggle_pickup_item(callback: CallbackQuery, state: FSMContext):
    """Переключить выбор предмета"""
    item_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])

    if item_id in selected_ids:
        selected_ids.remove(item_id)
    else:
        selected_ids.append(item_id)

    await state.update_data(selected_ids=selected_ids)

    items = db.get_user_inventory(user_id)
    keyboard = _build_pickup_keyboard(items, selected_ids, lang)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "inv_select_all", InventoryStates.selecting_pickup)
async def select_all_pickup(callback: CallbackQuery, state: FSMContext):
    """Выбрать / снять все предметы"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    items = db.get_user_inventory(user_id)
    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])
    all_ids = [item["id"] for item in items]

    if set(selected_ids) == set(all_ids):
        selected_ids = []
    else:
        selected_ids = all_ids

    await state.update_data(selected_ids=selected_ids)
    keyboard = _build_pickup_keyboard(items, selected_ids, lang)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "inv_back")
async def back_to_inventory_view(callback: CallbackQuery, state: FSMContext):
    """Вернуться к просмотру инвентаря"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"
    title = _inventory_title(user_id, lang)

    await state.clear()
    # edit=False — отправляем новое сообщение, чтобы корректно показать медиа если оно есть
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _send_inventory_page(callback, user_id, lang, page=0, edit=False, title=title)
    await callback.answer()


@router.callback_query(F.data == "inv_confirm_pickup", InventoryStates.selecting_pickup)
async def confirm_pickup(callback: CallbackQuery, state: FSMContext):
    """Подтвердить выбор — если есть предметы с qty>1, спросить количество"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])

    if not selected_ids:
        lc = "ru" if lang == "RUS" else "en"
        msg = locale_manager.get_text(lc, "inventory.select_at_least_one")
        await callback.answer(msg, show_alert=True)
        return

    selected_items = [db.get_inventory_item(iid) for iid in selected_ids]
    selected_items = [i for i in selected_items if i]

    if not selected_items:
        await callback.answer(locale_manager.get_text(lc, "inventory.items_not_found"), show_alert=True)
        await state.clear()
        return

    # Проверяем предметы с qty > 1
    items_with_qty = [i for i in selected_items if i.get("quantity", 1) > 1]

    if items_with_qty:
        await state.set_state(InventoryPickupStates.pickup_qty_input)
        await state.update_data(
            pickup_selected_ids=selected_ids,
            pickup_qty_queue=[i["id"] for i in items_with_qty],
            pickup_qty_map={},
            pickup_lang=lang,
        )
        await _ask_pickup_qty(callback.message, state, lang, edit=True)
        await callback.answer()
        return

    await _finalize_pickup(callback, state, user, user_id, lang, selected_ids, {})


async def _ask_pickup_qty(target, state: FSMContext, lang: str, edit: bool = False):
    """Спросить количество для следующего предмета в очереди забора."""
    data = await state.get_data()
    queue: List[int] = data.get("pickup_qty_queue", [])
    if not queue:
        return  # будет обработано в хендлере

    item_id = queue[0]
    item = db.get_inventory_item(item_id)
    if not item:
        qty_map = data.get("pickup_qty_map", {})
        qty_map[str(item_id)] = 1
        await state.update_data(pickup_qty_queue=queue[1:], pickup_qty_map=qty_map)
        await _ask_pickup_qty(target, state, lang, edit=edit)
        return

    max_qty = item.get("quantity", 1)
    name = item.get("name", "?")
    lc = "ru" if lang == "RUS" else "en"
    text = locale_manager.get_text(lc, "inventory.how_many_to_take").format(name=name, max_qty=max_qty)
    msg = target if isinstance(target, Message) else target.message
    if edit:
        try:
            await msg.edit_text(text, parse_mode="HTML", reply_markup=None)
            return
        except Exception:
            pass
    await msg.answer(text, parse_mode="HTML")


@router.message(InventoryPickupStates.pickup_qty_input)
async def pickup_qty_receive(message: Message, state: FSMContext):
    """Получить количество для забираемого предмета."""
    data = await state.get_data()
    lang = data.get("pickup_lang", "RUS")

    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        lc = "ru" if lang == "RUS" else "en"
        await message.answer(locale_manager.get_text(lc, "common.cancelled"))
        return

    queue: List[int] = data.get("pickup_qty_queue", [])
    qty_map: dict = data.get("pickup_qty_map", {})
    selected_ids: List[int] = data.get("pickup_selected_ids", [])

    if not queue:
        user = db.get_user(message.from_user.id)
        await _finalize_pickup(message, state, user, message.from_user.id, lang, selected_ids, qty_map)
        return

    item_id = queue[0]
    item = db.get_inventory_item(item_id)
    max_qty = item.get("quantity", 1) if item else 1

    text = message.text.strip().lower() if message.text else ""
    if text in ("все", "all"):
        qty = max_qty
    else:
        try:
            qty = int(text)
            if qty < 1 or qty > max_qty:
                err = f"❌ Введите число от 1 до {max_qty} или «все»" if lang == "RUS" else f"❌ Enter 1–{max_qty} or 'all'"
                await message.answer(err)
                return
        except ValueError:
            err = f"❌ Введите число от 1 до {max_qty} или «все»" if lang == "RUS" else f"❌ Enter 1–{max_qty} or 'all'"
            await message.answer(err)
            return

    qty_map[str(item_id)] = qty
    new_queue = queue[1:]
    await state.update_data(pickup_qty_queue=new_queue, pickup_qty_map=qty_map)

    if new_queue:
        await _ask_pickup_qty(message, state, lang)
    else:
        user = db.get_user(message.from_user.id)
        await _finalize_pickup(message, state, user, message.from_user.id, lang, selected_ids, qty_map)


async def _finalize_pickup(target, state: FSMContext, user, user_id: int,
                            lang: str, selected_ids: List[int], qty_map: dict):
    """Создать запрос на выдачу и уведомить всех админов."""
    selected_items = [db.get_inventory_item(iid) for iid in selected_ids]
    selected_items = [i for i in selected_items if i]

    # Создаём запрос с количествами в item_ids как список пар [id, qty]
    import json
    items_with_qty_list = []
    for item in selected_items:
        iid = item["id"]
        max_qty = item.get("quantity", 1)
        qty = int(qty_map.get(str(iid), max_qty if max_qty > 1 else 1))
        items_with_qty_list.append({"id": iid, "qty": qty})

    request_id = db.create_pickup_request(user_id, [i["id"] for i in items_with_qty_list])
    if not request_id:
        msg = target if isinstance(target, Message) else target.message
        lc = "ru" if lang == "RUS" else "en"
        await msg.answer(locale_manager.get_text(lc, "inventory.request_creation_error"))
        await state.clear()
        return

    # Сохраняем qty_map в запросе через admin_msg_ids временно — нет, используем отдельное поле
    # Сохраняем qty_map в БД как часть admin_msg_ids (ключ "qty_map")
    db.save_request_admin_msg_ids(request_id, {"qty_map": qty_map})

    await state.clear()

    msg = target if isinstance(target, Message) else target.message
    lc = "ru" if lang == "RUS" else "en"
    user_msg = locale_manager.get_text(lc, "inventory.request_sent")

    try:
        await msg.edit_text(user_msg, parse_mode="HTML")
    except Exception:
        await msg.answer(user_msg, parse_mode="HTML")

    if hasattr(target, "answer") and not isinstance(target, Message):
        try:
            await target.answer()
        except Exception:
            pass

    # Лог: запрос на забор
    _items_log = ", ".join(
        f"{i.get('name', '?')} x{qty_map.get(str(i['id']), i.get('quantity', 1))}"
        for i in selected_items
    )
    _bot = target.bot if isinstance(target, Message) else target.bot
    await log_inventory_pickup_request(
        _bot,
        user_id=user_id,
        user_name=(user or {}).get("roblox_nick") or (user or {}).get("username") or str(user_id),
        item_name=_items_log,
    )

    # Уведомляем всех администраторов
    user_display = _user_display(user)
    items_text = "\n".join(
        f"• <b>{item['name']}</b> x{qty_map.get(str(item['id']), item.get('quantity', 1))}"
        + (f"\n  {item['description']}" if item.get("description") else "")
        for item in selected_items
    )
    admin_header = (
        f"📦 <b>Запрос на выдачу</b>\n\n"
        f"👤 Пользователь: {user_display}\n"
        f"🎒 Предметы:\n{items_text}"
    )

    take_btn_text = "🙋 Я выполню"
    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=take_btn_text, callback_data=f"inv_adm_take_{request_id}"),
    ]])

    bot = msg.bot
    admin_msg_ids: dict = {}
    for admin_id in ADMIN_IDS:
        try:
            sent = await bot.send_message(
                admin_id,
                admin_header,
                parse_mode="HTML",
                reply_markup=admin_keyboard
            )
            admin_msg_ids[str(admin_id)] = sent.message_id
            for item in selected_items:
                if item.get("media_file_id"):
                    await _send_item_media(
                        bot, admin_id, item,
                        caption=f"<b>{item['name']}</b>" + (f"\n{item['description']}" if item.get("description") else "")
                    )
        except Exception as e:
            logger.error(f"Error notifying admin {admin_id} about pickup request {request_id}: {e}")

    # Сохраняем message_id уведомлений
    admin_msg_ids["qty_map"] = qty_map
    db.save_request_admin_msg_ids(request_id, admin_msg_ids)


# ========== ХЕНДЛЕРЫ АДМИНИСТРАТОРА — ЗАПРОСЫ НА ВЫДАЧУ ==========

@router.callback_query(F.data.startswith("inv_adm_chat_"))
async def admin_chat_from_pickup(callback: CallbackQuery, state: FSMContext):
    """Начать чат с пользователем из уведомления о выдаче"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    target_user_id = int(callback.data.split("_")[3])
    user = db.get_user(target_user_id)

    from handlers.admin_common import active_chats, ChatStates

    active_chats[target_user_id] = callback.from_user.id
    db.set_active_chat(target_user_id, callback.from_user.id)  # сохраняем в БД

    user_lang = user.get("language", "RUS") if user else "RUS"
    if user_lang == "RUS":
        notification = "👤 <b>С Вами связался администратор</b>\n\nДля завершения диалога напишите /stop"
    else:
        notification = "👤 <b>An administrator has contacted you</b>\n\nType /stop to end the conversation"

    try:
        await callback.bot.send_message(target_user_id, notification, parse_mode="HTML")
    except Exception as e:
        await callback.answer(f"❌ Не удалось отправить уведомление: {e}", show_alert=True)
        del active_chats[target_user_id]
        db.remove_active_chat(target_user_id)
        return

    await state.set_state(ChatStates.chatting)
    await state.update_data(chat_with_user=target_user_id)

    user_display = _user_display(user)
    await callback.message.answer(
        f"✅ Чат начат с пользователем {user_display}\n"
        f"Для завершения отправьте /stop"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("inv_adm_take_"))
async def admin_take_pickup_request(callback: CallbackQuery):
    """Администратор нажал «Я выполню» — берёт запрос в работу"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    request_id = int(callback.data.split("_")[3])
    request = db.get_pickup_request(request_id)

    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    if request["status"] == "in_progress":
        await callback.answer("ℹ️ Запрос уже взят другим администратором", show_alert=True)
        return

    if request["status"] == "done":
        await callback.answer("ℹ️ Запрос уже выполнен", show_alert=True)
        return

    # Помечаем запрос как взятый в работу и сразу выполняем (выдача предметов)
    success = db.complete_pickup_request(request_id, callback.from_user.id)
    if not success:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    user_id = request["user_id"]
    user = db.get_user(user_id)
    user_display = _user_display(user)
    executor = callback.from_user
    admin_msg_ids: dict = request.get("admin_msg_ids", {})
    qty_map: dict = admin_msg_ids.pop("qty_map", {}) if isinstance(admin_msg_ids, dict) else {}

    # Удаляем предметы с учётом количеств
    item_ids: List[int] = request.get("item_ids", [])
    for iid in item_ids:
        item = db.get_inventory_item(iid)
        if not item:
            continue
        max_qty = item.get("quantity", 1)
        qty = int(qty_map.get(str(iid), max_qty))
        db.reduce_inventory_item_qty(iid, qty)

    # Уведомляем пользователя
    user_lang = user.get("language", "RUS") if user else "RUS"
    user_lc = "ru" if user_lang == "RUS" else "en"
    user_msg = locale_manager.get_text(user_lc, "inventory.items_given")
    try:
        await callback.bot.send_message(user_id, user_msg)
    except Exception as e:
        logger.error(f"Error notifying user {user_id}: {e}")

    # У того кто взял — кнопки «Связаться» и «Инвентарь»
    executor_text = (
        f"✅ <b>Вы взяли запрос в работу</b>\n\n"
        f"👤 Пользователь: {user_display}"
    )
    executor_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Связаться", callback_data=f"inv_adm_chat_{user_id}"),
        InlineKeyboardButton(text="🎒 Инвентарь", callback_data=f"inv_adm_view_{user_id}"),
    ]])
    try:
        await callback.message.edit_text(executor_text, parse_mode="HTML", reply_markup=executor_keyboard)
    except Exception:
        pass
    await callback.answer("✅ Запрос взят в работу!")

    # Лог: забор предметов выполнен
    items_names = []
    for iid in item_ids:
        _it = db.get_inventory_item(iid)
        if _it:
            items_names.append(_it.get("name", str(iid)))
    _items_str = ", ".join(items_names) if items_names else "—"
    await log_inventory_pickup_done(
        callback.bot,
        admin_id=executor.id,
        admin_name=executor.full_name,
        user_id=user_id,
        user_name=(user or {}).get("roblox_nick") or (user or {}).get("username") or str(user_id),
        item_name=_items_str,
    )

    # У остальных админов — обновляем сообщение: кто выполнил
    other_text = (
        f"✅ <b>Запрос выполнен</b>\n\n"
        f"👤 Пользователь: {user_display}\n"
        f"👑 Выполнил: <a href='tg://user?id={executor.id}'>{executor.full_name}</a>"
    )
    for admin_id in ADMIN_IDS:
        if admin_id == executor.id:
            continue
        msg_id = admin_msg_ids.get(str(admin_id))
        if not msg_id:
            continue
        try:
            await callback.bot.edit_message_text(
                chat_id=admin_id,
                message_id=msg_id,
                text=other_text,
                parse_mode="HTML",
                reply_markup=None
            )
        except Exception as e:
            logger.warning(f"Could not update admin {admin_id} message: {e}")


@router.callback_query(F.data.startswith("inv_adm_pet_take_"))
async def admin_take_pet_request(callback: CallbackQuery):
    """Администратор берёт запрос на добавление питомца в работу."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    # inv_adm_pet_take_{request_id}_{user_id}
    request_id = int(parts[4])
    user_id = int(parts[5])

    request = db.get_pickup_request(request_id)
    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return
    if request["status"] == "in_progress":
        await callback.answer("ℹ️ Запрос уже взят другим администратором", show_alert=True)
        return
    if request["status"] == "done":
        await callback.answer("ℹ️ Запрос уже выполнен", show_alert=True)
        return

    # Берём запрос в работу (статус pending → in_progress), но НЕ завершаем
    success = db.take_pickup_request(request_id, callback.from_user.id)
    if not success:
        await callback.answer("ℹ️ Запрос уже взят другим администратором", show_alert=True)
        return

    user = db.get_user(user_id)
    user_display = _user_display(user)
    executor = callback.from_user
    admin_msg_ids: dict = request.get("admin_msg_ids", {})

    # У того кто взял — кнопки «Связаться» и «Инвентарь»
    executor_text = (
        f"🙋 <b>Вы взяли запрос в работу</b>\n\n"
        f"🐾 Запрос на добавление питомца\n"
        f"👤 Пользователь: {user_display}\n\n"
        f"Свяжитесь с пользователем и добавьте питомца вручную через 🎒 Инвентарь."
    )
    executor_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Связаться", callback_data=f"inv_adm_chat_{user_id}"),
        InlineKeyboardButton(text="🎒 Инвентарь", callback_data=f"inv_adm_view_{user_id}"),
    ]])
    try:
        await callback.message.edit_caption(
            caption=executor_text, parse_mode="HTML", reply_markup=executor_keyboard
        )
    except Exception:
        try:
            await callback.message.edit_text(
                executor_text, parse_mode="HTML", reply_markup=executor_keyboard
            )
        except Exception:
            pass
    await callback.answer("✅ Запрос взят в работу!")

    # У остальных — обновляем сообщение: убираем кнопку, показываем кто взял
    other_text = (
        f"🔄 <b>Запрос взят в работу</b>\n\n"
        f"🐾 Запрос на добавление питомца\n"
        f"👤 Пользователь: {user_display}\n"
        f"🙋 Выполняет: <a href='tg://user?id={executor.id}'>{executor.full_name}</a>"
    )
    for admin_id in ADMIN_IDS:
        if admin_id == executor.id:
            continue
        msg_id = admin_msg_ids.get(str(admin_id))
        if not msg_id:
            continue
        try:
            await callback.bot.edit_message_caption(
                chat_id=admin_id,
                message_id=msg_id,
                caption=other_text,
                parse_mode="HTML",
                reply_markup=None
            )
        except Exception:
            try:
                await callback.bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=msg_id,
                    text=other_text,
                    parse_mode="HTML",
                    reply_markup=None
                )
            except Exception as e:
                logger.warning(f"Could not update admin {admin_id} pet request message: {e}")


@router.callback_query(F.data.startswith("inv_adm_done_"))
async def admin_confirm_pickup_done(callback: CallbackQuery):
    """Устаревший хендлер — перенаправляем на новый"""
    await callback.answer("ℹ️ Используйте кнопку «Я выполню»", show_alert=True)


# ========== ХЕНДЛЕРЫ АДМИНИСТРАТОРА — УПРАВЛЕНИЕ ИНВЕНТАРЁМ ==========

@router.callback_query(F.data == "admin_inventory_menu")
async def admin_inventory_menu(callback: CallbackQuery, state: FSMContext):
    """Меню управления инвентарём из админ-панели"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    await state.set_state(AdminInventoryStates.adding_item_user)
    await callback.message.answer(
        "🎒 <b>Управление инвентарём</b>\n\n"
        "Отправьте ID или @username пользователя, чей инвентарь хотите просмотреть/изменить.\n\n"
        "Для отмены отправьте /cancel",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminInventoryStates.adding_item_user)
async def admin_select_inventory_user(message: Message, state: FSMContext):
    """Выбор пользователя для просмотра инвентаря"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено")
        return

    input_text = message.text.strip() if message.text else ""
    user = None
    all_users = db.get_all_users()

    if input_text.startswith("@"):
        username = input_text[1:].lower()
        for u in all_users:
            if u.get("username") and u["username"].lower() == username:
                user = u
                break
    elif input_text.isdigit():
        user = db.get_user(int(input_text))

    if not user:
        await message.answer("❌ Пользователь не найден. Попробуйте ещё раз или /cancel")
        return

    await state.clear()
    await _show_admin_user_inventory(message, user["user_id"])


async def _show_admin_user_inventory(target, user_id: int, edit: bool = False):
    """Показать инвентарь пользователя администратору"""
    user = db.get_user(user_id)
    items = db.get_user_inventory(user_id)
    user_display = _user_display(user)

    title = f"🎒 <b>Инвентарь {user_display}:</b>"
    text = _inventory_text(items, "RUS", title=title)

    keyboard_buttons = []
    if items:
        keyboard_buttons.append([
            InlineKeyboardButton(text="🗑 Удалить предметы", callback_data=f"inv_adm_del_mode_{user_id}"),
            InlineKeyboardButton(text="📤 Передать", callback_data=f"inv_adm_transfer_{user_id}"),
        ])
    keyboard_buttons.append([
        InlineKeyboardButton(text="➕ Добавить предмет", callback_data=f"inv_adm_add_{user_id}"),
        InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel")
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    if edit and hasattr(target, "message"):
        try:
            await target.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            return
        except Exception:
            pass

    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("inv_adm_view_"))
async def admin_view_user_inventory(callback: CallbackQuery, state: FSMContext):
    """Вернуться к просмотру инвентаря пользователя"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await state.clear()
    user_id = int(callback.data.split("_")[3])
    await _show_admin_user_inventory(callback, user_id, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("inv_adm_del_mode_"))
async def admin_enter_delete_mode(callback: CallbackQuery, state: FSMContext):
    """Войти в режим удаления предметов"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    target_user_id = int(callback.data.split("_")[4])
    items = db.get_user_inventory(target_user_id)

    if not items:
        await callback.answer("Инвентарь пуст", show_alert=True)
        return

    await state.set_state(AdminInventoryStates.selecting_delete)
    await state.update_data(selected_ids=[], target_user_id=target_user_id)

    user = db.get_user(target_user_id)
    user_display = _user_display(user)

    keyboard = _build_admin_delete_keyboard(items, [], target_user_id)
    await callback.message.edit_text(
        f"🗑 <b>Удаление предметов из инвентаря {user_display}</b>\n\nВыберите предметы для удаления:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("inv_adm_toggle_"), AdminInventoryStates.selecting_delete)
async def admin_toggle_delete_item(callback: CallbackQuery, state: FSMContext):
    """Переключить выбор предмета для удаления"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    item_id = int(parts[3])
    target_user_id = int(parts[4])

    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])

    if item_id in selected_ids:
        selected_ids.remove(item_id)
    else:
        selected_ids.append(item_id)

    await state.update_data(selected_ids=selected_ids)

    items = db.get_user_inventory(target_user_id)
    keyboard = _build_admin_delete_keyboard(items, selected_ids, target_user_id)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("inv_adm_selall_"), AdminInventoryStates.selecting_delete)
async def admin_select_all_delete(callback: CallbackQuery, state: FSMContext):
    """Выбрать/снять все предметы для удаления"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    target_user_id = int(callback.data.split("_")[3])
    items = db.get_user_inventory(target_user_id)
    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])
    all_ids = [item["id"] for item in items]

    if set(selected_ids) == set(all_ids):
        selected_ids = []
    else:
        selected_ids = all_ids

    await state.update_data(selected_ids=selected_ids)
    keyboard = _build_admin_delete_keyboard(items, selected_ids, target_user_id)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("inv_adm_delete_"), AdminInventoryStates.selecting_delete)
async def admin_delete_selected_items(callback: CallbackQuery, state: FSMContext):
    """Начать удаление: если есть предметы с qty>1 — спросить количество"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    target_user_id = int(callback.data.split("_")[3])
    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])

    if not selected_ids:
        await callback.answer("Выберите хотя бы один предмет", show_alert=True)
        return

    # Проверяем, есть ли предметы с qty > 1
    items_with_qty = []
    for iid in selected_ids:
        item = db.get_inventory_item(iid)
        if item and item.get("quantity", 1) > 1:
            items_with_qty.append(item)

    if not items_with_qty:
        # Все предметы с qty=1 — удаляем сразу
        db.remove_inventory_items(selected_ids)
        await state.clear()
        await callback.answer(f"✅ Удалено {len(selected_ids)} предм.")
        await _show_admin_user_inventory(callback, target_user_id, edit=True)
        return

    # Есть предметы с qty>1 — спрашиваем количество по очереди
    await state.set_state(AdminInventoryStates.delete_qty_input)
    await state.update_data(
        target_user_id=target_user_id,
        delete_selected_ids=selected_ids,
        delete_qty_queue=[i["id"] for i in items_with_qty],
        delete_qty_map={},
    )
    await _ask_delete_qty(callback.message, state, edit=True)
    await callback.answer()


async def _ask_delete_qty(target, state: FSMContext, edit: bool = False):
    """Спросить количество для следующего предмета в очереди удаления."""
    data = await state.get_data()
    queue: List[int] = data.get("delete_qty_queue", [])
    if not queue:
        # Очередь закончилась — выполняем удаление
        await _execute_admin_delete(target, state)
        return

    item_id = queue[0]
    item = db.get_inventory_item(item_id)
    if not item:
        # Предмет не найден — пропускаем
        qty_map: dict = data.get("delete_qty_map", {})
        qty_map[str(item_id)] = 1
        await state.update_data(delete_qty_queue=queue[1:], delete_qty_map=qty_map)
        await _ask_delete_qty(target, state, edit=edit)
        return

    max_qty = item.get("quantity", 1)
    name = item.get("name", "?")
    text = (
        f"🗑 <b>Сколько удалить?</b>\n\n"
        f"Предмет: <b>{name}</b>\n"
        f"Доступно: <b>{max_qty}</b> шт.\n\n"
        f"Введите число от 1 до {max_qty} или <b>все</b> для полного удаления.\n"
        f"Для отмены — /cancel"
    )
    msg = target if isinstance(target, Message) else target.message
    if edit:
        try:
            await msg.edit_text(text, parse_mode="HTML", reply_markup=None)
            return
        except Exception:
            pass
    await msg.answer(text, parse_mode="HTML")


async def _execute_admin_delete(target, state: FSMContext):
    """Выполнить удаление с учётом указанных количеств."""
    data = await state.get_data()
    selected_ids: List[int] = data.get("delete_selected_ids", [])
    qty_map: dict = data.get("delete_qty_map", {})
    target_user_id: int = data.get("target_user_id", 0)
    await state.clear()

    deleted_count = 0
    deleted_items = []
    for iid in selected_ids:
        item = db.get_inventory_item(iid)
        if not item:
            continue
        max_qty = item.get("quantity", 1)
        if max_qty <= 1:
            db.remove_inventory_items([iid])
            deleted_count += 1
            deleted_items.append((item.get("name", str(iid)), 1))
        else:
            qty = int(qty_map.get(str(iid), max_qty))
            db.reduce_inventory_item_qty(iid, qty)
            deleted_count += 1
            deleted_items.append((item.get("name", str(iid)), qty))

    msg = target if isinstance(target, Message) else target.message
    await msg.answer(f"✅ Удалено {deleted_count} позиций.")

    # Лог: удаление предметов
    if deleted_items:
        _tuser = db.get_user(target_user_id)
        _adm = target.from_user if isinstance(target, Message) else target.from_user
        _bot = target.bot if isinstance(target, Message) else target.bot
        _items_str = ", ".join(f"{n} x{q}" for n, q in deleted_items)
        await log_inventory_remove(
            _bot,
            admin_id=_adm.id,
            admin_name=_adm.full_name,
            user_id=target_user_id,
            user_name=(_tuser or {}).get("roblox_nick") or (_tuser or {}).get("username") or str(target_user_id),
            item_name=_items_str,
            quantity=sum(q for _, q in deleted_items),
        )

    await _show_admin_user_inventory(target if isinstance(target, Message) else target, target_user_id)


@router.message(AdminInventoryStates.delete_qty_input)
async def admin_delete_qty_receive(message: Message, state: FSMContext):
    """Получить количество для удаляемого предмета."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено")
        return

    data = await state.get_data()
    queue: List[int] = data.get("delete_qty_queue", [])
    qty_map: dict = data.get("delete_qty_map", {})

    if not queue:
        await _execute_admin_delete(message, state)
        return

    item_id = queue[0]
    item = db.get_inventory_item(item_id)
    max_qty = item.get("quantity", 1) if item else 1

    text = message.text.strip().lower() if message.text else ""
    if text in ("все", "all"):
        qty = max_qty
    else:
        try:
            qty = int(text)
            if qty < 1 or qty > max_qty:
                await message.answer(f"❌ Введите число от 1 до {max_qty} или «все»")
                return
        except ValueError:
            await message.answer(f"❌ Введите число от 1 до {max_qty} или «все»")
            return

    qty_map[str(item_id)] = qty
    await state.update_data(delete_qty_queue=queue[1:], delete_qty_map=qty_map)
    await _ask_delete_qty(message, state)


# ========== ПЕРЕДАЧА ПРЕДМЕТОВ АДМИНИСТРАТОРОМ ==========

def _build_admin_transfer_keyboard(items: list, selected_ids: List[int], source_user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора предметов для передачи."""
    keyboard = []
    for item in items:
        item_id = item["id"]
        checked = item_id in selected_ids
        mark = "✅" if checked else "☑️"
        qty = f" x{item['quantity']}" if item.get("quantity", 1) > 1 else ""
        keyboard.append([
            InlineKeyboardButton(
                text=f"{mark} {item['name']}{qty}",
                callback_data=f"inv_adm_tr_tog_{item_id}_{source_user_id}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton(text="✔️ Выбрать все", callback_data=f"inv_adm_tr_all_{source_user_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"inv_adm_view_{source_user_id}"),
    ])
    keyboard.append([
        InlineKeyboardButton(text="➡️ Далее — выбрать получателя", callback_data=f"inv_adm_tr_next_{source_user_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.callback_query(F.data.startswith("inv_adm_transfer_"))
async def admin_enter_transfer_mode(callback: CallbackQuery, state: FSMContext):
    """Войти в режим передачи предметов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    source_user_id = int(callback.data.split("_")[3])
    items = db.get_user_inventory(source_user_id)

    if not items:
        await callback.answer("Инвентарь пуст", show_alert=True)
        return

    await state.set_state(AdminInventoryStates.transfer_selecting)
    await state.update_data(transfer_selected_ids=[], transfer_source_user_id=source_user_id)

    user = db.get_user(source_user_id)
    user_display = _user_display(user)

    keyboard = _build_admin_transfer_keyboard(items, [], source_user_id)
    await callback.message.edit_text(
        f"📤 <b>Передача предметов из инвентаря {user_display}</b>\n\n"
        f"Выберите предметы для передачи:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("inv_adm_tr_tog_"), AdminInventoryStates.transfer_selecting)
async def admin_transfer_toggle_item(callback: CallbackQuery, state: FSMContext):
    """Переключить выбор предмета для передачи."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    item_id = int(parts[4])
    source_user_id = int(parts[5])

    data = await state.get_data()
    selected_ids: List[int] = data.get("transfer_selected_ids", [])

    if item_id in selected_ids:
        selected_ids.remove(item_id)
    else:
        selected_ids.append(item_id)

    await state.update_data(transfer_selected_ids=selected_ids)
    items = db.get_user_inventory(source_user_id)
    keyboard = _build_admin_transfer_keyboard(items, selected_ids, source_user_id)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("inv_adm_tr_all_"), AdminInventoryStates.transfer_selecting)
async def admin_transfer_select_all(callback: CallbackQuery, state: FSMContext):
    """Выбрать/снять все предметы для передачи."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    source_user_id = int(callback.data.split("_")[4])
    items = db.get_user_inventory(source_user_id)
    data = await state.get_data()
    selected_ids: List[int] = data.get("transfer_selected_ids", [])
    all_ids = [item["id"] for item in items]

    selected_ids = [] if set(selected_ids) == set(all_ids) else all_ids
    await state.update_data(transfer_selected_ids=selected_ids)

    keyboard = _build_admin_transfer_keyboard(items, selected_ids, source_user_id)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("inv_adm_tr_next_"), AdminInventoryStates.transfer_selecting)
async def admin_transfer_next(callback: CallbackQuery, state: FSMContext):
    """Перейти к вводу количества (для еды) или сразу к выбору получателя."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    data = await state.get_data()
    selected_ids: List[int] = data.get("transfer_selected_ids", [])
    source_user_id: int = data.get("transfer_source_user_id", 0)

    if not selected_ids:
        await callback.answer("Выберите хотя бы один предмет", show_alert=True)
        return

    # Ищем предметы с qty > 1 (нужно спросить сколько передать)
    items_with_qty = []
    for iid in selected_ids:
        item = db.get_inventory_item(iid)
        if item and item.get("quantity", 1) > 1:
            items_with_qty.append(item)

    if items_with_qty:
        await state.set_state(AdminInventoryStates.transfer_qty_input)
        await state.update_data(
            transfer_qty_queue=[i["id"] for i in items_with_qty],
            transfer_qty_map={},
        )
        await _ask_transfer_qty(callback.message, state, edit=True)
    else:
        # Все предметы по 1 шт — сразу к получателю
        await state.set_state(AdminInventoryStates.transfer_recipient)
        await callback.message.edit_text(
            "📤 <b>Кому передать?</b>\n\n"
            "Введите @username или ID пользователя.\n"
            "Для отмены — /cancel",
            parse_mode="HTML",
            reply_markup=None
        )
    await callback.answer()


async def _ask_transfer_qty(target, state: FSMContext, edit: bool = False):
    """Спросить количество для следующего предмета в очереди передачи."""
    data = await state.get_data()
    queue: List[int] = data.get("transfer_qty_queue", [])

    if not queue:
        # Очередь закончилась — переходим к вводу получателя
        await state.set_state(AdminInventoryStates.transfer_recipient)
        msg = target if isinstance(target, Message) else target.message
        text = (
            "📤 <b>Кому передать?</b>\n\n"
            "Введите @username или ID пользователя.\n"
            "Для отмены — /cancel"
        )
        if edit:
            try:
                await msg.edit_text(text, parse_mode="HTML", reply_markup=None)
                return
            except Exception:
                pass
        await msg.answer(text, parse_mode="HTML")
        return

    item_id = queue[0]
    item = db.get_inventory_item(item_id)
    if not item:
        qty_map = data.get("transfer_qty_map", {})
        qty_map[str(item_id)] = 1
        await state.update_data(transfer_qty_queue=queue[1:], transfer_qty_map=qty_map)
        await _ask_transfer_qty(target, state, edit=edit)
        return

    max_qty = item.get("quantity", 1)
    name = item.get("name", "?")
    text = (
        f"📤 <b>Сколько передать?</b>\n\n"
        f"Предмет: <b>{name}</b>\n"
        f"Доступно: <b>{max_qty}</b> шт.\n\n"
        f"Введите число от 1 до {max_qty} или <b>все</b> для полной передачи.\n"
        f"Для отмены — /cancel"
    )
    msg = target if isinstance(target, Message) else target.message
    if edit:
        try:
            await msg.edit_text(text, parse_mode="HTML", reply_markup=None)
            return
        except Exception:
            pass
    await msg.answer(text, parse_mode="HTML")


@router.message(AdminInventoryStates.transfer_qty_input)
async def admin_transfer_qty_receive(message: Message, state: FSMContext):
    """Получить количество для передаваемого предмета."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text and message.text.strip().lower() in ("/cancel", "отмена"):
        data = await state.get_data()
        source_user_id = data.get("transfer_source_user_id", 0)
        await state.clear()
        await message.answer("🚫 Передача отменена.")
        if source_user_id:
            await _show_admin_user_inventory(message, source_user_id)
        return

    data = await state.get_data()
    queue: List[int] = data.get("transfer_qty_queue", [])
    qty_map: dict = data.get("transfer_qty_map", {})

    if not queue:
        await _ask_transfer_qty(message, state)
        return

    item_id = queue[0]
    item = db.get_inventory_item(item_id)
    max_qty = item.get("quantity", 1) if item else 1

    text = message.text.strip().lower() if message.text else ""
    if text in ("все", "all"):
        qty = max_qty
    else:
        try:
            qty = int(text)
            if qty < 1 or qty > max_qty:
                await message.answer(f"❌ Введите число от 1 до {max_qty} или «все»")
                return
        except ValueError:
            await message.answer(f"❌ Введите число от 1 до {max_qty} или «все»")
            return

    qty_map[str(item_id)] = qty
    await state.update_data(transfer_qty_queue=queue[1:], transfer_qty_map=qty_map)
    await _ask_transfer_qty(message, state)


@router.message(AdminInventoryStates.transfer_recipient)
async def admin_transfer_recipient_receive(message: Message, state: FSMContext):
    """Получить получателя и выполнить передачу."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text and message.text.strip().lower() in ("/cancel", "отмена"):
        data = await state.get_data()
        source_user_id = data.get("transfer_source_user_id", 0)
        await state.clear()
        await message.answer("🚫 Передача отменена.")
        if source_user_id:
            await _show_admin_user_inventory(message, source_user_id)
        return

    raw = message.text.strip() if message.text else ""
    # Пробуем найти пользователя по ID или @username
    recipient = None
    if raw.lstrip('-').isdigit():
        recipient = db.get_user(int(raw))
    elif raw.startswith('@'):
        username = raw[1:]
        all_users = db.get_all_users()
        recipient = next((u for u in all_users if (u.get("username") or "").lower() == username.lower()), None)
    else:
        all_users = db.get_all_users()
        recipient = next((u for u in all_users if (u.get("username") or "").lower() == raw.lower()), None)

    if not recipient:
        await message.answer(
            f"❌ Пользователь <code>{raw}</code> не найден.\n"
            "Введите @username или ID, или /cancel для отмены.",
            parse_mode="HTML"
        )
        return

    recipient_id = recipient["user_id"]
    data = await state.get_data()
    source_user_id: int = data.get("transfer_source_user_id", 0)
    selected_ids: List[int] = data.get("transfer_selected_ids", [])
    qty_map: dict = data.get("transfer_qty_map", {})

    if recipient_id == source_user_id:
        await message.answer("❌ Нельзя передать предметы самому себе.")
        return

    await state.clear()

    # Выполняем передачу
    transferred = []
    errors = []
    for iid in selected_ids:
        item = db.get_inventory_item(iid)
        if not item:
            continue
        max_qty = item.get("quantity", 1)
        qty = int(qty_map.get(str(iid), max_qty))
        qty = min(qty, max_qty)

        try:
            if qty >= max_qty:
                # Передаём всю запись
                if item.get("item_type") == "food" and not item.get("media_file_id"):
                    # Для еды без медиа — используем merge через add_inventory_item
                    db.add_inventory_item(
                        recipient_id,
                        item["name"],
                        description=item.get("description"),
                        media_file_id=item.get("media_file_id"),
                        media_type=item.get("media_type"),
                        quantity=qty,
                        added_by=message.from_user.id,
                        item_type=item["item_type"],
                        pet_income=item.get("pet_income"),
                        pet_mutation=item.get("pet_mutation"),
                        pet_weather=item.get("pet_weather"),
                        pet_coeff=item.get("pet_coeff"),
                    )
                    db.remove_inventory_items([iid])
                else:
                    conn = db.get_connection()
                    conn.execute(
                        "UPDATE inventory_items SET user_id=? WHERE id=?",
                        (recipient_id, iid)
                    )
                    conn.commit()
                    conn.close()
            else:
                # Частичная передача
                db.reduce_inventory_item_qty(iid, qty)
                db.add_inventory_item(
                    recipient_id,
                    item["name"],
                    description=item.get("description"),
                    media_file_id=item.get("media_file_id"),
                    media_type=item.get("media_type"),
                    quantity=qty,
                    added_by=message.from_user.id,
                    item_type=item["item_type"],
                    pet_income=item.get("pet_income"),
                    pet_mutation=item.get("pet_mutation"),
                    pet_weather=item.get("pet_weather"),
                    pet_coeff=item.get("pet_coeff"),
                )
            transferred.append((item.get("name", str(iid)), qty))
        except Exception as e:
            logger.error(f"Transfer item {iid}: {e}")
            errors.append(item.get("name", str(iid)))

    # Формируем отчёт
    source_user = db.get_user(source_user_id)
    recipient_display = _user_display(recipient)
    source_display = _user_display(source_user)

    if transferred:
        items_str = "\n".join(f"• {name} x{qty}" for name, qty in transferred)
        await message.answer(
            f"✅ <b>Передача выполнена</b>\n\n"
            f"От: {source_display}\n"
            f"Кому: {recipient_display}\n\n"
            f"Предметы:\n{items_str}",
            parse_mode="HTML"
        )
        # Уведомляем получателя
        try:
            lang_r = (recipient or {}).get("language", "RUS")
            notif = (
                f"📦 Вам переданы предметы от администратора:\n{items_str}"
                if lang_r == "RUS" else
                f"📦 Items transferred to you by admin:\n{items_str}"
            )
            await message.bot.send_message(recipient_id, notif, parse_mode="HTML")
        except Exception:
            pass
        # Лог передачи
        try:
            _adm = message.from_user
            _src = db.get_user(source_user_id)
            _dst = db.get_user(recipient_id)
            await log_inventory_transfer(
                message.bot,
                admin_id=_adm.id,
                admin_name=_adm.full_name,
                from_user_id=source_user_id,
                from_user_name=(_src or {}).get("roblox_nick") or (_src or {}).get("username") or str(source_user_id),
                to_user_id=recipient_id,
                to_user_name=(_dst or {}).get("roblox_nick") or (_dst or {}).get("username") or str(recipient_id),
                item_name=", ".join(f"{n} x{q}" for n, q in transferred),
                quantity=sum(q for _, q in transferred),
            )
        except Exception as e:
            logger.warning(f"Transfer log error: {e}")
    else:
        await message.answer("❌ Не удалось передать ни один предмет.")

    if errors:
        await message.answer(f"⚠️ Ошибка при передаче: {', '.join(errors)}")

    await _show_admin_user_inventory(message, source_user_id)


# ========== ДОБАВЛЕНИЕ ПРЕДМЕТОВ АДМИНИСТРАТОРОМ ==========

def _admin_add_type_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🍎 Еда / Food",    callback_data=f"inv_adm_type_food_{user_id}"),
            InlineKeyboardButton(text="🐾 Пет / Pet",     callback_data=f"inv_adm_type_pet_{user_id}"),
        ],
        [
            InlineKeyboardButton(text="📦 Предмет / Item", callback_data=f"inv_adm_type_item_{user_id}"),
        ],
    ])


def _food_select_keyboard(selected: list[str], lang: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for key, ru, en, emoji in FOOD_LIST:
        name = en if lang == "EN" else ru
        mark = "✅" if key in selected else "☑️"
        row.append(InlineKeyboardButton(text=f"{mark}{emoji}{name}", callback_data=f"inv_food_tog_{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    done_text = "✅ Done" if lang == "EN" else "✅ Готово"
    rows.append([InlineKeyboardButton(text=done_text, callback_data="inv_food_done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pet_mutation_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for key, emoji, ru, en in PET_MUTATIONS:
        name = en if lang == "EN" else ru
        row.append(InlineKeyboardButton(text=f"{emoji} {name}", callback_data=f"inv_pet_mut_{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pet_weather_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for key, emoji, ru, en in PET_WEATHERS:
        name = en if lang == "EN" else ru
        row.append(InlineKeyboardButton(text=f"{emoji} {name}", callback_data=f"inv_pet_wth_{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    no_text = "❌ No weather" if lang == "EN" else "❌ Нет погоды"
    rows.append([InlineKeyboardButton(text=no_text, callback_data="inv_pet_wth_none")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("inv_adm_add_"))
async def admin_start_add_item(callback: CallbackQuery, state: FSMContext):
    """Начать добавление предмета — выбор типа"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    target_user_id = int(callback.data.split("_")[3])
    await state.update_data(target_user_id=target_user_id)

    user = db.get_user(target_user_id)
    user_display = _user_display(user)

    await callback.message.answer(
        f"➕ <b>Добавление в инвентарь {user_display}</b>\n\nВыберите тип:",
        parse_mode="HTML",
        reply_markup=_admin_add_type_keyboard(target_user_id),
    )
    await callback.answer()


# ─── ТИП: ПРЕДМЕТ ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("inv_adm_type_item"))
async def admin_add_item_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    if len(parts) >= 5 and parts[4].isdigit():
        await state.update_data(target_user_id=int(parts[4]))
    await state.set_state(AdminInventoryStates.adding_item_data)
    await callback.message.answer(
        "📦 <b>Добавление предмета</b>\n\n"
        "Отправьте сообщение:\n"
        "• <b>Текст</b> — первая строка название, остальные — описание\n"
        "• <b>Фото/видео/документ</b> — прикрепите медиафайл, в подписи укажите название\n\n"
        "Для отмены: /cancel",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminInventoryStates.adding_item_data)
async def admin_receive_item(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено")
        return

    data = await state.get_data()
    target_user_id = data.get("target_user_id")

    if not target_user_id:
        await state.clear()
        await message.answer("❌ Ошибка: не удалось определить пользователя. Начните добавление заново.")
        return

    # ── Обработка медиагруппы (альбома) ──────────────────────────────────────
    if message.media_group_id:
        group_id = message.media_group_id
        current_group = data.get("item_media_group_id")

        # Извлекаем file_id и тип из текущего сообщения
        if message.photo:
            fid = message.photo[-1].file_id
            mtype = "photo"
            raw_text = message.caption or ""
        elif message.video:
            fid = message.video.file_id
            mtype = "video"
            raw_text = message.caption or ""
        elif message.document:
            fid = message.document.file_id
            mtype = "document"
            raw_text = message.caption or ""
        else:
            fid = None
            mtype = None
            raw_text = ""

        if current_group == group_id:
            # Продолжаем накапливать файлы того же альбома
            accumulated = data.get("item_media_accumulated", [])
            if fid:
                accumulated.append({"file_id": fid, "media_type": mtype})
            await state.update_data(item_media_accumulated=accumulated)
            return  # Ждём следующих сообщений альбома
        else:
            # Первое сообщение нового альбома — инициализируем накопление
            accumulated = []
            if fid:
                accumulated.append({"file_id": fid, "media_type": mtype})
            await state.update_data(
                item_media_group_id=group_id,
                item_media_accumulated=accumulated,
                item_media_caption=raw_text,
                item_media_message_id=message.message_id,
                item_media_chat_id=message.chat.id,
            )
            # Запускаем отложенную обработку через 0.7 сек
            # Флаг item_media_finalized предотвращает повторный запуск если
            # пользователь пришлёт ещё одно сообщение до истечения задержки
            await state.update_data(item_media_finalized=False)
            async def _delayed_finalize():
                await asyncio.sleep(0.7)
                cur_data = await state.get_data()
                if cur_data.get("item_media_finalized"):
                    return  # уже обработано
                await state.update_data(item_media_finalized=True)
                await _finalize_item_media_group(message, state, target_user_id)
            asyncio.create_task(_delayed_finalize())
            return
    # ── Одиночное сообщение ───────────────────────────────────────────────────
    if message.photo:
        media_file_id = message.photo[-1].file_id
        media_type = "photo"
        raw_text = message.caption or ""
    elif message.video:
        media_file_id = message.video.file_id
        media_type = "video"
        raw_text = message.caption or ""
    elif message.document:
        media_file_id = message.document.file_id
        media_type = "document"
        raw_text = message.caption or ""
    else:
        media_file_id = None
        media_type = None
        raw_text = message.text or ""

    lines = raw_text.strip().splitlines()
    name = lines[0].strip() if lines else "Предмет"
    description = "\n".join(lines[1:]).strip() if len(lines) > 1 else None

    item_id = db.add_inventory_item(
        user_id=target_user_id, name=name, description=description,
        media_file_id=media_file_id, media_type=media_type,
        quantity=1, added_by=message.from_user.id, item_type="item",
    )
    await state.clear()
    if item_id:
        _tuser = db.get_user(target_user_id)
        await log_inventory_add(
            message.bot,
            admin_id=message.from_user.id,
            admin_name=message.from_user.full_name,
            user_id=target_user_id,
            user_name=(_tuser or {}).get("roblox_nick") or (_tuser or {}).get("username") or str(target_user_id),
            item_type="item",
            item_name=name,
        )
    await _notify_item_added(message, target_user_id, name, item_id,
                             media_file_id, media_type, description)


async def _finalize_item_media_group(message: Message, state: FSMContext, target_user_id: int):
    """Вызывается после задержки — обрабатывает накопленный альбом как один предмет."""
    try:
        data = await state.get_data()
        accumulated: list = data.get("item_media_accumulated", [])
        raw_text: str = data.get("item_media_caption", "")

        if not accumulated:
            await state.clear()
            return

        lines = raw_text.strip().splitlines()
        name = lines[0].strip() if lines else "Предмет"
        description = "\n".join(lines[1:]).strip() if len(lines) > 1 else None

        # Сохраняем список медиафайлов как JSON в media_file_id
        media_file_id = json.dumps(accumulated)
        # media_type = тип первого файла (для обратной совместимости)
        media_type = accumulated[0].get("media_type", "photo") if accumulated else None

        item_id = db.add_inventory_item(
            user_id=target_user_id, name=name, description=description,
            media_file_id=media_file_id, media_type=media_type,
            quantity=1, added_by=message.from_user.id, item_type="item",
        )
        await state.clear()

        if item_id:
            _tuser = db.get_user(target_user_id)
            await log_inventory_add(
                message.bot,
                admin_id=message.from_user.id,
                admin_name=message.from_user.full_name,
                user_id=target_user_id,
                user_name=(_tuser or {}).get("roblox_nick") or (_tuser or {}).get("username") or str(target_user_id),
                item_type="item",
                item_name=name,
            )
        await _notify_item_added(message, target_user_id, name, item_id,
                                 media_file_id, media_type, description)
    except Exception as e:
        logger.error(f"_finalize_item_media_group error: {e}")


# ─── ТИП: ЕДА ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("inv_adm_type_food"))
async def admin_add_food_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    data = await state.get_data()
    lang = (db.get_user(callback.from_user.id) or {}).get("language", "RUS")
    parts = callback.data.split("_")
    target_user_id = int(parts[4]) if len(parts) >= 5 and parts[4].isdigit() else data.get("target_user_id")
    await state.set_state(AdminInventoryStates.food_selecting)
    await state.update_data(food_selected=[], food_lang=lang,
                            target_user_id=target_user_id)
    header = "🍎 <b>Выберите еду для добавления:</b>" if lang == "RUS" else "🍎 <b>Select food to add:</b>"
    await callback.message.answer(header, parse_mode="HTML",
                                  reply_markup=_food_select_keyboard([], lang))
    await callback.answer()


@router.callback_query(F.data.startswith("inv_food_tog_"), AdminInventoryStates.food_selecting)
async def admin_food_toggle(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    key = callback.data[len("inv_food_tog_"):]
    data = await state.get_data()
    selected: list = list(data.get("food_selected", []))
    lang = data.get("food_lang", "RUS")
    if key in selected:
        selected.remove(key)
    else:
        selected.append(key)
    await state.update_data(food_selected=selected)
    await callback.message.edit_reply_markup(reply_markup=_food_select_keyboard(selected, lang))
    await callback.answer()


@router.callback_query(F.data == "inv_food_done", AdminInventoryStates.food_selecting)
async def admin_food_done(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    data = await state.get_data()
    selected: list = data.get("food_selected", [])
    lang = data.get("food_lang", "RUS")
    if not selected:
        msg = "Выберите хотя бы одну еду" if lang == "RUS" else "Select at least one food item"
        await callback.answer(msg, show_alert=True)
        return
    # Начинаем спрашивать количество для каждой выбранной еды
    await state.set_state(AdminInventoryStates.food_qty_input)
    await state.update_data(food_queue=list(selected), food_quantities={})
    await _ask_food_qty(callback.message, selected[0], lang)
    await callback.answer()


async def _ask_food_qty(message: Message, food_key: str, lang: str):
    display = _food_display(food_key, lang)
    if lang == "RUS":
        text = f"Сколько штук <b>{display}</b> добавить?\n\nВведите число (например: 5)"
    else:
        text = f"How many <b>{display}</b> to add?\n\nEnter a number (e.g. 5)"
    await message.answer(text, parse_mode="HTML")


@router.message(AdminInventoryStates.food_qty_input)
async def admin_food_qty_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено")
        return

    data = await state.get_data()
    lang = data.get("food_lang", "RUS")
    queue: list = list(data.get("food_queue", []))
    quantities: dict = dict(data.get("food_quantities", {}))
    target_user_id = data.get("target_user_id")

    if not target_user_id:
        await state.clear()
        await message.answer("❌ Ошибка: не удалось определить пользователя. Начните добавление заново.")
        return

    if not message.text or not message.text.strip().isdigit() or int(message.text.strip()) < 1:
        err = "Введите целое положительное число" if lang == "RUS" else "Enter a positive integer"
        await message.answer(err)
        return

    current_key = queue[0]
    qty = int(message.text.strip())
    quantities[current_key] = qty
    queue.pop(0)

    if queue:
        await state.update_data(food_queue=queue, food_quantities=quantities)
        await _ask_food_qty(message, queue[0], lang)
        return

    # Все количества собраны — добавляем в инвентарь
    await state.clear()
    user = db.get_user(target_user_id)
    user_lang = (user or {}).get("language", "RUS")
    added_lines = []
    for fkey, fqty in quantities.items():
        f = FOOD_BY_KEY[fkey]
        fname = f[2] if user_lang == "EN" else f[1]
        full_name = f"{f[3]} {fname}"
        item_id = db.add_inventory_item(
            user_id=target_user_id, name=full_name,
            quantity=fqty, added_by=message.from_user.id, item_type="food",
        )
        if item_id:
            added_lines.append(f"• {full_name} x{fqty}")

    summary = "\n".join(added_lines)
    user_display = _user_display(user)
    await message.answer(
        f"✅ Добавлено в инвентарь {user_display}:\n{summary}" if lang == "RUS" else
        f"✅ Added to {user_display}'s inventory:\n{summary}",
        parse_mode="HTML",
    )
    # Лог: добавление еды
    if added_lines:
        await log_inventory_add(
            message.bot,
            admin_id=message.from_user.id,
            admin_name=message.from_user.full_name,
            user_id=target_user_id,
            user_name=(user or {}).get("roblox_nick") or (user or {}).get("username") or str(target_user_id),
            item_type="food",
            item_name=", ".join(f"{k} x{v}" for k, v in quantities.items()),
            quantity=sum(quantities.values()),
        )
    # Уведомляем пользователя
    if user_lang == "RUS":
        notif_lang = (db.get_user(user_id) or {}).get("language", "RUS")
        notif_lc = "ru" if notif_lang == "RUS" else "en"
        notif = locale_manager.get_text(notif_lc, "inventory.food_added_notification").format(summary=summary)
    else:
        notif = f"🎒 Administrator added food to your inventory:\n{summary}"
    try:
        await message.bot.send_message(target_user_id, notif, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error notifying user {target_user_id}: {e}")


# ─── ТИП: ПЕТ ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("inv_adm_type_pet"))
async def admin_add_pet_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    data = await state.get_data()
    lang = (db.get_user(callback.from_user.id) or {}).get("language", "RUS")
    parts = callback.data.split("_")
    target_user_id = int(parts[4]) if len(parts) >= 5 and parts[4].isdigit() else data.get("target_user_id")
    await state.set_state(AdminInventoryStates.pet_name)
    await state.update_data(target_user_id=target_user_id, pet_lang=lang)
    text = "🐾 Введите имя пета (например: Дракон):" if lang == "RUS" else "🐾 Enter the pet name (e.g. Dragon):"
    await callback.message.answer(text)
    await callback.answer()


@router.message(AdminInventoryStates.pet_name)
async def admin_pet_name_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено")
        return
    name = message.text.strip() if message.text else ""
    if not name:
        await message.answer("❌ Введите имя текстом")
        return
    data = await state.get_data()
    lang = data.get("pet_lang", "RUS")
    await state.set_state(AdminInventoryStates.pet_income)
    await state.update_data(pet_name=name)
    text = (
        f'💰 Введите доход пета в формате "1 222 333" (только цифры и пробелы):'
        if lang == "RUS" else
        f'💰 Enter the pet income in format "1 222 333" (digits and spaces only):'
    )
    await message.answer(text)


@router.message(AdminInventoryStates.pet_income)
async def admin_pet_income_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено")
        return
    raw = re.sub(r'[\s]', '', message.text or "")
    if not raw.isdigit():
        await message.answer("❌ Введите число (можно с пробелами): например 1 222 333")
        return
    # Форматируем с пробелами
    income_fmt = f"{int(raw):,}".replace(",", " ")
    data = await state.get_data()
    lang = data.get("pet_lang", "RUS")
    await state.set_state(AdminInventoryStates.pet_mutation)
    await state.update_data(pet_income=income_fmt)
    text = "🧬 Выберите мутацию пета:" if lang == "RUS" else "🧬 Choose pet mutation:"
    await message.answer(text, reply_markup=_pet_mutation_keyboard(lang))


@router.callback_query(F.data.startswith("inv_pet_mut_"), AdminInventoryStates.pet_mutation)
async def admin_pet_mutation_select(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    mut_key = callback.data[len("inv_pet_mut_"):]
    if mut_key not in PET_MUT_BY_KEY:
        await callback.answer("❌ Неизвестная мутация")
        return
    data = await state.get_data()
    lang = data.get("pet_lang", "RUS")
    await state.set_state(AdminInventoryStates.pet_weather)
    await state.update_data(pet_mutation=mut_key)
    text = "🌤 Выберите погоду пета:" if lang == "RUS" else "🌤 Choose pet weather:"
    await callback.message.answer(text, reply_markup=_pet_weather_keyboard(lang))
    await callback.answer()


@router.callback_query(F.data.startswith("inv_pet_wth_"), AdminInventoryStates.pet_weather)
async def admin_pet_weather_select(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    wth_key = callback.data[len("inv_pet_wth_"):]
    weather = None if wth_key == "none" else wth_key
    if weather and weather not in PET_WEATHER_BY_KEY:
        await callback.answer("❌ Неизвестная погода")
        return
    data = await state.get_data()
    lang = data.get("pet_lang", "RUS")
    await state.set_state(AdminInventoryStates.pet_coeff)
    await state.update_data(pet_weather=weather)
    text = (
        '✖️ Введите коэффициент в формате "1.99":' if lang == "RUS" else
        '✖️ Enter the coefficient in format "1.99":'
    )
    await callback.message.answer(text)
    await callback.answer()


@router.message(AdminInventoryStates.pet_coeff)
async def admin_pet_coeff_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено")
        return

    coeff_raw = (message.text or "").strip().replace(",", ".")
    try:
        float(coeff_raw)
    except ValueError:
        await message.answer('❌ Введите число в формате "1.99"')
        return

    data = await state.get_data()
    lang = data.get("pet_lang", "RUS")

    await state.set_state(AdminInventoryStates.pet_photo)
    await state.update_data(pet_coeff=coeff_raw)

    skip_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="⏭ Пропустить" if lang == "RUS" else "⏭ Skip",
            callback_data="inv_pet_photo_skip"
        )
    ]])
    text = (
        "📸 Отправьте фото пета (необязательно) или нажмите «Пропустить»:"
        if lang == "RUS" else
        "📸 Send a photo of the pet (optional) or press «Skip»:"
    )
    await message.answer(text, reply_markup=skip_kb)


async def _save_pet_to_db(message_or_callback, state: FSMContext,
                           media_file_id: str = None, media_type: str = None):
    """Финальный шаг добавления пета — сохранить в БД и уведомить."""
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    lang = data.get("pet_lang", "RUS")

    if not target_user_id:
        await state.clear()
        answer = (message_or_callback.answer
                  if isinstance(message_or_callback, Message)
                  else message_or_callback.message.answer)
        await answer("❌ Ошибка: не удалось определить пользователя. Начните добавление заново.")
        return
    pet_name = data.get("pet_name", "")
    income = data.get("pet_income", "")
    mutation_key = data.get("pet_mutation", "")
    weather_key = data.get("pet_weather")
    coeff_raw = data.get("pet_coeff", "")

    user = db.get_user(target_user_id)
    user_lang = (user or {}).get("language", "RUS")
    full_name = _pet_full_name(pet_name, income, mutation_key, weather_key, coeff_raw, user_lang)

    item_id = db.add_inventory_item(
        user_id=target_user_id,
        name=full_name,
        item_type="pet",
        added_by=message_or_callback.from_user.id,
        pet_income=income,
        pet_mutation=mutation_key,
        pet_weather=weather_key,
        pet_coeff=coeff_raw,
        media_file_id=media_file_id,
        media_type=media_type,
    )
    await state.clear()

    bot = (message_or_callback.bot
           if isinstance(message_or_callback, Message)
           else message_or_callback.bot)
    answer = (message_or_callback.answer
              if isinstance(message_or_callback, Message)
              else message_or_callback.message.answer)

    user_display = _user_display(user)
    if item_id:
        await answer(
            f"✅ Пет добавлен в инвентарь {user_display}:\n<b>{full_name}</b>" if lang == "RUS" else
            f"✅ Pet added to {user_display}'s inventory:\n<b>{full_name}</b>",
            parse_mode="HTML",
        )
        # Лог: добавление пета — для фото возвращает стабильный file_id из лог-группы
        _adm = message_or_callback.from_user
        stable_file_id = await log_inventory_add(
            bot,
            admin_id=_adm.id,
            admin_name=_adm.full_name,
            user_id=target_user_id,
            user_name=(user or {}).get("roblox_nick") or (user or {}).get("username") or str(target_user_id),
            item_type="pet",
            item_name=full_name,
            media_file_id=media_file_id,
            media_type=media_type,
        )
        # Если лог-группа вернула стабильный file_id — обновляем запись в БД
        if stable_file_id and stable_file_id != media_file_id:
            db.update_inventory_item_media(item_id, stable_file_id, media_type)
            logger.info(f"Pet {item_id}: media_file_id updated to stable log-group file_id")

        notif_lc = "ru" if user_lang == "RUS" else "en"
        notif = locale_manager.get_text(notif_lc, "inventory.pet_added_notification").format(full_name=full_name)
        try:
            await bot.send_message(target_user_id, notif, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error notifying user {target_user_id}: {e}")
    else:
        await answer("❌ Ошибка добавления пета")


@router.message(AdminInventoryStates.pet_photo)
async def admin_pet_photo_receive(message: Message, state: FSMContext):
    """Получаем фото пета (или текст — игнорируем, просим фото или пропустить)."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено")
        return

    if message.photo:
        file_id = message.photo[-1].file_id
        await _save_pet_to_db(message, state, media_file_id=file_id, media_type="photo")
    else:
        data = await state.get_data()
        lang = data.get("pet_lang", "RUS")
        skip_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="⏭ Пропустить" if lang == "RUS" else "⏭ Skip",
                callback_data="inv_pet_photo_skip"
            )
        ]])
        await message.answer(
            "❌ Отправьте фото или нажмите «Пропустить»" if lang == "RUS"
            else "❌ Send a photo or press «Skip»",
            reply_markup=skip_kb
        )


@router.callback_query(F.data == "inv_pet_photo_skip", AdminInventoryStates.pet_photo)
async def admin_pet_photo_skip(callback: CallbackQuery, state: FSMContext):
    """Пропустить фото пета."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await _save_pet_to_db(callback, state)
    await callback.answer()


# ─── ОБЩИЙ ХЕЛПЕР ────────────────────────────────────────────────────────────

async def _notify_item_added(message: Message, target_user_id: int, name: str,
                              item_id, media_file_id, media_type, description):
    user = db.get_user(target_user_id)
    user_display = _user_display(user)
    if item_id:
        await message.answer(
            f"✅ Предмет <b>{name}</b> добавлен в инвентарь {user_display}!",
            parse_mode="HTML",
        )
        user_lang = (user or {}).get("language", "RUS")
        notif_lc = "ru" if user_lang == "RUS" else "en"
        notif = locale_manager.get_text(notif_lc, "inventory.item_added_notification").format(name=name)
        try:
            await message.bot.send_message(target_user_id, notif, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error notifying user {target_user_id}: {e}")
    else:
        await message.answer("❌ Ошибка добавления предмета")


# ========== ДОБАВЛЕНИЕ ПИТОМЦА ПОЛЬЗОВАТЕЛЕМ ==========

def _get_example_photo_file_id() -> str | None:
    """Получить file_id примера фото питомца из файла."""
    import json, os
    path = "pet_example.json"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("file_id")
    except Exception:
        return None


def _save_example_photo_file_id(file_id: str):
    """Сохранить file_id примера фото питомца."""
    import json
    with open("pet_example.json", "w") as f:
        json.dump({"file_id": file_id}, f)


@router.callback_query(F.data == "inv_add_pet_request")
async def user_add_pet_start(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал «Добавить питомца» — просим прислать фото."""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    if lang == "RUS":
        text = (
            "📸 <b>Добавление питомца в инвентарь</b>\n\n"
            "🐾 Для добавления питомца вам нужно <b>передать его на аккаунт администратора</b> в игре. "
            "Это необходимо для безопасного обмена — администратор выступает гарантом сделки, "
            "чтобы никто никого не обманул.\n\n"
            "📦 После передачи питомец появится в вашем инвентаре.\n\n"
            "🔄 Когда захотите забрать питомца обратно или обменяться — нажмите "
            "<b>«Забрать»</b>, и администратор вернёт его вам в игре.\n\n"
            "📸 Отправьте фотографию вашего питомца (скриншот из игры).\n"
            "⚠️ Фото должно быть чётким, питомец хорошо виден.\n"
            "Ниже пример правильного фото:"
        )
        cancel_text = "Для отмены отправьте /cancel"
    else:
        text = (
            "📸 <b>Add pet to inventory</b>\n\n"
            "🐾 To add a pet you need to <b>transfer it to the administrator's account</b> in-game. "
            "This is required for safe trading — the administrator acts as a guarantor "
            "so no one gets scammed.\n\n"
            "📦 After the transfer, the pet will appear in your inventory.\n\n"
            "🔄 When you want to get your pet back or trade it — press "
            "<b>«Pick up»</b> and the administrator will return it to you in-game.\n\n"
            "📸 Send a photo of your pet (in-game screenshot).\n"
            "⚠️ The photo must be clear, the pet should be clearly visible.\n"
            "Below is an example of a correct photo:"
        )
        cancel_text = "To cancel, send /cancel"

    example_file_id = _get_example_photo_file_id()

    await state.set_state(UserAddItemStates.waiting_for_pet_photo)

    if example_file_id:
        try:
            await callback.message.answer_photo(
                example_file_id,
                caption=f"{text}\n\n{cancel_text}",
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                f"{text}\n\n{cancel_text}",
                parse_mode="HTML"
            )
    else:
        await callback.message.answer(
            f"{text}\n\n{cancel_text}",
            parse_mode="HTML"
        )
    await callback.answer()


@router.message(UserAddItemStates.waiting_for_pet_photo)
async def user_add_pet_receive_photo(message: Message, state: FSMContext):
    """Получаем фото(графии) питомца от пользователя и пересылаем администраторам."""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    lc = "ru" if lang == "RUS" else "en"

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(locale_manager.get_text(lc, "common.cancelled_dot"))
        return

    if not message.photo:
        await message.answer(locale_manager.get_text(lc, "inventory.send_photo_please"))
        return

    file_id = message.photo[-1].file_id

    # ── Обработка альбома (несколько фото сразу) ─────────────────────────────
    if message.media_group_id:
        group_id = message.media_group_id
        data = await state.get_data()
        current_group = data.get("pet_media_group_id")

        if current_group == group_id:
            # Накапливаем file_id следующих фото альбома
            accumulated = data.get("pet_media_accumulated", [])
            accumulated.append(file_id)
            await state.update_data(pet_media_accumulated=accumulated)
            return

        # Первое фото нового альбома — инициализируем накопление
        await state.update_data(
            pet_media_group_id=group_id,
            pet_media_accumulated=[file_id],
            pet_media_finalized=False,
        )
        async def _delayed_pet():
            await asyncio.sleep(0.7)
            cur_data = await state.get_data()
            if cur_data.get("pet_media_finalized"):
                return  # уже обработано
            await state.update_data(pet_media_finalized=True)
            await _finalize_pet_request(message, state, user, lang)
        asyncio.create_task(_delayed_pet())
        return

    # ── Одиночное фото ────────────────────────────────────────────────────────
    await state.clear()
    await _send_pet_request_to_admins(message, user, lang, [file_id])


async def _finalize_pet_request(message: Message, state: FSMContext, user: dict, lang: str):
    """Вызывается после задержки — отправляет накопленный альбом администраторам."""
    try:
        data = await state.get_data()
        file_ids: list = data.get("pet_media_accumulated", [])
        await state.clear()
        if not file_ids:
            return
        await _send_pet_request_to_admins(message, user, lang, file_ids)
    except Exception as e:
        logger.error(f"_finalize_pet_request error: {e}")


async def _send_pet_request_to_admins(
    message: Message, user: dict, lang: str, file_ids: list
):
    """Подтверждение пользователю + уведомление всех администраторов."""
    user_id = message.from_user.id

    lc = "ru" if lang == "RUS" else "en"
    await message.answer(locale_manager.get_text(lc, "inventory.photo_received"))

    pet_request_id = db.create_pickup_request(user_id, [], request_type="pet_add")

    user_display = _user_display(user)
    caption = (
        f"🐾 <b>Запрос на добавление питомца</b>\n\n"
        f"👤 Пользователь: {user_display}\n"
        f"🆔 ID: <code>{user_id}</code>"
    )
    take_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🙋 Я выполню",
            callback_data=f"inv_adm_pet_take_{pet_request_id}_{user_id}"
        ),
    ]])

    admin_msg_ids: dict = {}
    for admin_id in ADMIN_IDS:
        try:
            if len(file_ids) == 1:
                # Одно фото — отправляем с кнопкой прямо на нём
                sent = await message.bot.send_photo(
                    admin_id,
                    file_ids[0],
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=take_keyboard,
                )
                admin_msg_ids[str(admin_id)] = sent.message_id
            else:
                # Несколько фото — альбом + отдельное сообщение с кнопкой
                album = [
                    InputMediaPhoto(
                        media=fid,
                        caption=caption if i == 0 else None,
                        parse_mode="HTML",
                    )
                    for i, fid in enumerate(file_ids)
                ]
                await message.bot.send_media_group(admin_id, album)
                # Кнопка отдельным сообщением после альбома
                sent = await message.bot.send_message(
                    admin_id,
                    f"🐾 Запрос от {user_display}",
                    parse_mode="HTML",
                    reply_markup=take_keyboard,
                )
                admin_msg_ids[str(admin_id)] = sent.message_id
        except Exception as e:
            logger.error(f"Error notifying admin {admin_id} about pet add request: {e}")

    if pet_request_id:
        db.save_request_admin_msg_ids(pet_request_id, admin_msg_ids)


# ========== УСТАНОВКА ПРИМЕРА ФОТО ПИТОМЦА (ТОЛЬКО ADMIN) ==========

@router.message(F.chat.type == "private", F.text == "/set_pet_example")
async def admin_set_pet_example_start(message: Message, state: FSMContext):
    """Начало установки примера фото питомца."""
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminInventoryStates.setting_example_photo)
    await message.answer(
        "📸 Отправьте фото которое будет показываться пользователям как пример правильного фото питомца.\n\n"
        "❌ /cancel — отменить"
    )


@router.message(AdminInventoryStates.setting_example_photo)
async def admin_set_pet_example_receive(message: Message, state: FSMContext):
    """Получаем и сохраняем пример фото."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено.")
        return

    if not message.photo:
        await message.answer("❌ Отправьте фотографию.")
        return

    file_id = message.photo[-1].file_id
    _save_example_photo_file_id(file_id)
    await state.clear()
    await message.answer(
        "✅ Пример фото питомца сохранён!\n"
        "Теперь он будет показываться пользователям при запросе добавления питомца."
    )
