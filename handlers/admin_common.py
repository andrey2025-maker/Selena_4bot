"""
handlers/admin_common.py — Общие константы, зависимости и FSM-состояния для всех admin_*.py
Импортируется всеми остальными admin-модулями.
"""

from aiogram.fsm.state import State, StatesGroup
from database import Database
from utils.messages import locale_manager
import logging

logger = logging.getLogger(__name__)
db = Database()

# ========== СПИСОК АДМИНИСТРАТОРОВ ==========
ADMIN_IDS: list[int] = [1835558263, 8529443364, 1012045768]

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ========== ГЛОБАЛЬНЫЙ СЛОВАРЬ АКТИВНЫХ ЧАТОВ ==========
# Структура: {user_id: {"admin_id": int, "mode": "bot"|"group", "topic_id": int|None}}
# Восстанавливается из БД при старте — не теряется при перезапуске бота.
# Для обратной совместимости: если значение — int, считаем mode="bot".
try:
    _raw_chats: dict[int, int] = db.get_all_active_chats()
    active_chats: dict[int, dict] = {}
    for uid, aid in _raw_chats.items():
        topic_id = db.get_chat_topic(uid)
        mode = "group" if topic_id else "bot"
        active_chats[uid] = {"admin_id": aid, "mode": mode, "topic_id": topic_id}
    if active_chats:
        logger.info(f"Восстановлено {len(active_chats)} активных чатов из БД")
except Exception:
    active_chats: dict[int, dict] = {}


def _get_admin_id(chat_entry) -> int:
    """Совместимость: вернуть admin_id из записи active_chats (dict или int)."""
    if isinstance(chat_entry, dict):
        return chat_entry["admin_id"]
    return int(chat_entry)


def user_link(user_id: int, user: dict = None) -> str:
    """Вернуть HTML-ссылку на пользователя.
    Если user не передан — пытается получить из БД.
    Формат: <a href="tg://user?id=...">@username</a> или
            <a href="tg://user?id=...">ID: ...</a>
    """
    if user is None:
        user = db.get_user(user_id)
    if user and user.get("username"):
        label = f"@{user['username']}"
    else:
        label = f"ID: {user_id}"
    return f'<a href="tg://user?id={user_id}">{label}</a>'

# ========== FSM-СОСТОЯНИЯ ==========

class BroadcastStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_confirmation = State()

class ChatStates(StatesGroup):
    waiting_for_user = State()
    choosing_channel = State()   # выбор канала: бот или группа
    chatting = State()           # чат через ЛС бота
    group_chatting = State()     # чат через топик группы
    waiting_for_exception = State()

class RobloxNickStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_new_nick = State()
