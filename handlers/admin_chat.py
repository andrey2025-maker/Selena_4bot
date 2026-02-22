"""
handlers/admin_chat.py — Двусторонняя связь с пользователями, управление исключениями,
управление Roblox-никами.
"""

from aiogram import Router, types, F
from utils.log_events import (
    log_exception_added, log_exception_removed, log_roblox_nick_changed,
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import default_state
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
import logging

from handlers.admin_common import (
    db, is_admin, active_chats,
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

    input_text = message.text.strip()
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
    admin_id = message.from_user.id
    active_chats[user_id] = admin_id
    db.set_active_chat(user_id, admin_id)  # сохраняем в БД

    lang_code = "ru" if user.get("language", "RUS") == "RUS" else "en"
    notification = (
        "👤 <b>С Вами связался администратор</b>\n\nДля завершения диалога напишите /stop"
        if lang_code == "ru"
        else "👤 <b>An administrator has contacted you</b>\n\nType /stop to end the conversation"
    )

    try:
        await message.bot.send_message(user_id, notification, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить уведомление пользователю: {e}")
        active_chats.pop(user_id, None)
        db.remove_active_chat(user_id)
        await state.clear()
        return

    user_info = f"ID: {user_id}"
    if user.get("username"):
        user_info += f" (@{user['username']})"

    await message.answer(
        f"✅ Чат начат с пользователем {user_info}\n\n"
        "Все ваши сообщения будут пересылаться пользователю.\n"
        "Для завершения диалога отправьте /stop\n\n"
        "Напишите первое сообщение:"
    )
    await state.set_state(ChatStates.chatting)
    await state.update_data(chat_with_user=user_id)


@router.message(ChatStates.chatting)
async def forward_admin_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    user_id = data.get("chat_with_user")
    if not user_id:
        await state.clear()
        return

    if user_id not in active_chats or active_chats[user_id] != message.from_user.id:
        await message.answer("❌ Чат с пользователем не активен или был завершен")
        await state.clear()
        return

    if message.text == "/stop":
        user = db.get_user(user_id)
        lang_code = "ru" if (user.get("language", "RUS") if user else "RUS") == "RUS" else "en"
        end_msg = "Диалог завершен администратором." if lang_code == "ru" else "Conversation ended by administrator."
        try:
            await message.bot.send_message(user_id, end_msg)
        except Exception:
            pass
        active_chats.pop(user_id, None)
        db.remove_active_chat(user_id)
        await message.answer("✅ Диалог завершен.")
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


@router.message(F.chat.type == "private", StateFilter(default_state))
async def handle_user_to_admin(message: Message):
    """Пересылка сообщений от пользователей администратору (только вне FSM-состояний)."""
    user_id = message.from_user.id
    if is_admin(user_id):
        return
    if user_id not in active_chats:
        return

    admin_id = active_chats[user_id]

    if message.text and message.text.strip() == "/stop":
        user_info = f"ID: {user_id}"
        user = db.get_user(user_id)
        if user and user.get("username"):
            user_info += f" (@{user['username']})"
        try:
            await message.bot.send_message(admin_id, f"❌ Пользователь {user_info} завершил диалог командой /stop")
        except Exception:
            pass
        active_chats.pop(user_id, None)
        db.remove_active_chat(user_id)
        return

    try:
        user_info = f"ID: {user_id}"
        user = db.get_user(user_id)
        if user and user.get("username"):
            user_info += f" (@{user['username']})"
        await message.forward(admin_id)
        await message.bot.send_message(admin_id, f"📨 <b>Сообщение от пользователя:</b>\n{user_info}", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {e}")
        if "Forbidden" in str(e) or "chat not found" in str(e):
            active_chats.pop(user_id, None)
            db.remove_active_chat(user_id)


@router.message(Command("active_chats"), F.chat.type == "private")
async def cmd_active_chats(message: Message):
    if not is_admin(message.from_user.id):
        return
    if not active_chats:
        await message.answer("📭 Нет активных чатов")
        return
    text = "💬 <b>Активные чаты:</b>\n\n"
    for uid, admin_id in active_chats.items():
        user = db.get_user(uid)
        if user and user.get("username"):
            user_link = f"<a href='tg://user?id={uid}'>@{user['username']}</a>"
        else:
            user_link = f"<a href='tg://user?id={uid}'>ID: {uid}</a>"
        text += f"👤 {user_link} → 👑 Админ: {admin_id}\n"
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
        return
    text = message.text.strip() if message.text else ""
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
    display = f"@{user_data['username']}" if user_data.get("username") else f"ID: {target_id}"

    if action == "view":
        if current_nick:
            await message.answer(f"🎮 Roblox-ник {display}: <b>@{current_nick}</b>")
        else:
            await message.answer(f"🎮 У {display} не установлен Roblox-ник.")
        await state.clear()
        return

    nick_info = f"\nТекущий ник: <b>@{current_nick}</b>" if current_nick else "\nНик не установлен."
    await message.answer(
        f"Пользователь: {display}{nick_info}\n\nВведите новый Roblox-ник (без @):"
    )
    await state.set_state(RobloxNickStates.waiting_for_new_nick)
    await state.update_data(target_user_id=target_id, target_display=display)


@router.message(RobloxNickStates.waiting_for_new_nick)
async def admin_roblox_receive_new_nick(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    new_nick = message.text.strip().lstrip("@") if message.text else ""
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
