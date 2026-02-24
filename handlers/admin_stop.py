"""
handlers/admin_stop.py — Универсальные /cancel и /stop, пересылка сообщений
пользователя администратору. Подключается отдельно, до trade_router.
"""

import logging
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import default_state
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from aiogram.filters import BaseFilter
from config import Config
from handlers.admin_common import db, is_admin, active_chats, _get_admin_id, ChatStates, user_link

logger = logging.getLogger(__name__)
router = Router()


class IsInActiveChat(BaseFilter):
    """Фильтр: пользователь находится в активном чате с администратором."""
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in active_chats


def _chat_entry(user_id: int) -> dict:
    """Вернуть запись active_chats как dict (совместимость со старым int-форматом)."""
    entry = active_chats.get(user_id)
    if entry is None:
        return {}
    if isinstance(entry, dict):
        return entry
    return {"admin_id": int(entry), "mode": "bot", "topic_id": None}


async def _end_chat(bot, user_id: int, admin_id: int, topic_id: int | None, notify_user: bool = True):
    """Завершить чат: убрать из active_chats, БД, уведомить участников."""
    user = db.get_user(user_id)
    lang_code = "ru" if (user.get("language", "RUS") if user else "RUS") == "RUS" else "en"
    end_msg = "Диалог завершен администратором." if lang_code == "ru" else "Conversation ended by administrator."

    if notify_user:
        try:
            await bot.send_message(user_id, end_msg)
        except Exception:
            pass

    # Сообщение о завершении в топик
    if topic_id and Config.CHAT_ADMIN_GROUP_ID:
        try:
            await bot.send_message(
                chat_id=Config.CHAT_ADMIN_GROUP_ID,
                message_thread_id=topic_id,
                text=f"🔴 <b>Диалог завершён</b>\n👤 {user_link(user_id, user)}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить завершение в топик {topic_id}: {e}")

    active_chats.pop(user_id, None)
    db.remove_active_chat(user_id)


# ─── /stop в состоянии ChatStates.chatting (ЛС бота) ────────────────────────

@router.message(Command("stop"), ChatStates.chatting)
async def stop_admin_chat_in_state(message: Message, state: FSMContext):
    """Администратор завершает чат /stop из состояния ChatStates.chatting."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    user_id = data.get("chat_with_user")
    if user_id:
        entry = _chat_entry(user_id)
        await _end_chat(message.bot, user_id, entry.get("admin_id", message.from_user.id),
                        entry.get("topic_id"))
    await state.clear()
    await message.answer("✅ Диалог завершен.")


# ─── /stop в состоянии ChatStates.group_chatting (ЛС бота, режим группы) ────

@router.message(Command("stop"), ChatStates.group_chatting)
async def stop_group_chat_in_state(message: Message, state: FSMContext):
    """Администратор завершает чат через группу командой /stop в ЛС бота."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    user_id = data.get("chat_with_user")
    if user_id:
        entry = _chat_entry(user_id)
        await _end_chat(message.bot, user_id, entry.get("admin_id", message.from_user.id),
                        entry.get("topic_id"))
    await state.clear()
    await message.answer("✅ Диалог завершен.")


# ─── /stop в топике группы ───────────────────────────────────────────────────

