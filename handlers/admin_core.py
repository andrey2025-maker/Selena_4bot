"""
handlers/admin_core.py — Главная панель, статистика, список пользователей.
"""

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta
import logging

from config import Config
from utils.messages import locale_manager
from handlers.admin_common import db, is_admin, ADMIN_IDS, active_chats
from utils.log_events import log_admin_action

logger = logging.getLogger(__name__)
router = Router()

USER_PER_PAGE = 10


class AdminSearchStates(StatesGroup):
    waiting_for_query = State()


class AdminHiddenStates(StatesGroup):
    waiting_for_user_id = State()   # ввод @username или ID
    waiting_for_alias   = State()   # ввод псевдонима


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

async def get_user_page(page: int = 0) -> tuple[str, InlineKeyboardMarkup, int]:
    """Страница списка пользователей с пагинацией."""
    users = db.get_all_users()
    total_pages = (len(users) + USER_PER_PAGE - 1) // USER_PER_PAGE if users else 1

    start_idx = page * USER_PER_PAGE
    page_users = users[start_idx:start_idx + USER_PER_PAGE] if users else []

    text = f"📋 <b>Список пользователей ({len(users)})</b>\n"
    text += f"📄 Страница {page + 1}/{total_pages or 1}\n\n"

    if page_users:
        for i, user in enumerate(page_users, start_idx + 1):
            status = "✅" if user.get("is_subscribed") else "❌"
            uid = user["user_id"]
            if user.get("username"):
                user_link = f"<a href='tg://user?id={uid}'>@{user['username']}</a>"
            else:
                user_link = f"<a href='tg://user?id={uid}'>ID: {uid}</a>"
            text += f"{i}. {user_link} - {status}\n"
    else:
        text += "📭 Нет пользователей\n"

    active_count = sum(1 for u in users if u.get("is_subscribed"))
    if users:
        pct = active_count / len(users) * 100 if len(users) > 0 else 0.0
        text += f"\n📊 <b>Статистика:</b>\n• Активных: {active_count}/{len(users)}\n• Процент: {pct:.1f}%"

    keyboard_buttons = []
    if total_pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"userlist_page_{page-1}"))
        row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="current_page"))
        if page < total_pages - 1:
            row.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"userlist_page_{page+1}"))
        keyboard_buttons.append(row)

    keyboard_buttons.extend([
        [
            InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast_menu"),
            InlineKeyboardButton(text="💬 Связаться", callback_data="admin_start_chat"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel"),
        ],
    ])

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard_buttons), total_pages


async def show_stats(message_or_callback):
    """Показ статистики — работает и с Message, и с CallbackQuery."""
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

    try:
        stats = db.get_statistics()

        fruit_stats_text = ""
        if stats["fruit_stats"]:
            for fruit, count in stats["fruit_stats"].items():
                fruit_display = locale_manager.translate_fruit(fruit, "RUS") if fruit != "all" else "Все фрукты"
                fruit_stats_text += f"  • {fruit_display}: {count}\n"
        else:
            fruit_stats_text = "  • Нет данных\n"

        week_ago = datetime.now() - timedelta(days=7)
        all_users = db.get_all_users()
        recent_users = []
        for user in all_users:
            created = user.get("created_at")
            if isinstance(created, str):
                try:
                    # SQLite может хранить timestamp с микросекундами или без
                    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in created else "%Y-%m-%d %H:%M:%S"
                    if datetime.strptime(created, fmt) > week_ago:
                        recent_users.append(user)
                except Exception:
                    pass

        text = locale_manager.get_text("ru", "admin.stats",
            total_users=stats["total_users"],
            active_subscribers=stats["active_subscribers"],
            fruit_stats=fruit_stats_text,
            free_totems=stats["free_totems"],
            paid_totems=stats["paid_totems"],
        )
        text += f"\n📈 За последние 7 дней: {len(recent_users)} новых"
        if stats["total_users"] > 0:
            text += f"\n📊 Подписка: {stats['active_subscribers']}/{stats['total_users']} ({stats['active_subscribers']/stats['total_users']*100:.1f}%)"
        else:
            text += "\n📊 Подписка: 0/0 (0.0%)"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="admin_refresh_stats"),
                InlineKeyboardButton(text="📋 Полный список", callback_data="admin_userlist"),
            ],
            [
                InlineKeyboardButton(text="📊 Детальная статистика", callback_data="admin_detailed_stats"),
                InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel"),
            ],
            [
                InlineKeyboardButton(text="📋 Исключения", callback_data="admin_exceptions"),
                InlineKeyboardButton(text="💬 Связаться", callback_data="admin_start_chat"),
            ],
        ])

        if isinstance(message_or_callback, types.CallbackQuery):
            try:
                await message.edit_text(text, reply_markup=keyboard)
            except Exception:
                await message.answer(text, reply_markup=keyboard)
        else:
            await message.answer(text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"❌ Ошибка в show_stats: {e}")
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.answer(f"❌ Ошибка: {str(e)[:50]}", show_alert=True)
        else:
            await message.answer("❌ Ошибка при получении статистики")


