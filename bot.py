import asyncio
import logging
import sys
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from config import Config
from database import Database
from utils.subscription import daily_subscription_check
from handlers.start import get_user_language

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

async def create_backup():
    """
    Создание бэкапа через SQLite BACKUP API — безопасно при активных WAL-транзакциях.
    Сначала делаем консистентную копию .db, потом сжимаем в .gz.
    """
    try:
        import sqlite3
        import gzip
        import shutil

        backup_dir = "database_backups"
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_path = os.path.join(backup_dir, f"_tmp_{timestamp}.db")
        backup_name = f"database_backup_{timestamp}.db.gz"
        backup_path = os.path.join(backup_dir, backup_name)

        # SQLite BACKUP API — корректно работает с WAL, не блокирует писателей
        src = sqlite3.connect(Config.DATABASE_PATH)
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        # Сжимаем временный файл
        with open(tmp_path, 'rb') as f_in:
            with gzip.open(backup_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(tmp_path)

        file_size = os.path.getsize(backup_path)
        logger.info(f"💾 Бэкап создан: {backup_name} ({file_size:,} байт)")
        return backup_path

    except Exception as e:
        logger.error(f"❌ Ошибка создания бэкапа: {e}")
        # Удаляем временный файл если остался
        try:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return None

async def send_backup_to_admin(bot: Bot, backup_path: str):
    """Отправка бэкапа администратору"""
    try:
        if not os.path.exists(backup_path):
            logger.error(f"❌ Файл не найден: {backup_path}")
            return False
        
        file_size = os.path.getsize(backup_path)
        file_size_mb = file_size / (1024 * 1024)
        
        if file_size_mb > 48:
            logger.warning(f"⚠️ Файл слишком большой: {file_size_mb:.1f} MB")
            return False
        
        with open(backup_path, 'rb') as file:
            file_data = file.read()
            
        input_file = BufferedInputFile(
            file=file_data,
            filename=os.path.basename(backup_path)
        )
        
        await bot.send_document(
            chat_id=Config.ADMIN_ID,
            document=input_file,
            caption=f"💾 Бэкап базы данных\nРазмер: {file_size_mb:.2f} MB\nДата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        logger.info("✅ Бэкап отправлен администратору")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки бэкапа: {e}")
        return False

async def refresh_keyboards_task(bot: Bot, db_instance: Database):
    """
    При запуске бота рассылает всем пользователям актуальную клавиатуру.
    Запускается один раз через 10 секунд после старта.
    Отчёт о результатах отправляется только главному администратору.
    """
    from utils.keyboards import get_main_keyboard
    from handlers.admin_common import ADMIN_IDS

    MAIN_ADMIN_ID = ADMIN_IDS[0] if ADMIN_IDS else None

    await asyncio.sleep(10)

    users = db_instance.get_all_users()
    sent = 0
    failed = 0

    logger.info(f"🔄 Обновление клавиатур для {len(users)} пользователей...")

    for user in users:
        user_id = user["user_id"]
        # Администраторам не отправляем — только главному придёт отчёт
        if user_id in ADMIN_IDS:
            continue
        lang = user.get("language", "RUS")
        lang_code = "ru" if lang == "RUS" else "en"
        text = "🔄 Клавиатура обновлена" if lang_code == "ru" else "🔄 Keyboard updated"

        try:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=get_main_keyboard(lang),
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    logger.info(f"✅ Клавиатуры обновлены: {sent} успешно, {failed} ошибок")

    if MAIN_ADMIN_ID:
        try:
            await bot.send_message(
                MAIN_ADMIN_ID,
                f"🔄 <b>Обновление клавиатур завершено</b>\n\n"
                f"✅ Успешно: {sent}\n"
                f"❌ Ошибок: {failed}\n"
                f"👥 Всего пользователей: {len(users)}",
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить отчёт главному админу: {e}")


async def auto_backup_task(bot: Bot):
    """Задача автоматических бэкапов"""
    await asyncio.sleep(60)
    
    try:
        logger.info("🔄 Создаю стартовый бэкап...")
        backup_path = await create_backup()
        if backup_path:
            await send_backup_to_admin(bot, backup_path)
    except Exception as e:
        logger.error(f"❌ Ошибка стартового бэкапа: {e}")
    
    last_backup_date = None  # флаг: дата последнего ночного бэкапа

    while True:
        try:
            now = datetime.now()
            today = now.date()

            if now.hour == 3 and now.minute == 0 and last_backup_date != today:
                logger.info("🔄 Создаю автоматический бэкап...")
                backup_path = await create_backup()
                last_backup_date = today

                if backup_path:
                    await send_backup_to_admin(bot, backup_path)
            
            await asyncio.sleep(60)
            
        except Exception as e:
            logger.error(f"❌ Ошибка в задаче бэкапа: {e}")
            await asyncio.sleep(300)

async def main():
    """Основная функция запуска бота"""
    if not Config.BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не найден!")
        return
    
    # Проверяем наличие базы данных
    if not os.path.exists(Config.DATABASE_PATH):
        logger.warning(f"⚠️ База данных {Config.DATABASE_PATH} не найдена")
        logger.info("🆕 Создаю новую базу...")
        try:
            db = Database()
            logger.info("✅ Новая база создана")
        except Exception as e:
            logger.error(f"❌ Не удалось создать базу: {e}")
            return
    else:
        try:
            db = Database()
            logger.info("✅ База данных инициализирована")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации БД: {e}")
            return
    
    # Создаем бота
    bot = Bot(
        token=Config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Проверяем доступ к каналу
    try:
        chat = await bot.get_chat(Config.SOURCE_CHANNEL_ID)
        logger.info(f"✅ Бот имеет доступ к каналу: {chat.title}")
    except Exception as e:
        logger.warning(f"⚠️ Нет доступа к каналу {Config.SOURCE_CHANNEL_ID}: {e}")
    
    # Создаем диспетчер
    dp = Dispatcher()

    # Регистрируем роутеры - В ТОЧНОСТИ КАК В ИЗНАЧАЛЬНОМ КОДЕ!
    try:
        from handlers.start import router as start_router
        from handlers.settings import router as settings_router
        from handlers.admin import router as admin_router
        from handlers.channel import router as channel_router
        from handlers.group_commands import router as group_commands_router
        from handlers.publish import router as publish_router
        from handlers.inventory import router as inventory_router
        from handlers.giveaway import router as giveaway_router
        from handlers.trade import router as trade_router
        from handlers.item_trade import router as item_trade_router

        # Порядок важен: специфичные фильтры — раньше, широкие — позже
        dp.include_router(group_commands_router)  # !число / !инв — перехватывает '!' в группах
        dp.include_router(start_router)           # /start, выбор языка, подписка
        dp.include_router(settings_router)        # настройки уведомлений
        dp.include_router(inventory_router)       # 🎒 Инвентарь — до admin, иначе перехватывается
        dp.include_router(item_trade_router)      # P2P-обмен предметами инвентаря
        dp.include_router(trade_router)           # обмены через администратора — FSM-хендлеры
        dp.include_router(giveaway_router)        # розыгрыши
        dp.include_router(admin_router)           # админка (широкий F.chat.type=="private" — последним)
        dp.include_router(channel_router)         # каналы
        dp.include_router(publish_router)         # публикации
        
        logger.info("✅ Все роутеры зарегистрированы")
        
    except ImportError as e:
        logger.error(f"❌ Ошибка импорта роутера: {e}")
        return
    
    # Запускаем фоновые задачи
    try:
        from handlers.giveaway import giveaway_timer_task

        asyncio.create_task(daily_subscription_check(bot))
        logger.info("✅ Проверка подписок запущена")

        asyncio.create_task(auto_backup_task(bot))
        logger.info("✅ Автобэкапы запущены")

        asyncio.create_task(giveaway_timer_task(bot))
        logger.info("✅ Таймер розыгрышей запущен")

        asyncio.create_task(refresh_keyboards_task(bot, db))
        logger.info("✅ Обновление клавиатур запущено")

    except Exception as e:
        logger.error(f"❌ Ошибка запуска фоновых задач: {e}")
    
    # Запуск бота
    logger.info("🤖 Бот запущен и готов к работе!")
    
    try:
        bot_info = await bot.get_me()
        logger.info(f"👤 Бот: @{bot_info.username}")
        
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        
    finally:
        logger.info("🛑 Бот завершает работу...")
        try:
            logger.info("🔄 Создаю финальный бэкап...")
            backup_path = await create_backup()
            if backup_path:
                await send_backup_to_admin(bot, backup_path)
        except Exception as e:
            logger.error(f"❌ Ошибка финального бэкапа: {e}")
        await bot.session.close()
        logger.info("👋 Сессия бота закрыта")

if __name__ == "__main__":
    print("=" * 50)
    print("🤖 BUILD A ZOO NOTIFICATION BOT")
    print("=" * 50)
    print("📢 Функции:")
    print("• Уведомления о фруктах и тотемах")
    print("• Админ-панель с рассылкой")
    print("• Автоматические бэкапы")
    print("=" * 50)
    
    asyncio.run(main())
