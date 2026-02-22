"""
handlers/admin_backup.py — Управление резервными копиями базы данных.
"""

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile
from aiogram.fsm.context import FSMContext
from datetime import datetime
import logging
import os

from backup_utils import backup_manager
from handlers.admin_common import is_admin
from config import Config

logger = logging.getLogger(__name__)
router = Router()


# ========== CALLBACK: МЕНЮ БЭКАПОВ ==========

@router.callback_query(F.data == "admin_backup_menu")
async def admin_backup_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return

    stats = backup_manager.get_backup_stats()
    text = (
        "💾 <b>Управление бэкапами базы данных</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Всего бэкапов: {stats['total_backups']}\n"
        f"• Общий размер: {stats.get('total_size_formatted', '0 байт')}\n"
    )
    if stats["oldest_backup"]:
        text += f"• Самый старый: {stats['oldest_backup'].strftime('%d.%m.%Y %H:%M')}\n"
    if stats["newest_backup"]:
        text += f"• Самый новый: {stats['newest_backup'].strftime('%d.%m.%Y %H:%M')}\n"
    text += "\n📁 <b>Типы файлов:</b>\n"
    for file_type, count in stats.get("backup_types", {}).items():
        if count > 0:
            text += f"• {file_type}: {count}\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 Создать бэкап (DB)", callback_data="create_db_backup"),
            InlineKeyboardButton(text="📦 Создать бэкап (сжатый)", callback_data="create_compressed_backup"),
        ],
        [
            InlineKeyboardButton(text="📄 Создать JSON бэкап", callback_data="create_json_backup"),
            InlineKeyboardButton(text="📋 Список бэкапов", callback_data="list_backups"),
        ],
        [
            InlineKeyboardButton(text="🔄 Автобэкап", callback_data="auto_backup_settings"),
            InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel"),
        ],
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ========== CALLBACK: СОЗДАНИЕ БЭКАПА ==========

@router.callback_query(F.data.in_({"create_db_backup", "create_compressed_backup", "create_json_backup"}))
async def create_backup_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return

    backup_type = callback.data.replace("create_", "").replace("_backup", "")
    await callback.message.edit_text("🔄 Создаю бэкап...")

    if backup_type == "db":
        backup_path = backup_manager.create_backup(compress=False)
        backup_type_name = "обычный"
    elif backup_type == "compressed":
        backup_path = backup_manager.create_backup(compress=True)
        backup_type_name = "сжатый"
    elif backup_type == "json":
        backup_path = backup_manager.create_json_backup()
        backup_type_name = "JSON"
    else:
        await callback.message.edit_text("❌ Неизвестный тип бэкапа")
        return

    if not backup_path or not os.path.exists(backup_path):
        await callback.message.edit_text("❌ Ошибка создания бэкапа")
        return

    try:
        file_size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        if file_size_mb > 48:
            await callback.message.edit_text(
                f"❌ Файл слишком большой: {file_size_mb:.1f} MB (лимит Telegram 50 MB)\n"
                f"Файл сохранен локально: {os.path.basename(backup_path)}"
            )
            return

        if backup_path.endswith(".gz"):
            caption = f"📦 Сжатый бэкап базы данных\nРазмер: {file_size_mb:.2f} MB"
        elif backup_path.endswith(".json"):
            caption = f"📄 JSON бэкап базы данных\nРазмер: {file_size_mb:.2f} MB"
        else:
            caption = f"💾 Бэкап базы данных\nРазмер: {file_size_mb:.2f} MB"

        with open(backup_path, "rb") as f:
            file_data = f.read()
        await callback.bot.send_document(
            chat_id=callback.from_user.id,
            document=BufferedInputFile(file_data, filename=os.path.basename(backup_path)),
            caption=caption,
        )
        await callback.message.edit_text(f"✅ {backup_type_name.capitalize()} бэкап создан и отправлен!")
    except Exception as e:
        logger.error(f"Ошибка отправки бэкапа: {e}")
        await callback.message.edit_text(f"✅ Бэкап создан, но не отправлен: {e}")


# ========== CALLBACK: СПИСОК БЭКАПОВ ==========

