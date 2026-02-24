"""
handlers/giveaway.py - Система розыгрышей
Создание, участие, завершение, рассылка победителей.
"""

import logging
import random
import asyncio
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LinkPreviewOptions,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import Database
from utils.messages import locale_manager
from config import Config
from utils.keyboards import get_main_keyboard
from utils.log_events import log_giveaway_created, log_giveaway_finished, log_inventory_add

logger = logging.getLogger(__name__)
router = Router()
db = Database()

from handlers.admin_common import ADMIN_IDS, is_admin
MSK = ZoneInfo("Europe/Moscow")

# Импортируем справочники из inventory для призов
from handlers.inventory import (
    FOOD_LIST, FOOD_BY_KEY,
    PET_MUTATIONS, PET_MUT_BY_KEY,
    PET_WEATHERS, PET_WEATHER_BY_KEY,
    _food_display, _pet_mut_display, _pet_weather_display, _pet_full_name,
)


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ПРИЗОВ ==========

def _prize_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🍎 Еда / Food",     callback_data="gw_prize_type_food"),
            InlineKeyboardButton(text="🐾 Пет / Pet",      callback_data="gw_prize_type_pet"),
        ],
        [
            InlineKeyboardButton(text="📦 Предмет / Item", callback_data="gw_prize_type_item"),
        ],
        [
            InlineKeyboardButton(text="⏭ Пропустить место", callback_data="gw_prize_skip"),
        ],
    ])


