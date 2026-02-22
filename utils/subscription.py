"""
subscription.py - Проверка подписок и отправка уведомлений
"""

from datetime import datetime
import asyncio
import logging
from typing import List, Dict, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import LinkPreviewOptions

from database import Database
from config import Config
from utils.messages import locale_manager
from utils.filters import MessageFilter

logger = logging.getLogger(__name__)
db = Database()

async def check_user_subscription(
    user_id: int, 
    group_id: int, 
    bot: Bot, 
    ignore_exceptions: bool = False
) -> bool:
    """
    Проверка подписки пользователя на группу
    
    Args:
        user_id: ID пользователя
        group_id: ID группы
        bot: Экземпляр бота
        ignore_exceptions: Игнорировать ли исключения
    
    Returns:
        bool: True если подписан или в исключениях
    """
    # Проверяем, есть ли пользователь в исключениях
    if not ignore_exceptions and db.is_exception(user_id):
        return True
    
    try:
        chat_member = await bot.get_chat_member(group_id, user_id)
        is_subscribed = chat_member.status in ["member", "administrator", "creator"]
        
        # Обновляем статус в БД
        db.update_subscription(user_id, is_subscribed)
        
        # Обновляем username если он изменился или появился
        if hasattr(chat_member, "user") and chat_member.user:
            new_username = chat_member.user.username
            current_user = db.get_user(user_id)
            if current_user and current_user.get("username") != new_username:
                db.update_username(user_id, new_username)
                logger.info(f"Updated username for {user_id}: {new_username}")
        
        return is_subscribed
    except TelegramForbiddenError:
        logger.warning(f"Bot blocked by user {user_id}")
        db.update_subscription(user_id, False)
        return False
    except TelegramBadRequest as e:
        if "chat not found" in str(e).lower():
            logger.error(f"Group {group_id} not found or bot is not a member")
        else:
            logger.error(f"Error checking subscription for {user_id}: {e}")
        db.update_subscription(user_id, False)
        return False
    except Exception as e:
        logger.error(f"Unexpected error checking subscription for {user_id}: {e}")
        db.update_subscription(user_id, False)
        return False

async def send_notification(
    user_id: int, 
    bot: Bot, 
    text: str, 
    parse_mode: str = "HTML"
) -> bool:
    """
    Отправка уведомления пользователю без изменения его клавиатуры
    
    Args:
        user_id: ID пользователя
        bot: Экземпляр бота
        text: Текст уведомления
        parse_mode: Режим форматирования (HTML/Markdown)
    
    Returns:
        bool: True если отправлено успешно
    """
    try:
        await bot.send_message(
            user_id,
            text,
            parse_mode=parse_mode,
            link_preview_options=LinkPreviewOptions(is_disabled=True)
            # НЕТ reply_markup - сохраняем текущую клавиатуру пользователя
        )
        return True
    except TelegramForbiddenError:
        logger.warning(f"Cannot send notification to {user_id}: bot blocked")
        db.update_subscription(user_id, False)
        return False
    except Exception as e:
        logger.error(f"Failed to send notification to {user_id}: {e}")
        return False

async def send_fruit_notification(
    bot: Bot,
    fruit_name: str,
    quantity: int,
    raw_message: Optional[str] = None
):
    """
    Отправка уведомлений о фрукте всем подписанным пользователям
    
    Args:
        bot: Экземпляр бота
        fruit_name: Название фрукта
        quantity: Количество
        raw_message: Оригинальное сообщение (для форматирования)
    """
    # Получаем пользователей, подписанных на этот фрукт
    user_ids = db.get_users_for_fruit(fruit_name)
    
    if not user_ids:
        logger.info(f"No subscribers for fruit: {fruit_name}")
        return
    
    logger.info(f"Sending {fruit_name} notification to {len(user_ids)} users")
    
    success_count = 0
    for user_id in user_ids:
        try:
            # Получаем язык пользователя
            user = db.get_user(user_id)
            if not user:
                continue
            
            lang = user.get("language", "RUS")
            
            # Формируем сообщение
            fruit_display = MessageFilter.get_fruit_emoji(fruit_name, lang)
            if lang == "RUS":
                fruit_name_rus = Config.FRUIT_TRANSLATIONS.get(fruit_name, fruit_name)
                message_text = f"{fruit_display} x{quantity} {fruit_name_rus} — stock"
            else:
                message_text = f"{fruit_display} x{quantity} {fruit_name} — stock"
            
            # Отправляем уведомление
            success = await send_notification(user_id, bot, message_text)
            if success:
                success_count += 1
            
            # Небольшая задержка чтобы не флудить
            await asyncio.sleep(0.05)
            
        except Exception as e:
            logger.error(f"Error sending fruit notification to {user_id}: {e}")
    
    logger.info(f"Fruit notifications sent: {success_count}/{len(user_ids)}")

