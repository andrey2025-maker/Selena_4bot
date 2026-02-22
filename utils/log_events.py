"""
Централизованное логирование событий в группу-лог.
Все события отправляются в Config.LOG_GROUP_ID если он задан.
"""
import logging
from datetime import datetime
from typing import Optional
from aiogram import Bot
from aiogram.types import LinkPreviewOptions

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def _user_link(user_id: int, name: str, is_admin: bool = False) -> str:
    """Ссылка на пользователя с ID в скобках. Для админов добавляет префикс."""
    prefix = "👑 Админ " if is_admin else ""
    return f'{prefix}<a href="tg://user?id={user_id}">{name}</a> (ID: {user_id})'


async def send_log(bot: Bot, text: str) -> None:
    """Отправить сообщение в группу логов. Тихо игнорирует ошибки."""
    from config import Config
    if not Config.LOG_GROUP_ID:
        return
    try:
        await bot.send_message(
            Config.LOG_GROUP_ID,
            text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logger.warning(f"[log_events] Не удалось отправить лог: {e}")


# ─────────────────────────────────────────────
#  ИНВЕНТАРЬ
# ─────────────────────────────────────────────

async def log_inventory_add(
    bot: Bot,
    *,
    admin_id: int,
    admin_name: str,
    user_id: int,
    user_name: str,
    item_type: str,
    item_name: str,
    quantity: int = 1,
) -> None:
    type_emoji = {"food": "🍎", "pet": "🐾", "item": "📦"}.get(item_type, "📦")
    qty_str = f" ×{quantity}" if quantity > 1 else ""
    text = (
        f"📥 <b>Добавлено в инвентарь</b>\n"
        f"👤 Пользователь: {_user_link(user_id, user_name)}\n"
        f"🧑‍💼 Администратор: {_user_link(admin_id, admin_name, is_admin=True)}\n"
        f"{type_emoji} Предмет: <b>{item_name}</b>{qty_str}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


async def log_inventory_remove(
    bot: Bot,
    *,
    admin_id: int,
    admin_name: str,
    user_id: int,
    user_name: str,
    item_name: str,
    quantity: int = 1,
) -> None:
    qty_str = f" ×{quantity}" if quantity > 1 else ""
    text = (
        f"📤 <b>Удалено из инвентаря</b>\n"
        f"👤 Пользователь: {_user_link(user_id, user_name)}\n"
        f"🧑‍💼 Администратор: {_user_link(admin_id, admin_name, is_admin=True)}\n"
        f"🗑 Предмет: <b>{item_name}</b>{qty_str}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


async def log_inventory_pickup_request(
    bot: Bot,
    *,
    user_id: int,
    user_name: str,
    item_name: str,
    quantity: int = 1,
) -> None:
    qty_str = f" ×{quantity}" if quantity > 1 else ""
    text = (
        f"📬 <b>Запрос на забор предмета</b>\n"
        f"👤 Пользователь: {_user_link(user_id, user_name)}\n"
        f"📦 Предмет: <b>{item_name}</b>{qty_str}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


async def log_inventory_pickup_done(
    bot: Bot,
    *,
    admin_id: int,
    admin_name: str,
    user_id: int,
    user_name: str,
    item_name: str,
    quantity: int = 1,
) -> None:
    qty_str = f" ×{quantity}" if quantity > 1 else ""
    text = (
        f"✅ <b>Забор предмета выполнен</b>\n"
        f"👤 Пользователь: {_user_link(user_id, user_name)}\n"
        f"🧑‍💼 Выполнил: {_user_link(admin_id, admin_name, is_admin=True)}\n"
        f"📦 Предмет: <b>{item_name}</b>{qty_str}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


# ─────────────────────────────────────────────
#  P2P ОБМЕНЫ (item_trade)
# ─────────────────────────────────────────────

async def log_item_trade_start(
    bot: Bot,
    *,
    initiator_id: int,
    initiator_name: str,
    partner_id: int,
    partner_name: str,
) -> None:
    text = (
        f"🔄 <b>P2P обмен начат</b>\n"
        f"👤 Инициатор: {_user_link(initiator_id, initiator_name)}\n"
        f"👤 Партнёр: {_user_link(partner_id, partner_name)}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


async def log_item_trade_complete(
    bot: Bot,
    *,
    initiator_id: int,
    initiator_name: str,
    partner_id: int,
    partner_name: str,
    initiator_items: str,
    partner_items: str,
) -> None:
    text = (
        f"✅ <b>P2P обмен завершён</b>\n"
        f"👤 {_user_link(initiator_id, initiator_name)} отдал: <b>{initiator_items}</b>\n"
        f"👤 {_user_link(partner_id, partner_name)} отдал: <b>{partner_items}</b>\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


async def log_item_trade_cancel(
    bot: Bot,
    *,
    cancelled_by_id: int,
    cancelled_by_name: str,
    other_id: int,
    other_name: str,
) -> None:
    text = (
        f"❌ <b>P2P обмен отменён</b>\n"
        f"👤 Отменил: {_user_link(cancelled_by_id, cancelled_by_name)}\n"
        f"👤 Второй участник: {_user_link(other_id, other_name)}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


# ─────────────────────────────────────────────
#  ОБМЕНЫ ЧЕРЕЗ АДМИНИСТРАТОРА (trade)
# ─────────────────────────────────────────────

async def log_trade_session_start(
    bot: Bot,
    *,
    user1_id: int,
    user1_name: str,
    user2_id: int,
    user2_name: str,
) -> None:
    text = (
        f"💬 <b>Обмен через администратора начат</b>\n"
        f"👤 {_user_link(user1_id, user1_name)}\n"
        f"👤 {_user_link(user2_id, user2_name)}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


async def log_trade_session_stop(
    bot: Bot,
    *,
    stopped_by_id: int,
    stopped_by_name: str,
    user1_id: int,
    user1_name: str,
    user2_id: int,
    user2_name: str,
) -> None:
    text = (
        f"🛑 <b>Обмен через администратора завершён</b>\n"
        f"👤 {_user_link(user1_id, user1_name)} ↔ {_user_link(user2_id, user2_name)}\n"
        f"🧑‍💼 Завершил: {_user_link(stopped_by_id, stopped_by_name, is_admin=True)}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


# ─────────────────────────────────────────────
#  РОЗЫГРЫШИ
# ─────────────────────────────────────────────

async def log_giveaway_created(
    bot: Bot,
    *,
    admin_id: int,
    admin_name: str,
    giveaway_id: int,
    title: str,
    winner_count: int,
    end_type: str,
    end_value: str,
) -> None:
    end_str = (
        f"⏰ по времени: {end_value} (МСК)"
        if end_type == "time"
        else f"👥 при {end_value} участниках"
    )
    text = (
        f"🎰 <b>Розыгрыш создан</b>\n"
        f"🧑‍💼 Администратор: {_user_link(admin_id, admin_name, is_admin=True)}\n"
        f"📌 #{giveaway_id} — <b>{title}</b>\n"
        f"🏆 Победителей: {winner_count}\n"
        f"🏁 Завершение: {end_str}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


async def log_giveaway_finished(
    bot: Bot,
    *,
    giveaway_id: int,
    title: str,
    participant_count: int,
    winners: list,  # list of (user_id, name, place)
) -> None:
    winners_str = "\n".join(
        f"  {place}. {_user_link(uid, name)}" for uid, name, place in winners
    ) or "  —"
    text = (
        f"🏁 <b>Розыгрыш завершён</b>\n"
        f"📌 #{giveaway_id} — <b>{title}</b>\n"
        f"👥 Участников: {participant_count}\n"
        f"🏆 Победители:\n{winners_str}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


# ─────────────────────────────────────────────
#  РАССЫЛКИ
# ─────────────────────────────────────────────

async def log_broadcast(
    bot: Bot,
    *,
    admin_id: int,
    admin_name: str,
    target: str,
    sent: int,
    failed: int,
) -> None:
    text = (
        f"📢 <b>Рассылка выполнена</b>\n"
        f"🧑‍💼 Администратор: {_user_link(admin_id, admin_name, is_admin=True)}\n"
        f"🎯 Аудитория: <b>{target}</b>\n"
        f"✅ Доставлено: {sent}  ❌ Ошибок: {failed}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


# ─────────────────────────────────────────────
#  ИСКЛЮЧЕНИЯ ИЗ ПОДПИСКИ
# ─────────────────────────────────────────────

async def log_exception_added(
    bot: Bot,
    *,
    admin_id: int,
    admin_name: str,
    user_id: int,
    user_name: str,
) -> None:
    text = (
        f"🔓 <b>Исключение добавлено</b>\n"
        f"👤 Пользователь: {_user_link(user_id, user_name)}\n"
        f"🧑‍💼 Администратор: {_user_link(admin_id, admin_name, is_admin=True)}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


async def log_exception_removed(
    bot: Bot,
    *,
    admin_id: int,
    admin_name: str,
    user_id: int,
    user_name: str,
) -> None:
    text = (
        f"🔒 <b>Исключение удалено</b>\n"
        f"👤 Пользователь: {_user_link(user_id, user_name)}\n"
        f"🧑‍💼 Администратор: {_user_link(admin_id, admin_name, is_admin=True)}\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)


# ─────────────────────────────────────────────
#  СМЕНА ROBLOX-НИКНЕЙМОВ
# ─────────────────────────────────────────────

async def log_roblox_nick_changed(
    bot: Bot,
    *,
    admin_id: int,
    admin_name: str,
    user_id: int,
    user_name: str,
    old_nick: Optional[str],
    new_nick: str,
) -> None:
    old_str = f"<s>{old_nick}</s>" if old_nick else "—"
    text = (
        f"🎮 <b>Roblox-никнейм изменён</b>\n"
        f"👤 Пользователь: {_user_link(user_id, user_name)}\n"
        f"🧑‍💼 Администратор: {_user_link(admin_id, admin_name, is_admin=True)}\n"
        f"📝 {old_str} → <b>{new_nick}</b>\n"
        f"🕐 {_now()}"
    )
    await send_log(bot, text)
