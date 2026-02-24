"""
handlers/admin_chat.py — Двусторонняя связь с пользователями, управление исключениями,
управление Roblox-никами.
"""

from aiogram import Router, types, F
from utils.log_events import (
    log_exception_added, log_exception_removed, log_roblox_nick_changed,
    log_admin_action,
)
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
import logging

from config import Config
from handlers.admin_common import (
    db, is_admin, active_chats, _get_admin_id,
    ChatStates, RobloxNickStates,
)

logger = logging.getLogger(__name__)
router = Router()


# ========== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ==========

async def show_exceptions(target, user_id: int):
    """Показ списка исключений — работает и с Message, и с CallbackQuery."""
    if not is_admin(user_id):
        if isinstance(target, types.CallbackQuery):
            await target.answer("⛔ У вас нет прав администратора", show_alert=True)
        else:
            await target.answer("⛔ У вас нет прав администратора")
        return

    exceptions = db.get_exceptions() if hasattr(db, "get_exceptions") else []
    text = "📋 <b>Список исключений:</b>\n\n"

    if not exceptions:
        text += "Нет пользователей в списке исключений."
    else:
        for i, exc in enumerate(exceptions, 1):
            exc_uid = exc["user_id"]
            if exc.get("username"):
                user_link = f"<a href='tg://user?id={exc_uid}'>@{exc['username']}</a>"
            else:
                user_link = f"<a href='tg://user?id={exc_uid}'>ID: {exc_uid}</a>"
            text += f"{i}. {user_link}\n"
            text += f"   👑 Добавил: ID: {exc['admin_id']}\n"
            text += f"   📅 Дата: {exc.get('created_at', 'неизвестно')}\n\n"

    keyboard_buttons = []
    if hasattr(db, "add_exception"):
        keyboard_buttons.append([
            InlineKeyboardButton(text="➕ Добавить исключение", callback_data="add_exception"),
            InlineKeyboardButton(text="➖ Удалить исключение", callback_data="remove_exception"),
        ])
    keyboard_buttons.append([
        InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel"),
        InlineKeyboardButton(text="📋 Список", callback_data="admin_userlist"),
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=keyboard)


# ========== ДВУСТОРОННЯЯ СВЯЗЬ ==========

@router.callback_query(F.data == "admin_start_chat")
async def start_chat_with_user(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    await callback.message.answer(
        "💬 <b>Выберите пользователя для связи:</b>\n\n"
        "Отправьте:\n"
        "• Номер пользователя из списка (например, 15)\n"
        "• @username пользователя\n"
        "• Или ID пользователя\n\n"
        "Для отмены отправьте /cancel",
        parse_mode="HTML",
    )
    await state.set_state(ChatStates.waiting_for_user)
    await callback.answer()


@router.message(ChatStates.waiting_for_user)
async def process_user_selection(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    input_text = message.text.strip() if message.text else ""
    if input_text == "/cancel":
        await message.answer("🚫 Операция отменена")
        await state.clear()
        return

    user = None
    users = db.get_all_users()

    if input_text.isdigit() and len(input_text) < 6:
        idx = int(input_text) - 1
        if 0 <= idx < len(users):
            user = users[idx]
    elif input_text.startswith("@"):
        username = input_text[1:].lower()
        for u in users:
            if u.get("username") and u["username"].lower() == username:
                user = u
                break
    elif input_text.isdigit():
        user = db.get_user(int(input_text))

    if not user:
        await message.answer("❌ Пользователь не найден. Попробуйте еще раз или отправьте /cancel")
        return

    user_id = user["user_id"]
    if user.get("username"):
        user_info = f'<a href="tg://user?id={user_id}">@{user["username"]}</a>'
    else:
        user_info = f'<a href="tg://user?id={user_id}">ID: {user_id}</a>'

    # Сохраняем выбранного пользователя и предлагаем выбрать канал
    await state.update_data(chat_with_user=user_id, chat_user_info=user_info)

    if Config.CHAT_ADMIN_GROUP_ID:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="💬 В боте", callback_data="chat_channel_bot"),
                InlineKeyboardButton(text="👥 В группе (топик)", callback_data="chat_channel_group"),
            ],
            [InlineKeyboardButton(text="🚫 Отмена", callback_data="chat_channel_cancel")],
        ])
        await message.answer(
            f"👤 Пользователь: <b>{user_info}</b>\n\n"
            "Где вести переписку?",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        await state.set_state(ChatStates.choosing_channel)
    else:
        # Группа не настроена — сразу запускаем чат в боте
        await _start_bot_chat(message, state, user, user_id, user_info)


@router.callback_query(ChatStates.choosing_channel, F.data.in_({"chat_channel_bot", "chat_channel_group", "chat_channel_cancel"}))
async def choose_chat_channel(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        await state.clear()
        return

    if callback.data == "chat_channel_cancel":
        await callback.message.edit_text("🚫 Операция отменена")
        await state.clear()
        await callback.answer()
        return

    data = await state.get_data()
    user_id = data.get("chat_with_user")
    user_info = data.get("chat_user_info", str(user_id))
    user = db.get_user(user_id)

    await callback.answer()

    if callback.data == "chat_channel_bot":
        await callback.message.edit_text(f"💬 Запускаю чат с {user_info}…", parse_mode="HTML")
        await _start_bot_chat(callback.message, state, user, user_id, user_info)
    else:
        await callback.message.edit_text(f"👥 Создаю топик для {user_info}…", parse_mode="HTML")
        await _start_group_chat(callback.message, state, user, user_id, user_info, callback.bot)


async def _start_bot_chat(target: Message, state: FSMContext, user: dict, user_id: int, user_info: str):
    """Запустить чат через ЛС бота (старое поведение)."""
    admin_id = (
        target.from_user.id
        if (hasattr(target, 'from_user') and target.from_user)
        else (await state.get_data()).get('admin_id')
    )

    lang_code = "ru" if (user or {}).get("language", "RUS") == "RUS" else "en"
    notification = (
        "👤 <b>С Вами связался администратор</b>"
        if lang_code == "ru"
        else "👤 <b>An administrator has contacted you</b>"
    )

    try:
        await target.bot.send_message(user_id, notification, parse_mode="HTML")
    except Exception as e:
        await target.answer(f"❌ Не удалось отправить уведомление пользователю: {e}")
        active_chats.pop(user_id, None)
        db.remove_active_chat(user_id)
        await state.clear()
        return

    active_chats[user_id] = {"admin_id": admin_id, "mode": "bot", "topic_id": None}
    db.set_active_chat(user_id, admin_id)

    await target.answer(
        f"✅ Чат начат с пользователем {user_info}\n\n"
        "Все ваши сообщения будут пересылаться пользователю.\n"
        "Для завершения диалога отправьте /stop\n\n"
        "Напишите первое сообщение:",
        parse_mode="HTML",
    )
    await state.set_state(ChatStates.chatting)
    await state.update_data(chat_with_user=user_id)

    try:
        await log_admin_action(
            target.bot,
            admin_id=admin_id,
            admin_name=target.from_user.full_name if hasattr(target, 'from_user') else "Админ",
            action="Открыт чат с пользователем (бот)",
            details=user_info,
        )
    except Exception:
        pass


async def _start_group_chat(target: Message, state: FSMContext, user: dict, user_id: int, user_info: str, bot):
    """Запустить чат через топик группы."""
    from aiogram import Bot as AiogramBot
    admin_id = (
        target.from_user.id
        if (hasattr(target, 'from_user') and target.from_user)
        else (await state.get_data()).get('admin_id')
    )

    if not Config.CHAT_ADMIN_GROUP_ID:
        await target.answer("❌ Группа для чатов не настроена (CHAT_ADMIN_GROUP_ID).")
        await state.clear()
        return

    # Ищем существующий топик
    existing_topic_id = db.get_chat_topic(user_id)
    topic_id = existing_topic_id

    if not topic_id:
        # Создаём новый топик
        try:
            topic_name = f"User {user_id}"
            if user and user.get("username"):
                topic_name = f"@{user['username']} ({user_id})"
            forum_topic = await bot.create_forum_topic(
                chat_id=Config.CHAT_ADMIN_GROUP_ID,
                name=topic_name,
            )
            topic_id = forum_topic.message_thread_id
            db.set_chat_topic(user_id, topic_id)
            logger.info(f"Создан топик {topic_id} для пользователя {user_id}")
        except Exception as e:
            await target.answer(f"❌ Не удалось создать топик в группе: {e}")
            await state.clear()
            return

    # Отправляем заголовок в топик (user_info уже содержит HTML-ссылку)
    tg_link = user_info
    roblox_nick = (user or {}).get("roblox_nick") or ""
    lang = (user or {}).get("language", "RUS")
    lang_flag = "🇷🇺 RUS" if lang == "RUS" else "🇬🇧 EN"

    if existing_topic_id:
        header = (
            f"🔄 <b>Новый диалог начат</b>\n"
            f"👤 {tg_link}"
            + (f" (Roblox: {roblox_nick})" if roblox_nick else "")
            + f" | {lang_flag}\n"
            f"🧑‍💼 Администратор: {admin_id}\n"
            f"🕐 {__import__('datetime').datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
    else:
        header = (
            f"💬 <b>Чат с пользователем</b>\n"
            f"👤 {tg_link}"
            + (f" (Roblox: {roblox_nick})" if roblox_nick else "")
            + f" | {lang_flag}\n"
            f"🧑‍💼 Администратор: {admin_id}\n"
            f"📌 Для завершения напишите /stop в этом топике\n"
            f"🕐 {__import__('datetime').datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

    try:
        await bot.send_message(
            chat_id=Config.CHAT_ADMIN_GROUP_ID,
            message_thread_id=topic_id,
            text=header,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Не удалось отправить заголовок в топик {topic_id}: {e}")

    # Уведомляем пользователя
    lang_code = "ru" if lang == "RUS" else "en"
    notification = (
        "👤 <b>С Вами связался администратор</b>"
        if lang_code == "ru"
        else "👤 <b>An administrator has contacted you</b>"
    )
    try:
        await bot.send_message(user_id, notification, parse_mode="HTML")
    except Exception as e:
        await target.answer(f"❌ Не удалось уведомить пользователя: {e}")
        await state.clear()
        return

    active_chats[user_id] = {"admin_id": admin_id, "mode": "group", "topic_id": topic_id}
    db.set_active_chat(user_id, admin_id)

    await target.answer(
        f"✅ Чат через группу начат!\n"
        f"👤 Пользователь: {user_info}\n"
        f"📌 Топик: {topic_id}\n\n"
        f"Пишите сообщения в топик группы.\n"
        f"Для завершения напишите /stop в топике.",
        parse_mode="HTML",
    )
    await state.set_state(ChatStates.group_chatting)
    await state.update_data(chat_with_user=user_id, chat_topic_id=topic_id)

    try:
        await log_admin_action(
            bot,
            admin_id=admin_id,
            admin_name=target.from_user.full_name if hasattr(target, 'from_user') else "Админ",
            action="Открыт чат с пользователем (группа)",
            details=f"{user_info}, топик: {topic_id}",
        )
    except Exception:
        pass


@router.message(ChatStates.chatting)
async def forward_admin_message(message: Message, state: FSMContext):
    """Пересылка сообщения администратора пользователю (режим бота)."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    user_id = data.get("chat_with_user")
    if not user_id:
        await state.clear()
        return

    entry = active_chats.get(user_id)
    admin_id = _get_admin_id(entry) if entry else None
    if not entry or admin_id != message.from_user.id:
        await message.answer("❌ Чат с пользователем не активен или был завершен")
        await state.clear()
        return

    try:
        await message.copy_to(user_id)
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить сообщение: {e}")
        if "Forbidden" in str(e) or "blocked" in str(e):
            active_chats.pop(user_id, None)
            db.remove_active_chat(user_id)
            await state.clear()


@router.message(ChatStates.group_chatting)
async def forward_admin_message_group(message: Message, state: FSMContext):
    """Пересылка сообщения администратора пользователю (режим группы).
    Сообщение в ЛС бота → копируется пользователю."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    user_id = data.get("chat_with_user")
    if not user_id:
        await state.clear()
        return

    entry = active_chats.get(user_id)
    admin_id = _get_admin_id(entry) if entry else None
    if not entry or admin_id != message.from_user.id:
        await message.answer("❌ Чат с пользователем не активен или был завершен")
        await state.clear()
        return

    try:
        await message.copy_to(user_id)
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить сообщение: {e}")
        if "Forbidden" in str(e) or "blocked" in str(e):
            active_chats.pop(user_id, None)
            db.remove_active_chat(user_id)
            await state.clear()





@router.message(Command("active_chats"), F.chat.type == "private")
async def cmd_active_chats(message: Message):
    if not is_admin(message.from_user.id):
        return
    if not active_chats:
        await message.answer("📭 Нет активных чатов")
        return
    text = "💬 <b>Активные чаты:</b>\n\n"
    for uid, entry in active_chats.items():
        user = db.get_user(uid)
        if user and user.get("username"):
            u_link = f"<a href='tg://user?id={uid}'>@{user['username']}</a>"
        else:
            u_link = f"<a href='tg://user?id={uid}'>ID: {uid}</a>"
        if isinstance(entry, dict):
            adm_id = entry.get("admin_id", "?")
            mode = entry.get("mode", "bot")
        else:
            adm_id = entry
            mode = "bot"
        text += f"👤 {u_link} → 👑 Админ: {adm_id} [{mode}]\n"
    await message.answer(text, parse_mode="HTML")


# ========== ИСКЛЮЧЕНИЯ ==========

@router.callback_query(F.data == "admin_exceptions")
async def admin_exceptions_callback(callback: types.CallbackQuery):
    await show_exceptions(callback, callback.from_user.id)

@router.callback_query(F.data == "add_exception")
async def add_exception_callback(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    await callback.message.answer(
        "➕ <b>Добавление исключения</b>\n\n"
        "Отправьте ID или @username пользователя, которого хотите добавить в исключения.\n"
        "Для отмены отправьте /cancel",
        parse_mode="HTML",
    )
    await state.set_state(ChatStates.waiting_for_exception)
    await state.update_data(action="add")
    await callback.answer()

@router.callback_query(F.data == "remove_exception")
async def remove_exception_callback(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    await callback.message.answer(
        "➖ <b>Удаление исключения</b>\n\n"
        "Отправьте ID или @username пользователя, которого хотите удалить из исключений.\n"
        "Для отмены отправьте /cancel",
        parse_mode="HTML",
    )
    await state.set_state(ChatStates.waiting_for_exception)
    await state.update_data(action="remove")
    await callback.answer()


@router.message(ChatStates.waiting_for_exception)
async def process_exception_action(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    action = data.get("action")
    input_text = message.text.strip()

    if input_text == "/cancel":
        await message.answer("🚫 Операция отменена")
        await state.clear()
        return

    logger.info(f"🔍 Получен ввод для исключения: '{input_text}', действие: '{action}'")

    if not hasattr(db, "add_exception") or not hasattr(db, "remove_exception"):
        await message.answer("❌ Функция исключений не настроена в базе данных")
        await state.clear()
        return

    user = None

    if input_text.startswith("@"):
        username_to_find = input_text[1:].strip().lower()
        all_users = db.get_all_users()
        for u in all_users:
            if u.get("username") and u["username"].lower().strip() == username_to_find:
                user = u
                break
        if not user:
            for u in all_users:
                if u.get("username") and username_to_find in u["username"].lower():
                    user = u
                    break
        if not user:
            users_with_names = [u for u in all_users if u.get("username")]
            hint = ""
            if users_with_names:
                hint = "\n\n📋 Пользователи с username (первые 10):\n"
                hint += "\n".join(f"• @{u['username']} (ID: {u['user_id']})" for u in users_with_names[:10])
                if len(users_with_names) > 10:
                    hint += f"\n... и ещё {len(users_with_names) - 10}"
            await message.answer(
                f"❌ Пользователь @{input_text[1:]} не найден в базе.\n"
                f"Убедитесь, что он хотя бы раз запускал бота.{hint}\n\n"
                "Попробуйте ещё раз или отправьте /cancel для отмены."
            )
            return

    elif input_text.isdigit():
        user = db.get_user(int(input_text))
        if not user:
            await message.answer(
                f"❌ Пользователь с ID {input_text} не найден.\n\n"
                "Попробуйте ещё раз или отправьте /cancel для отмены."
            )
            return

    else:
        await message.answer(
            "❌ Неверный формат. Отправьте ID (числом) или @username.\n\n"
            "Для отмены отправьте /cancel"
        )
        return

    if not user:
        await message.answer(f"❌ Пользователь не найден: {input_text}")
        await state.clear()
        return

    user_id = user["user_id"]
    username = user.get("username", "без username")
    logger.info(f"🎯 Выбран пользователь: ID {user_id}, @{username}")

    if action == "add":
        success = db.add_exception(user_id, message.from_user.id)
        response = (
            f"✅ Пользователь @{username} (ID: {user_id}) добавлен в исключения!"
            if success else "❌ Не удалось добавить пользователя в исключения."
        )
        if success:
            await log_exception_added(
                message.bot,
                admin_id=message.from_user.id,
                admin_name=message.from_user.full_name,
                user_id=user_id,
                user_name=f"@{username}" if username != "без username" else str(user_id),
            )
    else:
        success = db.remove_exception(user_id)
        response = (
            f"✅ Пользователь @{username} (ID: {user_id}) удален из исключений!"
            if success else "❌ Пользователь не найден в списке исключений."
        )
        if success:
            await log_exception_removed(
                message.bot,
                admin_id=message.from_user.id,
                admin_name=message.from_user.full_name,
                user_id=user_id,
                user_name=f"@{username}" if username != "без username" else str(user_id),
            )

    await message.answer(response)
    await show_exceptions(message, message.from_user.id)
    await state.clear()


@router.message(Command("add_exception"), F.chat.type == "private")
async def cmd_add_exception(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Использование: /add_exception <id или @username>")
        return
    identifier = parts[1]
    user = None
    if identifier.startswith("@"):
        username = identifier[1:]
        for u in db.get_all_users():
            if u.get("username") and u["username"].lower() == username.lower():
                user = u
                break
    elif identifier.isdigit():
        user = db.get_user(int(identifier))
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    user_id = user["user_id"]
    username = user.get("username", "без username")
    success = db.add_exception(user_id, message.from_user.id)
    if success:
        await message.answer(f"✅ Пользователь {username} (ID: {user_id}) добавлен в исключения!")
        await log_exception_added(
            message.bot,
            admin_id=message.from_user.id,
            admin_name=message.from_user.full_name,
            user_id=user_id,
            user_name=f"@{username}",
        )
    else:
        await message.answer("❌ Не удалось добавить пользователя в исключения.")


@router.message(Command("remove_exception"), F.chat.type == "private")
async def cmd_remove_exception(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Использование: /remove_exception <id или @username>")
        return
    identifier = parts[1]
    user = None
    if identifier.startswith("@"):
        username = identifier[1:]
        for u in db.get_all_users():
            if u.get("username") and u["username"].lower() == username.lower():
                user = u
                break
    elif identifier.isdigit():
        user = db.get_user(int(identifier))
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    user_id = user["user_id"]
    username = user.get("username", "без username")
    success = db.remove_exception(user_id)
    if success:
        await message.answer(f"✅ Пользователь {username} (ID: {user_id}) удален из исключений!")
        await log_exception_removed(
            message.bot,
            admin_id=message.from_user.id,
            admin_name=message.from_user.full_name,
            user_id=user_id,
            user_name=f"@{username}",
        )
    else:
        await message.answer("❌ Пользователь не найден в списке исключений.")


@router.message(Command("check_exception"), F.chat.type == "private")
async def cmd_check_exception(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Использование: /check_exception <id или @username>")
        return
    identifier = parts[1]
    user = None
    if identifier.startswith("@"):
        username = identifier[1:]
        for u in db.get_all_users():
            if u.get("username") and u["username"].lower() == username.lower():
                user = u
                break
    elif identifier.isdigit():
        user = db.get_user(int(identifier))
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    user_id = user["user_id"]
    if db.is_exception(user_id):
        await message.answer(f"✅ Пользователь {user_id} находится в списке исключений!")
    else:
        await message.answer(f"❌ Пользователь {user_id} НЕ находится в списке исключений.")


@router.message(Command("exceptions"), F.chat.type == "private")
async def cmd_exceptions(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора")
        return
    await show_exceptions(message, message.from_user.id)


# ========== ROBLOX-НИКИ ==========

@router.callback_query(F.data == "admin_roblox_nicks")
async def admin_roblox_nicks_menu(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить ник пользователю", callback_data="admin_roblox_change")],
        [InlineKeyboardButton(text="🔍 Посмотреть ник пользователя", callback_data="admin_roblox_view")],
        [InlineKeyboardButton(text="🛠️ Назад", callback_data="admin_panel")],
    ])
    await callback.message.edit_text(
        "🎮 <b>Управление Roblox-никами</b>\n\nВыберите действие:",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "admin_roblox_change")
async def admin_roblox_change_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.set_state(RobloxNickStates.waiting_for_user_id)
    await state.update_data(roblox_action="change")
    await callback.message.answer(
        "Введите Telegram ID или @username пользователя, которому хотите изменить Roblox-ник:"
    )


@router.callback_query(F.data == "admin_roblox_view")
async def admin_roblox_view_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.set_state(RobloxNickStates.waiting_for_user_id)
    await state.update_data(roblox_action="view")
    await callback.message.answer(
        "Введите Telegram ID или @username пользователя для просмотра Roblox-ника:"
    )


@router.message(RobloxNickStates.waiting_for_user_id)
async def admin_roblox_receive_user_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    text = message.text.strip() if message.text else ""

    if text in ("/cancel", "/отмена"):
        await state.clear()
        await message.answer("🚫 Операция отменена.")
        return

    target_id: int | None = None

    if text.startswith("@"):
        username = text.lstrip("@")
        for u in db.get_all_users():
            if u.get("username", "").lower() == username.lower():
                target_id = u["user_id"]
                break
        if not target_id:
            await message.answer(f"❌ Пользователь @{username} не найден в базе.")
            await state.clear()
            return
    else:
        try:
            target_id = int(text)
        except ValueError:
            await message.answer("❌ Введите корректный Telegram ID или @username.")
            return

    user_data = db.get_user(target_id)
    if not user_data:
        await message.answer(f"❌ Пользователь с ID {target_id} не найден в базе.")
        await state.clear()
        return

    data = await state.get_data()
    action = data.get("roblox_action", "view")
    current_nick = db.get_roblox_nick(target_id)
    _dname = f"@{user_data['username']}" if user_data.get("username") else f"ID: {target_id}"
    display = f'<a href="tg://user?id={target_id}">{_dname}</a>'

    if action == "view":
        if current_nick:
            await message.answer(f"🎮 Roblox-ник {display}: <b>@{current_nick}</b>", parse_mode="HTML")
        else:
            await message.answer(f"🎮 У {display} не установлен Roblox-ник.", parse_mode="HTML")
        await state.clear()
        return

    nick_info = f"\nТекущий ник: <b>@{current_nick}</b>" if current_nick else "\nНик не установлен."
    await message.answer(
        f"Пользователь: {display}{nick_info}\n\nВведите новый Roblox-ник (без @):",
        parse_mode="HTML",
    )
    await state.set_state(RobloxNickStates.waiting_for_new_nick)
    await state.update_data(target_user_id=target_id, target_display=display)


@router.message(RobloxNickStates.waiting_for_new_nick)
async def admin_roblox_receive_new_nick(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    raw = message.text.strip() if message.text else ""
    if raw in ("/cancel", "/отмена"):
        await state.clear()
        await message.answer("🚫 Операция отменена.")
        return

    new_nick = raw.lstrip("@")
    if not new_nick:
        await message.answer("❌ Ник не может быть пустым. Введите ник ещё раз:")
        return

    data = await state.get_data()
    target_id = data.get("target_user_id")
    display = data.get("target_display", str(target_id))

    if not target_id:
        await message.answer("❌ Ошибка: данные пользователя не найдены.")
        await state.clear()
        return

    old_nick = db.get_roblox_nick(target_id)
    db.set_roblox_nick(target_id, new_nick)
    await state.clear()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Roblox-ники", callback_data="admin_roblox_nicks")],
        [InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel")],
    ])
    await message.answer(
        f"✅ Roblox-ник пользователя {display} изменён на <b>@{new_nick}</b>",
        reply_markup=keyboard,
    )
    await log_roblox_nick_changed(
        message.bot,
        admin_id=message.from_user.id,
        admin_name=message.from_user.full_name,
        user_id=target_id,
        user_name=display,
        old_nick=old_nick,
        new_nick=new_nick,
    )


# ========== СПРАВКА ==========

@router.message(Command("help_admin"), F.chat.type == "private")
async def cmd_help_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора")
        return
    help_text = (
        "🛠️ <b>Админ-команды:</b>\n\n"
        "<b>/admin</b> — 🛠️ Главная панель\n"
        "<b>/stats</b> — 📊 Статистика\n"
        "<b>/broadcast</b> — 📢 Меню рассылки\n"
        "<b>/broadcast_rus</b> — 🇷🇺 Рассылка русским\n"
        "<b>/broadcast_eng</b> — 🇺🇸 Рассылка английским\n"
        "<b>/broadcast_all</b> — 🌍 Рассылка всем\n"
        "<b>/exceptions</b> — 📋 Управление исключениями\n"
        "<b>/active_chats</b> — 💬 Активные чаты\n"
        "<b>/backup</b> — 💾 Создать бэкап\n"
        "<b>/help_admin</b> — ❓ Эта справка\n\n"
        f"<b>💬 Активных чатов:</b> {len(active_chats)}"
    )
    await message.answer(help_text, parse_mode="HTML")