async def show_admin_panel(message_or_callback):
    """Показ главной админ-панели — работает и с Message, и с CallbackQuery."""
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

    text = (
        "🛠️ <b>Панель администратора</b>\n\n"
        f"👑 Ваш ID: {user_id}\n"
        f"📋 Админов: {len(ADMIN_IDS)}\n"
        f"💬 Активных чатов: {len(active_chats)}\n\n"
        "Выберите действие:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="📋 Список", callback_data="admin_userlist"),
        ],
        [
            InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast_menu"),
            InlineKeyboardButton(text="💬 Связаться", callback_data="admin_start_chat"),
        ],
        [
            InlineKeyboardButton(text="📋 Исключения", callback_data="admin_exceptions"),
            InlineKeyboardButton(text="🔍 Поиск", callback_data="admin_search"),
        ],
        [
            InlineKeyboardButton(text="🧹 Очистка", callback_data="admin_cleanup"),
            InlineKeyboardButton(text="🛠️ Утилиты", callback_data="admin_utils"),
        ],
        [
            InlineKeyboardButton(text="🎒 Инвентарь", callback_data="admin_inventory_menu"),
            InlineKeyboardButton(text="🎰 Розыгрыш", callback_data="admin_giveaway_menu"),
        ],
        [
            InlineKeyboardButton(text="🐾 Петы", callback_data="admin_pets_list"),
            InlineKeyboardButton(text="🎮 Roblox-ники", callback_data="admin_roblox_nicks"),
        ],
        [
            InlineKeyboardButton(text="💾 Бэкапы", callback_data="admin_backup_menu"),
            InlineKeyboardButton(text="🕵️ Скрыть", callback_data="admin_hidden_menu"),
        ],
        [
            InlineKeyboardButton(text="ℹ️ О боте", callback_data="admin_about"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_refresh"),
        ],
    ])

    if isinstance(message_or_callback, types.CallbackQuery):
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


# ========== КОМАНДЫ ==========

@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin(message: Message):
    logger.info(f"[/admin] от {message.from_user.id} (@{message.from_user.username})")
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return
    try:
        await show_admin_panel(message)
    except Exception as e:
        logger.error(f"[/admin] ошибка: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка при открытии панели: {e}")

@router.message(Command("stats"), F.chat.type == "private")
async def cmd_stats(message: Message):
    logger.info(f"[/stats] от {message.from_user.id}")
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return
    try:
        await show_stats(message)
    except Exception as e:
        logger.error(f"[/stats] ошибка: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}")


# ========== CALLBACKS: ПАНЕЛЬ И СТАТИСТИКА ==========

@router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: types.CallbackQuery):
    await show_stats(callback)
    await callback.answer()

@router.callback_query(F.data == "admin_back_to_stats")
async def back_to_stats(callback: types.CallbackQuery):
    await show_stats(callback)
    await callback.answer("✅ Возврат к статистике")

@router.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback: types.CallbackQuery):
    await show_admin_panel(callback)
    await callback.answer()

@router.callback_query(F.data == "admin_refresh_stats")
async def refresh_stats(callback: types.CallbackQuery):
    await show_stats(callback)
    await callback.answer("✅ Статистика обновлена!")

@router.callback_query(F.data == "admin_refresh")
async def admin_refresh_callback(callback: types.CallbackQuery):
    await show_admin_panel(callback)
    await callback.answer("🔄 Панель обновлена!")


# ========== CALLBACKS: СПИСОК ПОЛЬЗОВАТЕЛЕЙ ==========

