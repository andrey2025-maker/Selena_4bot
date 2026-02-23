"""
start.py - Обработчик команды /start и проверки подписки
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from database import Database
from utils.messages import locale_manager
from utils.keyboards import get_main_keyboard
from utils.subscription import check_user_subscription
from config import Config
import logging

router = Router()
db = Database()
logger = logging.getLogger(__name__)

async def get_user_language(user_id: int) -> str:
    """Получение языка пользователя"""
    user = db.get_user(user_id)
    return user.get("language", "RUS") if user else "RUS"

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработка команды /start"""
    if message.chat.type != "private":
        return
    user_id = message.from_user.id
    username = message.from_user.username
    
    # Добавляем пользователя в БД
    db.add_user(user_id, username)
    logger.info(f"Пользователь {user_id} запустил бота")
    
    # Текст на двух языках
    text = (
        f"{locale_manager.get_text('ru', 'start.welcome')}\n"
        f"{locale_manager.get_text('ru', 'start.choose_language')}\n\n"
        f"{locale_manager.get_text('en', 'start.welcome')}\n"
        f"{locale_manager.get_text('en', 'start.choose_language')}"
    )
    
    # Клавиатура выбора языка
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="🇷🇺 Русский", callback_data="lang_rus")
    builder.button(text="🇺🇸 English", callback_data="lang_en")
    builder.adjust(2)
    
    await message.answer(text, reply_markup=builder.as_markup())

@router.callback_query(F.data.in_(["lang_rus", "lang_en"]))
async def set_language(callback: CallbackQuery):
    """Установка языка после выбора"""
    user_id = callback.from_user.id
    lang = "RUS" if callback.data == "lang_rus" else "EN"
    
    # Сохраняем язык в БД
    db.update_user_language(user_id, lang)
    lang_code = "ru" if lang == "RUS" else "en"
    
    logger.info(f"Пользователь {user_id} выбрал язык: {lang}")
    
    # Удаляем сообщение с выбором языка
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    # Проверяем подписку
    is_subscribed = await check_user_subscription(
        user_id, 
        Config.REQUIRED_GROUP_ID, 
        callback.bot
    )
    
    is_exception = db.is_exception(user_id)
    
    if is_subscribed or is_exception:
        # Уже подписан - показываем настройки и главную клавиатуру
        await show_settings_menu(callback.message, user_id, lang, lang_code, callback.bot)
    else:
        # Требуется подписка
        require_text = locale_manager.get_text(lang_code, "subscription.require")
        check_button_text = locale_manager.get_text(lang_code, "subscription.check_button")
        
        # Клавиатура с кнопкой проверки
        check_keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=check_button_text)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        
        await callback.message.answer(
            require_text,
            reply_markup=check_keyboard
        )
    
    await callback.answer()

@router.message(F.text.in_(["🔍 Проверить подписку", "🔍 Check subscription"]))
async def check_subscription(message: Message):
    """Проверка подписки по кнопке"""
    if message.chat.type != "private":
        return
    user_id = message.from_user.id
    lang = await get_user_language(user_id)
    lang_code = "ru" if lang == "RUS" else "en"
    
    logger.info(f"Пользователь {user_id} проверяет подписку")
    
    is_subscribed = await check_user_subscription(
        user_id, 
        Config.REQUIRED_GROUP_ID, 
        message.bot
    )
    
    is_exception = db.is_exception(user_id)
    
    if is_subscribed or is_exception:
        # Подписка подтверждена - показываем настройки
        await show_settings_menu(message, user_id, lang, lang_code, message.bot)
    else:
        # Не подписан
        not_subscribed_text = locale_manager.get_text(lang_code, "subscription.not_subscribed")
        check_button_text = locale_manager.get_text(lang_code, "subscription.check_button")
        
        # Показываем снова кнопку проверки
        check_keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=check_button_text)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        
        await message.answer(
            not_subscribed_text,
            reply_markup=check_keyboard
        )

