import asyncio
import logging
from aiogram import Router, Bot, F  # ДОБАВЬТЕ F СЮДА!
from aiogram.types import Message
from aiogram.filters import Command
from aiogram import exceptions
from aiogram.enums import ChatType

from database import Database
from config import Config
from utils.filters import MessageFilter

router = Router()
db = Database()
logger = logging.getLogger(__name__)

async def send_with_semaphore(bot: Bot, user_id: int, text: str, parse_mode: str, semaphore: asyncio.Semaphore):
    """Отправка сообщения с ограничением количества одновременных запросов"""
    async with semaphore:
        try:
            await bot.send_message(user_id, text, parse_mode=parse_mode)
            return True, None
        except exceptions.TelegramAPIError as e:
            # Проверяем, не является ли это ошибкой rate limit
            error_msg = str(e)
            if "Too Many Requests" in error_msg or "retry after" in error_msg.lower():
                # Пытаемся извлечь время ожидания из ошибки
                import re
                retry_match = re.search(r'retry after (\d+)', error_msg.lower())
                if retry_match:
                    wait_time = int(retry_match.group(1))
                else:
                    wait_time = 5  # По умолчанию 5 секунд
                
                logger.warning(f"Rate limit hit for user {user_id}, waiting {wait_time} seconds")
                await asyncio.sleep(wait_time)
                # Пробуем еще раз после ожидания
                try:
                    await bot.send_message(user_id, text, parse_mode=parse_mode)
                    return True, None
                except Exception as retry_error:
                    return False, str(retry_error)
            else:
                return False, error_msg
        except Exception as e:
            error_msg = str(e)
            if "Forbidden" in error_msg or "bot was blocked" in error_msg:
                logger.warning(f"User {user_id} blocked the bot")
            elif "chat not found" in error_msg:
                logger.warning(f"Chat with user {user_id} not found")
            else:
                logger.error(f"Error sending to user {user_id}: {error_msg}")
            return False, error_msg

@router.channel_post()
async def handle_channel_post(message: Message, bot: Bot):
    """Обработка сообщений из каналов"""
    # Проверяем, что это наш канал-источник
    if message.chat.id != Config.SOURCE_CHANNEL_ID:
        return
    
    text = message.text or message.caption or ""
    
    if not text:
        return
    
    logger.info(f"🚀 ПОЛУЧЕНО СООБЩЕНИЕ ИЗ КАНАЛА!")
    logger.info(f"📝 Текст: {text[:200]}")
    
    # Классифицируем сообщение
    classification = MessageFilter.classify_message(text)
    logger.info(f"🔍 Классификация: {classification['type']}")
    
    if classification["type"] == "food":
        fruits = classification["data"]
        if not fruits:
            logger.warning("⚠️ Найдены фрукты, но список пуст!")
            return
            
        logger.info(f"🍎 Найдены фрукты ({len(fruits)} шт): {[f['name'] for f in fruits]}")
        await process_food_notification(fruits, bot)
        logger.info(f"✅ Рассылка еды завершена")
        
    elif classification["type"] == "totem":
        logger.info(f"🗿 Найден тотем ({classification['subtype']})")
        await process_totem_notification(
            classification["subtype"],
            classification["text"],
            classification["link"],
            bot
        )
        logger.info(f"✅ Рассылка тотемов завершена")
    else:
        logger.warning(f"❌ Сообщение не распознано")