@router.callback_query(F.data == "list_backups")
async def list_backups_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return

    backups = backup_manager.list_backups()
    if not backups:
        await callback.message.edit_text("📭 Нет доступных бэкапов")
        return

    text = "📋 <b>Список бэкапов:</b>\n\n"
    for i, backup in enumerate(backups[:10], 1):
        text += (
            f"{i}. <code>{backup['filename']}</code>\n"
            f"   📏 {backup['size_formatted']} | 🕐 {backup['modified'].strftime('%d.%m.%Y %H:%M')} | 📁 {backup['type']}\n\n"
        )
    if len(backups) > 10:
        text += f"\n... и еще {len(backups) - 10} бэкапов"

    keyboard_buttons = []
    if backups:
        row = [
            InlineKeyboardButton(text=f"📤 {i+1}", callback_data=f"send_backup_{backups[i]['filename']}")
            for i in range(min(3, len(backups)))
        ]
        keyboard_buttons.append(row)
    keyboard_buttons.extend([
        [InlineKeyboardButton(text="🔄 Обновить список", callback_data="list_backups")],
        [InlineKeyboardButton(text="📥 Создать новый", callback_data="admin_backup_menu")],
        [InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel")],
    ])
    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    )
    await callback.answer()


# ========== CALLBACK: ОТПРАВКА КОНКРЕТНОГО БЭКАПА ==========

@router.callback_query(F.data.startswith("send_backup_"))
async def send_backup_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return

    filename = callback.data.replace("send_backup_", "")
    backup_path = os.path.join("database_backups", filename)

    if not os.path.exists(backup_path):
        await callback.answer("❌ Файл не найден", show_alert=True)
        return

    await callback.message.edit_text(f"📤 Отправляю {filename}...")

    try:
        file_size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        if file_size_mb > 48:
            await callback.message.edit_text(f"❌ Файл слишком большой: {file_size_mb:.1f} MB")
            return
        with open(backup_path, "rb") as f:
            file_data = f.read()
        await callback.bot.send_document(
            chat_id=callback.from_user.id,
            document=BufferedInputFile(file_data, filename=filename),
            caption=f"💾 Бэкап: {filename}\nРазмер: {file_size_mb:.2f} MB",
        )
        await callback.message.edit_text(f"✅ Бэкап {filename} отправлен!")
    except Exception as e:
        logger.error(f"Ошибка отправки бэкапа: {e}")
        await callback.message.edit_text(f"❌ Ошибка отправки: {e}")


# ========== КОМАНДЫ ==========

@router.message(Command("backup"), F.chat.type == "private")
async def cmd_backup(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора")
        return

    await message.answer("🔄 Создаю бэкап базы данных...")

    try:
        backup_path = backup_manager.create_backup(compress=True)
        if not backup_path:
            await message.answer("❌ Не удалось создать бэкап.")
            return

        file_size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        backup_name = os.path.basename(backup_path)
        with open(backup_path, "rb") as f:
            file_data = f.read()
        await message.bot.send_document(
            chat_id=message.from_user.id,
            document=BufferedInputFile(file=file_data, filename=backup_name),
            caption=(
                f"💾 Бэкап базы данных\n"
                f"Размер: {file_size_mb:.2f} MB\n"
                f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            ),
        )
        await message.answer("✅ Бэкап создан и отправлен!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("backup_json"), F.chat.type == "private")
async def cmd_backup_json(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора")
        return

    backup_path = backup_manager.create_json_backup()
    if not backup_path or not os.path.exists(backup_path):
        await message.answer("❌ Ошибка создания JSON бэкапа")
        return

    try:
        file_size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        with open(backup_path, "rb") as f:
            file_data = f.read()
        await message.bot.send_document(
            chat_id=message.from_user.id,
            document=BufferedInputFile(file_data, filename=os.path.basename(backup_path)),
            caption=f"📄 JSON бэкап базы данных\nРазмер: {file_size_mb:.2f} MB",
        )
        await message.answer("✅ JSON бэкап создан и отправлен!")
    except Exception as e:
        await message.answer(f"✅ JSON бэкап создан, но ошибка отправки: {e}")


@router.message(Command("backup_stats"), F.chat.type == "private")
async def cmd_backup_stats(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора")
        return

    stats = backup_manager.get_backup_stats()
    text = (
        "📊 <b>Статистика бэкапов:</b>\n\n"
        f"• Всего бэкапов: {stats['total_backups']}\n"
        f"• Общий размер: {stats.get('total_size_formatted', '0 байт')}\n"
    )
    if stats["oldest_backup"]:
        text += f"• Самый старый: {stats['oldest_backup'].strftime('%d.%m.%Y %H:%M')}\n"
    if stats["newest_backup"]:
        text += f"• Самый новый: {stats['newest_backup'].strftime('%d.%m.%Y %H:%M')}\n"
    text += "\n📁 <b>Типы файлов:</b>\n"
    for file_type, count in stats.get("backup_types", {}).items():
        if count > 0:
            text += f"• {file_type}: {count}\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Создать бэкап", callback_data="admin_backup_menu")],
        [InlineKeyboardButton(text="📋 Список бэкапов", callback_data="list_backups")],
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
