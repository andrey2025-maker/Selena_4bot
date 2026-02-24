import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from config import Config

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = Config.DATABASE_PATH):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        """Создание подключения к БД"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL-режим: читатели не блокируют писателей и наоборот.
        # Безопасен при одновременной работе нескольких администраторов.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    
    def init_db(self):
        """Инициализация таблиц базы данных"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Таблица пользователей - ДОБАВЛЕНО ПОЛЕ USERNAME
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    language TEXT DEFAULT 'RUS',
                    is_subscribed INTEGER DEFAULT 0,
                    free_totems INTEGER DEFAULT 1,
                    paid_totems INTEGER DEFAULT 1,
                    last_check TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица выбранных фруктов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_fruits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    fruit_name TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    UNIQUE(user_id, fruit_name)
                )
            ''')
            
            # Таблица исключений подписок
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscription_exceptions (
                    user_id INTEGER PRIMARY KEY,
                    admin_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                )
            ''')
            
            # Таблица предметов инвентаря
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS inventory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    item_type TEXT NOT NULL DEFAULT 'item',
                    name TEXT NOT NULL,
                    description TEXT,
                    media_file_id TEXT,
                    media_type TEXT,
                    quantity INTEGER DEFAULT 1,
                    added_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    pet_income TEXT,
                    pet_mutation TEXT,
                    pet_weather TEXT,
                    pet_coeff TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')
            # Миграция: добавляем новые колонки если их нет (для существующих БД)
            for col, definition in [
                ("item_type", "TEXT NOT NULL DEFAULT 'item'"),
                ("pet_income", "TEXT"),
                ("pet_mutation", "TEXT"),
                ("pet_weather", "TEXT"),
                ("pet_coeff", "TEXT"),
            ]:
                try:
                    cursor.execute(f'ALTER TABLE inventory_items ADD COLUMN {col} {definition}')
                except Exception:
                    pass  # Колонка уже существует

            # Таблица запросов на выдачу
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pickup_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    item_ids TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    admin_id INTEGER,
                    request_type TEXT DEFAULT 'pickup',
                    admin_msg_ids TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')
            for col, definition in [
                ("request_type", "TEXT DEFAULT 'pickup'"),
                ("admin_msg_ids", "TEXT DEFAULT '{}'"),
            ]:
                try:
                    cursor.execute(f'ALTER TABLE pickup_requests ADD COLUMN {col} {definition}')
                except Exception:
                    pass

            # Таблица P2P-обменов предметами инвентаря
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS item_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    initiator_id INTEGER NOT NULL,
                    partner_id INTEGER NOT NULL,
                    initiator_items TEXT NOT NULL DEFAULT '[]',
                    partner_items TEXT NOT NULL DEFAULT '[]',
                    initiator_qty TEXT NOT NULL DEFAULT '{}',
                    partner_qty TEXT NOT NULL DEFAULT '{}',
                    initiator_confirmed INTEGER DEFAULT 0,
                    partner_confirmed INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'selecting',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (initiator_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (partner_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            # Блокировка предметов на время обмена
            for col, definition in [
                ("locked_trade_id", "INTEGER DEFAULT NULL"),
            ]:
                try:
                    cursor.execute(f'ALTER TABLE inventory_items ADD COLUMN {col} {definition}')
                except Exception:
                    pass

            # Таблица розыгрышей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS giveaways (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title_ru TEXT NOT NULL,
                    text_ru TEXT NOT NULL,
                    media_file_id_ru TEXT,
                    media_type_ru TEXT,
                    title_en TEXT,
                    text_en TEXT,
                    media_file_id_en TEXT,
                    media_type_en TEXT,
                    button_text TEXT NOT NULL,
                    required_channels TEXT NOT NULL DEFAULT '[]',
                    winner_count INTEGER NOT NULL DEFAULT 1,
                    end_type TEXT NOT NULL DEFAULT 'time',
                    end_value TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    group_message_id INTEGER,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP
                )
            ''')

            # Таблица призов розыгрыша (по местам)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS giveaway_prizes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    giveaway_id INTEGER NOT NULL,
                    place INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    media_file_id TEXT,
                    media_type TEXT,
                    prize_type TEXT DEFAULT 'item',
                    food_items TEXT DEFAULT NULL,
                    pet_income TEXT DEFAULT NULL,
                    pet_mutation TEXT DEFAULT NULL,
                    pet_weather TEXT DEFAULT NULL,
                    pet_coeff TEXT DEFAULT NULL,
                    FOREIGN KEY (giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE
                )
            ''')
            for col, definition in [
                ("prize_type", "TEXT DEFAULT 'item'"),
                ("food_items", "TEXT DEFAULT NULL"),
                ("pet_income", "TEXT DEFAULT NULL"),
                ("pet_mutation", "TEXT DEFAULT NULL"),
                ("pet_weather", "TEXT DEFAULT NULL"),
                ("pet_coeff", "TEXT DEFAULT NULL"),
            ]:
                try:
                    cursor.execute(f'ALTER TABLE giveaway_prizes ADD COLUMN {col} {definition}')
                except Exception:
                    pass

            # Таблица участников розыгрыша
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS giveaway_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    giveaway_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(giveaway_id, user_id),
                    FOREIGN KEY (giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            # Таблица Roblox-никнеймов пользователей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS roblox_nicks (
                    user_id INTEGER PRIMARY KEY,
                    roblox_nick TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            # Таблица сессий обмена
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user1_id INTEGER NOT NULL,
                    user2_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    topic_id INTEGER,
                    admin_joined INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP,
                    FOREIGN KEY (user1_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (user2_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            # Таблица активных чатов (admin ↔ user) — сохраняется между перезапусками
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS active_chats (
                    user_id INTEGER PRIMARY KEY,
                    admin_id INTEGER NOT NULL,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Топики группы для чатов администратора с пользователями
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS admin_chat_topics (
                    user_id INTEGER PRIMARY KEY,
                    topic_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Скрытые пользователи — псевдоним вместо TG-ника/ID для публичного отображения
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS hidden_users (
                    user_id INTEGER PRIMARY KEY,
                    alias TEXT NOT NULL,
                    added_by INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Создаем индексы для ускорения запросов
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_subscribed ON users(is_subscribed)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_fruits_user ON user_fruits(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_fruits_fruit ON user_fruits(fruit_name)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_exceptions_user ON subscription_exceptions(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_inventory_user ON inventory_items(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_pickup_user ON pickup_requests(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_giveaway_status ON giveaways(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_giveaway_participants ON giveaway_participants(giveaway_id, user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_users ON trade_sessions(user1_id, user2_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_status ON trade_sessions(status)')
            
            conn.commit()
        logger.info("Database initialized with indexes")
    
    def add_user(self, user_id: int, username: str = None, language: str = "RUS"):
        """Добавление нового пользователя с username"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                # Проверяем, существует ли пользователь
                cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                existing_user = cursor.fetchone()
                
                if existing_user:
                    # Обновляем username если пользователь уже существует
                    if username:
                        cursor.execute('''
                            UPDATE users SET username = ? WHERE user_id = ?
                        ''', (username, user_id))
                else:
                    # Добавляем нового пользователя
                    cursor.execute('''
                        INSERT INTO users (user_id, username, language) 
                        VALUES (?, ?, ?)
                    ''', (user_id, username, language))
                
                conn.commit()
                logger.info(f"User {user_id} added/updated with username: {username}")
                return True
            except Exception as e:
                logger.error(f"Error adding user {user_id}: {e}")
                return False
    
    def update_user_language(self, user_id: int, language: str):
        """Обновление языка пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET language = ? WHERE user_id = ?
            ''', (language, user_id))
            conn.commit()
    
    def update_subscription(self, user_id: int, is_subscribed: bool):
        """Обновление статуса подписки"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET is_subscribed = ?, last_check = ?
                WHERE user_id = ?
            ''', (1 if is_subscribed else 0, datetime.now(), user_id))
            conn.commit()
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Получение информации о пользователе"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM users WHERE user_id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_user_fruits(self, user_id: int) -> List[str]:
        """Получение списка выбранных фруктов пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT fruit_name FROM user_fruits WHERE user_id = ?
            ''', (user_id,))
            return [row[0] for row in cursor.fetchall()]
    
    def update_user_fruits(self, user_id: int, fruits: List[str]):
        """Обновление списка фруктов пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Удаляем старые записи
            cursor.execute('DELETE FROM user_fruits WHERE user_id = ?', (user_id,))
            # Добавляем новые
            for fruit in fruits:
                cursor.execute('''
                    INSERT INTO user_fruits (user_id, fruit_name) VALUES (?, ?)
                ''', (user_id, fruit))
            conn.commit()
    
    def update_totem_settings(self, user_id: int, free_totems: bool = None, paid_totems: bool = None):
        """Обновление настроек тотемов"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            updates = []
            params = []
            
            if free_totems is not None:
                updates.append("free_totems = ?")
                params.append(1 if free_totems else 0)
            
            if paid_totems is not None:
                updates.append("paid_totems = ?")
                params.append(1 if paid_totems else 0)
            
            if updates:
                params.append(user_id)
                query = f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?"
                cursor.execute(query, params)
                conn.commit()
    
    def get_all_users(self) -> List[Dict]:
        """Получение списка всех пользователей с Roblox-никами"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.*, r.roblox_nick
                FROM users u
                LEFT JOIN roblox_nicks r ON u.user_id = r.user_id
                ORDER BY u.created_at DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]
    
    def get_active_subscribers(self) -> List[Dict]:
        """Получение пользователей с активной подпиской"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.*, 
                       GROUP_CONCAT(uf.fruit_name) as fruits
                FROM users u
                LEFT JOIN user_fruits uf ON u.user_id = uf.user_id
                WHERE u.is_subscribed = 1
                GROUP BY u.user_id
            ''')
            return [dict(row) for row in cursor.fetchall()]
    
    def get_users_for_fruit(self, fruit_name: str) -> List[int]:
        """Получение пользователей, подписанных на конкретный фрукт"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT u.user_id 
                FROM users u
                JOIN user_fruits uf ON u.user_id = uf.user_id
                WHERE u.is_subscribed = 1 
                AND (uf.fruit_name = ? OR uf.fruit_name = 'all')
            ''', (fruit_name,))
            return [row[0] for row in cursor.fetchall()]
    
    def get_users_for_totem(self, is_free: bool) -> List[int]:
        """Получение пользователей, подписанных на тотемы"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            column = "free_totems" if is_free else "paid_totems"
            cursor.execute(f'''
                SELECT user_id FROM users 
                WHERE is_subscribed = 1 AND {column} = 1
            ''')
            return [row[0] for row in cursor.fetchall()]
    
    def get_statistics(self) -> Dict:
        """Получение статистики"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM users WHERE is_subscribed = 1")
            active_subscribers = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT fruit_name, COUNT(*) as count 
                FROM user_fruits 
                GROUP BY fruit_name 
                ORDER BY count DESC
            ''')
            fruit_stats = cursor.fetchall()
            
            cursor.execute('''
                SELECT 
                    SUM(free_totems) as free_totems_count,
                    SUM(paid_totems) as paid_totems_count
                FROM users 
                WHERE is_subscribed = 1
            ''')
            totem_stats = cursor.fetchone()
            
            # Форматируем статистику фруктов с переводами
            formatted_fruit_stats = {}
            for fruit, count in fruit_stats:
                if fruit == "all":
                    formatted_fruit_stats["Все фрукты"] = count
                else:
                    russian_name = Config.FRUIT_TRANSLATIONS.get(fruit, fruit)
                    formatted_fruit_stats[russian_name] = count
            
            return {
                "total_users": total_users,
                "active_subscribers": active_subscribers,
                "fruit_stats": formatted_fruit_stats,
                "free_totems": totem_stats[0] or 0,
                "paid_totems": totem_stats[1] or 0
            }
    
    def update_username(self, user_id: int, username: str):
        """Обновление username пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET username = ? WHERE user_id = ?
            ''', (username, user_id))
            conn.commit()
            logger.info(f"Username updated for user {user_id}: {username}")
    
    # ========== МЕТОДЫ ДЛЯ ИСКЛЮЧЕНИЙ ==========
    
    def is_exception(self, user_id: int) -> bool:
        """Проверка, есть ли пользователь в исключениях"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM subscription_exceptions WHERE user_id = ?', (user_id,))
            return cursor.fetchone() is not None
    
    def add_exception(self, user_id: int, admin_id: int) -> bool:
        """Добавление пользователя в исключения"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT OR REPLACE INTO subscription_exceptions (user_id, admin_id) 
                    VALUES (?, ?)
                ''', (user_id, admin_id))
                conn.commit()
                logger.info(f"User {user_id} added to exceptions by admin {admin_id}")
                return True
            except Exception as e:
                logger.error(f"Error adding exception for user {user_id}: {e}")
                return False
    
    def remove_exception(self, user_id: int) -> bool:
        """Удаление пользователя из исключений"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM subscription_exceptions WHERE user_id = ?', (user_id,))
            conn.commit()
            success = cursor.rowcount > 0
            if success:
                logger.info(f"User {user_id} removed from exceptions")
            return success
    
    def get_exceptions(self) -> List[Dict]:
        """Получение списка исключений"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT se.*, u.username, u.language
                FROM subscription_exceptions se
                LEFT JOIN users u ON se.user_id = u.user_id
                ORDER BY se.created_at DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]
    
    def get_user_with_exception_status(self, user_id: int) -> Optional[Dict]:
        """Получение информации о пользователе со статусом исключения. Возвращает None если пользователь не найден."""
        user = self.get_user(user_id)
        if user:
            user['is_exception'] = self.is_exception(user_id)
        return user

    # ========== МЕТОДЫ ДЛЯ АКТИВНЫХ ЧАТОВ ==========

    def set_active_chat(self, user_id: int, admin_id: int):
        """Сохранить активный чат admin↔user в БД."""
        with self.get_connection() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO active_chats (user_id, admin_id) VALUES (?, ?)',
                (user_id, admin_id)
            )
            conn.commit()

    def remove_active_chat(self, user_id: int):
        """Удалить активный чат из БД."""
        with self.get_connection() as conn:
            conn.execute('DELETE FROM active_chats WHERE user_id = ?', (user_id,))
            conn.commit()

    def get_all_active_chats(self) -> dict:
        """Вернуть все активные чаты как {user_id: admin_id}."""
        with self.get_connection() as conn:
            rows = conn.execute('SELECT user_id, admin_id FROM active_chats').fetchall()
        return {row[0]: row[1] for row in rows}

    # ========== ТОПИКИ ЧАТОВ АДМИНИСТРАТОРА ==========

    def get_chat_topic(self, user_id: int) -> Optional[int]:
        """Вернуть topic_id для чата с пользователем, или None если не было."""
        with self.get_connection() as conn:
            row = conn.execute(
                'SELECT topic_id FROM admin_chat_topics WHERE user_id = ?', (user_id,)
            ).fetchone()
        return row[0] if row else None

    def set_chat_topic(self, user_id: int, topic_id: int):
        """Сохранить (или обновить) topic_id для чата с пользователем."""
        with self.get_connection() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO admin_chat_topics (user_id, topic_id) VALUES (?, ?)',
                (user_id, topic_id)
            )
            conn.commit()

    def delete_chat_topic(self, user_id: int):
        """Удалить запись о топике (например, если топик был закрыт)."""
        with self.get_connection() as conn:
            conn.execute('DELETE FROM admin_chat_topics WHERE user_id = ?', (user_id,))
            conn.commit()

    # ========== МЕТОДЫ ДЛЯ ИНВЕНТАРЯ ==========

    def add_inventory_item(
        self,
        user_id: int,
        name: str,
        description: str = None,
        media_file_id: str = None,
        media_type: str = None,
        quantity: int = 1,
        added_by: int = None,
        item_type: str = "item",
        pet_income: str = None,
        pet_mutation: str = None,
        pet_weather: str = None,
        pet_coeff: str = None,
    ) -> Optional[int]:
        """Добавление предмета в инвентарь пользователя. Возвращает id предмета.
        Для еды (item_type='food') без медиа — объединяет с существующей записью с тем же именем."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                # Для еды без медиа — объединяем с существующей записью
                if item_type == 'food' and not media_file_id:
                    cursor.execute(
                        '''SELECT id, quantity FROM inventory_items
                           WHERE user_id=? AND item_type='food' AND name=?
                             AND (media_file_id IS NULL OR media_file_id='')
                             AND (locked_trade_id IS NULL OR locked_trade_id=0)
                           LIMIT 1''',
                        (user_id, name)
                    )
                    existing = cursor.fetchone()
                    if existing:
                        new_qty = existing[1] + quantity
                        cursor.execute(
                            'UPDATE inventory_items SET quantity=? WHERE id=?',
                            (new_qty, existing[0])
                        )
                        conn.commit()
                        logger.info(f"Merged food '{name}' for user {user_id}: +{quantity} → total {new_qty}")
                        return existing[0]

                cursor.execute('''
                    INSERT INTO inventory_items
                        (user_id, item_type, name, description, media_file_id, media_type,
                         quantity, added_by, pet_income, pet_mutation, pet_weather, pet_coeff)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, item_type, name, description, media_file_id, media_type,
                      quantity, added_by, pet_income, pet_mutation, pet_weather, pet_coeff))
                conn.commit()
                item_id = cursor.lastrowid
                logger.info(f"Inventory item {item_id} ({item_type}) added for user {user_id} by admin {added_by}")
                return item_id
            except Exception as e:
                logger.error(f"Error adding inventory item for user {user_id}: {e}")
                return None

    def update_inventory_item_media(self, item_id: int, media_file_id: str, media_type: str = None) -> bool:
        """Обновить media_file_id (и media_type) предмета инвентаря."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                if media_type:
                    cursor.execute(
                        'UPDATE inventory_items SET media_file_id=?, media_type=? WHERE id=?',
                        (media_file_id, media_type, item_id)
                    )
                else:
                    cursor.execute(
                        'UPDATE inventory_items SET media_file_id=? WHERE id=?',
                        (media_file_id, item_id)
                    )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"Error updating media for item {item_id}: {e}")
                return False

    def get_user_inventory(self, user_id: int) -> List[Dict]:
        """Получение всех предметов инвентаря пользователя.
        Порядок: item → food → pet.
        Петы сортируются: сначала по мутации (редкие → обычные),
        внутри мутации — по доходу убывая."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM inventory_items WHERE user_id = ?
                ORDER BY
                    CASE item_type
                        WHEN 'item' THEN 1
                        WHEN 'food' THEN 2
                        WHEN 'pet'  THEN 3
                        ELSE 4
                    END,
                    CASE WHEN item_type = 'pet' THEN
                        CASE pet_mutation
                            WHEN 'valentine' THEN 1
                            WHEN 'xmas'      THEN 2
                            WHEN 'thanks'    THEN 3
                            WHEN 'halloween' THEN 4
                            WHEN 'snowy'     THEN 5
                            WHEN 'jurassic'  THEN 6
                            WHEN 'fiery'     THEN 7
                            WHEN 'electric'  THEN 8
                            WHEN 'diamond'   THEN 9
                            WHEN 'golden'    THEN 10
                            WHEN 'normal'    THEN 11
                            ELSE 12
                        END
                    ELSE 0 END ASC,
                    CASE WHEN item_type = 'pet' THEN
                        CAST(REPLACE(REPLACE(COALESCE(pet_income, '0'), ' ', ''), ',', '') AS INTEGER)
                    ELSE 0 END DESC,
                    created_at ASC
            ''', (user_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_inventory_item(self, item_id: int) -> Optional[Dict]:
        """Получение предмета инвентаря по ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM inventory_items WHERE id = ?', (item_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def remove_inventory_items(self, item_ids: List[int]) -> bool:
        """Удаление предметов инвентаря по списку ID"""
        if not item_ids:
            return False
        with self.get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(item_ids))
            cursor.execute(f'DELETE FROM inventory_items WHERE id IN ({placeholders})', item_ids)
            conn.commit()
            deleted = cursor.rowcount
            logger.info(f"Removed {deleted} inventory items: {item_ids}")
            return deleted > 0

    def reduce_inventory_item_qty(self, item_id: int, amount: int) -> bool:
        """Уменьшить количество предмета. Если <= 0 — удалить."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT quantity FROM inventory_items WHERE id = ?', (item_id,))
            row = cursor.fetchone()
            if not row:
                return False
            new_qty = row[0] - amount
            if new_qty <= 0:
                cursor.execute('DELETE FROM inventory_items WHERE id = ?', (item_id,))
            else:
                cursor.execute('UPDATE inventory_items SET quantity = ? WHERE id = ?', (new_qty, item_id))
            conn.commit()
            return True

    def create_pickup_request(self, user_id: int, item_ids: List[int],
                               request_type: str = "pickup") -> Optional[int]:
        """Создание запроса на выдачу предметов. Возвращает id запроса."""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO pickup_requests (user_id, item_ids, status, request_type, admin_msg_ids)
                    VALUES (?, ?, 'pending', ?, '{}')
                ''', (user_id, json.dumps(item_ids), request_type))
                conn.commit()
                request_id = cursor.lastrowid
                logger.info(f"Pickup request {request_id} ({request_type}) created for user {user_id}")
                return request_id
            except Exception as e:
                logger.error(f"Error creating pickup request for user {user_id}: {e}")
                return None

    def get_pickup_request(self, request_id: int) -> Optional[Dict]:
        """Получение запроса на выдачу по ID"""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM pickup_requests WHERE id = ?', (request_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data['item_ids'] = json.loads(data['item_ids'])
            except Exception:
                data['item_ids'] = []
            try:
                data['admin_msg_ids'] = json.loads(data.get('admin_msg_ids') or '{}')
            except Exception:
                data['admin_msg_ids'] = {}
            return data

    def save_request_admin_msg_ids(self, request_id: int, admin_msg_ids: dict) -> None:
        """Сохранить словарь {admin_id: message_id} для запроса."""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE pickup_requests SET admin_msg_ids = ? WHERE id = ?',
                (json.dumps({str(k): v for k, v in admin_msg_ids.items()}), request_id)
            )
            conn.commit()

    def take_pickup_request(self, request_id: int, admin_id: int) -> bool:
        """
        Администратор берёт запрос в работу: статус 'pending' → 'in_progress'.
        Возвращает False если запрос не найден или уже взят/выполнен.
        """
        request = self.get_pickup_request(request_id)
        if not request or request['status'] in ('in_progress', 'done'):
            return False
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE pickup_requests SET status='in_progress', admin_id=? WHERE id=?",
                (admin_id, request_id)
            )
            conn.commit()
        logger.info(f"Pickup request {request_id} taken by admin {admin_id}")
        return True

    def complete_pickup_request(self, request_id: int, admin_id: int) -> bool:
        """
        Помечает запрос как выполненный (статус 'done').
        Удаление предметов с учётом qty_map выполняется в хендлере через reduce_inventory_item_qty.
        """
        request = self.get_pickup_request(request_id)
        if not request or request['status'] == 'done':
            return False
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE pickup_requests SET status = 'done', admin_id = ? WHERE id = ?
            ''', (admin_id, request_id))
            conn.commit()
        logger.info(f"Pickup request {request_id} completed by admin {admin_id}")
        return True

    # ========== МЕТОДЫ ДЛЯ РОЗЫГРЫШЕЙ ==========

    def create_giveaway(
        self,
        title_ru: str, text_ru: str,
        media_file_id_ru: str, media_type_ru: str,
        title_en: str, text_en: str,
        media_file_id_en: str, media_type_en: str,
        button_text: str,
        required_channels: list,
        winner_count: int,
        end_type: str,
        end_value: str,
        created_by: int
    ) -> Optional[int]:
        """Создание розыгрыша. Возвращает id."""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO giveaways
                        (title_ru, text_ru, media_file_id_ru, media_type_ru,
                         title_en, text_en, media_file_id_en, media_type_en,
                         button_text, required_channels, winner_count,
                         end_type, end_value, status, created_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?)
                ''', (
                    title_ru, text_ru, media_file_id_ru, media_type_ru,
                    title_en, text_en, media_file_id_en, media_type_en,
                    button_text, json.dumps(required_channels), winner_count,
                    end_type, end_value, created_by
                ))
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"Error creating giveaway: {e}")
                return None

    def add_giveaway_prize(
        self,
        giveaway_id: int,
        place: int,
        name: str,
        description: str = None,
        media_file_id: str = None,
        media_type: str = None,
        prize_type: str = "item",
        food_items: dict = None,
        pet_income: str = None,
        pet_mutation: str = None,
        pet_weather: str = None,
        pet_coeff: str = None,
    ) -> Optional[int]:
        """Добавить приз для места в розыгрыше."""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO giveaway_prizes
                        (giveaway_id, place, name, description, media_file_id, media_type,
                         prize_type, food_items, pet_income, pet_mutation, pet_weather, pet_coeff)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (
                    giveaway_id, place, name, description, media_file_id, media_type,
                    prize_type,
                    json.dumps(food_items) if food_items else None,
                    pet_income, pet_mutation, pet_weather, pet_coeff,
                ))
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"Error adding giveaway prize: {e}")
                return None

    def get_giveaway(self, giveaway_id: int) -> Optional[Dict]:
        """Получить розыгрыш по ID."""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM giveaways WHERE id = ?', (giveaway_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data['required_channels'] = json.loads(data['required_channels'])
            except Exception:
                data['required_channels'] = []
            return data

    def get_active_giveaways(self) -> List[Dict]:
        """Получить все активные розыгрыши."""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM giveaways WHERE status = 'active' ORDER BY created_at DESC")
            rows = cursor.fetchall()
            result = []
            for row in rows:
                data = dict(row)
                try:
                    data['required_channels'] = json.loads(data['required_channels'])
                except Exception:
                    data['required_channels'] = []
                result.append(data)
            return result

    def get_all_giveaways(self) -> List[Dict]:
        """Получить все розыгрыши (включая завершённые)."""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM giveaways ORDER BY created_at DESC")
            rows = cursor.fetchall()
            result = []
            for row in rows:
                data = dict(row)
                try:
                    data['required_channels'] = json.loads(data['required_channels'])
                except Exception:
                    data['required_channels'] = []
                result.append(data)
            return result

    def get_giveaway_prizes(self, giveaway_id: int) -> List[Dict]:
        """Получить все призы розыгрыша, сгруппированные по местам."""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM giveaway_prizes WHERE giveaway_id = ? ORDER BY place, id
            ''', (giveaway_id,))
            rows = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get("food_items"):
                    try:
                        d["food_items"] = json.loads(d["food_items"])
                    except Exception:
                        d["food_items"] = {}
                rows.append(d)
            return rows

    def join_giveaway(self, giveaway_id: int, user_id: int) -> bool:
        """Добавить участника в розыгрыш. Возвращает True если успешно (не дублирует)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO giveaway_participants (giveaway_id, user_id) VALUES (?,?)
                ''', (giveaway_id, user_id))
                conn.commit()
                return True
            except Exception:
                return False

    def is_giveaway_participant(self, giveaway_id: int, user_id: int) -> bool:
        """Проверить, участвует ли пользователь в розыгрыше."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 1 FROM giveaway_participants WHERE giveaway_id = ? AND user_id = ?
            ''', (giveaway_id, user_id))
            return cursor.fetchone() is not None

    def get_giveaway_participants(self, giveaway_id: int) -> List[Dict]:
        """Получить всех участников розыгрыша."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT gp.user_id, u.username, u.language
                FROM giveaway_participants gp
                LEFT JOIN users u ON gp.user_id = u.user_id
                WHERE gp.giveaway_id = ?
                ORDER BY gp.joined_at
            ''', (giveaway_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_giveaway_participant_count(self, giveaway_id: int) -> int:
        """Количество участников розыгрыша."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) FROM giveaway_participants WHERE giveaway_id = ?
            ''', (giveaway_id,))
            return cursor.fetchone()[0]

    def finish_giveaway(self, giveaway_id: int) -> bool:
        """Завершить розыгрыш (поставить статус finished)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE giveaways SET status = 'finished', ended_at = ? WHERE id = ?
            ''', (datetime.now(), giveaway_id))
            conn.commit()
            return cursor.rowcount > 0

    def set_giveaway_message_id(self, giveaway_id: int, message_id: int):
        """Сохранить ID сообщения розыгрыша в группе."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE giveaways SET group_message_id = ? WHERE id = ?
            ''', (message_id, giveaway_id))
            conn.commit()

    # ========== МЕТОДЫ ДЛЯ ROBLOX-НИКНЕЙМОВ ==========

    def get_roblox_nick(self, user_id: int) -> Optional[str]:
        """Получить Roblox-ник пользователя."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT roblox_nick FROM roblox_nicks WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            return row[0] if row else None

    def set_roblox_nick(self, user_id: int, nick: str) -> bool:
        """Установить/обновить Roblox-ник пользователя."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO roblox_nicks (user_id, roblox_nick, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET roblox_nick = excluded.roblox_nick, updated_at = excluded.updated_at
                ''', (user_id, nick, datetime.now()))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error setting roblox nick for {user_id}: {e}")
                return False

    # ========== МЕТОДЫ ДЛЯ ОБМЕНОВ ==========

    def get_trade_session(self, user1_id: int, user2_id: int) -> Optional[Dict]:
        """Найти существующую сессию обмена между двумя пользователями (в любом порядке)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM trade_sessions
                WHERE ((user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?))
                ORDER BY created_at DESC LIMIT 1
            ''', (user1_id, user2_id, user2_id, user1_id))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_trade_by_user(self, user_id: int) -> Optional[Dict]:
        """Найти активную сессию обмена для пользователя."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM trade_sessions
                WHERE (user1_id = ? OR user2_id = ?) AND status = 'active'
                ORDER BY created_at DESC LIMIT 1
            ''', (user_id, user_id))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_trade_by_topic(self, topic_id: int) -> Optional[Dict]:
        """Найти активную сессию обмена по ID топика."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM trade_sessions WHERE topic_id = ? AND status = 'active'
            ''', (topic_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_trade_session(self, user1_id: int, user2_id: int, topic_id: int = None) -> Optional[int]:
        """Создать новую сессию обмена."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO trade_sessions (user1_id, user2_id, topic_id, status)
                    VALUES (?, ?, ?, 'active')
                ''', (user1_id, user2_id, topic_id))
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"Error creating trade session: {e}")
                return None

    def update_trade_topic(self, session_id: int, topic_id: int):
        """Обновить ID топика для сессии обмена."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE trade_sessions SET topic_id = ? WHERE id = ?
            ''', (topic_id, session_id))
            conn.commit()

    def set_trade_admin_joined(self, session_id: int):
        """Отметить, что администратор подключился к диалогу."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE trade_sessions SET admin_joined = 1 WHERE id = ?
            ''', (session_id,))
            conn.commit()

    def finish_trade_session(self, session_id: int):
        """Завершить сессию обмена."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE trade_sessions SET status = 'finished', ended_at = ? WHERE id = ?
            ''', (datetime.now(), session_id))
            conn.commit()

    def get_trade_session_by_id(self, session_id: int) -> Optional[Dict]:
        """Получить сессию обмена по ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM trade_sessions WHERE id = ?', (session_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # ========== P2P ITEM TRADE ==========

    def create_item_trade(self, initiator_id: int, partner_id: int) -> Optional[int]:
        """Создать P2P-сессию обмена предметами."""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO item_trades
                        (initiator_id, partner_id, initiator_items, partner_items,
                         initiator_qty, partner_qty, status)
                    VALUES (?, ?, '[]', '[]', '{}', '{}', 'selecting')
                ''', (initiator_id, partner_id))
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"Error creating item trade: {e}")
                return None

    def get_item_trade(self, trade_id: int) -> Optional[Dict]:
        """Получить P2P-сессию по ID."""
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM item_trades WHERE id = ?', (trade_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            for field in ('initiator_items', 'partner_items'):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    d[field] = []
            for field in ('initiator_qty', 'partner_qty'):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    d[field] = {}
            return d

    def get_active_item_trade_for_user(self, user_id: int) -> Optional[Dict]:
        """Найти активную P2P-сессию для пользователя."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM item_trades
                WHERE (initiator_id = ? OR partner_id = ?)
                  AND status IN ('selecting', 'confirming')
                ORDER BY id DESC LIMIT 1
            ''', (user_id, user_id))
            row = cursor.fetchone()
            if not row:
                return None
            import json
            d = dict(row)
            for field in ('initiator_items', 'partner_items'):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    d[field] = []
            for field in ('initiator_qty', 'partner_qty'):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    d[field] = {}
            return d

    def get_stale_trades(self, older_than_minutes: int = 30) -> List[Dict]:
        """Вернуть P2P-обмены в статусе 'selecting' или 'confirming',
        которые созданы дольше older_than_minutes минут назад.

        Сравнение идёт по локальному времени (created_at хранится через datetime.now()),
        поэтому порог передаётся как Python-строка, а не через datetime('now') SQLite.
        """
        import json
        from datetime import datetime, timedelta
        threshold = (datetime.now() - timedelta(minutes=older_than_minutes)).strftime('%Y-%m-%d %H:%M:%S')
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM item_trades
                WHERE status IN ('selecting', 'confirming')
                  AND created_at <= ?
            ''', (threshold,))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                for field in ('initiator_items', 'partner_items'):
                    try:
                        d[field] = json.loads(d[field])
                    except Exception:
                        d[field] = []
                for field in ('initiator_qty', 'partner_qty'):
                    try:
                        d[field] = json.loads(d[field])
                    except Exception:
                        d[field] = {}
                result.append(d)
            return result

    def update_item_trade_offer(self, trade_id: int, user_id: int,
                                 item_ids: List[int], qty_map: dict) -> bool:
        """Обновить предложение участника (список предметов + количества)."""
        import json
        trade = self.get_item_trade(trade_id)
        if not trade:
            return False
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if trade['initiator_id'] == user_id:
                cursor.execute(
                    'UPDATE item_trades SET initiator_items=?, initiator_qty=?, '
                    'initiator_confirmed=0, partner_confirmed=0 WHERE id=?',
                    (json.dumps(item_ids), json.dumps(qty_map), trade_id)
                )
            else:
                cursor.execute(
                    'UPDATE item_trades SET partner_items=?, partner_qty=?, '
                    'initiator_confirmed=0, partner_confirmed=0 WHERE id=?',
                    (json.dumps(item_ids), json.dumps(qty_map), trade_id)
                )
            conn.commit()
            return True

    def set_item_trade_confirmed(self, trade_id: int, user_id: int) -> dict:
        """
        Атомарно установить подтверждение участника.
        Использует BEGIN EXCLUSIVE чтобы исключить race condition при одновременном
        нажатии «Подтвердить» обоими участниками.
        Возвращает обновлённую сессию или {} если обмен уже завершён/отменён.
        """
        with self.get_connection() as conn:
            conn.execute("BEGIN EXCLUSIVE")
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM item_trades WHERE id=?', (trade_id,))
            row = cursor.fetchone()
            if not row:
                conn.rollback()
                return {}
            trade = dict(row)
            # Не подтверждаем уже выполняющийся или завершённый обмен
            if trade['status'] in ('done', 'executing', 'cancelled'):
                conn.rollback()
                return trade
            if trade['initiator_id'] == user_id:
                cursor.execute(
                    'UPDATE item_trades SET initiator_confirmed=1, status=? WHERE id=?',
                    ('confirming', trade_id)
                )
            else:
                cursor.execute(
                    'UPDATE item_trades SET partner_confirmed=1, status=? WHERE id=?',
                    ('confirming', trade_id)
                )
            conn.commit()
        return self.get_item_trade(trade_id)

    def execute_item_trade(self, trade_id: int) -> bool:
        """
        Атомарно выполнить обмен: переместить предметы между пользователями.
        Возвращает True при успехе.

        Защита от двойного нажатия: BEGIN EXCLUSIVE + проверка статуса внутри транзакции.
        Если два запроса придут одновременно — второй увидит статус 'done' и вернёт False.
        """
        import json

        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                # Блокируем таблицу эксклюзивно — только один поток войдёт
                conn.execute("BEGIN EXCLUSIVE")

                # Перечитываем статус внутри транзакции — защита от race condition
                cursor.execute("SELECT * FROM item_trades WHERE id=?", (trade_id,))
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return False
                trade = dict(row)

                if trade['status'] != 'confirming':
                    # Уже выполнен или отменён другим запросом
                    conn.rollback()
                    return False
                if not (trade['initiator_confirmed'] and trade['partner_confirmed']):
                    conn.rollback()
                    return False

                # Сразу помечаем как 'executing' чтобы второй вызов не прошёл
                cursor.execute(
                    "UPDATE item_trades SET status='executing' WHERE id=? AND status='confirming'",
                    (trade_id,)
                )
                if cursor.rowcount == 0:
                    # Другой поток уже захватил — отступаем
                    conn.rollback()
                    return False

                conn.commit()
            except Exception as e:
                logger.error(f"execute_item_trade lock error: {e}")
                try:
                    conn.rollback()
                except Exception:
                    pass
                return False

        # Теперь безопасно читаем данные — мы единственные владельцы этого обмена
        trade = self.get_item_trade(trade_id)
        if not trade:
            return False

        initiator_id = trade['initiator_id']
        partner_id = trade['partner_id']
        init_items = trade['initiator_items']
        part_items = trade['partner_items']
        init_qty = trade['initiator_qty']
        part_qty = trade['partner_qty']

        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                def _transfer_item(cursor, iid: int, from_uid: int, to_uid: int, qty: int):
                    """Перенести qty единиц предмета iid от from_uid к to_uid.
                    Для еды без медиа — объединяет с существующей записью получателя."""
                    cursor.execute('SELECT quantity, user_id, item_type, name, media_file_id FROM inventory_items WHERE id=?', (iid,))
                    row = cursor.fetchone()
                    if not row or row[1] != from_uid:
                        raise ValueError(f"Item {iid} not owned by user {from_uid}")
                    cur_qty, _, item_type, name, media_file_id = row

                    if qty >= cur_qty:
                        # Передаём всю запись целиком
                        if item_type == 'food' and not media_file_id:
                            # Ищем у получателя запись с тем же именем
                            cursor.execute(
                                '''SELECT id, quantity FROM inventory_items
                                   WHERE user_id=? AND item_type='food' AND name=?
                                     AND (media_file_id IS NULL OR media_file_id='')
                                     AND id != ?
                                   LIMIT 1''',
                                (to_uid, name, iid)
                            )
                            existing = cursor.fetchone()
                            if existing:
                                cursor.execute(
                                    'UPDATE inventory_items SET quantity=? WHERE id=?',
                                    (existing[1] + cur_qty, existing[0])
                                )
                                cursor.execute('DELETE FROM inventory_items WHERE id=?', (iid,))
                                return
                        cursor.execute(
                            'UPDATE inventory_items SET user_id=?, locked_trade_id=NULL WHERE id=?',
                            (to_uid, iid)
                        )
                    else:
                        # Частичный перенос
                        cursor.execute(
                            'UPDATE inventory_items SET quantity=? WHERE id=?',
                            (cur_qty - qty, iid)
                        )
                        if item_type == 'food' and not media_file_id:
                            # Ищем у получателя запись с тем же именем
                            cursor.execute(
                                '''SELECT id, quantity FROM inventory_items
                                   WHERE user_id=? AND item_type='food' AND name=?
                                     AND (media_file_id IS NULL OR media_file_id='')
                                   LIMIT 1''',
                                (to_uid, name)
                            )
                            existing = cursor.fetchone()
                            if existing:
                                cursor.execute(
                                    'UPDATE inventory_items SET quantity=? WHERE id=?',
                                    (existing[1] + qty, existing[0])
                                )
                                return
                        cursor.execute('''
                            INSERT INTO inventory_items
                                (user_id, item_type, name, description, media_file_id, media_type,
                                 quantity, added_by, pet_income, pet_mutation, pet_weather, pet_coeff)
                            SELECT ?, item_type, name, description, media_file_id, media_type,
                                   ?, user_id, pet_income, pet_mutation, pet_weather, pet_coeff
                            FROM inventory_items WHERE id=?
                        ''', (to_uid, qty, iid))

                # Переносим предметы инициатора → партнёру
                for iid in init_items:
                    qty = int(init_qty.get(str(iid), 1))
                    _transfer_item(cursor, iid, initiator_id, partner_id, qty)

                # Переносим предметы партнёра → инициатору
                for iid in part_items:
                    qty = int(part_qty.get(str(iid), 1))
                    _transfer_item(cursor, iid, partner_id, initiator_id, qty)

                # Снимаем блокировку со всех предметов сессии
                all_ids = init_items + part_items
                if all_ids:
                    ph = ','.join('?' * len(all_ids))
                    cursor.execute(
                        f'UPDATE inventory_items SET locked_trade_id=NULL WHERE id IN ({ph})',
                        all_ids
                    )

                cursor.execute(
                    "UPDATE item_trades SET status='done' WHERE id=?", (trade_id,)
                )
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"execute_item_trade error: {e}")
                conn.rollback()
                return False

    def cancel_item_trade(self, trade_id: int) -> bool:
        """Отменить P2P-обмен и снять блокировку предметов.
        Нельзя отменить уже выполняющийся или завершённый обмен."""
        trade = self.get_item_trade(trade_id)
        if not trade:
            return False
        if trade['status'] in ('done', 'executing'):
            return False
        all_ids = trade['initiator_items'] + trade['partner_items']
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if all_ids:
                ph = ','.join('?' * len(all_ids))
                cursor.execute(
                    f'UPDATE inventory_items SET locked_trade_id=NULL WHERE id IN ({ph})',
                    all_ids
                )
            cursor.execute(
                "UPDATE item_trades SET status='cancelled' WHERE id=? AND status NOT IN ('done','executing')",
                (trade_id,)
            )
            conn.commit()
        return True

    def lock_items_for_trade(self, item_ids: List[int], trade_id: int) -> bool:
        """Заблокировать предметы для обмена."""
        if not item_ids:
            return True
        with self.get_connection() as conn:
            cursor = conn.cursor()
            ph = ','.join('?' * len(item_ids))
            cursor.execute(
                f'UPDATE inventory_items SET locked_trade_id=? WHERE id IN ({ph})',
                [trade_id] + item_ids
            )
            conn.commit()
        return True

    def unlock_items_for_trade(self, item_ids: List[int]) -> bool:
        """Снять блокировку предметов."""
        if not item_ids:
            return True
        with self.get_connection() as conn:
            cursor = conn.cursor()
            ph = ','.join('?' * len(item_ids))
            cursor.execute(
                f'UPDATE inventory_items SET locked_trade_id=NULL WHERE id IN ({ph})',
                item_ids
            )
            conn.commit()
        return True

    def get_all_pets_sorted(self) -> List[Dict]:
        """Получить всех петов из всех инвентарей, отсортированных по доходу (убывание).
        Доход берётся из поля pet_income (числовая строка вида '1 201 044' или '141685')."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT ii.*, u.username
                FROM inventory_items ii
                LEFT JOIN users u ON ii.user_id = u.user_id
                WHERE ii.item_type = 'pet'
                ORDER BY
                    CAST(REPLACE(REPLACE(ii.pet_income, ' ', ''), ',', '') AS REAL) DESC,
                    ii.created_at ASC
            ''')
            return [dict(r) for r in cursor.fetchall()]

    def search_pet_by_income(self, income_value: int) -> dict:
        """Найти пета по значению дохода.
        Возвращает dict с ключами:
          'exact'   — точное совпадение (или None)
          'nearest' — ближайший по значению (или None)
          'exact_index' — порядковый номер точного пета в отсортированном списке (1-based, или None)
          'nearest_index' — порядковый номер ближайшего пета (1-based, или None)
        """
        all_pets = self.get_all_pets_sorted()
        if not all_pets:
            return {'exact': None, 'nearest': None, 'exact_index': None, 'nearest_index': None}

        def _parse_income(pet: dict) -> int:
            raw = (pet.get('pet_income') or '').replace(' ', '').replace(',', '')
            try:
                return int(float(raw))
            except (ValueError, TypeError):
                return 0

        exact = None
        exact_index = None
        nearest = None
        nearest_index = None
        min_diff = None

        for idx, pet in enumerate(all_pets, 1):
            val = _parse_income(pet)
            if val == income_value:
                if exact is None:
                    exact = pet
                    exact_index = idx
            diff = abs(val - income_value)
            if min_diff is None or diff < min_diff:
                min_diff = diff
                nearest = pet
                nearest_index = idx

        return {
            'exact': exact,
            'nearest': nearest,
            'exact_index': exact_index,
            'nearest_index': nearest_index,
        }

    def cleanup_old_data(self, days: int = 30) -> dict:
        """
        Удаляет устаревшие данные из всех таблиц.
        Возвращает словарь с количеством удалённых записей по каждой таблице.

        Что удаляется:
          - item_trades:        завершённые/отменённые обмены старше days дней
          - trade_sessions:     завершённые сессии обменов через админа старше days дней
          - pickup_requests:    выполненные/отменённые запросы на выдачу старше days дней
          - giveaway_participants: участники завершённых розыгрышей (розыгрыш ended_at > days)
          - giveaways:          завершённые розыгрыши старше days дней (каскадно удалит prizes и participants)
        """
        result = {}
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. item_trades — завершённые/отменённые P2P-обмены
            cursor.execute('''
                DELETE FROM item_trades
                WHERE status IN ('done', 'completed', 'cancelled')
                  AND created_at < datetime('now', ? || ' days')
            ''', (f'-{days}',))
            result['item_trades'] = cursor.rowcount

            # 2. trade_sessions — завершённые сессии обменов через админа
            cursor.execute('''
                DELETE FROM trade_sessions
                WHERE status IN ('finished', 'ended')
                  AND ended_at < datetime('now', ? || ' days')
            ''', (f'-{days}',))
            result['trade_sessions'] = cursor.rowcount

            # 3. pickup_requests — выполненные/отменённые запросы на выдачу
            cursor.execute('''
                DELETE FROM pickup_requests
                WHERE status IN ('completed', 'cancelled', 'done')
                  AND created_at < datetime('now', ? || ' days')
            ''', (f'-{days}',))
            result['pickup_requests'] = cursor.rowcount

            # 4. giveaways — завершённые розыгрыши (каскадно удалит prizes и participants)
            cursor.execute('''
                DELETE FROM giveaways
                WHERE status IN ('ended', 'cancelled', 'finished')
                  AND ended_at < datetime('now', ? || ' days')
            ''', (f'-{days}',))
            result['giveaways'] = cursor.rowcount

            conn.commit()

        # VACUUM только если удалено что-то значимое (освобождает место на диске)
        total_deleted = sum(result.values())
        if total_deleted > 0:
            with self.get_connection() as conn:
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            logger.info(f"Cleanup: удалено {total_deleted} записей: {result}")
        else:
            logger.info("Cleanup: нечего удалять, БД актуальна")

        return result

    # ──────────────────────────────────────────────────────────────
    # HIDDEN USERS — скрытые пользователи (псевдоним вместо TG-ника)
    # ──────────────────────────────────────────────────────────────

    def add_hidden_user(self, user_id: int, alias: str, added_by: int) -> bool:
        """Добавить/обновить псевдоним скрытого пользователя."""
        with self.get_connection() as conn:
            try:
                conn.execute('''
                    INSERT INTO hidden_users (user_id, alias, added_by)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET alias=excluded.alias, added_by=excluded.added_by
                ''', (user_id, alias, added_by))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"add_hidden_user error: {e}")
                return False

    def remove_hidden_user(self, user_id: int) -> bool:
        """Убрать пользователя из скрытых."""
        with self.get_connection() as conn:
            cur = conn.execute('DELETE FROM hidden_users WHERE user_id=?', (user_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_hidden_user(self, user_id: int) -> Optional[Dict]:
        """Получить запись скрытого пользователя или None."""
        with self.get_connection() as conn:
            row = conn.execute(
                'SELECT * FROM hidden_users WHERE user_id=?', (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_all_hidden_users(self) -> List[Dict]:
        """Список всех скрытых пользователей."""
        with self.get_connection() as conn:
            rows = conn.execute(
                'SELECT * FROM hidden_users ORDER BY created_at DESC'
            ).fetchall()
            return [dict(r) for r in rows]

    def get_display_name(self, user_id: int, for_admin: bool = False) -> str:
        """
        Публичное имя пользователя.
        - for_admin=True  → всегда реальные данные (@username или ID:xxx)
        - for_admin=False → псевдоним если скрыт, иначе реальные данные
        """
        if not for_admin:
            hidden = self.get_hidden_user(user_id)
            if hidden:
                return hidden['alias']
        user = self.get_user(user_id)
        if user and user.get('username'):
            return f"@{user['username']}"
        return f"ID:{user_id}"

    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Найти пользователя по username (без @, без учёта регистра)."""
        uname = username.lstrip('@').lower()
        with self.get_connection() as conn:
            rows = conn.execute('SELECT * FROM users').fetchall()
            for row in rows:
                r = dict(row)
                if r.get('username') and r['username'].lower() == uname:
                    return r
        return None

    def get_unlocked_inventory(self, user_id: int) -> List[Dict]:
        """Получить предметы инвентаря, не заблокированные в обмене."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM inventory_items
                WHERE user_id = ? AND (locked_trade_id IS NULL OR locked_trade_id = 0)
                ORDER BY
                    CASE item_type WHEN 'item' THEN 1 WHEN 'food' THEN 2 WHEN 'pet' THEN 3 ELSE 4 END,
                    created_at ASC
            ''', (user_id,))
            return [dict(r) for r in cursor.fetchall()]