async def process_food_notification(fruits_data: list, bot: Bot):
    """Обработка и рассылка уведомлений о еде"""
    # Собираем всех пользователей для всех фруктов
    all_user_ids = set()
    fruit_users = {}
    
    for fruit_data in fruits_data:
        fruit_name = fruit_data["name"]
        user_ids = db.get_users_for_fruit(fruit_name)
        fruit_users[fruit_name] = user_ids
        all_user_ids.update(user_ids)
    
    logger.info(f"🍎 Рассылка уведомлений для {len(all_user_ids)} пользователей")
    logger.info(f"🍏 Фрукты для рассылки: {[f['name'] for f in fruits_data]}")
    
    if not all_user_ids:
        logger.warning("⚠️ Нет пользователей для рассылки!")
        return
    
    # Создаем семафор для ограничения одновременных отправок (20 одновременных запросов)
    semaphore = asyncio.Semaphore(20)
    tasks = []
    sent_count = 0
    error_count = 0
    
    for user_id in all_user_ids:
        user = db.get_user(user_id)
        if not user:
            logger.warning(f"❌ Пользователь {user_id} не найден в БД")
            continue
        
        if not user.get("is_subscribed", 0):
            logger.warning(f"❌ Пользователь {user_id} не подписан, пропускаем")
            continue
        
        lang = user.get("language", "RUS")
        
        # Формируем список фруктов для этого пользователя
        user_fruits = []
        for fruit_data in fruits_data:
            fruit_name = fruit_data["name"]
            if user_id in fruit_users.get(fruit_name, []):
                user_fruits.append(fruit_data)
        
        if not user_fruits:
            logger.debug(f"🔕 Пользователь {user_id} не подписан на эти фрукты")
            continue
        
        # Форматируем сообщение БЕЗ заголовка
        message_text = MessageFilter.format_food_message(user_fruits, lang)
        
        # Создаем задачу для отправки
        task = send_with_semaphore(bot, user_id, message_text, "HTML", semaphore)
        tasks.append(task)
    
    # Выполняем все задачи параллельно с ограничением
    results = await asyncio.gather(*tasks)
    
    for success, error in results:
        if success:
            sent_count += 1
        else:
            error_count += 1
    
    logger.info(f"📊 Итог: отправлено {sent_count}, ошибок {error_count}")

async def process_totem_notification(totem_type: str, text: str, link: str, bot: Bot):
    """Обработка и рассылка уведомлений о тотемах"""
    is_free = totem_type == "free"
    user_ids = db.get_users_for_totem(is_free)
    
    logger.info(f"🗿 Рассылка {totem_type} тотемов для {len(user_ids)} пользователей")
    
    if not user_ids:
        logger.warning(f"⚠️ Нет пользователей для рассылки {totem_type} тотемов")
        return
    
    # Создаем семафор для ограничения одновременных отправок
    semaphore = asyncio.Semaphore(20)
    tasks = []
    
    for user_id in user_ids:
        user = db.get_user(user_id)
        if not user or not user.get("is_subscribed", 0):
            continue
        
        lang = user.get("language", "RUS")
        
        # Форматируем сообщение
        message_text = MessageFilter.format_totem_message(totem_type, text, link, lang)
        
        # Создаем задачу для отправки
        task = send_with_semaphore(bot, user_id, message_text, "Markdown", semaphore)
        tasks.append(task)
    
    # Выполняем все задачи параллельно с ограничением
    results = await asyncio.gather(*tasks)
    
    sent_count = sum(1 for success, _ in results if success)
    error_count = sum(1 for success, _ in results if not success)
    
    logger.info(f"📊 Итог тотемы: отправлено {sent_count}, ошибок {error_count}")


# ========== КОМАНДЫ ТОЛЬКО В ЛИЧНЫХ СООБЩЕНИЯХ ==========

@router.message(Command("test_channel"), F.chat.type == ChatType.PRIVATE)
async def test_channel_command(message: Message, bot: Bot):
    """Тестовая команда для проверки работы канала - ТОЛЬКО в личных сообщениях"""
    await message.answer("✅ Канал работает! Бот получает сообщения.")

