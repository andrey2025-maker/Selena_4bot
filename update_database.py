import sqlite3
import logging
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_PATH = "database.db"

def update_database():
    """Обновление существующей базы данных"""
    
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        logger.info("🔄 Начинаю обновление базы данных...")
        
        # 1. Добавляем таблицу исключений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscription_exceptions (
                user_id INTEGER PRIMARY KEY,
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        ''')
        logger.info("✅ Таблица 'subscription_exceptions' создана/проверена")
        
        # 2. Создаем индексы для ускорения
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_exceptions_user ON subscription_exceptions(user_id)')
        logger.info("✅ Индекс для таблицы исключений создан/проверен")
        
        # 3. Проверяем наличие столбца username в таблице users
        cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'username' not in columns:
            # Добавляем столбец username если его нет
            cursor.execute('ALTER TABLE users ADD COLUMN username TEXT')
            logger.info("✅ Столбец 'username' добавлен в таблицу 'users'")
        
        # 4. Проверяем наличие столбца last_check
        if 'last_check' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN last_check TIMESTAMP')
            logger.info("✅ Столбец 'last_check' добавлен в таблицу 'users'")
        
        # 5. Обновляем значения по умолчанию для существующих пользователей
        cursor.execute('UPDATE users SET free_totems = 1 WHERE free_totems IS NULL')
        cursor.execute('UPDATE users SET paid_totems = 1 WHERE paid_totems IS NULL')
        logger.info("✅ Значения по умолчанию для тотемов установлены")
        
        conn.commit()
        conn.close()
        
        logger.info("🎉 База данных успешно обновлена!")
        logger.info("🆕 Добавлены новые функции:")
        logger.info("   • Таблица исключений подписок")
        logger.info("   • Индексы для ускорения запросов")
        logger.info("   • Столбец username (если отсутствовал)")
        logger.info("   • Значения по умолчанию для тотемов")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при обновлении базы данных: {e}")
        sys.exit(1)

def check_database_integrity():
    """Проверка целостности базы данных"""
    
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        logger.info("🔍 Проверяю целостность базы данных...")
        
        # Проверяем существование таблиц
        tables = ['users', 'user_fruits', 'subscription_exceptions']
        
        for table in tables:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if cursor.fetchone():
                logger.info(f"✅ Таблица '{table}' существует")
            else:
                logger.warning(f"⚠️ Таблица '{table}' не существует")
        
        # Проверяем количество пользователей
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        logger.info(f"👥 Пользователей в базе: {user_count}")
        
        # Проверяем количество исключений
        cursor.execute("SELECT COUNT(*) FROM subscription_exceptions")
        exception_count = cursor.fetchone()[0]
        logger.info(f"📋 Исключений в базе: {exception_count}")
        
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке базы данных: {e}")

if __name__ == "__main__":
    print("=" * 50)
    print("🛠️  ОБНОВЛЕНИЕ БАЗЫ ДАННЫХ БОТА")
    print("=" * 50)
    
    update_database()
    print("\n" + "=" * 50)
    check_database_integrity()
    print("=" * 50)
    
    print("\n📋 ИНСТРУКЦИЯ:")
    print("1. Убедитесь, что бот НЕ запущен во время обновления")
    print("2. Запустите этот скрипт: python update_database.py")
    print("3. После успешного обновления запустите бота")
    print("=" * 50)