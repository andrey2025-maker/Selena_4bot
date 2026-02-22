"""
handlers/trade.py - Система обмена предметами через бота
Пользователи инициируют обмен, указывают Roblox-ник и второго участника.
Диалог ведётся через бота, сообщения дублируются в тему админской группы.
Администратор может завершить обмен командой /stop в теме.
"""

import asyncio
import logging
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import Database
from config import Config
from utils.log_events import log_trade_session_start, log_trade_session_stop
from utils.messages import locale_manager

logger = logging.getLogger(__name__)
router = Router()
db = Database()

from handlers.admin_common import ADMIN_IDS, is_admin


# ========== FSM ==========

class TradeStates(StatesGroup):
    waiting_for_own_nick = State()         # пользователь вводит свой Roblox-ник
    waiting_for_partner = State()          # пользователь указывает второго участника
    in_trade = State()                     # активный диалог обмена
    waiting_for_nick_then_trade = State()  # второй участник вводит ник перед стартом
    waiting_for_confirmation = State()     # второй участник подтверждает/отклоняет обмен


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def _user_link(user_id: int, display: str) -> str:
    """HTML-ссылка на пользователя Telegram."""
    return f'<a href="tg://user?id={user_id}">{display}</a>'


def _roblox_link(nick: str) -> str:
    """HTML-ссылка на профиль Roblox."""
    return f'<a href="https://www.roblox.com/users/profile?username={nick}">{nick}</a>'


async def _get_admins(bot: Bot) -> list[int]:
    """Получить список ID администраторов."""
    return ADMIN_IDS


async def _notify_admins(bot: Bot, text: str, reply_markup: InlineKeyboardMarkup = None):
    """Отправить уведомление всем администраторам в ЛС."""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception as e:
            logger.warning(f"Failed to notify admin {admin_id}: {e}")


async def _send_to_topic(bot: Bot, topic_id: int, text: str):
    """Отправить сообщение в тему (топик) группы обменов."""
    if not Config.TRADE_ADMIN_GROUP_ID:
        logger.warning("TRADE_ADMIN_GROUP_ID не задан — сообщение в топик не отправлено")
        return
    try:
        await bot.send_message(
            chat_id=Config.TRADE_ADMIN_GROUP_ID,
            message_thread_id=topic_id,
            text=text,
        )
    except Exception as e:
        logger.warning(f"Failed to send to topic {topic_id}: {e}")


async def _create_topic(bot: Bot, user1_id: int, user2_id: int, nick1: str, nick2: str) -> Optional[int]:
    """Создать тему в группе обменов и закрепить первое сообщение."""
    if not Config.TRADE_ADMIN_GROUP_ID:
        logger.warning("TRADE_ADMIN_GROUP_ID не задан — топик не создан")
        return None
    try:
        topic_name = f"{user1_id} и {user2_id}"
        forum_topic = await bot.create_forum_topic(
            chat_id=Config.TRADE_ADMIN_GROUP_ID,
            name=topic_name,
        )
        topic_id = forum_topic.message_thread_id

        link1 = _user_link(user1_id, nick1)
        link2 = _user_link(user2_id, nick2)
        header_text = (
            f"🔄 <b>Обмен между {_roblox_link(nick1)} и {_roblox_link(nick2)}</b>\n\n"
            f"👤 Участник 1: {link1} (Roblox: {nick1})\n"
            f"👤 Участник 2: {link2} (Roblox: {nick2})"
        )
        msg = await bot.send_message(
            chat_id=Config.TRADE_ADMIN_GROUP_ID,
            message_thread_id=topic_id,
            text=header_text,
        )
        try:
            await bot.pin_chat_message(
                chat_id=Config.TRADE_ADMIN_GROUP_ID,
                message_id=msg.message_id,
                disable_notification=True,
            )
        except Exception as e:
            logger.warning(f"Could not pin message in topic: {e}")

        return topic_id
    except Exception as e:
        logger.error(f"Failed to create forum topic: {e}")
        return None


