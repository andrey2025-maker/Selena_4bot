#!/usr/bin/env python3
"""
Скрипт для проверки базы данных и поиска проблем с исключениями
"""

import sqlite3
import os
import sys
from datetime import datetime

def check_database():
    """Основная функция проверки БД"""
    
    db_path = "database.db"
    
    if not os.path.exists(db_path):
        print("❌ Файл базы данных не найден!")
        print(f"Ищем по пути: {os.path.abspath(db_path)}")
        return False
    
    print("=" * 60)
    print("🔍 ПРОВЕРКА БАЗЫ ДАННЫХ")
    print("=" * 60)
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Проверяем все таблицы
        print("\n📊 ТАБЛИЦЫ В БАЗЕ:")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        if not tables:
            print("❌ Нет таблиц в базе!")
            return False
        
        for table in tables:
            print(f"  ✅ {table[0]}")
        
        # 2. Проверяем таблицу subscription_exceptions
        print("\n🔍 ПРОВЕРКА ТАБЛИЦЫ subscription_exceptions:")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='subscription_exceptions'")
        exceptions_table = cursor.fetchone()
        
        if not exceptions_table:
            print("❌ Таблица subscription_exceptions НЕ СУЩЕСТВУЕТ!")
            print("\n💡 РЕШЕНИЕ:")
            print("1. Перезапустите бота - он создаст таблицы автоматически")
            print("2. Или запустите эту команду вручную:")
            print("""
CREATE TABLE subscription_exceptions (
    user_id INTEGER PRIMARY KEY,
    admin_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
CREATE INDEX idx_exceptions_user ON subscription_exceptions(user_id);
            """)
            return False
        
        print("✅ Таблица subscription_exceptions существует")
        
        # 3. Проверяем структуру таблицы
        print("\n📐 СТРУКТУРА ТАБЛИЦЫ subscription_exceptions:")
        cursor.execute("PRAGMA table_info(subscription_exceptions)")
        columns = cursor.fetchall()
        
        for col in columns:
            print(f"  • {col[1]} ({col[2]}) {'PRIMARY KEY' if col[5] == 1 else ''}")
        
        # 4. Проверяем данные в таблице
        print("\n📋 ДАННЫЕ В ТАБЛИЦЕ subscription_exceptions:")
        cursor.execute("SELECT * FROM subscription_exceptions")
        records = cursor.fetchall()
        
        if not records:
            print("  ℹ️ Таблица пуста (нет исключений)")
        else:
            print(f"  📊 Всего записей: {len(records)}")
            for i, record in enumerate(records, 1):
                print(f"  {i}. User ID: {record[0]}, Admin ID: {record[1]}, Дата: {record[2]}")
        
        # 5. Проверяем таблицу users
        print("\n👥 ПРОВЕРКА ТАБЛИЦЫ users:")
        cursor.execute("SELECT COUNT(*) FROM users")
        users_count = cursor.fetchone()[0]
        print(f"  📊 Всего пользователей: {users_count}")
        
        # 6. Ищем пользователей с username
        print("\n🔎 ПОЛЬЗОВАТЕЛИ С USERNAME:")
        cursor.execute("SELECT user_id, username FROM users WHERE username IS NOT NULL AND username != ''")
        users_with_username = cursor.fetchall()
        
        print(f"  📊 Пользователей с username: {len(users_with_username)}")
        if users_with_username:
            for user_id, username in users_with_username[:20]:  # Первые 20
                print(f"  • ID: {user_id}, @{username}")
            
            if len(users_with_username) > 20:
                print(f"  ... и еще {len(users_with_username) - 20} пользователей")
        
        # 7. Ищем конкретного пользователя
        print("\n🔍 ПОИСК ПОЛЬЗОВАТЕЛЯ @sakyrbaevnaa:")
        cursor.execute("SELECT * FROM users WHERE username LIKE ?", ('%sakyrbaevnaa%',))
        found_users = cursor.fetchall()
        
        if not found_users:
            print("  ❌ Пользователь @sakyrbaevnaa НЕ НАЙДЕН в таблице users!")
            print("\n💡 ПРИЧИНА: Пользователь должен сначала написать боту /start")
            print("  Только после этого он появится в базе данных")
        else:
            print(f"  ✅ Найдено пользователей: {len(found_users)}")
            for user in found_users:
                print(f"  • ID: {user[0]}, Username: @{user[1]}, Язык: {user[2]}, Подписка: {'Да' if user[3] else 'Нет'}")
        
        # 8. Проверяем связи между таблицами
        print("\n🔗 ПРОВЕРКА СВЯЗЕЙ МЕЖДУ ТАБЛИЦАМИ:")
        
        # Пользователи в исключениях, которых нет в users
        cursor.execute("""
            SELECT se.user_id 
            FROM subscription_exceptions se
            LEFT JOIN users u ON se.user_id = u.user_id
            WHERE u.user_id IS NULL
        """)
        orphaned_exceptions = cursor.fetchall()
        
        if orphaned_exceptions:
            print(f"  ⚠️  Найдено {len(orphaned_exceptions)} 'осиротевших' исключений:")
            for user_id in orphaned_exceptions:
                print(f"    • User ID: {user_id[0]} (нет в таблице users)")
        else:
            print("  ✅ Все исключения привязаны к существующим пользователям")
        
        # 9. Проверяем индексы
        print("\n📈 ПРОВЕРКА ИНДЕКСОВ:")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = cursor.fetchall()
        
        for idx in indexes:
            print(f"  • {idx[0]}")
        
        conn.close()
        
        print("\n" + "=" * 60)
        print("📋 ИТОГИ ПРОВЕРКИ:")
        print("=" * 60)
        
        if not found_users:
            print("❌ ПРОБЛЕМА: Пользователь @sakyrbaevnaa не найден в базе.")
            print("   Решение: Попросите пользователя написать /start боту")
        elif len(records) == 0:
            print("ℹ️  Исключений пока нет, но пользователь найден")
            print("   Можно добавить исключение через админ-панель")
        else:
            print("✅ База данных в порядке")
        
        return True
        
    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ПРОВЕРКЕ: {e}")
        return False

