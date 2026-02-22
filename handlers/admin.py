"""
handlers/admin.py — Фасад: собирает все admin-роутеры в один объект `router`.

Структура модулей:
  admin_common.py   — общие константы, db, is_admin, FSM-состояния
  admin_core.py     — панель, статистика, список пользователей
  admin_broadcast.py — рассылка
  admin_chat.py     — двусторонняя связь, исключения, Roblox-ники
  admin_backup.py   — резервные копии БД
"""

from aiogram import Router

from handlers.admin_common import db, is_admin, ADMIN_IDS, active_chats  # noqa: F401 — re-export
from handlers.admin_core import router as _core_router, show_admin_panel, show_stats  # noqa: F401
from handlers.admin_broadcast import router as _broadcast_router
from handlers.admin_chat import router as _chat_router
from handlers.admin_backup import router as _backup_router

router = Router()
router.include_router(_core_router)
router.include_router(_broadcast_router)
router.include_router(_chat_router)
router.include_router(_backup_router)