async def _start_trade_dialog(bot: Bot, session_id: int, user1_id: int, user2_id: int,
                               nick1: str, nick2: str, topic_id: int, state1: FSMContext, state2: FSMContext):
    """Запустить диалог обмена для обоих участников."""
    trade_data = {
        "session_id": session_id,
        "partner_id": user2_id,
        "my_nick": nick1,
        "topic_id": topic_id,
    }
    await state1.set_state(TradeStates.in_trade)
    await state1.update_data(**trade_data)

    trade_data2 = {
        "session_id": session_id,
        "partner_id": user1_id,
        "my_nick": nick2,
        "topic_id": topic_id,
    }
    await state2.set_state(TradeStates.in_trade)
    await state2.update_data(**trade_data2)

    user1_data = db.get_user(user1_id)
    user2_data = db.get_user(user2_id)
    lang1 = (user1_data or {}).get("language", "RUS")
    lang2 = (user2_data or {}).get("language", "RUS")

    await bot.send_message(user1_id, locale_manager.get_text("ru" if lang1 == "RUS" else "en", "trade.trade_started"), parse_mode="HTML")
    await bot.send_message(user2_id, locale_manager.get_text("ru" if lang2 == "RUS" else "en", "trade.trade_started"), parse_mode="HTML")

    await _notify_admins(
        bot,
        f"🔄 <b>Новый запрос на обмен!</b>\n\n"
        f"👤 Участник 1: {_user_link(user1_id, nick1)} (Roblox: {nick1})\n"
        f"👤 Участник 2: {_user_link(user2_id, nick2)} (Roblox: {nick2})\n\n"
        f"💬 Диалог ведётся в теме группы обменов.",
    )


# ========== ОБРАБОТЧИКИ ПОЛЬЗОВАТЕЛЕЙ ==========

async def _trade_start_flow(user_id: int, reply_target, state: FSMContext, bot: Bot):
    """Общая логика старта обмена — используется и из reply-кнопки, и из инлайн-кнопки."""
    existing_nick = db.get_roblox_nick(user_id)
    user_data = db.get_user(user_id)
    lang = (user_data or {}).get("language", "RUS")

    lc = "ru" if lang == "RUS" else "en"
    if existing_nick:
        text = locale_manager.get_text(lc, "trade.enter_partner").format(nick=existing_nick)
        await state.set_state(TradeStates.waiting_for_partner)
        await state.update_data(my_nick=existing_nick)
    else:
        text = locale_manager.get_text(lc, "trade.enter_own_nick_cancel")
        await state.set_state(TradeStates.waiting_for_own_nick)
        await state.update_data(trade_flow=True)

    await reply_target.answer(text, parse_mode="HTML")


@router.message(F.chat.type == "private", F.text.in_(["🔄 Обмен", "🔄 Trade"]))
async def trade_start_reply(message: Message, state: FSMContext, bot: Bot):
    """Пользователь нажал кнопку Обмен в reply-клавиатуре."""
    await _trade_start_flow(message.from_user.id, message, state, bot)


