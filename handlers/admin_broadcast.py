"""
handlers/admin_broadcast.py — Система рассылки сообщений пользователям.
"""

from aiogram import Router, types, F
from utils.log_events import log_broadcast
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from datetime import datetime
import asyncio
import logging

from handlers.admin_common import db, is_admin, BroadcastStates, ChatStates

logger = logging.getLogger(__name__)
router = Router()


# ========== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ==========

async def broadcast_by_language(message_or_callback, state: FSMContext, lang_filter: str = None):
    """Запуск рассылки с фильтром по языку."""
    if isinstance(message_or_callback, types.CallbackQuery):
        user_id = message_or_callback.from_user.id
        message = message_or_callback.message
    else:
        user_id = message_or_callback.from_user.id
        message = message_or_callback

    if not is_admin(user_id):
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        else:
            await message.answer("⛔ У вас нет прав администратора")
        return

    users = db.get_all_users()

    if lang_filter == "RUS":
        filtered_users = [u for u in users if u.get("language") == "RUS"]
        lang_text = "русский"
    elif lang_filter == "ENG":
        filtered_users = [u for u in users if u.get("language") == "EN"]
        lang_text = "английский"
    else:
        filtered_users = users
        lang_text = "все"

    if not filtered_users:
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.answer(f"❌ Нет пользователей с языком {lang_text}", show_alert=True)
        else:
            await message.answer(f"❌ Нет пользователей с языком {lang_text}")
        return

    if isinstance(message_or_callback, types.CallbackQuery):
        await message.answer(
            f"📢 <b>Рассылка ({lang_text} язык)</b>\n\n"
            f"👥 Получателей: {len(filtered_users)}\n"
            f"✅ Активных: {sum(1 for u in filtered_users if u.get('is_subscribed'))}\n\n"
            "<b>Отправьте сообщение для рассылки:</b>\n"
            "(текст, фото, видео, документ)\n\n"
            "❌ Для отмены отправьте /cancel",
            parse_mode="HTML",
        )

    await state.update_data(
        broadcast_admin_id=user_id,
        broadcast_start_time=datetime.now().strftime("%H:%M:%S"),
        broadcast_filter_lang=lang_filter,
        broadcast_users=filtered_users,
    )
    await state.set_state(BroadcastStates.waiting_for_message)


# ========== CALLBACKS: МЕНЮ РАССЫЛКИ ==========