@router.message(
    Command("stop"),
    F.chat.type.in_({"group", "supergroup"}),
    F.message_thread_id.is_not(None),
)
async def stop_chat_from_topic(message: Message, state: FSMContext):
    """Администратор завершает чат командой /stop прямо в топике группы."""
    if not is_admin(message.from_user.id):
        return

    if not Config.CHAT_ADMIN_GROUP_ID or message.chat.id != Config.CHAT_ADMIN_GROUP_ID:
        return

    topic_id = message.message_thread_id

    # Ищем пользователя по topic_id
    user_id = None
    for uid, entry in list(active_chats.items()):
        e = entry if isinstance(entry, dict) else {"admin_id": entry, "mode": "bot", "topic_id": None}
        if e.get("topic_id") == topic_id:
            user_id = uid
            break

    if not user_id:
        await message.reply("❌ Активный чат для этого топика не найден.")
        return

    entry = _chat_entry(user_id)
    admin_id = entry.get("admin_id", message.from_user.id)

    # Уведомляем пользователя
    user = db.get_user(user_id)
    lang_code = "ru" if (user.get("language", "RUS") if user else "RUS") == "RUS" else "en"
    end_msg = "Диалог завершен администратором." if lang_code == "ru" else "Conversation ended by administrator."
    try:
        await message.bot.send_message(user_id, end_msg)
    except Exception:
        pass

    # Сообщение в топик
    _ulink = user_link(user_id, user)
    try:
        await message.bot.send_message(
            chat_id=Config.CHAT_ADMIN_GROUP_ID,
            message_thread_id=topic_id,
            text=f"🔴 <b>Диалог завершён администратором</b>\n👤 {_ulink}",
            parse_mode="HTML",
        )
    except Exception:
        pass

    active_chats.pop(user_id, None)
    db.remove_active_chat(user_id)

    # Сбрасываем FSM-состояние администратора (если он в group_chatting)
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.context import FSMContext as FSMCtx
    try:
        bot_me = await message.bot.get_me()
        key = StorageKey(bot_id=bot_me.id, chat_id=admin_id, user_id=admin_id)
        admin_fsm = FSMCtx(storage=state.storage, key=key)
        await admin_fsm.clear()
    except Exception as e:
        logger.warning(f"Не удалось сбросить FSM администратора {admin_id}: {e}")

    await message.reply(f"✅ Диалог с пользователем {_ulink} завершён.", parse_mode="HTML")


# ─── Универсальная отмена /cancel ────────────────────────────────────────────

@router.message(Command("cancel"), F.chat.type == "private")
async def cmd_cancel_universal(message: Message, state: FSMContext):
    """Универсальная отмена любого FSM-состояния для всех пользователей."""
    current_state = await state.get_state()
    user_id = message.from_user.id
    user = db.get_user(user_id)
    lang = (user or {}).get("language", "RUS")

    if current_state is None:
        text = "❌ Нет активных операций для отмены." if lang == "RUS" else "❌ Nothing to cancel."
        await message.answer(text)
        return

    await state.clear()
    text = "🚫 Операция отменена." if lang == "RUS" else "🚫 Operation cancelled."
    await message.answer(text)


# ─── /stop в ЛС вне FSM-состояний ───────────────────────────────────────────

@router.message(Command("stop"), F.chat.type == "private", StateFilter(default_state))
async def cmd_stop_admin_chat(message: Message, state: FSMContext):
    """Администратор завершает чат с пользователем командой /stop (только вне FSM-состояний)."""
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        return False

    # Ищем активный чат этого администратора
    user_id = None
    for uid, entry in list(active_chats.items()):
        if _get_admin_id(entry) == admin_id:
            user_id = uid
            break

    if not user_id:
        return False

    entry = _chat_entry(user_id)
    await _end_chat(message.bot, user_id, admin_id, entry.get("topic_id"))
    await state.clear()

    user = db.get_user(user_id)
    await message.answer(f"✅ Диалог с пользователем {user_link(user_id, user)} завершён.", parse_mode="HTML")


# ─── Пересылка сообщений пользователя администратору/в топик ─────────────────

