"""
group_commands.py - Команды для работы в группах и ЛС
!число работает везде, НО НЕ ПЕРЕХВАТЫВАЕТ /start
"""

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
import re
import logging
from datetime import datetime

from database import Database

_db = Database()

router = Router()
logger = logging.getLogger(__name__)

# ========== МУТАЦИИ И ИХ ПРОЦЕНТЫ ==========
MUTATIONS = {
    "⚪️": {
        "name_ru": "Обычная",       "name_en": "Normal",
        "percentages": [100, 200, 300, 400],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
    "🟡": {
        "name_ru": "Золотая",       "name_en": "Golden",
        "percentages": [50, 75, 100, 125],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
    "💎": {
        "name_ru": "Алмазная",      "name_en": "Diamond",
        "percentages": [40, 60, 80, 100],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
    "⚡️": {
        "name_ru": "Электрическая", "name_en": "Electric",
        "percentages": [25, 37.5, 50, 62.5],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
    "🔥": {
        "name_ru": "Огненная",      "name_en": "Fiery",
        "percentages": [20, 30, 40, 50],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
    "🦖": {
        "name_ru": "Юрская",        "name_en": "Jurassic",
        "percentages": [16.67, 25, 33.33, 41.67],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
    "❄️": {
        "name_ru": "Снежная",       "name_en": "Snowy",
        "percentages": [16.67, 25, 33.33, 41.67],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
    "🎃": {
        "name_ru": "Хэллуин",       "name_en": "Halloween",
        "percentages": [15.38, 23.08, 30.78, 38.46],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
    "🦃": {
        "name_ru": "Благодарения",  "name_en": "Thanksgiving",
        "percentages": [14.81, 22.22, 29.63, 37.04],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
    "🎄": {
        "name_ru": "Рождество",     "name_en": "Christmas",
        "percentages": [13.33, 20, 26.67, 33.33],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
    "🌸🩷": {
        "name_ru": "День Валентина","name_en": "Valentine's Day",
        "percentages": [12.49, 18.75, 25, 31.24],
        "weather_keys": ["storm", "aurora", "volcano", "admin"],
    },
}

# ========== ПОГОДА: КЛЮЧИ → ЛОКАЛИЗАЦИЯ И ЭМОДЗИ ==========
WEATHER = {
    "storm":   {"ru": "Буря",    "en": "Storm",   "emoji": "💨"},
    "aurora":  {"ru": "Аврора",  "en": "Aurora",  "emoji": "🌀"},
    "volcano": {"ru": "Вулкан",  "en": "Volcano", "emoji": "🌋"},
    "admin":   {"ru": "Админ",   "en": "Admin",   "emoji": "🪯"},
}

# Хранилище для отслеживания авторов сообщений + их язык
message_authors: dict[str, dict] = {}
MESSAGE_AUTHORS_MAX_SIZE = 1000


def _mut_name(data: dict, lang: str) -> str:
    return data["name_en"] if lang == "EN" else data["name_ru"]


def _weather_name(key: str, lang: str) -> str:
    return WEATHER[key]["en"] if lang == "EN" else WEATHER[key]["ru"]


def _weather_emoji(key: str) -> str:
    return WEATHER[key]["emoji"]


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def get_mutation_keyboard(number: int, lang: str = "RUS") -> InlineKeyboardMarkup:
    """Создает инлайн-клавиатуру для выбора мутации"""
    keyboard = []
    row = []

    for i, (emoji, data) in enumerate(MUTATIONS.items(), 1):
        row.append(
            InlineKeyboardButton(
                text=f"{emoji} {_mut_name(data, lang)}",
                callback_data=f"mut_{emoji}_{number}",
            )
        )
        if i % 2 == 0:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_weather_keyboard(number: int, mutation_emoji: str, lang: str = "RUS") -> InlineKeyboardMarkup:
    """Создает инлайн-клавиатуру для выбора погоды"""
    keyboard = []
    row = []

    mutation = MUTATIONS[mutation_emoji]

    for i, wkey in enumerate(mutation["weather_keys"]):
        row.append(
            InlineKeyboardButton(
                text=f"{_weather_emoji(wkey)} {_weather_name(wkey, lang)}",
                callback_data=f"weather_{wkey}_{mutation_emoji}_{number}",
            )
        )
        if (i + 1) % 2 == 0:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def check_author(callback: types.CallbackQuery) -> bool:
    """Проверяет, является ли пользователь автором сообщения"""
    message_id = callback.message.message_id
    chat_id = callback.message.chat.id
    key = f"{chat_id}_{message_id}"

    entry = message_authors.get(key)
    author_id = entry["user_id"] if isinstance(entry, dict) else entry
    if not author_id:
        return True

    if callback.from_user.id != author_id:
        await callback.answer(
            "❌ Этот калькулятор запомнит твой отпечаток пальца и пожалуется Селене. Селена уже идет с ремнем!",
            show_alert=True,
        )
        return False

    return True


def _get_author_lang(chat_id: int, message_id: int) -> str:
    """Получить язык автора сообщения из кэша."""
    key = f"{chat_id}_{message_id}"
    entry = message_authors.get(key)
    if isinstance(entry, dict):
        return entry.get("lang", "RUS")
    return "RUS"

# ========== ОСНОВНЫЕ КОМАНДЫ - ТОЛЬКО !ЧИСЛО ==========

def fmt(n: int) -> str:
    """Форматирует число с пробелами каждые 3 цифры: 12321515 → 12 321 515"""
    return f"{n:,}".replace(",", "\u00a0")


@router.message(F.text.startswith('!'), ~F.text.regexp(r'(?i)^!(инв|инвентарь)$'))
async def handle_exclamation_command(message: Message):
    """Обработка команд с !"""
    text = message.text.strip()
    logger.info(f"🔧 Обработка команды с ! в чате '{message.chat.type}': '{text}'")

    # Убираем ! и все пробелы, точки, запятые — получаем чистое число
    raw = text[1:]
    cleaned = re.sub(r'[\s.,]', '', raw)
    if not cleaned.isdigit():
        logger.warning(f"❌ Неправильный формат команды: {text}")
        return

    number = int(cleaned)
    logger.info(f"✅ Формат правильный! Число: {number}")

    # Определяем язык пользователя
    user = _db.get_user(message.from_user.id)
    lang = (user or {}).get("language", "RUS")

    keyboard = get_mutation_keyboard(number, lang)

    if lang == "EN":
        header = (
            f"🧮 <b>Mutation Calculator</b>\n\n"
            f"<b>Number:</b> {fmt(number)}\n"
            f"<b>Choose mutation:</b>"
        )
    else:
        header = (
            f"🧮 <b>Калькулятор мутаций</b>\n\n"
            f"<b>Число:</b> {fmt(number)}\n"
            f"<b>Выберите мутацию:</b>"
        )

    try:
        sent_message = await message.reply(header, parse_mode="HTML", reply_markup=keyboard)

        # Сохраняем автора и его язык
        key = f"{sent_message.chat.id}_{sent_message.message_id}"
        message_authors[key] = {"user_id": message.from_user.id, "lang": lang}
        logger.info(f"✅ Автор сохранен: {message.from_user.id} (lang={lang}) для {key}")

        # Очищаем старые записи
        if len(message_authors) > MESSAGE_AUTHORS_MAX_SIZE:
            oldest_keys = list(message_authors.keys())[:len(message_authors) - MESSAGE_AUTHORS_MAX_SIZE]
            for old_key in oldest_keys:
                del message_authors[old_key]

    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {type(e).__name__}: {str(e)}")

# ========== ОБРАБОТКА ВЫБОРА МУТАЦИИ ==========

@router.callback_query(F.data.startswith("mut_"))
async def handle_mutation_selection(callback: types.CallbackQuery):
    """Обработка выбора мутации"""
    if not await check_author(callback):
        return

    logger.info(f"🔘 Выбрана мутация: {callback.data}")

    parts = callback.data.split("_")
    if len(parts) != 3:
        await callback.answer("❌ Ошибка данных")
        return

    mutation_emoji = parts[1]
    number = int(parts[2])

    if mutation_emoji not in MUTATIONS:
        await callback.answer("❌ Мутация не найдена" if True else "❌ Mutation not found")
        return

    lang = _get_author_lang(callback.message.chat.id, callback.message.message_id)
    mutation = MUTATIONS[mutation_emoji]
    mut_name = _mut_name(mutation, lang)

    if lang == "EN":
        result_text = (
            f"🧮 <b>Results for {fmt(number)}</b>\n\n"
            f"<b>Mutation:</b> {mutation_emoji} {mut_name}\n"
            f"🌤 <b>Weather: None</b>\n\n"
        )
    else:
        result_text = (
            f"🧮 <b>Результаты для {fmt(number)}</b>\n\n"
            f"<b>Мутация:</b> {mutation_emoji} {mut_name}\n"
            f"🌤 <b>Погода: Отсутствует</b>\n\n"
        )

    for i, percentage in enumerate(mutation["percentages"]):
        wkey = mutation["weather_keys"][i]
        emoji = _weather_emoji(wkey)
        wname = _weather_name(wkey, lang)
        result = int(number + (number * percentage / 100))
        result_text += f"{emoji}<b>{wname}:</b> {fmt(result)} (+{percentage}%)\n"

    weather_keyboard = get_weather_keyboard(number, mutation_emoji, lang)

    try:
        await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=weather_keyboard)
        await callback.answer("✅ Choose weather" if lang == "EN" else "✅ Выберите погоду")
    except Exception as e:
        logger.error(f"❌ Ошибка обновления сообщения: {type(e).__name__}: {str(e)}")

# ========== ОБРАБОТКА ВЫБОРА ПОГОДЫ ==========

@router.callback_query(F.data.startswith("weather_"))
async def handle_weather_selection(callback: types.CallbackQuery):
    """Обработка выбора погоды"""
    if not await check_author(callback):
        return

    logger.info(f"☀️ Выбрана погода: {callback.data}")

    parts = callback.data.split("_")
    if len(parts) != 4:
        await callback.answer("❌ Ошибка данных")
        return

    weather_key = parts[1]
    mutation_emoji = parts[2]
    number_with_weather = int(parts[3])

    if mutation_emoji not in MUTATIONS:
        await callback.answer("❌ Мутация не найдена")
        return

    if weather_key not in WEATHER:
        await callback.answer("❌ Погода не найдена")
        return

    lang = _get_author_lang(callback.message.chat.id, callback.message.message_id)
    mutation = MUTATIONS[mutation_emoji]
    mut_name = _mut_name(mutation, lang)

    weather_index = mutation["weather_keys"].index(weather_key)
    weather_percentage = mutation["percentages"][weather_index]

    # Вычисляем ИЗНАЧАЛЬНОЕ число БЕЗ погоды
    base_number = int(number_with_weather / (1 + weather_percentage / 100))

    w_emoji = _weather_emoji(weather_key)
    w_name = _weather_name(weather_key, lang)

    if lang == "EN":
        result_text = (
            f"🧮 <b>Results for {fmt(number_with_weather)}</b>\n\n"
            f"<b>Mutation:</b> {mutation_emoji} {mut_name}\n"
            f"{w_emoji} <b>Weather: {w_name} (+{weather_percentage}%)</b>\n\n"
        )
    else:
        result_text = (
            f"🧮 <b>Результаты для {fmt(number_with_weather)}</b>\n\n"
            f"<b>Мутация:</b> {mutation_emoji} {mut_name}\n"
            f"{w_emoji} <b>Погода: {w_name} (+{weather_percentage}%)</b>\n\n"
        )

    for i, percentage in enumerate(mutation["percentages"]):
        wkey = mutation["weather_keys"][i]
        emoji = _weather_emoji(wkey)
        wname = _weather_name(wkey, lang)

        if wkey == weather_key:
            result_text += f"{emoji}<b>{wname}:</b> {fmt(number_with_weather)} (+{percentage}%)\n"
        else:
            result = int(base_number + (base_number * percentage / 100))
            result_text += f"{emoji}<b>{wname}:</b> {fmt(result)} (+{percentage}%)\n"

    try:
        await callback.message.edit_text(result_text, parse_mode="HTML")
        await callback.answer(f"✅ {w_name}")
    except Exception as e:
        logger.error(f"❌ Ошибка обновления сообщения: {type(e).__name__}: {str(e)}")

# ========== ИНВЕНТАРЬ В ГРУППЕ ==========

ITEMS_PER_PAGE = 10


def _build_group_inv_keyboard(owner_id: int, page: int, total: int) -> "InlineKeyboardMarkup | None":
    """Кнопки пагинации для инвентаря в группе."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    if total_pages <= 1:
        return None
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ginv_page_{owner_id}_{page - 1}"))
    nav.append(InlineKeyboardButton(
        text=f"{page + 1}/{total_pages}",
        callback_data="inv_page_noop"
    ))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ginv_page_{owner_id}_{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[nav])


def _group_inv_text(items: list, lang: str, full_name: str, page: int) -> str:
    """Текст страницы инвентаря для группы."""
    if lang == "RUS":
        header = f"🎒 <b>Инвентарь {full_name}:</b>"
        empty = "🎒 Инвентарь пуст."
    else:
        header = f"🎒 <b>{full_name}'s inventory:</b>"
        empty = "🎒 Inventory is empty."

    if not items:
        return empty

    start = page * ITEMS_PER_PAGE
    page_items = items[start: start + ITEMS_PER_PAGE]
    lines = [header, ""]
    for i, item in enumerate(page_items, start + 1):
        qty = f" x{item['quantity']}" if item.get("quantity", 1) > 1 else ""
        line = f"{i}. <b>{item['name']}</b>{qty}"
        if item.get("description"):
            line += f"\n   {item['description']}"
        lines.append(line)
    return "\n".join(lines)


@router.message(F.text.regexp(r'(?i)^!(инв|инвентарь)$'))
async def group_inventory_command(message: Message):
    """!инв / !инвентарь — показать инвентарь пользователя в группе с пагинацией"""
    user_id = message.from_user.id
    user = _db.get_user(user_id)

    if not user:
        await message.reply(
            "❌ Вы не зарегистрированы в боте.\n"
            "Напишите боту в личные сообщения, чтобы зарегистрироваться."
        )
        return

    lang = user.get("language", "RUS")
    items = _db.get_user_inventory(user_id)

    if not items:
        text = "🎒 Инвентарь пуст." if lang == "RUS" else "🎒 Inventory is empty."
        await message.reply(text)
        return

    full_name = message.from_user.full_name
    text = _group_inv_text(items, lang, full_name, page=0)
    keyboard = _build_group_inv_keyboard(user_id, page=0, total=len(items))

    # Ищем первый предмет с фото
    first_photo = next((it for it in items if it.get("media_file_id") and it.get("media_type") == "photo"), None)

    if first_photo:
        try:
            await message.reply_photo(
                first_photo["media_file_id"],
                caption=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            return
        except Exception as e:
            logger.warning(f"Failed to send group inventory photo: {e}")

    await message.reply(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("ginv_page_"))
async def group_inventory_page_turn(callback: CallbackQuery):
    """Листание страниц инвентаря в группе — только владелец."""
    parts = callback.data.split("_")
    owner_id = int(parts[2])
    page = int(parts[3])

    user = _db.get_user(owner_id)
    lang = (user or {}).get("language", "RUS")

    # Проверяем что листает именно владелец
    if callback.from_user.id != owner_id:
        msg = "❌ Only the inventory owner can browse." if lang == "EN" else "❌ Листать может только владелец инвентаря."
        await callback.answer(msg, show_alert=True)
        return
    items = _db.get_user_inventory(owner_id)

    if not items:
        await callback.answer()
        return

    # Имя берём из сообщения (forward_from недоступен, берём из БД или оставляем ID)
    full_name = user.get("username") and f"@{user['username']}" or f"ID: {owner_id}"
    # Попробуем взять из текста сообщения
    if callback.message and callback.message.caption:
        import re
        m = re.search(r'<b>(?:Инвентарь |)(.+?)(?:\'s inventory)?:</b>', callback.message.caption)
        if m:
            full_name = m.group(1)
    elif callback.message and callback.message.text:
        import re
        m = re.search(r'<b>(?:Инвентарь |)(.+?)(?:\'s inventory)?:</b>', callback.message.text)
        if m:
            full_name = m.group(1)

    text = _group_inv_text(items, lang, full_name, page=page)
    keyboard = _build_group_inv_keyboard(owner_id, page=page, total=len(items))

    try:
        if callback.message.photo or callback.message.caption:
            await callback.message.edit_caption(caption=text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Failed to edit group inventory message: {e}")

    await callback.answer()


# ========== КОМАНДА ПОМОЩИ ==========

@router.message(Command("help_group"))
async def help_group_command(message: Message):
    """Команда помощи для группы"""
    if message.chat.type == "private":
        return
    
    logger.info(f"📖 Запрос помощи от {message.from_user.id}")
    
    help_text = (
        "🤖 <b>Команды бота в группе:</b>\n\n"
        "<b>!число</b> — Калькулятор мутаций\n"
        "Примеры: !1000, !500, !25 000\n\n"
        "<b>!инв</b> или <b>!инвентарь</b> — Показать свой инвентарь\n\n"
        "📱 <b>Как использовать калькулятор:</b>\n"
        "1. Напишите !число (например: !36455)\n"
        "2. Выберите мутацию\n"
        "3. Выберите погоду (Буря/Аврора/Вулкан/Админ)\n"
        "4. Получите результат для всех погод\n\n"
        "📊 <b>Доступные мутации:</b>\n"
        "⚪️ Обычная, 🟡 Золотая, 💎 Алмазная\n"
        "⚡️ Электрическая, 🔥 Огненная, 🦖 Юрская\n"
        "❄️ Снежная, 🎃 Хэллуин, 🦃 Благодарения, 🎄 Рождество, 🌸🩷 День святого Валентина"
    )
    
    await message.answer(help_text, parse_mode="HTML")

# ========== ПРОСТАЯ КОМАНДА ДЛЯ ТЕСТА ==========

@router.message(Command("ping", "test"))
async def ping_command(message: Message):
    """Проверка работы бота"""
    logger.info(f"🏓 Ping команда от {message.from_user.id} в чате {message.chat.type}")
    
    current_time = datetime.now().strftime("%H:%M:%S")
    response = (
        f"🏓 PONG!\n"
        f"🕐 Время: {current_time}\n"
        f"💬 Чат: {message.chat.title or message.chat.type}\n"
        f"👤 Отправитель: {message.from_user.full_name}\n"
        f"✅ Калькулятор мутаций с погодой работает!"
    )
    
    await message.reply(response)
    
@router.message(Command("hide_keyboard"))
async def hide_keyboard(message: Message):
    """Скрыть клавиатуру в группе"""
    from aiogram.types import ReplyKeyboardRemove
    await message.answer(
        "⌨️ Клавиатура скрыта",
        reply_markup=ReplyKeyboardRemove()
    )