def fix_exceptions_table():
    """Создание таблицы исключений если её нет"""
    
    print("\n" + "=" * 60)
    print("🔧 ИСПРАВЛЕНИЕ ТАБЛИЦЫ subscription_exceptions")
    print("=" * 60)
    
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        
        # Проверяем, существует ли таблица
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='subscription_exceptions'")
        table_exists = cursor.fetchone()
        
        if table_exists:
            print("✅ Таблица subscription_exceptions уже существует")
            return True
        
        # Создаем таблицу
        print("🛠️ Создаю таблицу subscription_exceptions...")
        
        cursor.execute('''
            CREATE TABLE subscription_exceptions (
                user_id INTEGER PRIMARY KEY,
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        ''')
        
        # Создаем индекс
        cursor.execute('CREATE INDEX idx_exceptions_user ON subscription_exceptions(user_id)')
        
        conn.commit()
        conn.close()
        
        print("✅ Таблица subscription_exceptions создана успешно!")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при создании таблицы: {e}")
        return False

def add_exception_manually(user_id: int, admin_id: int = 1835558263):
    """Добавление исключения вручную"""
    
    print("\n" + "=" * 60)
    print(f"➕ ДОБАВЛЕНИЕ ИСКЛЮЧЕНИЯ ДЛЯ USER_ID: {user_id}")
    print("=" * 60)
    
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        
        # Проверяем, существует ли пользователь
        cursor.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            print(f"❌ Пользователь с ID {user_id} не найден в таблице users!")
            print("   Сначала пользователь должен написать боту /start")
            return False
        
        username = user[0] if user[0] else "без username"
        print(f"✅ Найден пользователь: ID {user_id}, @{username}")
        
        # Проверяем, есть ли уже исключение
        cursor.execute("SELECT * FROM subscription_exceptions WHERE user_id = ?", (user_id,))
        existing = cursor.fetchone()
        
        if existing:
            print(f"⚠️  Пользователь уже в исключениях (добавил админ ID: {existing[1]})")
            return False
        
        # Добавляем исключение
        cursor.execute(
            "INSERT INTO subscription_exceptions (user_id, admin_id) VALUES (?, ?)",
            (user_id, admin_id)
        )
        
        conn.commit()
        conn.close()
        
        print(f"✅ Пользователь {user_id} добавлен в исключения!")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при добавлении исключения: {e}")
        return False

def show_all_exceptions():
    """Показать все исключения"""
    
    print("\n" + "=" * 60)
    print("📋 ВСЕ ИСКЛЮЧЕНИЯ")
    print("=" * 60)
    
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT se.*, u.username, u.language, u.is_subscribed
            FROM subscription_exceptions se
            LEFT JOIN users u ON se.user_id = u.user_id
            ORDER BY se.created_at DESC
        ''')
        
        exceptions = cursor.fetchall()
        
        if not exceptions:
            print("ℹ️  Нет исключений в базе данных")
            return
        
        print(f"📊 Всего исключений: {len(exceptions)}\n")
        
        for exc in exceptions:
            user_id = exc[0]
            admin_id = exc[1]
            created_at = exc[2]
            username = exc[3] or "нет username"
            language = exc[4] or "неизвестно"
            is_subscribed = "✅" if exc[5] else "❌"
            
            print(f"👤 Пользователь: {username} (ID: {user_id})")
            print(f"   Язык: {language}, Подписка: {is_subscribed}")
            print(f"   👑 Добавил админ: {admin_id}")
            print(f"   📅 Дата добавления: {created_at}")
            print()
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    print("🤖 СКРИПТ ПРОВЕРКИ БАЗЫ ДАННЫХ")
    print(f"📅 Дата проверки: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    
    # Основная проверка
    check_database()
    
    # Меню действий
    print("\n" + "=" * 60)
    print("🎯 ВЫБЕРИТЕ ДЕЙСТВИЕ:")
    print("=" * 60)
    print("1. Исправить таблицу subscription_exceptions")
    print("2. Показать все исключения")
    print("3. Добавить исключение вручную (по ID)")
    print("4. Выход")
    
    choice = input("\nВыберите действие (1-4): ").strip()
    
    if choice == "1":
        fix_exceptions_table()
    elif choice == "2":
        show_all_exceptions()
    elif choice == "3":
        try:
            user_id = int(input("Введите ID пользователя: ").strip())
            admin_id_input = input("Введите ID администратора (по умолчанию 1835558263): ").strip()
            admin_id = int(admin_id_input) if admin_id_input else 1835558263
            add_exception_manually(user_id, admin_id)
        except ValueError:
            print("❌ Неверный формат ID!")
    elif choice == "4":
        print("👋 Выход...")
    else:
        print("❌ Неверный выбор")
    
    print("\n" + "=" * 60)
    print("✅ Проверка завершена")
    print("=" * 60)
    
    input("\nНажмите Enter для выхода...")