def _food_select_keyboard_gw(selected: list, lang: str = "RUS") -> InlineKeyboardMarkup:
    rows = []
    row = []
    for key, ru, en, emoji in FOOD_LIST:
        name = en if lang == "EN" else ru
        mark = "✅" if key in selected else "☑️"
        row.append(InlineKeyboardButton(
            text=f"{mark}{emoji}{name}", callback_data=f"gw_food_tog_{key}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="gw_food_done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pet_mutation_keyboard_gw() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for key, emoji, ru, en in PET_MUTATIONS:
        row.append(InlineKeyboardButton(
            text=f"{emoji} {ru}", callback_data=f"gw_pet_mut_{key}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pet_weather_keyboard_gw() -> InlineKeyboardMarkup:
    rows = []
    for key, emoji, ru, en in PET_WEATHERS:
        rows.append([InlineKeyboardButton(
            text=f"{emoji} {ru}", callback_data=f"gw_pet_wth_{key}"
        )])
    rows.append([InlineKeyboardButton(text="🚫 Нет погоды", callback_data="gw_pet_wth_none")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _prize_display_name(prize: dict) -> str:
    """Короткое отображаемое имя приза для сводки."""
    ptype = prize.get("prize_type", "item")
    if ptype == "food":
        parts = []
        for key, qty in (prize.get("food_items") or {}).items():
            f = FOOD_BY_KEY.get(key)
            name = f[1] if f else key
            parts.append(f"{name} x{qty}")
        return ", ".join(parts) if parts else "Еда"
    elif ptype == "pet":
        return prize.get("name", "Пет")
    else:
        return prize.get("name", "Предмет")



# ========== FSM ==========

class GiveawayCreateStates(StatesGroup):
    media_ru = State()          # медиа + текст RU
    media_en = State()          # медиа + текст EN
    button_text = State()       # текст кнопки
    channels = State()          # теги каналов/групп
    winner_count = State()      # количество победителей
    prize_type = State()        # выбор типа приза (item/food/pet)
    prize_media = State()       # медиа + текст приза (для предмета)
    prize_food = State()        # мультиселект еды
    prize_food_qty = State()    # ввод количества для каждой еды
    prize_pet_name = State()    # имя пета
    prize_pet_income = State()  # доход пета
    prize_pet_mutation = State()# мутация пета
    prize_pet_weather = State() # погода пета
    prize_pet_coeff = State()   # коэффициент пета
    prize_pet_photo = State()   # фото пета (опционально)
    end_type = State()          # способ завершения
    end_time = State()          # дата/время (если по времени)
    end_count = State()         # кол-во участников (если по кол-ву)
    confirm = State()           # финальное подтверждение


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def _user_link(user_id: int, username: str = None) -> str:
    if username:
        return f"<a href='tg://user?id={user_id}'>@{username}</a>"
    return f"<a href='tg://user?id={user_id}'>ID: {user_id}</a>"


async def _send_media_message(
    bot: Bot, chat_id: int,
    file_id: str, media_type: str,
    text: str, reply_markup=None,
    parse_mode: str = "HTML"
) -> Optional[types.Message]:
    """Отправить сообщение с медиафайлом или просто текст."""
    try:
        kwargs = dict(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
        if file_id and media_type == "photo":
            return await bot.send_photo(chat_id, file_id, **kwargs)
        elif file_id and media_type == "video":
            return await bot.send_video(chat_id, file_id, **kwargs)
        elif file_id and media_type == "animation":
            return await bot.send_animation(chat_id, file_id, **kwargs)
        elif file_id and media_type == "document":
            return await bot.send_document(chat_id, file_id, **kwargs)
        else:
            return await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error sending media to {chat_id}: {e}")
        return None


def _extract_media(message: Message):
    """Извлечь file_id и media_type из сообщения."""
    if message.photo:
        return message.photo[-1].file_id, "photo"
    elif message.video:
        return message.video.file_id, "video"
    elif message.animation:
        return message.animation.file_id, "animation"
    elif message.document:
        return message.document.file_id, "document"
    return None, None


def _get_text_from_message(message: Message) -> str:
    """Получить текст или подпись из сообщения."""
    return (message.text or message.caption or "").strip()


def _build_participate_keyboard(giveaway_id: int, button_text: str, count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"{button_text} ({count})",
            callback_data=f"gw_join_{giveaway_id}"
        )
    ]])


def _giveaway_post_text(giveaway: dict, lang: str = "RUS") -> str:
    """Текст публикации розыгрыша."""
    if lang == "RUS":
        title = giveaway.get("title_ru") or ""
        text = giveaway.get("text_ru") or ""
    else:
        title = giveaway.get("title_en") or giveaway.get("title_ru") or ""
        text = giveaway.get("text_en") or giveaway.get("text_ru") or ""

    end_type = giveaway.get("end_type")
    end_value = giveaway.get("end_value", "")

    if lang == "RUS":
        winners_line = f"🏆 Победителей: {giveaway['winner_count']}"
        if end_type == "time":
            end_line = f"⏰ Завершение: {end_value} (МСК)"
        else:
            end_line = f"👥 Завершится при {end_value} участниках"
    else:
        winners_line = f"🏆 Winners: {giveaway['winner_count']}"
        if end_type == "time":
            end_line = f"⏰ Ends: {end_value} (MSK)"
        else:
            end_line = f"👥 Ends at {end_value} participants"

    parts = []
    if title:
        parts.append(f"<b>{title}</b>")
    if text:
        parts.append(text)
    parts.append("")
    parts.append(winners_line)
    parts.append(end_line)
    return "\n".join(parts)


async def _check_subscriptions(bot: Bot, user_id: int, channels: list) -> bool:
    """Проверить подписку пользователя на все указанные каналы/группы."""
    for channel in channels:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except Exception:
            return False
    return True


async def finish_giveaway(bot: Bot, giveaway_id: int):
    """Завершить розыгрыш: выбрать победителей, разослать результаты."""
    giveaway = db.get_giveaway(giveaway_id)
    if not giveaway or giveaway["status"] != "active":
        return

    db.finish_giveaway(giveaway_id)

    participants = db.get_giveaway_participants(giveaway_id)
    winner_count = min(giveaway["winner_count"], len(participants))
    prizes = db.get_giveaway_prizes(giveaway_id)

    # Группируем призы по местам
    prizes_by_place: dict = {}
    for prize in prizes:
        p = prize["place"]
        prizes_by_place.setdefault(p, []).append(prize)

    # Выбираем победителей случайно
    winners = random.sample(participants, winner_count) if participants else []

    # Обновляем кнопку в группе — убираем кнопку участия
    if giveaway.get("group_message_id"):
        try:
            await bot.edit_message_reply_markup(
                chat_id=Config.REQUIRED_GROUP_ID,
                message_id=giveaway["group_message_id"],
                reply_markup=None
            )
        except Exception:
            pass

    # Формируем текст результатов
    if winners:
        winners_lines_ru = [locale_manager.get_text("ru", "giveaway.winners_header") + "\n"]
        winners_lines_en = [locale_manager.get_text("en", "giveaway.winners_header") + "\n"]
        for i, w in enumerate(winners, 1):
            link = _user_link(w["user_id"], w.get("username"))
            winners_lines_ru.append(f"{i}. {link}")
            winners_lines_en.append(f"{i}. {link}")
        results_ru = "\n".join(winners_lines_ru)
        results_en = "\n".join(winners_lines_en)
    else:
        results_ru = locale_manager.get_text("ru", "giveaway.no_participants")
        results_en = locale_manager.get_text("en", "giveaway.no_participants")

    # Рассылаем результаты только участникам розыгрыша
    for participant in participants:
        uid = participant["user_id"]
        p_user = db.get_user(uid)
        lang = (p_user or {}).get("language", "RUS")
        text = results_ru if lang == "RUS" else results_en
        try:
            await bot.send_message(uid, text, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
            await asyncio.sleep(0.05)
        except Exception:
            pass

    # Публикуем результаты в группу
    try:
        await bot.send_message(
            Config.REQUIRED_GROUP_ID,
            results_ru,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
    except Exception as e:
        logger.error(f"Error posting results to group: {e}")

    # Уведомляем каждого победителя о его призах
    for i, winner in enumerate(winners, 1):
        uid = winner["user_id"]
        lang = winner.get("language", "RUS")
        place_prizes = prizes_by_place.get(i, [])

        lc = "ru" if lang == "RUS" else "en"
        header = locale_manager.get_text(lc, "giveaway.winner_notification").format(i=i) + "\n"
        if not place_prizes:
            header = header.rstrip("\n")

        prize_lines = []
        for j, prize in enumerate(place_prizes, 1):
            line = f"{j}. <b>{prize['name']}</b>"
            if prize.get("description"):
                line += f"\n   {prize['description']}"
            prize_lines.append(line)

        full_text = header + "\n".join(prize_lines)

        try:
            await bot.send_message(uid, full_text, parse_mode="HTML")
            # Отправляем медиафайлы призов
            for prize in place_prizes:
                if prize.get("media_file_id"):
                    cap = f"<b>{prize['name']}</b>"
                    if prize.get("description"):
                        cap += f"\n{prize['description']}"
                    await _send_media_message(
                        bot, uid,
                        prize["media_file_id"], prize["media_type"],
                        cap
                    )
                    await asyncio.sleep(0.05)
            # Добавляем призы в инвентарь победителя
            for prize in place_prizes:
                ptype = prize.get("prize_type", "item")
                if ptype == "food":
                    food_items: dict = prize.get("food_items") or {}
                    for food_key, qty in food_items.items():
                        f = FOOD_BY_KEY.get(food_key)
                        food_name = f"{f[3]} {f[1]}" if f else food_key
                        db.add_inventory_item(
                            user_id=uid,
                            name=food_name,
                            item_type="food",
                            quantity=int(qty),
                            added_by=None,
                        )
                elif ptype == "pet":
                    pet_media_fid = prize.get("media_file_id")
                    pet_media_type = prize.get("media_type")
                    new_item_id = db.add_inventory_item(
                        user_id=uid,
                        name=prize["name"],
                        item_type="pet",
                        added_by=None,
                        pet_income=prize.get("pet_income"),
                        pet_mutation=prize.get("pet_mutation"),
                        pet_weather=prize.get("pet_weather"),
                        pet_coeff=prize.get("pet_coeff"),
                        media_file_id=pet_media_fid,
                        media_type=pet_media_type,
                    )
                    # Логируем добавление пета; для фото — получаем стабильный file_id из лог-группы
                    winner_user = db.get_user(uid)
                    winner_name = (winner_user or {}).get("username") or str(uid)
                    stable_fid = await log_inventory_add(
                        bot,
                        admin_id=0,
                        admin_name="🎰 Розыгрыш",
                        user_id=uid,
                        user_name=winner_name,
                        item_type="pet",
                        item_name=prize["name"],
                        media_file_id=pet_media_fid,
                        media_type=pet_media_type,
                    )
                    # Если фото переслано в лог-группу — обновляем стабильный file_id в БД
                    if stable_fid and new_item_id:
                        db.update_inventory_item_media(new_item_id, stable_fid, "photo")
                else:
                    db.add_inventory_item(
                        user_id=uid,
                        name=prize["name"],
                        description=prize.get("description"),
                        media_file_id=prize.get("media_file_id"),
                        media_type=prize.get("media_type"),
                        item_type="item",
                        quantity=1,
                        added_by=None,
                    )
        except Exception as e:
            logger.error(f"Error notifying winner {uid}: {e}")

    # Лог: завершение розыгрыша
    _winners_log = [
        (w["user_id"], w.get("username") or str(w["user_id"]), i)
        for i, w in enumerate(winners, 1)
    ]
    await log_giveaway_finished(
        bot,
        giveaway_id=giveaway_id,
        title=giveaway.get("title_ru", ""),
        participant_count=len(participants),
        winners=_winners_log,
    )


# ========== ФОНОВАЯ ЗАДАЧА ПРОВЕРКИ ТАЙМЕРА ==========

async def giveaway_timer_task(bot: Bot):
    """Фоновая задача: проверяет розыгрыши по времени каждую минуту."""
    while True:
        try:
            now_msk = datetime.now(MSK).replace(tzinfo=None)
            active = db.get_active_giveaways()
            for giveaway in active:
                if giveaway["end_type"] != "time":
                    continue
                end_value = giveaway.get("end_value", "")
                try:
                    end_dt = datetime.strptime(end_value, "%d.%m.%Y %H:%M")
                except Exception:
                    continue
                if now_msk >= end_dt:
                    logger.info(f"Giveaway {giveaway['id']} time expired, finishing...")
                    await finish_giveaway(bot, giveaway["id"])
        except Exception as e:
            logger.error(f"Error in giveaway timer task: {e}")
        await asyncio.sleep(60)


# ========== УЧАСТИЕ ПОЛЬЗОВАТЕЛЕЙ ==========

@router.callback_query(F.data.startswith("gw_join_"))
async def join_giveaway(callback: CallbackQuery):
    """Пользователь нажимает кнопку участия в розыгрыше."""
    user_id = callback.from_user.id
    giveaway_id = int(callback.data.split("_")[2])

    giveaway = db.get_giveaway(giveaway_id)
    if not giveaway or giveaway["status"] != "active":
        await callback.answer(locale_manager.get_text("ru", "giveaway.already_finished"), show_alert=True)
        return

    # Проверяем регистрацию в боте
    user = db.get_user(user_id)
    if not user:
        await callback.answer(locale_manager.get_text("ru", "giveaway.not_registered"), show_alert=True)
        return

    lang = user.get("language", "RUS")
    lc = "ru" if lang == "RUS" else "en"

    # Проверяем подписку на обязательную группу бота
    is_sub = await _check_subscriptions(
        callback.bot, user_id,
        [Config.REQUIRED_GROUP_ID] + giveaway.get("required_channels", [])
    )
    if not is_sub:
        channels = giveaway.get("required_channels", [])
        channel_list = "\n".join(f"• {ch}" for ch in channels) if channels else ""
        msg = locale_manager.get_text(lc, "giveaway.subscription_required").format(channels=channel_list)
        await callback.answer(msg, show_alert=True)
        return

    # Проверяем, не участвует ли уже
    if db.is_giveaway_participant(giveaway_id, user_id):
        await callback.answer(locale_manager.get_text(lc, "giveaway.already_participating"), show_alert=True)
        return

    # Добавляем участника
    db.join_giveaway(giveaway_id, user_id)
    count = db.get_giveaway_participant_count(giveaway_id)

    # Обновляем кнопку с новым счётчиком
    try:
        keyboard = _build_participate_keyboard(giveaway_id, giveaway["button_text"], count)
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass

    await callback.answer(locale_manager.get_text(lc, "giveaway.joined_success"), show_alert=True)

    # Проверяем завершение по кол-ву участников
    if giveaway["end_type"] == "count":
        try:
            target = int(giveaway["end_value"])
            if count >= target:
                await finish_giveaway(callback.bot, giveaway_id)
        except Exception:
            pass


# ========== СОЗДАНИЕ РОЗЫГРЫША (ADMIN FSM) ==========

@router.callback_query(F.data == "admin_giveaway_menu")
async def admin_giveaway_menu(callback: CallbackQuery):
    """Меню розыгрышей в админ-панели."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    giveaways = db.get_all_giveaways()
    active = [g for g in giveaways if g["status"] == "active"]
    finished = [g for g in giveaways if g["status"] == "finished"]

    text = "🎰 <b>Розыгрыши</b>\n\n"
    if active:
        text += f"🟢 <b>Активные ({len(active)}):</b>\n"
        for g in active:
            count = db.get_giveaway_participant_count(g["id"])
            text += f"• #{g['id']} {g['title_ru']} — {count} уч.\n"
    else:
        text += "🟢 Активных розыгрышей нет.\n"

    if finished:
        text += f"\n⚫ <b>Завершённые ({len(finished)}):</b>\n"
        for g in finished[:5]:
            text += f"• #{g['id']} {g['title_ru']}\n"

    keyboard_buttons = [
        [InlineKeyboardButton(text="➕ Создать розыгрыш", callback_data="gw_create_start")]
    ]
    for g in active:
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"🏁 Завершить #{g['id']} {g['title_ru'][:20]}",
                callback_data=f"gw_finish_{g['id']}"
            )
        ])
    keyboard_buttons.append([
        InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("gw_finish_"))
async def admin_finish_giveaway_early(callback: CallbackQuery):
    """Досрочное завершение розыгрыша."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    giveaway_id = int(callback.data.split("_")[2])
    giveaway = db.get_giveaway(giveaway_id)
    if not giveaway or giveaway["status"] != "active":
        await callback.answer("❌ Розыгрыш не найден или уже завершён", show_alert=True)
        return

    await callback.message.answer(f"🏁 Завершаю розыгрыш #{giveaway_id}...")
    await callback.answer()
    await finish_giveaway(callback.bot, giveaway_id)
    await callback.message.answer(f"✅ Розыгрыш #{giveaway_id} завершён, победители выбраны и уведомлены.")


@router.callback_query(F.data == "gw_create_start")
async def gw_create_start(callback: CallbackQuery, state: FSMContext):
    """Начало создания розыгрыша."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    await state.clear()
    await state.set_state(GiveawayCreateStates.media_ru)
    await callback.message.answer(
        "🎰 <b>Создание розыгрыша</b>\n\n"
        "<b>Шаг 1/10 — Русская версия</b>\n\n"
        "Отправьте медиафайл (фото, гиф, видео) с текстом в подписи.\n"
        "Или просто текст если медиа не нужно.\n\n"
        "❌ /cancel — отменить",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(GiveawayCreateStates.media_ru)
async def gw_step_media_ru(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Создание розыгрыша отменено.")
        return

    file_id, media_type = _extract_media(message)
    text = _get_text_from_message(message)

    lines = text.splitlines()
    title = lines[0].strip() if lines else "Розыгрыш"
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

    await state.update_data(
        title_ru=title, text_ru=body,
        media_file_id_ru=file_id, media_type_ru=media_type
    )
    await state.set_state(GiveawayCreateStates.media_en)
    await message.answer(
        "✅ Русская версия сохранена!\n\n"
        "<b>Шаг 2/10 — Английская версия</b>\n\n"
        "Отправьте медиафайл с текстом (или просто текст) на английском.\n"
        "Первая строка — заголовок, остальное — описание.\n\n"
        "❌ /cancel — отменить",
        parse_mode="HTML"
    )


@router.message(GiveawayCreateStates.media_en)
async def gw_step_media_en(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Создание розыгрыша отменено.")
        return

    file_id, media_type = _extract_media(message)
    text = _get_text_from_message(message)

    lines = text.splitlines()
    title = lines[0].strip() if lines else "Giveaway"
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

    await state.update_data(
        title_en=title, text_en=body,
        media_file_id_en=file_id, media_type_en=media_type
    )
    await state.set_state(GiveawayCreateStates.button_text)
    await message.answer(
        "✅ Английская версия сохранена!\n\n"
        "<b>Шаг 3/10 — Текст кнопки участия</b>\n\n"
        "Отправьте текст, который будет отображаться на кнопке.\n"
        "Например: <code>🎰 Участвовать</code>\n\n"
        "❌ /cancel — отменить",
        parse_mode="HTML"
    )


@router.message(GiveawayCreateStates.button_text)
async def gw_step_button_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Создание розыгрыша отменено.")
        return

    await state.update_data(button_text=message.text.strip())
    await state.set_state(GiveawayCreateStates.channels)
    await message.answer(
        "✅ Текст кнопки сохранён!\n\n"
        "<b>Шаг 4/10 — Каналы/группы для подписки</b>\n\n"
        "Отправьте теги каналов и групп через пробел или с новой строки.\n"
        "Например: <code>@channel1 @group2</code>\n\n"
        "Если дополнительных каналов нет — отправьте <code>-</code>\n\n"
        "❌ /cancel — отменить",
        parse_mode="HTML"
    )


@router.message(GiveawayCreateStates.channels)
async def gw_step_channels(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Создание розыгрыша отменено.")
        return

    raw = message.text.strip()
    if raw == "-":
        channels = []
    else:
        channels = [c.strip() for c in raw.replace("\n", " ").split() if c.strip().startswith("@")]

    await state.update_data(required_channels=channels)
    await state.set_state(GiveawayCreateStates.winner_count)
    await message.answer(
        "✅ Каналы сохранены!\n\n"
        "<b>Шаг 5/10 — Количество победителей</b>\n\n"
        "Отправьте число победителей (например: <code>3</code>)\n\n"
        "❌ /cancel — отменить",
        parse_mode="HTML"
    )


@router.message(GiveawayCreateStates.winner_count)
async def gw_step_winner_count(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Создание розыгрыша отменено.")
        return

    if not message.text.strip().isdigit() or int(message.text.strip()) < 1:
        await message.answer("❌ Введите целое число больше 0.")
        return

    winner_count = int(message.text.strip())
    await state.update_data(winner_count=winner_count, current_place=1, prizes=[])
    await state.set_state(GiveawayCreateStates.prize_type)

    place_emoji = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, winner_count + 1)]
    emoji = place_emoji[0] if winner_count >= 1 else "1."
    await message.answer(
        f"✅ Победителей: {winner_count}\n\n"
        f"<b>Шаг 6 — Призы</b>\n\n"
        f"<b>{emoji} 1 место — выберите тип приза:</b>",
        parse_mode="HTML",
        reply_markup=_prize_type_keyboard()
    )


# ========== ТИП ПРИЗА ==========

@router.callback_query(F.data == "gw_prize_skip", GiveawayCreateStates.prize_type)
async def gw_prize_skip_type(callback: CallbackQuery, state: FSMContext):
    """Пропустить место (из выбора типа)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await _advance_prize_place(callback, state)


@router.callback_query(F.data == "gw_prize_type_item", GiveawayCreateStates.prize_type)
async def gw_prize_type_item(callback: CallbackQuery, state: FSMContext):
    """Выбран тип «Предмет»."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await state.update_data(current_prize_type="item")
    await state.set_state(GiveawayCreateStates.prize_media)
    data = await state.get_data()
    place = data.get("current_place", 1)
    await callback.message.answer(
        f"📦 <b>Предмет — {place} место</b>\n\n"
        "Отправьте текст (название приза) и при необходимости медиафайл.\n"
        "Первая строка — название, остальное — описание.\n\n"
        "❌ /cancel — отменить",
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "gw_prize_type_food", GiveawayCreateStates.prize_type)
async def gw_prize_type_food(callback: CallbackQuery, state: FSMContext):
    """Выбран тип «Еда»."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await state.update_data(current_prize_type="food", gw_food_selected=[])
    await state.set_state(GiveawayCreateStates.prize_food)
    data = await state.get_data()
    place = data.get("current_place", 1)
    await callback.message.answer(
        f"🍎 <b>Еда — {place} место</b>\n\nВыберите фрукты:",
        parse_mode="HTML",
        reply_markup=_food_select_keyboard_gw([], "RUS")
    )
    await callback.answer()


@router.callback_query(F.data == "gw_prize_type_pet", GiveawayCreateStates.prize_type)
async def gw_prize_type_pet(callback: CallbackQuery, state: FSMContext):
    """Выбран тип «Пет»."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await state.update_data(current_prize_type="pet")
    await state.set_state(GiveawayCreateStates.prize_pet_name)
    await callback.message.answer(
        "🐾 Введите имя пета (например: Дракон):\n\n❌ /cancel — отменить"
    )
    await callback.answer()


# ========== ПРЕДМЕТ ==========

@router.message(GiveawayCreateStates.prize_media)
async def gw_step_prize_media(message: Message, state: FSMContext):
    """Получение предмета-приза для текущего места."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Создание розыгрыша отменено.")
        return

    data = await state.get_data()
    current_place = data.get("current_place", 1)
    prizes: list = data.get("prizes", [])
    winner_count = data.get("winner_count", 1)

    file_id, media_type = _extract_media(message)
    text = _get_text_from_message(message)
    lines = text.splitlines()
    name = lines[0].strip() if lines else f"Приз {current_place} места"
    description = "\n".join(lines[1:]).strip() if len(lines) > 1 else None

    prizes.append({
        "place": current_place,
        "prize_type": "item",
        "name": name,
        "description": description,
        "media_file_id": file_id,
        "media_type": media_type,
    })
    await state.update_data(prizes=prizes)
    await _show_prize_navigation(message, state, current_place, winner_count, prizes)


# ========== ЕДА ==========

@router.callback_query(F.data.startswith("gw_food_tog_"), GiveawayCreateStates.prize_food)
async def gw_food_toggle(callback: CallbackQuery, state: FSMContext):
    """Переключить выбор фрукта."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    key = callback.data[len("gw_food_tog_"):]
    data = await state.get_data()
    selected: list = data.get("gw_food_selected", [])
    if key in selected:
        selected.remove(key)
    else:
        selected.append(key)
    await state.update_data(gw_food_selected=selected)
    try:
        await callback.message.edit_reply_markup(
            reply_markup=_food_select_keyboard_gw(selected)
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "gw_food_done", GiveawayCreateStates.prize_food)
async def gw_food_done(callback: CallbackQuery, state: FSMContext):
    """Завершить выбор еды — спрашиваем количество."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    data = await state.get_data()
    selected: list = data.get("gw_food_selected", [])
    if not selected:
        await callback.answer("Выберите хотя бы один фрукт", show_alert=True)
        return
    await state.update_data(gw_food_qty_queue=list(selected), gw_food_qty_map={})
    await state.set_state(GiveawayCreateStates.prize_food_qty)
    await _ask_gw_food_qty(callback.message, state)
    await callback.answer()


async def _ask_gw_food_qty(msg: Message, state: FSMContext):
    data = await state.get_data()
    queue: list = data.get("gw_food_qty_queue", [])
    if not queue:
        await _save_gw_food_prize(msg, state)
        return
    key = queue[0]
    f = FOOD_BY_KEY.get(key)
    name = f"{f[3]} {f[1]}" if f else key
    await msg.answer(
        f"🔢 Сколько <b>{name}</b> добавить в приз?\n\nВведите число:",
        parse_mode="HTML"
    )


@router.message(GiveawayCreateStates.prize_food_qty)
async def gw_food_qty_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено.")
        return
    if not message.text or not message.text.strip().isdigit() or int(message.text.strip()) < 1:
        await message.answer("❌ Введите целое число больше 0.")
        return

    data = await state.get_data()
    queue: list = data.get("gw_food_qty_queue", [])
    qty_map: dict = data.get("gw_food_qty_map", {})
    qty_map[queue[0]] = int(message.text.strip())
    await state.update_data(gw_food_qty_queue=queue[1:], gw_food_qty_map=qty_map)
    await _ask_gw_food_qty(message, state)


async def _save_gw_food_prize(msg: Message, state: FSMContext):
    data = await state.get_data()
    current_place = data.get("current_place", 1)
    winner_count = data.get("winner_count", 1)
    prizes: list = data.get("prizes", [])
    qty_map: dict = data.get("gw_food_qty_map", {})

    # Формируем имя для сводки
    parts = []
    for key, qty in qty_map.items():
        f = FOOD_BY_KEY.get(key)
        name = f"{f[3]} {f[1]}" if f else key
        parts.append(f"{name} x{qty}")
    display_name = ", ".join(parts) if parts else "Еда"

    prizes.append({
        "place": current_place,
        "prize_type": "food",
        "name": display_name,
        "description": None,
        "media_file_id": None,
        "media_type": None,
        "food_items": qty_map,
    })
    await state.update_data(prizes=prizes, gw_food_selected=[], gw_food_qty_map={})
    await state.set_state(GiveawayCreateStates.prize_type)
    await _show_prize_navigation(msg, state, current_place, winner_count, prizes)


# ========== ПЕТ ==========

@router.message(GiveawayCreateStates.prize_pet_name)
async def gw_pet_name_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено.")
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Введите имя текстом.")
        return
    await state.update_data(gw_pet_name=name)
    await state.set_state(GiveawayCreateStates.prize_pet_income)
    await message.answer('💰 Введите доход пета в формате "1 222 333":')


@router.message(GiveawayCreateStates.prize_pet_income)
async def gw_pet_income_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено.")
        return
    import re as _re
    raw = _re.sub(r'[\s]', '', message.text or "")
    if not raw.isdigit():
        await message.answer("❌ Введите число (можно с пробелами): например 1 222 333")
        return
    income_fmt = f"{int(raw):,}".replace(",", " ")
    await state.update_data(gw_pet_income=income_fmt)
    await state.set_state(GiveawayCreateStates.prize_pet_mutation)
    await message.answer("🧬 Выберите мутацию пета:", reply_markup=_pet_mutation_keyboard_gw())


@router.callback_query(F.data.startswith("gw_pet_mut_"), GiveawayCreateStates.prize_pet_mutation)
async def gw_pet_mutation_select(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    mut_key = callback.data[len("gw_pet_mut_"):]
    if mut_key not in PET_MUT_BY_KEY:
        await callback.answer("❌ Неизвестная мутация")
        return
    await state.update_data(gw_pet_mutation=mut_key)
    await state.set_state(GiveawayCreateStates.prize_pet_weather)
    await callback.message.answer("🌤 Выберите погоду пета:", reply_markup=_pet_weather_keyboard_gw())
    await callback.answer()


@router.callback_query(F.data.startswith("gw_pet_wth_"), GiveawayCreateStates.prize_pet_weather)
async def gw_pet_weather_select(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    wth_key = callback.data[len("gw_pet_wth_"):]
    weather = None if wth_key == "none" else wth_key
    await state.update_data(gw_pet_weather=weather)
    await state.set_state(GiveawayCreateStates.prize_pet_coeff)
    await callback.message.answer('✖️ Введите коэффициент в формате "1.99":')
    await callback.answer()


@router.message(GiveawayCreateStates.prize_pet_coeff)
async def gw_pet_coeff_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Отменено.")
        return
    coeff_raw = (message.text or "").strip().replace(",", ".")
    try:
        float(coeff_raw)
    except ValueError:
        await message.answer('❌ Введите число в формате "1.99"')
        return

    await state.update_data(gw_pet_coeff=coeff_raw)
    await state.set_state(GiveawayCreateStates.prize_pet_photo)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить (без фото)", callback_data="gw_pet_photo_skip")],
    ])
    await message.answer(
        "📸 Отправьте фото пета для приза (будет показано победителю)\n"
        "или нажмите <b>Пропустить</b> если фото не нужно.\n\n"
        "❌ /cancel — отменить",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _save_pet_prize(data: dict, file_id: str | None, media_type: str | None) -> tuple[list, int, int]:
    """Собрать и добавить приз-пет в список призов. Возвращает (prizes, current_place, winner_count)."""
    current_place = data.get("current_place", 1)
    winner_count = data.get("winner_count", 1)
    prizes: list = data.get("prizes", [])
    pet_name = data.get("gw_pet_name", "")
    income = data.get("gw_pet_income", "")
    mutation_key = data.get("gw_pet_mutation", "")
    weather_key = data.get("gw_pet_weather")
    coeff_raw = data.get("gw_pet_coeff", "1.0")
    full_name = _pet_full_name(pet_name, income, mutation_key, weather_key, coeff_raw, "RUS")
    prizes.append({
        "place": current_place,
        "prize_type": "pet",
        "name": full_name,
        "description": None,
        "media_file_id": file_id,
        "media_type": media_type,
        "pet_income": income,
        "pet_mutation": mutation_key,
        "pet_weather": weather_key,
        "pet_coeff": coeff_raw,
    })
    return prizes, current_place, winner_count


@router.message(GiveawayCreateStates.prize_pet_photo)
async def gw_pet_photo_receive(message: Message, state: FSMContext):
    """Получить фото пета-приза."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Создание розыгрыша отменено.")
        return

    file_id, media_type = _extract_media(message)
    if not file_id:
        await message.answer(
            "❌ Пожалуйста, отправьте фото пета или нажмите <b>Пропустить</b>.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить (без фото)", callback_data="gw_pet_photo_skip")],
            ]),
        )
        return

    data = await state.get_data()
    prizes, current_place, winner_count = _save_pet_prize(data, file_id, media_type)
    await state.update_data(prizes=prizes)
    await state.set_state(GiveawayCreateStates.prize_type)
    await _show_prize_navigation(message, state, current_place, winner_count, prizes)


@router.callback_query(F.data == "gw_pet_photo_skip", GiveawayCreateStates.prize_pet_photo)
async def gw_pet_photo_skip(callback: CallbackQuery, state: FSMContext):
    """Пропустить фото пета-приза."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    data = await state.get_data()
    prizes, current_place, winner_count = _save_pet_prize(data, None, None)
    await state.update_data(prizes=prizes)
    await state.set_state(GiveawayCreateStates.prize_type)
    await callback.message.answer("✅ Фото пропущено.")
    await _show_prize_navigation(callback.message, state, current_place, winner_count, prizes)
    await callback.answer()


# ========== НАВИГАЦИЯ ПО ПРИЗАМ ==========

async def _show_prize_navigation(
    message: Message, state: FSMContext,
    current_place: int, winner_count: int, prizes: list
):
    """Показать навигацию по призам и предложить добавить ещё или перейти дальше."""
    place_prizes = [p for p in prizes if p["place"] == current_place]
    prizes_text = "\n".join(
        f"  • {_prize_display_name(p)}" for p in place_prizes
    ) or "  (нет призов)"

    place_emoji = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, winner_count + 1)]
    emoji = place_emoji[current_place - 1] if current_place <= len(place_emoji) else f"{current_place}."

    text = (
        f"<b>{emoji} {current_place} место</b>\n"
        f"Текущие призы:\n{prizes_text}\n\n"
        "Добавьте ещё приз для этого места или нажмите <b>Далее</b>."
    )

    buttons = []
    if current_place > 1:
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"gw_prize_prev_{current_place}"))
    if current_place < winner_count:
        buttons.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"gw_prize_next_{current_place}"))
    else:
        buttons.append(InlineKeyboardButton(text="✅ Завершить призы", callback_data="gw_prize_done"))

    row_add = [InlineKeyboardButton(text="➕ Добавить ещё приз", callback_data="gw_prize_add_more")]
    row_skip = [InlineKeyboardButton(text="⏭ Пропустить место", callback_data="gw_prize_skip")]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons, row_add, row_skip])
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(GiveawayCreateStates.prize_type)