async def show_settings_menu(message: Message, user_id: int, lang: str, lang_code: str, bot,
                             send_reply_keyboard: bool = True):
    """Показывает меню настроек — одно сообщение с текстом и инлайн-кнопками.

    send_reply_keyboard=True при первом входе (после /start / выбора языка),
    чтобы обновить reply-клавиатуру. При повторных вызовах она уже есть.
    """

    user = db.get_user(user_id)
    user_fruits = db.get_user_fruits(user_id)

    fruits_text = ""
    if user_fruits:
        if "all" in user_fruits:
            fruits_text = locale_manager.get_text(lang_code, "settings.all_fruits")
        else:
            fruit_names = []
            for fruit in user_fruits:
                display = locale_manager.get_fruit_display(fruit, lang)
                fruit_names.append(display)
            fruits_text = ", ".join(fruit_names)
    else:
        fruits_text = locale_manager.get_text(lang_code, "settings.no_fruits_selected")

    free_status = "✅" if user and user.get("free_totems", 1) else "❌"
    paid_status = "✅" if user and user.get("paid_totems", 1) else "❌"

    settings_text = locale_manager.get_text(lang_code, "settings.title")
    settings_text += (
        f"\n\n{locale_manager.get_text(lang_code, 'settings.current_header')}\n"
        f"{locale_manager.get_text(lang_code, 'settings.fruits_label')}: {fruits_text}\n"
        f"🗿 Free: {free_status}\n💎 Paid: {paid_status}"
    )

    from handlers.settings import get_settings_keyboard

    if send_reply_keyboard:
        # При первом входе — приветствие обновляет reply-клавиатуру
        welcome = locale_manager.get_text(lang_code, "start.welcome_menu")
        await message.answer(welcome, parse_mode="HTML", reply_markup=get_main_keyboard(lang))

    # Одно сообщение с текстом настроек + инлайн-кнопками (как «Уведомления»)
    await message.answer(
        settings_text,
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(lang, user or {})
    )

@router.message(F.text.in_(["🔔 Уведомления", "🔔 Notifications"]))
async def show_notifications_menu(message: Message):
    """Показ меню уведомлений"""
    if message.chat.type != "private":
        return
    user_id = message.from_user.id
    lang = await get_user_language(user_id)
    lang_code = "ru" if lang == "RUS" else "en"
    
    # Проверяем подписку
    is_subscribed = await check_user_subscription(
        user_id, 
        Config.REQUIRED_GROUP_ID, 
        message.bot
    )
    
    if not is_subscribed and not db.is_exception(user_id):
        not_subscribed_text = locale_manager.get_text(lang_code, "subscription.not_subscribed")
        check_button_text = locale_manager.get_text(lang_code, "subscription.check_button")
        
        check_keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=check_button_text)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        
        await message.answer(
            not_subscribed_text,
            reply_markup=check_keyboard
        )
        return
    
    # Получаем текущие настройки пользователя
    user = db.get_user(user_id)
    user_fruits = db.get_user_fruits(user_id)
    
    # Формируем текст о текущих настройках
    fruits_text = ""
    if user_fruits:
        if "all" in user_fruits:
            fruits_text = locale_manager.get_text(lang_code, "settings.all_fruits")
        else:
            fruit_names = []
            for fruit in user_fruits:
                display = locale_manager.get_fruit_display(fruit, lang)
                fruit_names.append(display)
            fruits_text = ", ".join(fruit_names)
    else:
        fruits_text = locale_manager.get_text(lang_code, "settings.no_fruits_selected")

    free_status = "✅" if user.get("free_totems", 1) else "❌"
    paid_status = "✅" if user.get("paid_totems", 1) else "❌"

    settings_text = locale_manager.get_text(lang_code, "settings.title")
    settings_text += (
        f"\n\n{locale_manager.get_text(lang_code, 'settings.current_header')}\n"
        f"{locale_manager.get_text(lang_code, 'settings.fruits_label')}: {fruits_text}\n"
        f"🗿 Free: {free_status}\n💎 Paid: {paid_status}"
    )
    
    from handlers.settings import get_settings_keyboard
    
    # Показываем настройки с инлайн-кнопками (главная клавиатура уже есть)
    await message.answer(
        settings_text,
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(lang, user)
    )

@router.message(F.text.in_(["🔕 Отключить", "🔕 Disable"]))
async def disable_notifications(message: Message):
    """Полное отключение всех уведомлений"""
    if message.chat.type != "private":
        return
    user_id = message.from_user.id
    lang = await get_user_language(user_id)
    
    # Отключаем все уведомления
    db.update_user_fruits(user_id, [])
    db.update_totem_settings(user_id, free_totems=False, paid_totems=False)
    
    lc = "ru" if lang == "RUS" else "en"
    text = locale_manager.get_text(lc, "settings.notifications_disabled")
    
    await message.answer(
        text,
        reply_markup=get_main_keyboard(lang)
    )