@router.message(Command("debug_fruits"), F.chat.type == ChatType.PRIVATE)
async def debug_fruits_command(message: Message):
    """Отладка выбора фруктов - ТОЛЬКО в личных сообщениях"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    user_fruits = db.get_user_fruits(user_id)
    
    response = f"🔍 ВАШИ ФРУКТЫ:\n\n"
    response += f"ID: {user_id}\n"
    response += f"Подписка: {'✅ да' if user and user.get('is_subscribed') else '❌ нет'}\n"
    response += f"Язык: {user.get('language') if user else 'неизвестно'}\n"
    response += f"Выбранные фрукты: {', '.join(user_fruits) if user_fruits else 'НЕТ'}\n\n"
    
    # Пример проверки конкретных фруктов
    has_pineapple = "Pineapple" in user_fruits or "all" in user_fruits
    has_dragon = "Dragon Fruit" in user_fruits or "all" in user_fruits
    
    response += f"🍍 Pineapple: {'✅ выбран' if has_pineapple else '❌ не выбран'}\n"
    response += f"🐲 Dragon Fruit: {'✅ выбран' if has_dragon else '❌ не выбран'}\n\n"
    
    if has_pineapple or has_dragon:
        response += "✅ Вы ДОЛЖНЫ получать уведомления об этих фруктах!"
    else:
        response += "❌ Вы НЕ получите уведомления (не выбраны эти фрукты)"
    
    await message.answer(response)

@router.message(Command("test_format"), F.chat.type == ChatType.PRIVATE)
async def test_format_command(message: Message):
    """Тест форматирования сообщения - ТОЛЬКО в личных сообщениях"""
    test_fruits = [
        {"name": "Pineapple", "quantity": 2},
        {"name": "Dragon Fruit", "quantity": 3},
        {"name": "Durian", "quantity": 1}
    ]
    
    # Тестируем русский
    rus_text = MessageFilter.format_food_message(test_fruits, "RUS")
    await message.answer(f"🇷🇺 Русский:\n{rus_text}", parse_mode="HTML")
    
    # Тестируем английский
    en_text = MessageFilter.format_food_message(test_fruits, "ENG")
    await message.answer(f"🇺🇸 Английский:\n{en_text}", parse_mode="HTML")

@router.message(Command("send_test_notification"), F.chat.type == ChatType.PRIVATE)
async def send_test_notification_command(message: Message, bot: Bot):
    """Отправка тестового уведомления - ТОЛЬКО в личных сообщениях"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if not user or not user.get("is_subscribed"):
        await message.answer("❌ Вы не подписаны или не найдены в базе")
        return
    
    lang = user.get("language", "RUS")
    
    test_fruits = [
        {"name": "Pineapple", "quantity": 2},
        {"name": "Dragon Fruit", "quantity": 1}
    ]
    
    # Форматируем сообщение
    message_text = MessageFilter.format_food_message(test_fruits, lang)
    
    try:
        await bot.send_message(user_id, message_text, parse_mode="HTML")
        await message.answer("✅ Тестовое уведомление отправлено!")
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {e}")

@router.message(Command("channel_status"), F.chat.type == ChatType.PRIVATE)
async def channel_status_command(message: Message, bot: Bot):
    """Проверка статуса канала - ТОЛЬКО в личных сообщениях"""
    response = f"📊 СТАТУС КАНАЛА:\n\n"
    response += f"ID канала в config: {Config.SOURCE_CHANNEL_ID}\n"
    response += f"ID группы подписки: {Config.REQUIRED_GROUP_ID}\n"
    response += f"Токен бота: {'✅ есть' if Config.BOT_TOKEN else '❌ нет'}\n\n"
    
    # Проверяем доступ к каналу
    try:
        chat = await bot.get_chat(Config.SOURCE_CHANNEL_ID)
        response += f"📢 Канал: {chat.title}\n"
        
        # Проверяем статус бота в канале
        bot_member = await bot.get_chat_member(Config.SOURCE_CHANNEL_ID, bot.id)
        response += f"🤖 Статус бота: {bot_member.status}\n"
        
        if hasattr(bot_member, 'can_read_messages'):
            response += f"👀 Может читать: {'✅ ДА' if bot_member.can_read_messages else '❌ НЕТ'}\n"
        
    except Exception as e:
        response += f"❌ Ошибка доступа: {e}\n"
    
    await message.answer(response)


# ========== ИГНОРИРОВАНИЕ КОМАНД В ГРУППАХ ==========

@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def ignore_commands_in_groups(message: Message):
    """
    Игнорировать команды бота в группах.
    Команды работают только в личных сообщениях.
    """
    if not message.text:
        return
    
    text = message.text.strip()
    
    # Проверяем только команды, начинающиеся с /
    if text.startswith('/'):
        # Это команда, но мы игнорируем ее в группах
        # Можно добавить логирование или просто ничего не делать
        logger.debug(f"Игнорируем команду в группе: {text} от {message.from_user.id}")
        return