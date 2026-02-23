"""
handlers/admin_stop.py — Универсальные /cancel и /stop, пересылка сообщений
пользователя администратору. Подключается отдельно, до trade_router.
"""

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import default_state
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from aiogram.filters import BaseFilter
from handlers.admin_common import db, is_admin, active_chats, ChatStates

router = Router()


class IsInActiveChat(BaseFilter):
    """Фильтр: пользователь находится в активном чате с администратором."""
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in active_chats


@router.message(Command("stop"), ChatStates.chatting)
async def stop_admin_chat_in_state(message: Message, state: FSMContext):
    """Администратор завершает чат /stop из состояния ChatStates.chatting — стоит ДО trade_router."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    user_id = data.get("chat_with_user")
    if user_id:
        user = db.get_user(user_id)
        lang_code = "ru" if (user.get("language", "RUS") if user else "RUS") == "RUS" else "en"
        end_msg = "Диалог завершен администратором." if lang_code == "ru" else "Conversation ended by administrator."
        try:
            await message.bot.send_message(user_id, end_msg)
        except Exception:
            pass
        active_chats.pop(user_id, None)
        db.remove_active_chat(user_id)
    await state.clear()
    await message.answer("✅ Диалог завершен.")


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


@router.message(Command("stop"), F.chat.type == "private", StateFilter(default_state))
async def cmd_stop_admin_chat(message: Message, state: FSMContext):
    """Администратор завершает чат с пользователем командой /stop (только вне FSM-состояний)."""
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        # Не администратор — передаём управление следующему хендлеру (trade.py)
        return False

    # Ищем активный чат, который ведёт этот администратор
    user_id = None
    for uid, aid in list(active_chats.items()):
        if aid == admin_id:
            user_id = uid
            break

    if not user_id:
        # Нет активного чата — не перехватываем, пусть обработает trade.py
        return False

    user = db.get_user(user_id)
    lang_code = "ru" if (user.get("language", "RUS") if user else "RUS") == "RUS" else "en"
    end_msg = "Диалог завершен администратором." if lang_code == "ru" else "Conversation ended by administrator."
    try:
        await message.bot.send_message(user_id, end_msg)
    except Exception:
        pass

    active_chats.pop(user_id, None)
    db.remove_active_chat(user_id)
    await state.clear()

    user_info = f"ID: {user_id}"
    if user and user.get("username"):
        user_info += f" (@{user['username']})"
    await message.answer(f"✅ Диалог с пользователем {user_info} завершён.")


@router.message(F.chat.type == "private", IsInActiveChat())
async def handle_user_to_admin(message: Message, state: FSMContext):
    """Пересылка сообщений от пользователя администратору во время активного чата.

    Фильтр StateFilter(default_state) намеренно убран: пользователь мог иметь
    незавершённое FSM-состояние (например, после перезапуска бота), и тогда
    сообщения переставали пересылаться.
    """
    user_id = message.from_user.id

    # Перепроверяем в БД — на случай рассинхронизации in-memory словаря
    if user_id not in active_chats:
        row = db.get_all_active_chats()
        if user_id in row:
            active_chats[user_id] = row[user_id]
        else:
            return

    admin_id = active_chats[user_id]

    if message.text and message.text.strip() == "/stop":
        user = db.get_user(user_id)
        lang_code = "ru" if (user.get("language", "RUS") if user else "RUS") == "RUS" else "en"
        user_info = f"ID: {user_id}"
        if user and user.get("username"):
            user_info += f" (@{user['username']})"
        try:
            await message.bot.send_message(
                admin_id,
                f"❌ Пользователь {user_info} завершил диалог командой /stop"
            )
        except Exception:
            pass
        active_chats.pop(user_id, None)
        db.remove_active_chat(user_id)
        # Сбрасываем любое FSM-состояние пользователя
        await state.clear()
        end_msg = "✅ Диалог завершён." if lang_code == "ru" else "✅ Conversation ended."
        await message.answer(end_msg)
        return

    user = db.get_user(user_id)
    user_info = f"ID: {user_id}"
    if user and user.get("username"):
        user_info += f" (@{user['username']})"

    try:
        # Пробуем forward; если запрещён настройками приватности — используем copy_to
        try:
            await message.forward(admin_id)
        except Exception:
            await message.copy_to(admin_id)
        await message.bot.send_message(
            admin_id,
            f"📨 <b>Сообщение от пользователя:</b>\n{user_info}",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {e}")
        if "Forbidden" in str(e) or "chat not found" in str(e):
            active_chats.pop(user_id, None)
            db.remove_active_chat(user_id)