@router.callback_query(F.data.startswith("userlist_page_"))
async def userlist_page_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    page = int(callback.data.split("_")[-1])
    text, keyboard, _ = await get_user_page(page)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data == "admin_userlist")
async def admin_userlist_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    text, keyboard, _ = await get_user_page(0)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ========== CALLBACKS: ПРОЧИЕ КНОПКИ ПАНЕЛИ ==========

@router.callback_query(F.data == "admin_search")
async def admin_search_callback(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    await state.set_state(AdminSearchStates.waiting_for_query)
    await callback.message.answer(
        "🔍 <b>Поиск пользователя</b>\n\n"
        "Введите одно из:\n"
        "• <code>@username</code> — поиск по тегу\n"
        "• Числовой ID — поиск по Telegram ID\n"
        "• Имя или часть имени\n"
        "• Roblox-ник\n\n"
        "❌ /cancel — отменить",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_for_query, F.chat.type == "private")
async def admin_search_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    query = message.text.strip() if message.text else ""

    if query == "/cancel":
        await state.clear()
        await message.answer("🚫 Поиск отменён.")
        return

    if not query:
        await state.clear()
        await message.answer("❌ Введите текстовый запрос для поиска.")
        return

    all_users = db.get_all_users()
    results = []
    q_lower = query.lower().lstrip("@")

    for user in all_users:
        uid = user.get("user_id", 0)
        username = (user.get("username") or "").lower()
        roblox = (user.get("roblox_nick") or "").lower()

        # Точное совпадение по ID
        if query.isdigit() and int(query) == uid:
            results.append(user)
            continue

        # Совпадение по username (с @ или без)
        if q_lower and username and (q_lower == username or q_lower in username):
            results.append(user)
            continue

        # Совпадение по Roblox-нику
        if q_lower and roblox and q_lower in roblox:
            results.append(user)
            continue

    await state.clear()

    if not results:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="admin_search")],
            [InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel")],
        ])
        await message.answer(
            f"🔍 По запросу <b>{query}</b> ничего не найдено.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # Формируем результаты
    text = f"🔍 <b>Результаты поиска:</b> «{query}» — найдено {len(results)}\n\n"
    for user in results[:20]:
        uid = user["user_id"]
        username = user.get("username")
        roblox = user.get("roblox_nick")
        is_sub = "✅" if user.get("is_subscribed") else "❌"
        lang = user.get("language", "?")

        if username:
            display = f"@{username}"
        else:
            display = f"ID: {uid}"

        name_link = f'<a href="tg://user?id={uid}">{display}</a>'
        roblox_str = f" | 🎮 {roblox}" if roblox else ""
        text += f"• {name_link} <code>{uid}</code>{roblox_str} {is_sub} {lang}\n"

    if len(results) > 20:
        text += f"\n<i>...и ещё {len(results) - 20}. Уточните запрос.</i>"

    # Кнопки действий для первого найденного пользователя
    first_uid = results[0]["user_id"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💬 Написать", callback_data=f"inv_adm_chat_{first_uid}"),
            InlineKeyboardButton(text="🎒 Инвентарь", callback_data=f"inv_adm_view_{first_uid}"),
        ],
        [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="admin_search")],
        [InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel")],
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@router.callback_query(F.data == "admin_cleanup")
async def admin_cleanup_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    await callback.answer("🧹 Запускаю очистку…")
    try:
        result = db.cleanup_old_data(days=14)
        total = sum(result.values())
        details = ", ".join(f"{k}: {v}" for k, v in result.items() if v > 0) or "нечего удалять"
        await callback.message.answer(
            f"🧹 <b>Очистка завершена</b>\n"
            f"Удалено записей: <b>{total}</b>\n"
            f"<i>{details}</i>",
            parse_mode="HTML",
        )
        try:
            await log_admin_action(
                callback.bot,
                admin_id=callback.from_user.id,
                admin_name=callback.from_user.full_name,
                action="Ручная очистка БД",
                details=f"Удалено {total} записей ({details})",
            )
        except Exception:
            pass
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка очистки: {e}")

@router.callback_query(F.data == "admin_utils")
async def admin_utils_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    await callback.answer("🛠️ Функция в разработке")
    await callback.message.answer("🛠️ Утилиты будут доступны в следующем обновлении.")

@router.callback_query(F.data == "admin_about")
async def admin_about_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    text = (
        "🤖 <b>Build a Zoo Notification Bot</b>\n\n"
        f"<b>Версия:</b> 2.0\n"
        f"<b>Ваш ID:</b> {callback.from_user.id}\n"
        f"<b>Админов:</b> {len(ADMIN_IDS)}\n"
        f"<b>Канал:</b> {Config.SOURCE_CHANNEL_ID}\n"
        f"<b>Активных чатов:</b> {len(active_chats)}\n\n"
        "<b>Функции:</b>\n"
        "• 📄 Пагинация списка пользователей\n"
        "• 💬 Система двусторонней связи\n"
        "• 📋 Управление исключениями подписок\n"
        "• 🌐 Рассылка по языкам\n"
        "• 🎒 Инвентарь и обмены\n"
        "• 🎰 Розыгрыши\n\n"
        "<i>Бот для уведомлений о фруктах и тотемах в Build a Zoo</i>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠️ В админ-панель", callback_data="admin_panel")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data == "admin_detailed_stats")