@router.callback_query(F.data == "inv_trade_start")
async def trade_start(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Пользователь нажал кнопку Обмен в инвентаре (inline)."""
    await callback.answer()
    await _trade_start_flow(callback.from_user.id, callback.message, state, bot)


@router.message(TradeStates.waiting_for_own_nick)
async def trade_receive_own_nick(message: Message, state: FSMContext, bot: Bot):
    """Пользователь вводит свой Roblox-ник."""
    user_id = message.from_user.id
    user_data = db.get_user(user_id)
    lang = (user_data or {}).get("language", "RUS")

    lc = "ru" if lang == "RUS" else "en"

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(locale_manager.get_text(lc, "trade.cancelled"))
        return

    nick = message.text.strip().lstrip("@") if message.text else ""

    if not nick:
        await message.answer(locale_manager.get_text(lc, "trade.enter_nick_text_only"))
        return

    db.set_roblox_nick(user_id, nick)
    data = await state.get_data()
    user_data = db.get_user(user_id)
    lang = (user_data or {}).get("language", "RUS")
    lc = "ru" if lang == "RUS" else "en"

    if data.get("trade_flow"):
        text = locale_manager.get_text(lc, "trade.nick_saved_enter_partner").format(nick=nick)
        await state.set_state(TradeStates.waiting_for_partner)
        await state.update_data(my_nick=nick)
        await message.answer(text, parse_mode="HTML")
    else:
        await message.answer(locale_manager.get_text(lc, "trade.nick_saved").format(nick=nick), parse_mode="HTML")
        await state.clear()


@router.message(TradeStates.waiting_for_partner)
async def trade_receive_partner(message: Message, state: FSMContext, bot: Bot):
    """Пользователь указывает второго участника обмена."""
    user_id = message.from_user.id
    user_data = db.get_user(user_id)
    lang = (user_data or {}).get("language", "RUS")

    lc = "ru" if lang == "RUS" else "en"

    # /cancel — отмена
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(locale_manager.get_text(lc, "trade.cancelled"))
        return

    partner_id: Optional[int] = None

    # 1. Пересланное сообщение — новый API (forward_origin) и старый (forward_from)
    if message.forward_from:
        partner_id = message.forward_from.id
    elif message.forward_origin:
        # MessageOriginUser — пользователь разрешил пересылку
        origin = message.forward_origin
        if hasattr(origin, "sender_user") and origin.sender_user:
            partner_id = origin.sender_user.id
        # Если пользователь скрыл пересылки — partner_id останется None,
        # сообщим об этом отдельно ниже
    elif message.text:
        text = message.text.strip()
        if text.startswith("@"):
            # Ищем по username в базе данных (bot.get_chat работает только для каналов/групп)
            username = text.lstrip("@").lower()
            all_users = db.get_all_users()
            for u in all_users:
                if u.get("username") and u["username"].lower() == username:
                    partner_id = u["user_id"]
                    break
            if not partner_id:
                await message.answer(
                    locale_manager.get_text(lc, "trade.partner_not_found_username").format(username=text.lstrip('@'))
                )
                return
        else:
            try:
                partner_id = int(text)
            except ValueError:
                partner_id = None

    if not partner_id:
        if message.forward_origin:
            await message.answer(locale_manager.get_text(lc, "trade.user_hidden_forwarding"))
        else:
            await message.answer(locale_manager.get_text(lc, "trade.user_not_identified"))
        return

    if partner_id == user_id:
        await message.answer(locale_manager.get_text(lc, "trade.cannot_trade_self"))
        return

    partner_data = db.get_user(partner_id)
    if not partner_data:
        bot_info = await bot.get_me()
        invite_link = f"https://t.me/{bot_info.username}"
        await message.answer(locale_manager.get_text(lc, "trade.user_not_registered").format(link=invite_link))
        await state.clear()
        return

    data = await state.get_data()
    my_nick = data.get("my_nick", "")
    partner_nick = db.get_roblox_nick(partner_id)

    if not partner_nick:
        partner_lang = partner_data.get("language", "RUS")

        partner_lc = "ru" if partner_lang == "RUS" else "en"
        notify_text = locale_manager.get_text(partner_lc, "trade.partner_needs_nick").format(
            link=_user_link(user_id, f'@{my_nick}')
        )

        from aiogram.fsm.storage.base import StorageKey
        bot_id = (await bot.get_me()).id
        key_partner = StorageKey(bot_id=bot_id, chat_id=partner_id, user_id=partner_id)
        state_partner = FSMContext(storage=state.storage, key=key_partner)

        await state_partner.set_state(TradeStates.waiting_for_nick_then_trade)
        await state_partner.update_data(initiator_id=user_id, initiator_nick=my_nick)

        try:
            await bot.send_message(partner_id, notify_text)
        except Exception as e:
            logger.warning(f"Cannot send message to partner {partner_id}: {e}")

        # Таймаут 24 часа: если партнёр так и не ввёл ник — очищаем состояние
        async def _nick_timeout():
            await asyncio.sleep(86400)
            cur_state = await state_partner.get_state()
            if cur_state == TradeStates.waiting_for_nick_then_trade:
                await state_partner.clear()
        asyncio.create_task(_nick_timeout())

        await state.clear()

        await message.answer(locale_manager.get_text(lc, "trade.request_sent_waiting_nick"))
        return

    await _launch_trade(bot, message, state, user_id, partner_id, my_nick, partner_nick)


async def _launch_trade(bot: Bot, message: Message, state: FSMContext,
                        user1_id: int, user2_id: int, nick1: str, nick2: str):
    """Отправить второму участнику запрос на подтверждение обмена."""
    from aiogram.fsm.storage.base import StorageKey

    user2_data = db.get_user(user2_id)
    partner_lang = (user2_data or {}).get("language", "RUS")
    user1_data = db.get_user(user1_id)
    initiator_lang = (user1_data or {}).get("language", "RUS")

    partner_lc = "ru" if partner_lang == "RUS" else "en"
    confirm_text = locale_manager.get_text(partner_lc, "trade.confirm_request").format(
        link=_user_link(user1_id, nick1), nick=nick1
    )
    yes_btn = "✅ Да" if partner_lang == "RUS" else "✅ Yes"
    no_btn = "❌ Нет" if partner_lang == "RUS" else "❌ No"

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=yes_btn, callback_data=f"trade_confirm_yes_{user1_id}_{user2_id}"),
        InlineKeyboardButton(text=no_btn,  callback_data=f"trade_confirm_no_{user1_id}_{user2_id}"),
    ]])

    # Ставим второму участнику состояние ожидания подтверждения
    bot_id = (await bot.get_me()).id
    key2 = StorageKey(bot_id=bot_id, chat_id=user2_id, user_id=user2_id)
    state2 = FSMContext(storage=state.storage, key=key2)
    await state2.set_state(TradeStates.waiting_for_confirmation)
    await state2.update_data(initiator_id=user1_id, initiator_nick=nick1,
                              partner_nick=nick2)

    try:
        await bot.send_message(user2_id, confirm_text,
                               reply_markup=confirm_kb, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Cannot send trade confirmation to {user2_id}: {e}")

    # Таймаут 10 минут: если партнёр не ответил — очищаем его состояние
    async def _confirmation_timeout():
        await asyncio.sleep(600)
        cur_state = await state2.get_state()
        if cur_state == TradeStates.waiting_for_confirmation:
            await state2.clear()
            try:
                user2_data = db.get_user(user2_id)
                lang2 = (user2_data or {}).get("language", "RUS")
                lc2 = "ru" if lang2 == "RUS" else "en"
                await bot.send_message(user2_id, locale_manager.get_text(lc2, "trade.confirmation_timeout"))
            except Exception:
                pass
    asyncio.create_task(_confirmation_timeout())

    # Сообщаем инициатору что запрос отправлен
    await state.clear()
    init_lc = "ru" if initiator_lang == "RUS" else "en"
    await message.answer(
        locale_manager.get_text(init_lc, "trade.request_sent_waiting_confirm").format(link=_roblox_link(nick2)),
        parse_mode="HTML",
    )


async def _do_start_trade(bot: Bot, storage, bot_id: int,
                          user1_id: int, user2_id: int, nick1: str, nick2: str,
                          state2: FSMContext):
    """Фактически запустить обмен после подтверждения."""
    from aiogram.fsm.storage.base import StorageKey

    existing = db.get_trade_session(user1_id, user2_id)
    topic_id = existing["topic_id"] if existing and existing.get("topic_id") else None
    topic_existed = topic_id is not None

    if not topic_id:
        topic_id = await _create_topic(bot, user1_id, user2_id, nick1, nick2)
    else:
        # Топик уже существует — пишем новое сообщение об обмене
        await _send_to_topic(
            bot, topic_id,
            f"🔄 <b>Обмен между {nick1} и {nick2}</b>",
        )

    session_id = db.create_trade_session(user1_id, user2_id, topic_id)

    key1 = StorageKey(bot_id=bot_id, chat_id=user1_id, user_id=user1_id)
    state1 = FSMContext(storage=storage, key=key1)

    await _start_trade_dialog(bot, session_id, user1_id, user2_id, nick1, nick2, topic_id, state1, state2)


@router.callback_query(F.data.startswith("trade_confirm_yes_"))
async def trade_confirm_yes(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Второй участник принял обмен."""
    parts = callback.data.split("_")
    user1_id = int(parts[3])
    user2_id = int(parts[4])

    if callback.from_user.id != user2_id:
        await callback.answer("❌ Это не ваш запрос.", show_alert=True)
        return

    data = await state.get_data()
    nick1 = data.get("initiator_nick", "")
    nick2 = data.get("partner_nick", "")

    await callback.message.edit_reply_markup(reply_markup=None)

    user2_data = db.get_user(user2_id)
    lang2 = (user2_data or {}).get("language", "RUS")
    lc2 = "ru" if lang2 == "RUS" else "en"
    await callback.message.answer(locale_manager.get_text(lc2, "trade.accepted"))

    bot_id = (await bot.get_me()).id
    await _do_start_trade(bot, state.storage, bot_id,
                          user1_id, user2_id, nick1, nick2, state)
    await log_trade_session_start(
        bot,
        user1_id=user1_id, user1_name=nick1,
        user2_id=user2_id, user2_name=nick2,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("trade_confirm_no_"))
async def trade_confirm_no(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Второй участник отклонил обмен."""
    parts = callback.data.split("_")
    user1_id = int(parts[3])
    user2_id = int(parts[4])

    if callback.from_user.id != user2_id:
        await callback.answer("❌ Это не ваш запрос.", show_alert=True)
        return

    data = await state.get_data()
    nick1 = data.get("initiator_nick", "")

    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)

    user2_data = db.get_user(user2_id)
    lang2 = (user2_data or {}).get("language", "RUS")
    lc2 = "ru" if lang2 == "RUS" else "en"
    await callback.message.answer(locale_manager.get_text(lc2, "trade.declined_by_you"))

    # Уведомляем инициатора
    user1_data = db.get_user(user1_id)
    lang1 = (user1_data or {}).get("language", "RUS")
    lc1 = "ru" if lang1 == "RUS" else "en"
    try:
        await bot.send_message(
            user1_id,
            locale_manager.get_text(lc1, "trade.declined_by_partner").format(link=_roblox_link(nick1)),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Cannot notify initiator {user1_id} about declined trade: {e}")

    await callback.answer()


@router.message(TradeStates.waiting_for_nick_then_trade)
async def trade_partner_enters_nick(message: Message, state: FSMContext, bot: Bot):
    """Второй участник вводит ник, после чего обмен стартует."""
    user_id = message.from_user.id
    nick = message.text.strip().lstrip("@") if message.text else ""

    user_data = db.get_user(user_id)
    lang = (user_data or {}).get("language", "RUS")
    lc = "ru" if lang == "RUS" else "en"

    if not nick:
        await message.answer(locale_manager.get_text(lc, "trade.enter_nick_text_only"))
        return

    db.set_roblox_nick(user_id, nick)
    data = await state.get_data()
    initiator_id = data.get("initiator_id")
    initiator_nick = data.get("initiator_nick")

    if not initiator_id or not initiator_nick:
        await message.answer(locale_manager.get_text(lc, "trade.trade_data_expired"))
        await state.clear()
        return

    await message.answer(locale_manager.get_text(lc, "trade.nick_saved_short").format(nick=nick), parse_mode="HTML")

    # Теперь запускаем флоу с подтверждением (инициатор = initiator_id, партнёр = user_id)
    await _launch_trade(bot, message, state, initiator_id, user_id, initiator_nick, nick)


@router.message(Command("stop"), F.chat.type == "private")
async def user_stop_trade(message: Message, state: FSMContext, bot: Bot):
    """Пользователь завершает обмен командой /stop в ЛС."""
    user_id = message.from_user.id
    current = await state.get_state()

    if current != TradeStates.in_trade:
        user_data = db.get_user(user_id)
        lang = (user_data or {}).get("language", "RUS")
        lc = "ru" if lang == "RUS" else "en"
        await message.answer(locale_manager.get_text(lc, "trade.no_active_trade"))
        return

    data = await state.get_data()
    partner_id = data.get("partner_id")
    session_id = data.get("session_id")
    topic_id = data.get("topic_id")

    user_data = db.get_user(user_id)
    lang = (user_data or {}).get("language", "RUS")
    lc = "ru" if lang == "RUS" else "en"

    # Завершаем сессию в БД
    if session_id:
        db.finish_trade_session(session_id)

    # Очищаем состояние инициатора
    await state.clear()

    await message.answer(locale_manager.get_text(lc, "trade.you_ended"))

    # Уведомляем партнёра
    if partner_id:
        partner_data = db.get_user(partner_id)
        partner_lang = (partner_data or {}).get("language", "RUS")
        partner_lc = "ru" if partner_lang == "RUS" else "en"
        end_text = locale_manager.get_text(partner_lc, "trade.partner_ended")
        try:
            await bot.send_message(partner_id, end_text)
        except Exception as e:
            logger.warning(f"Failed to notify partner {partner_id} about trade end: {e}")

        # Очищаем состояние партнёра
        from aiogram.fsm.storage.base import StorageKey
        bot_id = (await bot.get_me()).id
        key_partner = StorageKey(bot_id=bot_id, chat_id=partner_id, user_id=partner_id)
        state_partner = FSMContext(storage=state.storage, key=key_partner)
        try:
            if await state_partner.get_state() == TradeStates.in_trade:
                await state_partner.clear()
        except Exception as e:
            logger.warning(f"Failed to clear partner state: {e}")

    # Уведомляем в топик
    if topic_id:
        user_display = f"@{user_data['username']}" if user_data and user_data.get("username") else f"ID: {user_id}"
        await _send_to_topic(bot, topic_id, f"🔴 <b>Обмен завершён участником {user_display}.</b>")

    # Лог: завершение обмена пользователем
    _u1 = user_data
    _u2 = db.get_user(partner_id) if partner_id else None
    _n1 = (_u1 or {}).get("roblox_nick") or (_u1 or {}).get("username") or str(user_id)
    _n2 = (_u2 or {}).get("roblox_nick") or (_u2 or {}).get("username") or str(partner_id or "?")
    await log_trade_session_stop(
        bot,
        stopped_by_id=user_id, stopped_by_name=_n1,
        user1_id=user_id, user1_name=_n1,
        user2_id=partner_id or user_id, user2_name=_n2,
    )


@router.message(TradeStates.in_trade)
async def trade_message_handler(message: Message, state: FSMContext, bot: Bot):
    """Пользователь отправляет сообщение в рамках обмена."""
    user_id = message.from_user.id
    data = await state.get_data()
    partner_id = data.get("partner_id")
    my_nick = data.get("my_nick", "")
    topic_id = data.get("topic_id")
    session_id = data.get("session_id")

    if not partner_id or not session_id:
        user_data_tmp = db.get_user(user_id)
        lc_tmp = "ru" if (user_data_tmp or {}).get("language", "RUS") == "RUS" else "en"
        await message.answer(locale_manager.get_text(lc_tmp, "trade.session_not_found"))
        await state.clear()
        return

    session = db.get_trade_session_by_id(session_id)
    if not session or session["status"] != "active":
        user_data = db.get_user(user_id)
        lang = (user_data or {}).get("language", "RUS")
        lc = "ru" if lang == "RUS" else "en"
        await message.answer(locale_manager.get_text(lc, "trade.session_ended"))
        await state.clear()
        return

    text = message.text or message.caption or ""
    formatted = f"<b>{my_nick}:</b> {text}" if text else f"<b>{my_nick}:</b> [медиафайл]"

    # Всегда берём актуальный Roblox-ник из БД (на случай если FSM-данные устарели)
    actual_nick = db.get_roblox_nick(user_id) or my_nick or str(user_id)

    try:
        if message.text:
            await bot.send_message(partner_id, f"<b>{actual_nick}:</b> {text}", parse_mode="HTML")
        elif message.photo:
            await bot.send_photo(partner_id, message.photo[-1].file_id,
                                 caption=f"<b>{actual_nick}:</b> {message.caption or ''}",
                                 parse_mode="HTML")
        elif message.video:
            await bot.send_video(partner_id, message.video.file_id,
                                 caption=f"<b>{actual_nick}:</b> {message.caption or ''}",
                                 parse_mode="HTML")
        elif message.document:
            await bot.send_document(partner_id, message.document.file_id,
                                    caption=f"<b>{actual_nick}:</b> {message.caption or ''}",
                                    parse_mode="HTML")
        elif message.sticker:
            await bot.send_message(partner_id, f"<b>{actual_nick}:</b> [стикер]", parse_mode="HTML")
            await bot.send_sticker(partner_id, message.sticker.file_id)
        else:
            await bot.send_message(partner_id, f"<b>{actual_nick}:</b> [сообщение]", parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Failed to forward message to partner {partner_id}: {e}")

    if topic_id:
        topic_text = f"<b>{actual_nick}:</b> {text}" if text else f"<b>{actual_nick}:</b> [медиафайл]"
        try:
            if message.text:
                await _send_to_topic(bot, topic_id, topic_text)
            elif message.photo:
                await bot.send_photo(
                    Config.TRADE_ADMIN_GROUP_ID,
                    message.photo[-1].file_id,
                    caption=f"<b>{actual_nick}:</b> {message.caption or ''}",
                    message_thread_id=topic_id,
                    parse_mode="HTML",
                )
            elif message.video:
                await bot.send_video(
                    Config.TRADE_ADMIN_GROUP_ID,
                    message.video.file_id,
                    caption=f"<b>{actual_nick}:</b> {message.caption or ''}",
                    message_thread_id=topic_id,
                    parse_mode="HTML",
                )
            elif message.document:
                await bot.send_document(
                    Config.TRADE_ADMIN_GROUP_ID,
                    message.document.file_id,
                    caption=f"<b>{actual_nick}:</b> {message.caption or ''}",
                    message_thread_id=topic_id,
                    parse_mode="HTML",
                )
            else:
                await _send_to_topic(bot, topic_id, topic_text)
        except Exception as e:
            logger.warning(f"Failed to send to topic {topic_id}: {e}")



# ========== ОБРАБОТЧИКИ СООБЩЕНИЙ В ТЕМЕ ГРУППЫ (АДМИНИСТРАТОРЫ) ==========

@router.message(Command("stop"), F.chat.id == Config.TRADE_ADMIN_GROUP_ID)
async def admin_stop_trade(message: Message, bot: Bot, state: FSMContext):
    """Администратор завершает обмен командой /stop в теме группы."""
    topic_id = message.message_thread_id
    if not topic_id:
        await message.reply("❌ Эта команда работает только в теме обмена.")
        return

    session = db.get_active_trade_by_topic(topic_id)
    if not session:
        await message.reply("❌ Активный обмен в этой теме не найден.")
        return

    session_id = session["id"]
    user1_id = session["user1_id"]
    user2_id = session["user2_id"]

    db.finish_trade_session(session_id)

    await message.reply("✅ Обмен завершён администратором.")
    await _send_to_topic(bot, topic_id, "🔴 <b>Обмен завершён администратором.</b>")

    # Лог: завершение обмена администратором
    _u1 = db.get_user(user1_id)
    _u2 = db.get_user(user2_id)
    _n1 = (_u1 or {}).get("roblox_nick") or (_u1 or {}).get("username") or str(user1_id)
    _n2 = (_u2 or {}).get("roblox_nick") or (_u2 or {}).get("username") or str(user2_id)
    await log_trade_session_stop(
        bot,
        stopped_by_id=message.from_user.id,
        stopped_by_name=message.from_user.full_name,
        user1_id=user1_id, user1_name=_n1,
        user2_id=user2_id, user2_name=_n2,
    )

    for uid in [user1_id, user2_id]:
        user_data = db.get_user(uid)
        lang = (user_data or {}).get("language", "RUS")
        lc_uid = "ru" if lang == "RUS" else "en"
        end_text = locale_manager.get_text(lc_uid, "trade.completed_by_admin")
        try:
            await bot.send_message(uid, end_text)
        except Exception as e:
            logger.warning(f"Failed to notify user {uid} about trade end: {e}")

    from aiogram.fsm.storage.base import StorageKey
    bot_id = (await bot.get_me()).id
    for uid in [user1_id, user2_id]:
        key = StorageKey(bot_id=bot_id, chat_id=uid, user_id=uid)
        user_state = FSMContext(storage=state.storage, key=key)
        try:
            current = await user_state.get_state()
            if current == TradeStates.in_trade:
                await user_state.clear()
        except Exception as e:
            logger.warning(f"Failed to clear state for user {uid}: {e}")


@router.message(F.chat.id == Config.TRADE_ADMIN_GROUP_ID, F.message_thread_id.is_not(None))
async def admin_trade_message(message: Message, bot: Bot):
    """Администратор пишет в теме группы — сообщение пересылается участникам."""
    if message.from_user.is_bot:
        return

    sender_id = message.from_user.id
    if not is_admin(sender_id):
        return

    if message.text and message.text.startswith("/"):
        return

    topic_id = message.message_thread_id
    session = db.get_active_trade_by_topic(topic_id)
    if not session:
        return

    session_id = session["id"]
    user1_id = session["user1_id"]
    user2_id = session["user2_id"]

    if session.get("admin_joined") == 0:
        db.set_trade_admin_joined(session_id)
        for uid in [user1_id, user2_id]:
            user_data = db.get_user(uid)
            lang = (user_data or {}).get("language", "RUS")
            lc_uid = "ru" if lang == "RUS" else "en"
            joined_text = locale_manager.get_text(lc_uid, "trade.admin_joined_dialog")
            try:
                await bot.send_message(uid, joined_text)
            except Exception as e:
                logger.warning(f"Failed to notify user {uid} about admin join: {e}")

    text = message.text or message.caption or ""
    formatted = f"<b>Админ:</b> {text}" if text else "<b>Админ:</b> [медиафайл]"

    for uid in [user1_id, user2_id]:
        try:
            if message.text:
                await bot.send_message(uid, formatted, parse_mode="HTML")
            elif message.photo:
                await bot.send_photo(uid, message.photo[-1].file_id,
                                     caption=f"<b>Админ:</b> {message.caption or ''}",
                                     parse_mode="HTML")
            elif message.video:
                await bot.send_video(uid, message.video.file_id,
                                     caption=f"<b>Админ:</b> {message.caption or ''}",
                                     parse_mode="HTML")
            elif message.document:
                await bot.send_document(uid, message.document.file_id,
                                        caption=f"<b>Админ:</b> {message.caption or ''}",
                                        parse_mode="HTML")
            else:
                await bot.send_message(uid, formatted, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send admin message to user {uid}: {e}")


# ========== CALLBACK: ОТКРЫТЬ ИНВЕНТАРЬ ИЗ УВЕДОМЛЕНИЯ ==========

@router.callback_query(F.data.startswith("trade_inv_"))
async def trade_open_inventory(callback: CallbackQuery, bot: Bot):
    """Администратор нажал 'Открыть инвентарь' из уведомления об обмене."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    target_user_id = int(callback.data.split("_")[2])
    await callback.answer()

    items = db.get_user_inventory(target_user_id)
    user_data = db.get_user(target_user_id)
    nick = db.get_roblox_nick(target_user_id)

    display = f"@{user_data['username']}" if user_data and user_data.get("username") else f"ID: {target_user_id}"
    roblox_display = f" (Roblox: @{nick})" if nick else ""

    if not items:
        await callback.message.answer(f"📦 Инвентарь {display}{roblox_display} пуст.")
        return

    text = f"📦 <b>Инвентарь {display}{roblox_display}:</b>\n\n"
    for item in items:
        text += f"• {item['name']}"
        if item.get("quantity", 1) > 1:
            text += f" x{item['quantity']}"
        if item.get("description"):
            text += f" — {item['description']}"
        text += "\n"

    await callback.message.answer(text)