@router.message(F.text.in_(["❓ Помощь", "❓ Help"]))
async def show_help(message: Message):
    """Показ справки"""
    if message.chat.type != "private":
        return
    user_id = message.from_user.id
    lang = await get_user_language(user_id)
    lang_code = "ru" if lang == "RUS" else "en"
    
    help_text = {
        "ru": (
            "❓ <b>Помощь по боту</b>\n\n"
            "🔔 <b>Уведомления</b> — настройка фруктов и тотемов\n"
            "🔕 <b>Отключить</b> — полностью отключить все уведомления\n"
            "🎒 <b>Инвентарь</b> — просмотр ваших предметов, запрос на выдачу\n"
            "🔄 <b>Обмен</b> — обмен предметами с другим игроком\n\n"
            "<b>Команды в группе:</b>\n"
            "!число — калькулятор мутаций (например: !36455, !1 500 000)\n"
            "!инв / !инвентарь — показать свой инвентарь\n\n"
            "<b>Доступные команды:</b>\n"
            "/start — перезапустить бота\n"
            "/language — смена языка\n\n"
            "<b>Как работает калькулятор мутаций:</b>\n"
            "1. Напишите !число (например: !36455)\n"
            "2. Выберите мутацию\n"
            "3. Выберите погоду\n"
            "4. Получите результат\n\n"
            "<b>Поддержка:</b>\n"
            "По вопросам обращайтесь в группу @buildazoo_chat"
        ),
        "en": (
            "❓ <b>Bot Help</b>\n\n"
            "🔔 <b>Notifications</b> — configure fruits and totems\n"
            "🔕 <b>Disable</b> — completely disable all notifications\n"
            "🎒 <b>Inventory</b> — view your items, request pickup\n"
            "🔄 <b>Trade</b> — trade items with another player\n\n"
            "<b>Group commands:</b>\n"
            "!number — mutation calculator (e.g. !36455, !1 500 000)\n"
            "!inv / !inventory — show your inventory\n\n"
            "<b>Available commands:</b>\n"
            "/start — restart bot\n"
            "/language — change language\n\n"
            "<b>How the mutation calculator works:</b>\n"
            "1. Type !number (e.g. !36455)\n"
            "2. Choose a mutation\n"
            "3. Choose weather\n"
            "4. Get the result\n\n"
            "<b>Support:</b>\n"
            "Join our group @buildazoo_chat for help"
        ),
    }
    
    await message.answer(
        help_text[lang_code],
        parse_mode="HTML",
        reply_markup=get_main_keyboard(lang)
    )

@router.message(Command("кнопки", "buttons"), F.chat.type == "private")
async def cmd_refresh_keyboard(message: Message):
    """Рассылка обновлённой клавиатуры всем пользователям (только для админов)"""
    from handlers.admin_common import is_admin

    user_id = message.from_user.id

    if not is_admin(user_id):
        await message.answer("⛔ У вас нет прав для этой команды.")
        return

    await message.answer("⏳ Рассылаю обновлённую клавиатуру всем пользователям...")

    users = db.get_all_users()
    sent = 0
    failed = 0

    for user in users:
        uid = user["user_id"]
        lang = user.get("language", "RUS")
        lang_code = "ru" if lang == "RUS" else "en"
        text = "✅ Клавиатура обновлена!" if lang_code == "ru" else "✅ Keyboard updated!"
        try:
            await message.bot.send_message(uid, text, reply_markup=get_main_keyboard(lang))
            sent += 1
        except Exception:
            failed += 1

    await message.answer(
        f"✅ Готово! Отправлено: {sent}, не доставлено: {failed} (заблокировали бота)."
    )


@router.message(Command("language"), F.chat.type == "private")
async def cmd_language(message: Message):
    """Команда для смены языка"""
    user_id = message.from_user.id
    
    language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_rus")],
        [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
    ])
    
    await message.answer(
        locale_manager.get_text("ru", "start.choose_language_change"),
        reply_markup=language_keyboard
    )