@router.message(F.chat.type == "private", IsInActiveChat())
async def handle_user_to_admin(message: Message, state: FSMContext):
    """Пересылка сообщений от пользователя во время активного чата.

    Если mode="bot"  → пересылаем администратору в ЛС.
    Если mode="group" → пересылаем в топик группы.
    """
    user_id = message.from_user.id

    # Перепроверяем в БД — на случай рассинхронизации in-memory словаря
    if user_id not in active_chats:
        row = db.get_all_active_chats()
        if user_id in row:
            topic_id = db.get_chat_topic(user_id)
            mode = "group" if topic_id else "bot"
            active_chats[user_id] = {"admin_id": row[user_id], "mode": mode, "topic_id": topic_id}
        else:
            return

    entry = _chat_entry(user_id)
    admin_id = entry.get("admin_id")
    mode = entry.get("mode", "bot")
    topic_id = entry.get("topic_id")

    # Пользователь завершает чат
    if message.text and message.text.strip() == "/stop":
        user = db.get_user(user_id)
        lang_code = "ru" if (user.get("language", "RUS") if user else "RUS") == "RUS" else "en"
        stop_notify = f"❌ Пользователь {user_link(user_id, user)} завершил диалог командой /stop"
        if mode == "group" and topic_id and Config.CHAT_ADMIN_GROUP_ID:
            try:
                await message.bot.send_message(
                    chat_id=Config.CHAT_ADMIN_GROUP_ID,
                    message_thread_id=topic_id,
                    text=stop_notify,
                )
            except Exception:
                pass
        else:
            try:
                await message.bot.send_message(admin_id, stop_notify)
            except Exception:
                pass

        active_chats.pop(user_id, None)
        db.remove_active_chat(user_id)
        await state.clear()
        end_msg = "✅ Диалог завершён." if lang_code == "ru" else "✅ Conversation ended."
        await message.answer(end_msg)
        return

    # Пересылаем сообщение
    user = db.get_user(user_id)
    _ulink = user_link(user_id, user)

    # Игнорируем команды бота (кроме /stop который обработан выше)
    if message.text and message.text.startswith("/"):
        return

    if mode == "group" and topic_id and Config.CHAT_ADMIN_GROUP_ID:
        # Режим группы: пересылаем в топик
        try:
            try:
                await message.forward(
                    chat_id=Config.CHAT_ADMIN_GROUP_ID,
                    message_thread_id=topic_id,
                )
            except Exception:
                await message.copy_to(
                    chat_id=Config.CHAT_ADMIN_GROUP_ID,
                    message_thread_id=topic_id,
                )
        except Exception as e:
            logger.warning(f"Не удалось переслать в топик {topic_id}: {e}")
            if "Forbidden" in str(e) or "chat not found" in str(e):
                active_chats.pop(user_id, None)
                db.remove_active_chat(user_id)
    else:
        # Режим бота: пересылаем администратору в ЛС
        try:
            try:
                await message.forward(admin_id)
            except Exception:
                await message.copy_to(admin_id)
            await message.bot.send_message(
                admin_id,
                f"📨 <b>Сообщение от пользователя:</b>\n{_ulink}",
                parse_mode="HTML",
            )
        except Exception as e:
            await message.answer(f"❌ Ошибка отправки: {e}")
            if "Forbidden" in str(e) or "chat not found" in str(e):
                active_chats.pop(user_id, None)
                db.remove_active_chat(user_id)


# ─── Пересылка сообщений из топика группы пользователю ───────────────────────

@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.message_thread_id.is_not(None),
    ~F.from_user.is_bot,
)
async def handle_topic_message_to_user(message: Message):
    """Пересылка сообщений из топика группы пользователю.

    Любое сообщение в топике (кроме /stop) от администратора → пользователю.
    """
    if not Config.CHAT_ADMIN_GROUP_ID or message.chat.id != Config.CHAT_ADMIN_GROUP_ID:
        return
    if not is_admin(message.from_user.id):
        return
    # /stop обрабатывается отдельным хендлером выше
    if message.text and message.text.strip().startswith("/stop"):
        return

    topic_id = message.message_thread_id

    # Ищем пользователя по topic_id
    user_id = None
    for uid, entry in list(active_chats.items()):
        e = entry if isinstance(entry, dict) else {"admin_id": entry, "mode": "bot", "topic_id": None}
        if e.get("topic_id") == topic_id and e.get("mode") == "group":
            user_id = uid
            break

    if not user_id:
        return

    try:
        await message.copy_to(user_id)
    except Exception as e:
        logger.warning(f"Не удалось отправить сообщение из топика пользователю {user_id}: {e}")
        if "Forbidden" in str(e) or "blocked" in str(e):
            active_chats.pop(user_id, None)
            db.remove_active_chat(user_id)