async def admin_detailed_stats_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора", show_alert=True)
        return
    stats = db.get_statistics()
    exceptions = db.get_exceptions() if hasattr(db, "get_exceptions") else []
    text = (
        "📊 <b>Детальная статистика:</b>\n\n"
        f"👥 <b>Общая информация:</b>\n"
        f"• Всего пользователей: {stats['total_users']}\n"
        f"• Активных подписчиков: {stats['active_subscribers']}\n"
        f"• Исключений: {len(exceptions)}\n"
        f"• Активных чатов: {len(active_chats)}\n\n"
        f"🗿 <b>Настройки тотемов:</b>\n"
        f"• Free тотемы: {stats['free_totems']}\n"
        f"• Paid тотемы: {stats['paid_totems']}\n\n"
        "🍎 <b>Популярность фруктов:</b>\n"
    )
    if stats["fruit_stats"]:
        for fruit, count in stats["fruit_stats"].items():
            text += f"• {fruit}: {count}\n"
    else:
        text += "Нет данных\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Назад к статистике", callback_data="admin_stats")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ═══════════════════════════════════════════════════════════════
# РАЗДЕЛ «СКРЫТЬ» — псевдонимы для публичного отображения
# ═══════════════════════════════════════════════════════════════

def _hidden_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="admin_hidden_add"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data="admin_hidden_del"),
        ],
        [InlineKeyboardButton(text="📋 Список", callback_data="admin_hidden_list")],
        [InlineKeyboardButton(text="🛠️ Назад", callback_data="admin_panel")],
    ])


@router.callback_query(F.data == "admin_hidden_menu")
async def admin_hidden_menu(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    hidden = db.get_all_hidden_users()
    count = len(hidden)
    text = (
        "🕵️ <b>Скрытые пользователи</b>\n\n"
        f"Скрыто: <b>{count}</b>\n\n"
        "Скрытые пользователи отображаются под псевдонимом в инвентаре, "
        "P2P-обменах и обменах через администратора.\n"
        "Логи и данные для администраторов остаются без изменений."
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_hidden_menu_keyboard())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=_hidden_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_hidden_list")
async def admin_hidden_list(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    hidden = db.get_all_hidden_users()
    if not hidden:
        text = "🕵️ <b>Скрытые пользователи</b>\n\nСписок пуст."
    else:
        lines = ["🕵️ <b>Скрытые пользователи:</b>\n"]
        for h in hidden:
            uid = h["user_id"]
            user = db.get_user(uid)
            real = f"@{user['username']}" if user and user.get("username") else f"ID:{uid}"
            lines.append(f"• <a href=\"tg://user?id={uid}\">{real}</a> → «{h['alias']}»")
        text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_hidden_menu")]
    ])
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard,
                                         disable_web_page_preview=True)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard,
                                      disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data == "admin_hidden_add")
async def admin_hidden_add_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await state.set_state(AdminHiddenStates.waiting_for_user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_hidden_cancel")]
    ])
    await callback.message.answer(
        "🕵️ <b>Добавить скрытого пользователя</b>\n\n"
        "Введите <b>@username</b> или <b>числовой ID</b> пользователя:",
        parse_mode="HTML", reply_markup=keyboard,
    )
    await callback.answer()