@router.callback_query(F.data == "gw_prize_add_more", GiveawayCreateStates.prize_type)
async def gw_prize_add_more(callback: CallbackQuery, state: FSMContext):
    """Добавить ещё один приз для текущего места."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    data = await state.get_data()
    place = data.get("current_place", 1)
    place_emoji = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 20)]
    emoji = place_emoji[place - 1] if place <= len(place_emoji) else f"{place}."
    await callback.message.answer(
        f"<b>{emoji} {place} место — добавить ещё приз:</b>",
        parse_mode="HTML",
        reply_markup=_prize_type_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "gw_prize_skip", GiveawayCreateStates.prize_type)
async def gw_prize_skip(callback: CallbackQuery, state: FSMContext):
    """Пропустить место."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await _advance_prize_place(callback, state)


@router.callback_query(F.data.startswith("gw_prize_next_"), GiveawayCreateStates.prize_type)
async def gw_prize_next(callback: CallbackQuery, state: FSMContext):
    """Перейти к следующему месту."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await _advance_prize_place(callback, state)


@router.callback_query(F.data.startswith("gw_prize_prev_"), GiveawayCreateStates.prize_type)
async def gw_prize_prev(callback: CallbackQuery, state: FSMContext):
    """Вернуться к предыдущему месту."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    data = await state.get_data()
    current_place = data.get("current_place", 1)
    prev_place = max(1, current_place - 1)
    await state.update_data(current_place=prev_place)

    prizes = data.get("prizes", [])
    winner_count = data.get("winner_count", 1)
    place_prizes = [p for p in prizes if p["place"] == prev_place]
    prizes_text = "\n".join(f"  • {_prize_display_name(p)}" for p in place_prizes) or "  (нет призов)"
    place_emoji = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 20)]
    emoji = place_emoji[prev_place - 1] if prev_place <= len(place_emoji) else f"{prev_place}."

    await callback.message.answer(
        f"<b>{emoji} {prev_place} место</b>\n"
        f"Текущие призы:\n{prizes_text}\n\n"
        "Добавьте приз или нажмите «Далее».",
        parse_mode="HTML",
        reply_markup=_prize_type_keyboard()
    )
    await callback.answer()


