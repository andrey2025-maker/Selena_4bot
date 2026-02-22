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
# Восстанавливается из БД при старте — не теряется при перезапуске бота
try:
    active_chats: dict[int, int] = db.get_all_active_chats()
    if active_chats:
        logger.info(f"Восстановлено {len(active_chats)} активных чатов из БД")
except Exception:
    active_chats: dict[int, int] = {}

# ========== FSM-СОСТОЯНИЯ ==========

class BroadcastStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_confirmation = State()

class ChatStates(StatesGroup):
    waiting_for_user = State()
    chatting = State()
    waiting_for_exception = State()

class RobloxNickStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_new_nick = State()