@router.message(StateFilter(AdminHiddenStates.waiting_for_user_id))
async def admin_hidden_receive_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    # Ищем пользователя
    if text.startswith("@"):
        user = db.get_user_by_username(text[1:])
    elif text.lstrip("-").isdigit():
        user = db.get_user(int(text))
    else:
        user = db.get_user_by_username(text)

    if not user:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_hidden_cancel")]
        ])
        await message.answer(
            f"❌ Пользователь <code>{text}</code> не найден.\nПопробуйте ещё раз:",
            parse_mode="HTML", reply_markup=keyboard,
        )
        return

    uid = user["user_id"]
    real = f"@{user['username']}" if user.get("username") else f"ID:{uid}"
    await state.update_data(target_user_id=uid, target_real=real)
    await state.set_state(AdminHiddenStates.waiting_for_alias)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_hidden_cancel")]
    ])
    await message.answer(
        f"✅ Найден: <a href=\"tg://user?id={uid}\">{real}</a>\n\n"
        "Введите <b>псевдоним</b>, который будет показываться вместо имени:",
        parse_mode="HTML", reply_markup=keyboard,
    )


@router.message(StateFilter(AdminHiddenStates.waiting_for_alias))
async def admin_hidden_receive_alias(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    alias = (message.text or "").strip()
    if not alias:
        await message.answer("❌ Псевдоним не может быть пустым. Введите ещё раз:")
        return

    data = await state.get_data()
    uid = data["target_user_id"]
    real = data["target_real"]

    db.add_hidden_user(uid, alias, added_by=message.from_user.id)
    await state.clear()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕵️ К списку скрытых", callback_data="admin_hidden_menu")]
    ])
    await message.answer(
        f"✅ <b>Пользователь скрыт</b>\n\n"
        f"Кто: <a href=\"tg://user?id={uid}\">{real}</a>\n"
        f"Псевдоним: «{alias}»\n\n"
        f"Теперь в публичных местах вместо имени будет отображаться «{alias}».",
        parse_mode="HTML", reply_markup=keyboard,
    )
    try:
        await log_admin_action(
            message.bot,
            admin_id=message.from_user.id,
            admin_name=message.from_user.full_name,
            action="Скрыть пользователя",
            details=f"{real} (ID:{uid}) → псевдоним «{alias}»",
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_hidden_del")
async def admin_hidden_del_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    hidden = db.get_all_hidden_users()
    if not hidden:
        await callback.answer("Список скрытых пуст.", show_alert=True)
        return

    rows = []
    for h in hidden:
        uid = h["user_id"]
        user = db.get_user(uid)
        real = f"@{user['username']}" if user and user.get("username") else f"ID:{uid}"
        rows.append([InlineKeyboardButton(
            text=f"🗑 {real} → «{h['alias']}»",
            callback_data=f"admin_hidden_remove_{uid}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_hidden_menu")])

    await callback.message.answer(
        "🗑 <b>Выберите пользователя для удаления из скрытых:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_hidden_remove_"))
async def admin_hidden_remove(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    uid = int(callback.data.split("_")[-1])
    hidden = db.get_hidden_user(uid)
    alias = hidden["alias"] if hidden else "?"
    user = db.get_user(uid)
    real = f"@{user['username']}" if user and user.get("username") else f"ID:{uid}"

    db.remove_hidden_user(uid)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕵️ К списку скрытых", callback_data="admin_hidden_menu")]
    ])
    try:
        await callback.message.edit_text(
            f"✅ <a href=\"tg://user?id={uid}\">{real}</a> удалён из скрытых.\n"
            f"Псевдоним «{alias}» больше не используется.",
            parse_mode="HTML", reply_markup=keyboard,
        )
    except Exception:
        await callback.message.answer(
            f"✅ {real} удалён из скрытых.", parse_mode="HTML", reply_markup=keyboard,
        )
    await callback.answer()
    try:
        await log_admin_action(
            callback.bot,
            admin_id=callback.from_user.id,
            admin_name=callback.from_user.full_name,
            action="Раскрыть пользователя",
            details=f"{real} (ID:{uid}), был псевдоним «{alias}»",
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_hidden_cancel")
async def admin_hidden_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕵️ К скрытым", callback_data="admin_hidden_menu")]
    ])
    try:
        await callback.message.edit_text("❌ Отменено.", reply_markup=keyboard)
    except Exception:
        await callback.message.answer("❌ Отменено.", reply_markup=keyboard)
    await callback.answer()