async def _advance_prize_place(callback: CallbackQuery, state: FSMContext):
    """Перейти к следующему месту или завершить призы."""
    data = await state.get_data()
    current_place = data.get("current_place", 1)
    winner_count = data.get("winner_count", 1)
    next_place = current_place + 1

    if current_place < winner_count:
        await state.update_data(current_place=next_place)
        place_emoji = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, winner_count + 1)]
        emoji = place_emoji[next_place - 1] if next_place <= len(place_emoji) else f"{next_place}."
        prizes = data.get("prizes", [])
        place_prizes = [p for p in prizes if p["place"] == next_place]
        prizes_text = "\n".join(f"  • {_prize_display_name(p)}" for p in place_prizes) or "  (нет призов)"
        await callback.message.answer(
            f"<b>{emoji} {next_place} место — выберите тип приза:</b>\n\n"
            f"Текущие призы:\n{prizes_text}",
            parse_mode="HTML",
            reply_markup=_prize_type_keyboard()
        )
    else:
        await _go_to_end_type(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "gw_prize_done", GiveawayCreateStates.prize_type)
async def gw_prize_done(callback: CallbackQuery, state: FSMContext):
    """Завершить добавление призов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await _go_to_end_type(callback.message, state)
    await callback.answer()


async def _go_to_end_type(message: Message, state: FSMContext):
    """Перейти к выбору способа завершения."""
    await state.set_state(GiveawayCreateStates.end_type)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏰ По времени", callback_data="gw_end_time"),
            InlineKeyboardButton(text="👥 По кол-ву участников", callback_data="gw_end_count")
        ]
    ])
    await message.answer(
        "✅ Призы сохранены!\n\n"
        "<b>Шаг 7/10 — Способ завершения</b>\n\n"
        "Как завершить розыгрыш?",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "gw_end_time", GiveawayCreateStates.end_type)
async def gw_end_time(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await state.update_data(end_type="time")
    await state.set_state(GiveawayCreateStates.end_time)
    await callback.message.answer(
        "<b>Шаг 8/10 — Дата и время завершения (МСК)</b>\n\n"
        "Введите дату и время в формате:\n"
        "<code>дд.мм.гггг чч:мм</code>\n\n"
        "Например: <code>25.12.2025 20:00</code>\n\n"
        "❌ /cancel — отменить",
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "gw_end_count", GiveawayCreateStates.end_type)
async def gw_end_count(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await state.update_data(end_type="count")
    await state.set_state(GiveawayCreateStates.end_count)
    await callback.message.answer(
        "<b>Шаг 8/10 — Количество участников для завершения</b>\n\n"
        "Введите число участников:\n"
        "Например: <code>100</code>\n\n"
        "❌ /cancel — отменить",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(GiveawayCreateStates.end_time)
async def gw_step_end_time(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Создание розыгрыша отменено.")
        return

    raw = message.text.strip()
    try:
        datetime.strptime(raw, "%d.%m.%Y %H:%M")
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Введите дату в виде:\n<code>дд.мм.гггг чч:мм</code>",
            parse_mode="HTML"
        )
        return

    await state.update_data(end_value=raw)
    await _show_confirm(message, state)


@router.message(GiveawayCreateStates.end_count)
async def gw_step_end_count(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 Создание розыгрыша отменено.")
        return

    if not message.text.strip().isdigit() or int(message.text.strip()) < 1:
        await message.answer("❌ Введите целое число больше 0.")
        return

    await state.update_data(end_value=message.text.strip())
    await _show_confirm(message, state)


async def _show_confirm(message: Message, state: FSMContext):
    """Показать итоговое подтверждение."""
    data = await state.get_data()
    await state.set_state(GiveawayCreateStates.confirm)

    end_type = data.get("end_type")
    end_value = data.get("end_value")
    prizes = data.get("prizes", [])
    winner_count = data.get("winner_count", 1)
    channels = data.get("required_channels", [])

    prizes_summary = ""
    for place in range(1, winner_count + 1):
        place_prizes = [p for p in prizes if p["place"] == place]
        if place_prizes:
            prizes_summary += f"\n  {place} место: " + ", ".join(p["name"] for p in place_prizes)
        else:
            prizes_summary += f"\n  {place} место: (без приза)"

    end_str = f"По времени: {end_value} МСК" if end_type == "time" else f"По кол-ву: {end_value} участников"

    text = (
        "📋 <b>Подтверждение создания розыгрыша</b>\n\n"
        f"🇷🇺 Заголовок: <b>{data.get('title_ru')}</b>\n"
        f"🇺🇸 Заголовок: <b>{data.get('title_en')}</b>\n"
        f"🔘 Кнопка: <b>{data.get('button_text')}</b>\n"
        f"🏆 Победителей: <b>{winner_count}</b>\n"
        f"📢 Каналы: {', '.join(channels) if channels else 'только @buildazoo_chat'}\n"
        f"⏱ Завершение: {end_str}\n"
        f"🎁 Призы:{prizes_summary}\n\n"
        "Создать розыгрыш?"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Создать", callback_data="gw_confirm_create"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="gw_cancel_create")
        ]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "gw_cancel_create", GiveawayCreateStates.confirm)
async def gw_cancel_create(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🚫 Создание розыгрыша отменено.")
    await callback.answer()


@router.callback_query(F.data == "gw_confirm_create", GiveawayCreateStates.confirm)
async def gw_confirm_create(callback: CallbackQuery, state: FSMContext):
    """Финальное создание розыгрыша и публикация."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    data = await state.get_data()
    await state.clear()

    # Сохраняем розыгрыш в БД
    giveaway_id = db.create_giveaway(
        title_ru=data.get("title_ru", ""),
        text_ru=data.get("text_ru", ""),
        media_file_id_ru=data.get("media_file_id_ru"),
        media_type_ru=data.get("media_type_ru"),
        title_en=data.get("title_en", ""),
        text_en=data.get("text_en", ""),
        media_file_id_en=data.get("media_file_id_en"),
        media_type_en=data.get("media_type_en"),
        button_text=data.get("button_text", "🎰 Участвовать"),
        required_channels=data.get("required_channels", []),
        winner_count=data.get("winner_count", 1),
        end_type=data.get("end_type", "time"),
        end_value=data.get("end_value", ""),
        created_by=callback.from_user.id
    )

    if not giveaway_id:
        await callback.message.edit_text("❌ Ошибка создания розыгрыша.")
        await callback.answer()
        return

    # Сохраняем призы
    prizes = data.get("prizes", [])
    for prize in prizes:
        db.add_giveaway_prize(
            giveaway_id=giveaway_id,
            place=prize["place"],
            name=prize["name"],
            description=prize.get("description"),
            media_file_id=prize.get("media_file_id"),
            media_type=prize.get("media_type"),
            prize_type=prize.get("prize_type", "item"),
            food_items=prize.get("food_items"),
            pet_income=prize.get("pet_income"),
            pet_mutation=prize.get("pet_mutation"),
            pet_weather=prize.get("pet_weather"),
            pet_coeff=prize.get("pet_coeff"),
        )

    giveaway = db.get_giveaway(giveaway_id)
    button_text = giveaway["button_text"]
    keyboard = _build_participate_keyboard(giveaway_id, button_text, 0)

    # Публикуем в группу (русская версия)
    ru_text = _giveaway_post_text(giveaway, "RUS")
    group_msg = await _send_media_message(
        callback.bot,
        Config.REQUIRED_GROUP_ID,
        giveaway.get("media_file_id_ru"),
        giveaway.get("media_type_ru"),
        ru_text,
        reply_markup=keyboard
    )
    if group_msg:
        db.set_giveaway_message_id(giveaway_id, group_msg.message_id)

    # Рассылаем всем пользователям в ЛС
    all_users = db.get_all_users()
    sent = 0
    for user in all_users:
        uid = user["user_id"]
        lang = user.get("language", "RUS")
        post_text = _giveaway_post_text(giveaway, lang)
        file_id = giveaway.get("media_file_id_ru") if lang == "RUS" else giveaway.get("media_file_id_en")
        media_type = giveaway.get("media_type_ru") if lang == "RUS" else giveaway.get("media_type_en")
        try:
            await _send_media_message(
                callback.bot, uid, file_id, media_type, post_text,
                reply_markup=keyboard
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await callback.message.edit_text(
        f"✅ Розыгрыш #{giveaway_id} создан!\n"
        f"📢 Опубликован в группе.\n"
        f"📨 Разослан {sent} пользователям.",
        parse_mode="HTML"
    )
    # Лог: создание розыгрыша
    await log_giveaway_created(
        callback.bot,
        admin_id=callback.from_user.id,
        admin_name=callback.from_user.full_name,
        giveaway_id=giveaway_id,
        title=data.get("title_ru", ""),
        winner_count=data.get("winner_count", 1),
        end_type=data.get("end_type", "time"),
        end_value=data.get("end_value", ""),
    )
    await callback.answer()