async def send_totem_notification(
    bot: Bot,
    totem_type: str,
    text: str,
    link: Optional[str] = None
):
    """
    Отправка уведомлений о тотеме всем подписанным пользователям
    
    Args:
        bot: Экземпляр бота
        totem_type: Тип тотема ("free" или "paid")
        text: Текст сообщения
        link: Ссылка на Roblox
    """
    # Определяем тип тотема
    is_free = totem_type == "free"
    
    # Получаем пользователей, подписанных на этот тип тотема
    user_ids = db.get_users_for_totem(is_free)
    
    if not user_ids:
        logger.info(f"No subscribers for {totem_type} totems")
        return
    
    logger.info(f"Sending {totem_type} totem notification to {len(user_ids)} users")
    
    success_count = 0
    for user_id in user_ids:
        try:
            # Получаем язык пользователя
            user = db.get_user(user_id)
            if not user:
                continue
            
            lang = user.get("language", "RUS")
            lang_code = "ru" if lang == "RUS" else "en"
            
            # Формируем сообщение с помощью фильтра
            message_text = MessageFilter.format_totem_message(
                totem_type,
                text,
                link,
                lang_code
            )
            
            success = await send_notification(
                user_id,
                bot,
                message_text,
                parse_mode="HTML"
            )
            if success:
                success_count += 1
            
            # Небольшая задержка
            await asyncio.sleep(0.05)
            
        except Exception as e:
            logger.error(f"Error sending totem notification to {user_id}: {e}")
    
    logger.info(f"Totem notifications sent: {success_count}/{len(user_ids)}")

async def daily_subscription_check(bot: Bot):
    """
    Ежедневная проверка подписок всех пользователей
    Запускается как фоновая задача
    """
    while True:
        try:
            logger.info("Starting daily subscription check...")
            users = db.get_all_users()
            unsubscribed_users = []
            
            for user in users:
                user_id = user["user_id"]
                
                # Проверяем подписку (игнорируем исключения для проверки)
                # check_user_subscription уже вызывает db.update_subscription внутри
                is_subscribed = await check_user_subscription(
                    user_id,
                    Config.REQUIRED_GROUP_ID,
                    bot,
                    ignore_exceptions=True
                )
                
                # Проверяем, есть ли пользователь в исключениях
                is_exception = db.is_exception(user_id)
                
                # Если пользователь в исключениях, считаем его подписанным
                if is_exception:
                    is_subscribed = True
                    db.update_subscription(user_id, True)
                
                # Если пользователь отписался и не в исключениях, отправляем уведомление
                if user["is_subscribed"] and not is_subscribed and not is_exception:
                    unsubscribed_users.append(user_id)
            
            # Отправляем уведомления отписавшимся пользователям
            for user_id in unsubscribed_users:
                user = db.get_user(user_id)
                if user:
                    lang = user.get("language", "RUS")
                    lang_code = "ru" if lang == "RUS" else "en"
                    
                    text = locale_manager.get_text(lang_code, "notifications.unsubscribed")
                    await send_notification(user_id, bot, text)
                    
                    # Небольшая задержка
                    await asyncio.sleep(0.1)
            
            logger.info(f"Daily subscription check completed. Checked {len(users)} users, "
                       f"{len(unsubscribed_users)} unsubscribed.")
            
        except Exception as e:
            logger.error(f"Error in daily subscription check: {e}")
        
        # Ждем интервал до следующей проверки
        await asyncio.sleep(Config.SUBSCRIPTION_CHECK_INTERVAL)

async def verify_all_subscriptions(bot: Bot) -> Dict[str, int]:
    """
    Принудительная проверка всех подписок
    Используется администратором
    """
    logger.info("Starting forced subscription verification...")
    
    users = db.get_all_users()
    verified = 0
    unsubscribed = 0
    errors = 0
    
    for user in users:
        user_id = user["user_id"]
        
        try:
            # check_user_subscription уже вызывает db.update_subscription внутри
            is_subscribed = await check_user_subscription(
                user_id,
                Config.REQUIRED_GROUP_ID,
                bot,
                ignore_exceptions=True
            )
            
            # Проверяем исключения
            is_exception = db.is_exception(user_id)
            
            if is_exception:
                is_subscribed = True
                db.update_subscription(user_id, True)
            
            if is_subscribed:
                verified += 1
            else:
                unsubscribed += 1
                
        except Exception as e:
            logger.error(f"Error verifying user {user_id}: {e}")
            errors += 1
        
        await asyncio.sleep(0.05)
    
    logger.info(f"Verification completed. Verified: {verified}, "
               f"Unsubscribed: {unsubscribed}, Errors: {errors}")
    
    return {
        "total": len(users),
        "verified": verified,
        "unsubscribed": unsubscribed,
        "errors": errors
    }