@router.callback_query(F.data == "admin_broadcast_menu")
async def broadcast_menu_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русским", callback_data="admin_broadcast_rus"),
            InlineKeyboardButton(text="🇺🇸 Английским", callback_data="admin_broadcast_eng"),
        ],
        [InlineKeyboardButton(text="🌍 Всем", callback_data="admin_broadcast_all")],
        [InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel")],
    ])
    await callback.message.edit_text(
        "📢 <b>Выберите тип рассылки:</b>\n\n"
        "🇷🇺 <b>Русским</b> — только пользователям с русским языком\n"
        "🇺🇸 <b>Английским</b> — только пользователям с английским языком\n"
        "🌍 <b>Всем</b> — всем пользователям независимо от языка",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()

@router.callback_query(F.data == "admin_broadcast_rus")
async def broadcast_rus_callback(callback: types.CallbackQuery, state: FSMContext):
    await broadcast_by_language(callback, state, "RUS")
    await callback.answer()

@router.callback_query(F.data == "admin_broadcast_eng")
async def broadcast_eng_callback(callback: types.CallbackQuery, state: FSMContext):
    await broadcast_by_language(callback, state, "ENG")
    await callback.answer()

@router.callback_query(F.data == "admin_broadcast_all")
async def broadcast_all_callback(callback: types.CallbackQuery, state: FSMContext):
    await broadcast_by_language(callback, state)
    await callback.answer()


# ========== КОМАНДЫ ==========


@router.message(Command("broadcast"), F.chat.type == "private")
async def cmd_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора")
        return
    users = db.get_all_users()
    await message.answer(
        f"📢 <b>Команда рассылки</b>\n\n"
        f"👥 Получателей: {len(users)}\n\n"
        "Для выбора типа рассылки используйте админ-панель или команды:\n"
        "/broadcast_rus — рассылка русским\n"
        "/broadcast_eng — рассылка английским\n"
        "/broadcast_all — рассылка всем",
        parse_mode="HTML",
    )

@router.message(Command("broadcast_rus"), F.chat.type == "private")
async def cmd_broadcast_rus(message: Message, state: FSMContext):
    await broadcast_by_language(message, state, "RUS")

@router.message(Command("broadcast_eng"), F.chat.type == "private")
async def cmd_broadcast_eng(message: Message, state: FSMContext):
    await broadcast_by_language(message, state, "ENG")

@router.message(Command("broadcast_all"), F.chat.type == "private")
async def cmd_broadcast_all(message: Message, state: FSMContext):
    await broadcast_by_language(message, state)


# ========== FSM: ОБРАБОТКА СООБЩЕНИЯ ДЛЯ РАССЫЛКИ ==========

@router.message(BroadcastStates.waiting_for_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора")
        await state.clear()
        return

    if message.text and message.text.strip() in ("/cancel", "/отмена"):
        await state.clear()
        await message.answer("🚫 Рассылка отменена.")
        return

    data = await state.get_data()
    admin_id = data.get("broadcast_admin_id")
    if message.from_user.id != admin_id:
        await message.answer("❌ Вы не инициировали рассылку")
        await state.clear()
        return

    users = data.get("broadcast_users", [])
    if not users:
        await message.answer("❌ Нет пользователей для рассылки")
        await state.clear()
        return

    # ── Обработка медиагруппы (альбома) ──────────────────────────────────────
    if message.media_group_id:
        group_id = message.media_group_id
        current_group = data.get("broadcast_media_group_id")

        if current_group == group_id:
            # Накапливаем message_id альбома
            group_ids = data.get("broadcast_media_group_msg_ids", [])
            group_ids.append(message.message_id)
            await state.update_data(broadcast_media_group_msg_ids=group_ids)
            return  # Ждём остальные сообщения альбома

        # Первое сообщение нового альбома
        text_preview = message.caption or ""
        await state.update_data(
            broadcast_media_group_id=group_id,
            broadcast_media_group_msg_ids=[message.message_id],
            broadcast_chat_id=message.chat.id,
            broadcast_message_id=message.message_id,  # первое сообщение для превью
            broadcast_message_info={
                "content_type": "media_group",
                "text_preview": text_preview[:100] + ("..." if len(text_preview) > 100 else ""),
            },
        )
        # Задержка 0.7 сек чтобы накопить все сообщения альбома
        async def _delayed_confirm():
            await asyncio.sleep(0.7)
            await _show_broadcast_confirm(message, state)
        asyncio.create_task(_delayed_confirm())
        return
    # ── Одиночное сообщение ───────────────────────────────────────────────────
    text_preview = (message.text or message.caption or "")
    message_info = {
        "content_type": message.content_type,
        "text_preview": text_preview[:100] + ("..." if len(text_preview) > 100 else ""),
    }
    await state.update_data(
        broadcast_message_id=message.message_id,
        broadcast_chat_id=message.chat.id,
        broadcast_message_info=message_info,
        broadcast_media_group_id=None,
        broadcast_media_group_msg_ids=None,
    )
    await _show_broadcast_confirm(message, state)


async def _show_broadcast_confirm(message: Message, state: FSMContext):
    """Показать подтверждение рассылки (вызывается после накопления альбома или сразу)."""
    data = await state.get_data()
    users = data.get("broadcast_users", [])
    message_info = data.get("broadcast_message_info", {})
    group_ids = data.get("broadcast_media_group_msg_ids")

    content_type = message_info.get("content_type", "?")
    if content_type == "media_group" and group_ids:
        type_str = f"альбом ({len(group_ids)} фото/видео)"
    else:
        type_str = content_type

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, разослать", callback_data="broadcast_confirm"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="broadcast_cancel"),
        ]
    ])
    await message.answer(
        f"📢 <b>Подтверждение рассылки</b>\n\n"
        f"👥 Получателей: {len(users)}\n"
        f"📝 Тип: {type_str}\n"
        f"📄 Текст: {message_info.get('text_preview', '')}\n\n"
        "<i>Разослать это сообщение всем пользователям?</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await state.set_state(BroadcastStates.waiting_for_confirmation)


@router.callback_query(F.data.in_(["broadcast_confirm", "broadcast_cancel"]))
async def broadcast_confirmation(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return

    if callback.data == "broadcast_cancel":
        await callback.message.edit_text("🚫 Рассылка отменена")
        await state.clear()
        await callback.answer("🚫 Рассылка отменена")
        return

    data = await state.get_data()
    admin_id = data.get("broadcast_admin_id")
    message_id = data.get("broadcast_message_id")
    chat_id = data.get("broadcast_chat_id")
    users = data.get("broadcast_users", [])

    if callback.from_user.id != admin_id:
        await callback.answer("❌ Вы не инициировали рассылку", show_alert=True)
        return

    total_users = len(users)
    await callback.message.edit_text(f"🔄 Рассылка начата для {total_users} пользователей...")

    # Определяем режим: альбом или одиночное сообщение
    group_msg_ids: list = data.get("broadcast_media_group_msg_ids") or []
    is_album = bool(group_msg_ids and len(group_msg_ids) > 1)

    success_count = 0
    failed_count = 0
    failed_list = []

    for user in users:
        try:
            if is_album:
                # Копируем весь альбом одним вызовом
                await callback.bot.copy_messages(
                    chat_id=user["user_id"],
                    from_chat_id=chat_id,
                    message_ids=group_msg_ids,
                )
            else:
                await callback.bot.copy_message(
                    chat_id=user["user_id"],
                    from_chat_id=chat_id,
                    message_id=message_id,
                )
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed_count += 1
            error_msg = str(e)
            user_info = f"ID: {user['user_id']}"
            if user.get("username"):
                user_info += f" (@{user['username']})"
            if "Forbidden" in error_msg or "bot was blocked" in error_msg:
                failed_list.append(f"{user_info} (заблокировал бота)")
            elif "chat not found" in error_msg:
                failed_list.append(f"{user_info} (чат не найден)")
            else:
                failed_list.append(f"{user_info} ({error_msg[:30]}...)")

    report = (
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📊 <b>Результаты:</b>\n"
        f"• Всего получателей: {total_users}\n"
        f"• Успешно отправлено: {success_count}\n"
        f"• Не удалось отправить: {failed_count}\n"
    )
    if failed_list:
        report += "\n❌ <b>Ошибки отправки:</b>\n"
        for i, failed in enumerate(failed_list[:5], 1):
            report += f"{i}. {failed}\n"
        if len(failed_list) > 5:
            report += f"... и еще {len(failed_list) - 5} ошибок\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠️ В админ-панель", callback_data="admin_panel")]
    ])
    await callback.message.edit_text(report, parse_mode="HTML", reply_markup=keyboard)

    # Лог: рассылка
    _lang_filter = data.get("broadcast_filter_lang")
    _target_str = {"RUS": "🇷🇺 Русские", "ENG": "🇺🇸 Английские"}.get(_lang_filter, "👥 Все пользователи")
    await log_broadcast(
        callback.bot,
        admin_id=admin_id,
        admin_name=callback.from_user.full_name,
        target=_target_str,
        sent=success_count,
        failed=failed_count,
    )

    await state.clear()
    await callback.answer("✅ Рассылка завершена")
