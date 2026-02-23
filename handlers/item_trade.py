"""
handlers/item_trade.py — P2P-обмен предметами инвентаря без администратора.

Флоу:
  1. Инициатор нажимает «🔄 Обмен» в своём инвентаре.
  2. Бот просит указать партнёра (@username или переслать сообщение).
  3. Партнёру приходит приглашение — он принимает или отклоняет.
  4. Оба независимо выбирают предметы из своего инвентаря + количество.
     Выбранные предметы блокируются (locked_trade_id) — нельзя забрать или
     использовать в другом обмене.
  5. После выбора каждый видит сводку: «Вы предлагаете / Партнёр предлагает».
  6. Оба нажимают «✅ Подтвердить» — только тогда происходит атомарный обмен.
     Если кто-то нажимает «✏️ Изменить» — подтверждения сбрасываются, возврат к выбору.
     Если кто-то нажимает «❌ Отмена» — сессия отменяется, предметы разблокируются.
  7. После успешного обмена оба получают уведомление.

Защита от мошенничества:
  - Предметы блокируются сразу при выборе.
  - Обмен атомарен: либо оба получают предметы, либо ничего не меняется.
  - Нельзя подтвердить, не выбрав ни одного предмета (можно предложить «ничего»
    только если партнёр тоже явно согласен на это — кнопка «Предложить ничего»).
  - Изменение предложения сбрасывает оба подтверждения.
"""

import logging
from typing import List, Optional

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from database import Database
from utils.messages import locale_manager
from utils.log_events import (
    log_item_trade_start, log_item_trade_complete, log_item_trade_cancel,
)

logger = logging.getLogger(__name__)
router = Router()
db = Database()

ITEMS_PER_PAGE = 8  # предметов на странице при выборе


# ========== FSM ==========

class ItemTradeStates(StatesGroup):
    waiting_for_partner   = State()  # инициатор вводит партнёра
    waiting_invite        = State()  # партнёр ещё не ответил
    selecting_own_items   = State()  # участник выбирает свои предметы
    waiting_partner_ready = State()  # ждём пока партнёр тоже выберет
    reviewing             = State()  # просмотр сводки перед подтверждением
    final_confirm         = State()  # финальное двойное подтверждение


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def _user_display(user: dict | None) -> str:
    if not user:
        return "?"
    name = user.get("first_name") or user.get("username") or str(user.get("user_id", "?"))
    if user.get("username"):
        return f"@{user['username']}"
    return name


def _item_label(item: dict) -> str:
    qty = item.get("quantity", 1)
    qty_str = f" x{qty}" if qty > 1 else ""
    return f"{item['name']}{qty_str}"


def _offer_text(items: list, qty_map: dict, lang: str) -> str:
    """Текст предложения: список предметов с количествами."""
    if not items:
        return "  — (ничего)" if lang == "RUS" else "  — (nothing)"
    lines = []
    for item in items:
        iid = str(item["id"])
        qty = int(qty_map.get(iid, 1))
        max_qty = item.get("quantity", 1)
        qty_str = f" x{qty}" if max_qty > 1 else ""
        lines.append(f"  • {item['name']}{qty_str}")
    return "\n".join(lines)


def _build_select_keyboard(
    items: list,
    selected_ids: List[int],
    qty_map: dict,
    trade_id: int,
    page: int,
    lang: str,
    allow_empty: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура выбора предметов для обмена."""
    total = len(items)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page_items = items[page * ITEMS_PER_PAGE: (page + 1) * ITEMS_PER_PAGE]

    rows = []
    for item in page_items:
        iid = item["id"]
        checked = iid in selected_ids
        mark = "✅" if checked else "☑️"
        qty = int(qty_map.get(str(iid), 1))
        max_qty = item.get("quantity", 1)
        qty_str = f" x{qty}/{max_qty}" if max_qty > 1 else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark} {item['name']}{qty_str}",
            callback_data=f"itr_tog_{trade_id}_{iid}"
        )])

    # Пагинация
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"itr_page_{trade_id}_{page-1}"))
        nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="itr_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"itr_page_{trade_id}_{page+1}"))
        rows.append(nav)

    # Кнопки управления количеством для выбранных предметов с qty>1
    for iid in selected_ids:
        item = next((i for i in items if i["id"] == iid), None)
        if item and item.get("quantity", 1) > 1:
            cur = int(qty_map.get(str(iid), 1))
            rows.append([
                InlineKeyboardButton(text=f"➖ {item['name']}", callback_data=f"itr_dec_{trade_id}_{iid}"),
                InlineKeyboardButton(text=f"{cur}", callback_data="itr_noop"),
                InlineKeyboardButton(text="➕", callback_data=f"itr_inc_{trade_id}_{iid}"),
            ])

    # Нижние кнопки
    bottom = []
    if selected_ids:
        # Есть выбранные предметы — показываем «Готово»
        done_text = "✅ Готово" if lang == "RUS" else "✅ Done"
        bottom.append(InlineKeyboardButton(text=done_text, callback_data=f"itr_done_{trade_id}"))
    elif allow_empty:
        # Ничего не выбрано, но пустое предложение разрешено — показываем «Предложить ничего»
        nothing_text = "🤝 Предложить ничего" if lang == "RUS" else "🤝 Offer nothing"
        bottom.append(InlineKeyboardButton(text=nothing_text, callback_data=f"itr_done_{trade_id}"))
    cancel_text = "❌ Отменить обмен" if lang == "RUS" else "❌ Cancel trade"
    rows.append(bottom if bottom else [])
    rows.append([InlineKeyboardButton(text=cancel_text, callback_data=f"itr_cancel_{trade_id}")])
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def _build_review_keyboard(trade_id: int, confirmed: bool, lang: str) -> InlineKeyboardMarkup:
    """Клавиатура управления в сообщении с кнопками (под сводкой)."""
    rows = []
    if not confirmed:
        rows.append([InlineKeyboardButton(
            text="✅ Подтвердить обмен" if lang == "RUS" else "✅ Confirm trade",
            callback_data=f"itr_confirm_{trade_id}"
        )])
    else:
        waiting = "⏳ Ожидаем партнёра…" if lang == "RUS" else "⏳ Waiting for partner…"
        rows.append([InlineKeyboardButton(text=waiting, callback_data="itr_noop")])
    rows.append([InlineKeyboardButton(
        text="✏️ Изменить предложение" if lang == "RUS" else "✏️ Edit offer",
        callback_data=f"itr_edit_{trade_id}"
    )])
    rows.append([InlineKeyboardButton(
        text="❌ Отменить обмен" if lang == "RUS" else "❌ Cancel trade",
        callback_data=f"itr_cancel_{trade_id}"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _review_text(trade: dict, user_id: int, lang: str) -> str:
    """Текст сводки обмена (только данные, без инструкций — они в отдельном сообщении)."""
    is_init = trade['initiator_id'] == user_id
    my_item_ids = trade['initiator_items'] if is_init else trade['partner_items']
    their_item_ids = trade['partner_items'] if is_init else trade['initiator_items']
    my_qty = trade['initiator_qty'] if is_init else trade['partner_qty']
    their_qty = trade['partner_qty'] if is_init else trade['initiator_qty']

    partner_id = trade['partner_id'] if is_init else trade['initiator_id']
    partner = db.get_user(partner_id)
    partner_display = _user_display(partner)

    my_items = [db.get_inventory_item(iid) for iid in my_item_ids]
    my_items = [i for i in my_items if i]
    their_items = [db.get_inventory_item(iid) for iid in their_item_ids]
    their_items = [i for i in their_items if i]

    my_offer = _offer_text(my_items, my_qty, lang)
    their_offer = _offer_text(their_items, their_qty, lang)

    my_confirmed = trade['initiator_confirmed'] if is_init else trade['partner_confirmed']
    their_confirmed = trade['partner_confirmed'] if is_init else trade['initiator_confirmed']

    my_status = "✅" if my_confirmed else "⏳"
    their_status = "✅" if their_confirmed else "⏳"

    if lang == "RUS":
        return (
            f"🔄 <b>Обмен предметами</b>\n\n"
            f"📤 <b>Вы предлагаете:</b>\n{my_offer}\n\n"
            f"📥 <b>{partner_display} предлагает:</b>\n{their_offer}\n\n"
            f"<b>Статус:</b>\n"
            f"  Вы: {my_status}\n"
            f"  {partner_display}: {their_status}"
        )
    else:
        return (
            f"🔄 <b>Item Trade</b>\n\n"
            f"📤 <b>You offer:</b>\n{my_offer}\n\n"
            f"📥 <b>{partner_display} offers:</b>\n{their_offer}\n\n"
            f"<b>Status:</b>\n"
            f"  You: {my_status}\n"
            f"  {partner_display}: {their_status}"
        )


def _controls_text(lang: str) -> str:
    """Текст сообщения с кнопками управления."""
    if lang == "RUS":
        return (
            "Нажмите <b>«✅ Подтвердить»</b> когда будете готовы.\n"
            "Обмен произойдёт только когда оба подтвердят."
        )
    return (
        "Press <b>«✅ Confirm»</b> when ready.\n"
        "Trade happens only when both confirm."
    )


async def _final_confirm_text(trade: dict, user_id: int, lang: str) -> str:
    """Текст финального подтверждения — показывает что на что меняется."""
    is_init = trade['initiator_id'] == user_id
    my_item_ids = trade['initiator_items'] if is_init else trade['partner_items']
    their_item_ids = trade['partner_items'] if is_init else trade['initiator_items']
    my_qty = trade['initiator_qty'] if is_init else trade['partner_qty']
    their_qty = trade['partner_qty'] if is_init else trade['initiator_qty']

    partner_id = trade['partner_id'] if is_init else trade['initiator_id']
    partner = db.get_user(partner_id)
    partner_display = _user_display(partner)

    my_items = [db.get_inventory_item(iid) for iid in my_item_ids]
    my_items = [i for i in my_items if i]
    their_items = [db.get_inventory_item(iid) for iid in their_item_ids]
    their_items = [i for i in their_items if i]

    my_offer = _offer_text(my_items, my_qty, lang)
    their_offer = _offer_text(their_items, their_qty, lang)

    if lang == "RUS":
        return (
            f"⚠️ <b>Финальное подтверждение обмена</b>\n\n"
            f"Вы отдаёте <b>{partner_display}</b>:\n{my_offer}\n\n"
            f"Вы получаете от <b>{partner_display}</b>:\n{their_offer}\n\n"
            f"Оба участника должны нажать <b>«✅ Подтвердить»</b> для завершения обмена."
        )
    return (
        f"⚠️ <b>Final trade confirmation</b>\n\n"
        f"You give to <b>{partner_display}</b>:\n{my_offer}\n\n"
        f"You receive from <b>{partner_display}</b>:\n{their_offer}\n\n"
        f"Both participants must press <b>«✅ Confirm»</b> to complete the trade."
    )


def _final_confirm_keyboard(trade_id: int, confirmed: bool, lang: str) -> InlineKeyboardMarkup:
    """Клавиатура финального подтверждения."""
    if confirmed:
        waiting = "⏳ Ожидаем партнёра…" if lang == "RUS" else "⏳ Waiting for partner…"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=waiting, callback_data="itr_noop")],
            [InlineKeyboardButton(
                text="❌ Отменить обмен" if lang == "RUS" else "❌ Cancel trade",
                callback_data=f"itr_cancel_{trade_id}"
            )],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Подтвердить" if lang == "RUS" else "✅ Confirm",
            callback_data=f"itr_final_{trade_id}"
        )],
        [InlineKeyboardButton(
            text="❌ Отменить обмен" if lang == "RUS" else "❌ Cancel trade",
            callback_data=f"itr_cancel_{trade_id}"
        )],
    ])


async def _notify_partner_selection_done(bot, storage, bot_id: int, trade: dict, done_user_id: int):
    """Уведомить партнёра, что другой участник завершил выбор — обновляем его сводку."""
    partner_id = trade['partner_id'] if trade['initiator_id'] == done_user_id else trade['initiator_id']
    partner = db.get_user(partner_id)
    lang = partner.get("language", "RUS") if partner else "RUS"
    lc = "ru" if lang == "RUS" else "en"

    done_user = db.get_user(done_user_id)
    done_display = _user_display(done_user)

    # Проверяем, завершил ли уже партнёр выбор (есть ли у него review_msg_id)
    from aiogram.fsm.storage.base import StorageKey
    key = StorageKey(bot_id=bot_id, chat_id=partner_id, user_id=partner_id)
    from aiogram.fsm.context import FSMContext
    partner_fsm = FSMContext(storage=storage, key=key)
    partner_data = await partner_fsm.get_data()
    partner_state = await partner_fsm.get_state()

    if partner_state == ItemTradeStates.reviewing:
        # Партнёр уже в режиме просмотра — обновляем его сводку
        review_msg_id = partner_data.get("review_msg_id")
        controls_msg_id = partner_data.get("controls_msg_id")
        is_partner_confirmed = trade['partner_confirmed'] if trade['initiator_id'] == partner_id else trade['initiator_confirmed']
        review_text = await _review_text(trade, partner_id, lang)
        controls_kb = _build_review_keyboard(trade['id'], bool(is_partner_confirmed), lang)

        if review_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=partner_id, message_id=review_msg_id,
                    text=review_text, parse_mode="HTML"
                )
            except Exception:
                pass
        if controls_msg_id:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=partner_id, message_id=controls_msg_id,
                    reply_markup=controls_kb
                )
            except Exception:
                pass
        # Если сообщений нет — отправляем новые
        if not review_msg_id:
            notify = locale_manager.get_text(lc, "item_trade.partner_finished_selecting").format(partner=done_display)
            await _send_review_messages(bot, partner_id, trade, lang, notify_prefix=notify)
    else:
        # Партнёр ещё выбирает — просто уведомляем
        notify = locale_manager.get_text(lc, "item_trade.partner_finished_selecting").format(partner=done_display)
        try:
            await bot.send_message(partner_id, notify, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Could not notify partner {partner_id}: {e}")


async def _send_review_messages(bot, user_id: int, trade: dict, lang: str,
                                 storage=None, bot_id: int = None,
                                 notify_prefix: str = None) -> tuple[int, int]:
    """
    Отправить два сообщения: сводку (без кнопок) + управление (с кнопками).
    Возвращает (review_msg_id, controls_msg_id).
    """
    is_confirmed = False
    if trade['initiator_id'] == user_id:
        is_confirmed = bool(trade['initiator_confirmed'])
    else:
        is_confirmed = bool(trade['partner_confirmed'])

    review_text = await _review_text(trade, user_id, lang)
    controls_kb = _build_review_keyboard(trade['id'], is_confirmed, lang)
    controls_text = _controls_text(lang)

    if notify_prefix:
        review_text = f"{notify_prefix}\n\n{review_text}"

    review_msg_id = None
    controls_msg_id = None
    try:
        rm = await bot.send_message(user_id, review_text, parse_mode="HTML")
        review_msg_id = rm.message_id
    except Exception as e:
        logger.warning(f"Could not send review to {user_id}: {e}")
    try:
        cm = await bot.send_message(user_id, controls_text, parse_mode="HTML", reply_markup=controls_kb)
        controls_msg_id = cm.message_id
    except Exception as e:
        logger.warning(f"Could not send controls to {user_id}: {e}")

    if storage and bot_id:
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.context import FSMContext
        key = StorageKey(bot_id=bot_id, chat_id=user_id, user_id=user_id)
        fsm = FSMContext(storage=storage, key=key)
        await fsm.update_data(review_msg_id=review_msg_id, controls_msg_id=controls_msg_id)

    return review_msg_id, controls_msg_id


async def _update_review_messages(bot, user_id: int, trade: dict, lang: str,
                                   review_msg_id: int, controls_msg_id: int):
    """Обновить существующие сообщения сводки и управления."""
    is_confirmed = False
    if trade['initiator_id'] == user_id:
        is_confirmed = bool(trade['initiator_confirmed'])
    else:
        is_confirmed = bool(trade['partner_confirmed'])

    review_text = await _review_text(trade, user_id, lang)
    controls_kb = _build_review_keyboard(trade['id'], is_confirmed, lang)

    if review_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=user_id, message_id=review_msg_id,
                text=review_text, parse_mode="HTML"
            )
        except Exception:
            pass
    if controls_msg_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=user_id, message_id=controls_msg_id,
                reply_markup=controls_kb
            )
        except Exception:
            pass


async def _cancel_trade_notify(bot, trade: dict, cancelled_by: int):
    """Уведомить обоих участников об отмене."""
    canceller = db.get_user(cancelled_by)
    canceller_display = _user_display(canceller)

    for uid in (trade['initiator_id'], trade['partner_id']):
        user = db.get_user(uid)
        lang = user.get("language", "RUS") if user else "RUS"
        lc = "ru" if lang == "RUS" else "en"
        text = locale_manager.get_text(lc, "item_trade.trade_cancelled_notify").format(canceller=canceller_display)
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
        except Exception:
            pass


# ========== ИНИЦИАЦИЯ ОБМЕНА ИЗ ИНВЕНТАРЯ ==========

@router.callback_query(F.data == "inv_item_trade")
async def item_trade_start(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал «🔄 Обмен» в инвентаре."""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    lc = "ru" if lang == "RUS" else "en"

    # Проверяем нет ли активного обмена
    existing = db.get_active_item_trade_for_user(user_id)
    if existing:
        await callback.answer(locale_manager.get_text(lc, "item_trade.already_active"), show_alert=True)
        return

    items = db.get_unlocked_inventory(user_id)
    if not items:
        await callback.answer(locale_manager.get_text(lc, "item_trade.inventory_empty"), show_alert=True)
        return

    await state.set_state(ItemTradeStates.waiting_for_partner)
    await state.update_data(trade_lang=lang)

    await callback.message.answer(locale_manager.get_text(lc, "item_trade.start_text"), parse_mode="HTML")
    await callback.answer()


@router.message(ItemTradeStates.waiting_for_partner)
async def item_trade_receive_partner(message: Message, state: FSMContext):
    """Получаем партнёра — @username или пересланное сообщение."""
    user_id = message.from_user.id
    data = await state.get_data()
    lang = data.get("trade_lang", "RUS")

    lc = "ru" if lang == "RUS" else "en"

    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer(locale_manager.get_text(lc, "common.cancelled"))
        return

    partner_id: Optional[int] = None

    # Пересланное сообщение
    if message.forward_origin:
        fo = message.forward_origin
        if hasattr(fo, "sender_user") and fo.sender_user:
            partner_id = fo.sender_user.id
        elif hasattr(fo, "sender_user_name"):
            uname = fo.sender_user_name.lower()
            for u in db.get_all_users():
                if u.get("username") and u["username"].lower() == uname:
                    partner_id = u["user_id"]
                    break

    # @username
    elif message.text and message.text.strip().startswith("@"):
        uname = message.text.strip()[1:].lower()
        for u in db.get_all_users():
            if u.get("username") and u["username"].lower() == uname:
                partner_id = u["user_id"]
                break

    if not partner_id:
        await message.answer(locale_manager.get_text(lc, "item_trade.user_not_found"))
        return

    if partner_id == user_id:
        await message.answer(locale_manager.get_text(lc, "item_trade.cannot_trade_self"))
        return

    # Проверяем что партнёр зарегистрирован
    partner = db.get_user(partner_id)
    if not partner:
        await message.answer(locale_manager.get_text(lc, "item_trade.partner_not_registered"))
        return

    # Проверяем нет ли у партнёра активного обмена
    existing = db.get_active_item_trade_for_user(partner_id)
    if existing:
        await message.answer(locale_manager.get_text(lc, "item_trade.partner_has_active"))
        return

    # Создаём сессию
    trade_id = db.create_item_trade(user_id, partner_id)
    if not trade_id:
        await message.answer(locale_manager.get_text(lc, "item_trade.creation_error"))
        await state.clear()
        return

    await state.set_state(ItemTradeStates.waiting_invite)
    await state.update_data(trade_id=trade_id, partner_id=partner_id)

    partner_display = _user_display(partner)
    await message.answer(
        locale_manager.get_text(lc, "item_trade.invitation_sent").format(partner=partner_display),
        parse_mode="HTML"
    )

    # Отправляем приглашение партнёру
    initiator = db.get_user(user_id)
    init_display = _user_display(initiator)
    partner_lang = partner.get("language", "RUS")
    partner_lc = "ru" if partner_lang == "RUS" else "en"

    invite_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Принять" if partner_lang == "RUS" else "✅ Accept",
            callback_data=f"itr_accept_{trade_id}"
        ),
        InlineKeyboardButton(
            text="❌ Отклонить" if partner_lang == "RUS" else "❌ Decline",
            callback_data=f"itr_decline_{trade_id}"
        ),
    ]])

    invite_text = locale_manager.get_text(partner_lc, "item_trade.invite_text").format(init=init_display)

    try:
        await message.bot.send_message(
            partner_id, invite_text, parse_mode="HTML", reply_markup=invite_keyboard
        )
    except Exception as e:
        db.cancel_item_trade(trade_id)
        await state.clear()
        await message.answer(locale_manager.get_text(lc, "item_trade.invitation_send_error").format(e=e))


# ========== ОТВЕТ НА ПРИГЛАШЕНИЕ ==========

@router.callback_query(F.data.startswith("itr_accept_"))
async def item_trade_accept(callback: CallbackQuery, state: FSMContext):
    """Партнёр принял приглашение."""
    trade_id = int(callback.data.split("_")[2])
    trade = db.get_item_trade(trade_id)

    if not trade or trade['status'] not in ('selecting', 'confirming'):
        await callback.answer("❌ Обмен уже недоступен", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id != trade['partner_id']:
        await callback.answer("⛔ Это не ваш обмен", show_alert=True)
        return

    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    await callback.message.delete()

    # Лог: обмен начат
    initiator_id = trade['initiator_id']
    initiator_for_log = db.get_user(initiator_id)
    await log_item_trade_start(
        callback.bot,
        initiator_id=initiator_id,
        initiator_name=_user_display(initiator_for_log),
        partner_id=user_id,
        partner_name=_user_display(user),
    )

    # Устанавливаем состояние для партнёра
    await state.set_state(ItemTradeStates.selecting_own_items)
    await state.update_data(trade_id=trade_id, trade_lang=lang, select_page=0, selected_ids=[], qty_map={})

    # Уведомляем инициатора
    initiator_id = trade['initiator_id']
    initiator = db.get_user(initiator_id)
    init_lang = initiator.get("language", "RUS") if initiator else "RUS"
    partner_display = _user_display(user)

    init_lc = "ru" if init_lang == "RUS" else "en"
    init_text = locale_manager.get_text(init_lc, "item_trade.partner_accepted").format(partner=partner_display)

    # Устанавливаем FSM-состояние инициатора через storage напрямую
    from aiogram.fsm.storage.base import StorageKey
    bot_id = (await callback.bot.get_me()).id
    init_key = StorageKey(bot_id=bot_id, chat_id=initiator_id, user_id=initiator_id)
    init_fsm = FSMContext(storage=state.storage, key=init_key)
    await init_fsm.set_state(ItemTradeStates.selecting_own_items)
    await init_fsm.update_data(trade_id=trade_id, trade_lang=init_lang, select_page=0, selected_ids=[], qty_map={})

    items_init = db.get_unlocked_inventory(initiator_id)
    init_keyboard = _build_select_keyboard(items_init, [], {}, trade_id, 0, init_lang, allow_empty=True)
    try:
        await callback.bot.send_message(
            initiator_id, init_text, parse_mode="HTML", reply_markup=init_keyboard
        )
    except Exception as e:
        logger.warning(f"Could not notify initiator {initiator_id}: {e}")

    # Показываем партнёру выбор предметов
    items = db.get_unlocked_inventory(user_id)
    lc = "ru" if lang == "RUS" else "en"
    sel_text = locale_manager.get_text(lc, "item_trade.select_items")
    keyboard = _build_select_keyboard(items, [], {}, trade_id, 0, lang, allow_empty=True)
    await callback.message.answer(sel_text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()
    return


@router.callback_query(F.data.startswith("itr_decline_"))
async def item_trade_decline(callback: CallbackQuery, state: FSMContext):
    """Партнёр отклонил приглашение."""
    trade_id = int(callback.data.split("_")[2])
    trade = db.get_item_trade(trade_id)

    if not trade:
        await callback.answer()
        return

    user_id = callback.from_user.id
    if user_id != trade['partner_id']:
        await callback.answer("⛔ Это не ваш обмен", show_alert=True)
        return

    db.cancel_item_trade(trade_id)

    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"
    partner_display = _user_display(user)

    lc = "ru" if lang == "RUS" else "en"
    await callback.message.delete()
    await callback.answer(locale_manager.get_text(lc, "item_trade.you_declined"), show_alert=True)

    # Уведомляем инициатора
    initiator_id = trade['initiator_id']
    initiator = db.get_user(initiator_id)
    init_lang = initiator.get("language", "RUS") if initiator else "RUS"
    init_lc = "ru" if init_lang == "RUS" else "en"
    init_text = locale_manager.get_text(init_lc, "item_trade.partner_declined").format(partner=partner_display)
    try:
        await callback.bot.send_message(initiator_id, init_text, parse_mode="HTML")
    except Exception:
        pass


# ========== ВЫБОР ПРЕДМЕТОВ ==========

@router.callback_query(F.data.startswith("itr_tog_"), ItemTradeStates.selecting_own_items)
async def item_trade_toggle(callback: CallbackQuery, state: FSMContext):
    """Переключить выбор предмета."""
    parts = callback.data.split("_")
    trade_id = int(parts[2])
    item_id = int(parts[3])

    trade = db.get_item_trade(trade_id)
    if not trade or trade['status'] not in ('selecting', 'confirming'):
        await callback.answer("❌ Обмен недоступен", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id not in (trade['initiator_id'], trade['partner_id']):
        await callback.answer("⛔ Не ваш обмен", show_alert=True)
        return

    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    # Получаем текущий выбор из FSM или из БД
    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])
    qty_map: dict = data.get("qty_map", {})
    page: int = data.get("select_page", 0)

    # Проверяем что предмет принадлежит пользователю
    item = db.get_inventory_item(item_id)
    if not item or item['user_id'] != user_id:
        await callback.answer("❌ Предмет не найден" if lang == "RUS" else "❌ Item not found", show_alert=True)
        return

    if item_id in selected_ids:
        selected_ids.remove(item_id)
        qty_map.pop(str(item_id), None)
    else:
        selected_ids.append(item_id)
        qty_map[str(item_id)] = 1

    await state.update_data(selected_ids=selected_ids, qty_map=qty_map)

    items = db.get_unlocked_inventory(user_id)
    keyboard = _build_select_keyboard(items, selected_ids, qty_map, trade_id, page, lang, allow_empty=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("itr_inc_"), ItemTradeStates.selecting_own_items)
async def item_trade_inc(callback: CallbackQuery, state: FSMContext):
    """Увеличить количество выбранного предмета."""
    parts = callback.data.split("_")
    trade_id = int(parts[2])
    item_id = int(parts[3])

    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])
    qty_map: dict = data.get("qty_map", {})
    page: int = data.get("select_page", 0)

    item = db.get_inventory_item(item_id)
    if not item:
        await callback.answer()
        return

    max_qty = item.get("quantity", 1)
    cur = int(qty_map.get(str(item_id), 1))
    if cur < max_qty:
        qty_map[str(item_id)] = cur + 1
        await state.update_data(qty_map=qty_map)

    user = db.get_user(callback.from_user.id)
    lang = user.get("language", "RUS") if user else "RUS"
    items = db.get_unlocked_inventory(callback.from_user.id)
    keyboard = _build_select_keyboard(items, selected_ids, qty_map, trade_id, page, lang, allow_empty=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("itr_dec_"), ItemTradeStates.selecting_own_items)
async def item_trade_dec(callback: CallbackQuery, state: FSMContext):
    """Уменьшить количество выбранного предмета."""
    parts = callback.data.split("_")
    trade_id = int(parts[2])
    item_id = int(parts[3])

    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])
    qty_map: dict = data.get("qty_map", {})
    page: int = data.get("select_page", 0)

    cur = int(qty_map.get(str(item_id), 1))
    if cur > 1:
        qty_map[str(item_id)] = cur - 1
    else:
        selected_ids = [i for i in selected_ids if i != item_id]
        qty_map.pop(str(item_id), None)
    await state.update_data(selected_ids=selected_ids, qty_map=qty_map)

    user = db.get_user(callback.from_user.id)
    lang = user.get("language", "RUS") if user else "RUS"
    items = db.get_unlocked_inventory(callback.from_user.id)
    keyboard = _build_select_keyboard(items, selected_ids, qty_map, trade_id, page, lang, allow_empty=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("itr_page_"), ItemTradeStates.selecting_own_items)
async def item_trade_page(callback: CallbackQuery, state: FSMContext):
    """Листание страниц при выборе предметов."""
    parts = callback.data.split("_")
    trade_id = int(parts[2])
    page = int(parts[3])

    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])
    qty_map: dict = data.get("qty_map", {})
    await state.update_data(select_page=page)

    user = db.get_user(callback.from_user.id)
    lang = user.get("language", "RUS") if user else "RUS"
    items = db.get_unlocked_inventory(callback.from_user.id)
    keyboard = _build_select_keyboard(items, selected_ids, qty_map, trade_id, page, lang, allow_empty=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("itr_done_"))
async def item_trade_done_selecting(callback: CallbackQuery, state: FSMContext):
    """Участник завершил выбор предметов — переходим к сводке."""
    trade_id = int(callback.data.split("_")[2])
    trade = db.get_item_trade(trade_id)

    if not trade or trade['status'] not in ('selecting', 'confirming'):
        await callback.answer("❌ Обмен недоступен", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id not in (trade['initiator_id'], trade['partner_id']):
        await callback.answer("⛔ Не ваш обмен", show_alert=True)
        return

    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    data = await state.get_data()
    selected_ids: List[int] = data.get("selected_ids", [])
    qty_map: dict = data.get("qty_map", {})

    # Снимаем старую блокировку этого участника и ставим новую
    is_init = trade['initiator_id'] == user_id
    old_ids = trade['initiator_items'] if is_init else trade['partner_items']
    db.unlock_items_for_trade(old_ids)

    # Проверяем что все выбранные предметы ещё принадлежат пользователю
    valid_ids = []
    valid_qty = {}
    for iid in selected_ids:
        item = db.get_inventory_item(iid)
        if item and item['user_id'] == user_id:
            valid_ids.append(iid)
            valid_qty[str(iid)] = qty_map.get(str(iid), 1)

    db.lock_items_for_trade(valid_ids, trade_id)
    db.update_item_trade_offer(trade_id, user_id, valid_ids, valid_qty)

    await state.set_state(ItemTradeStates.reviewing)
    await state.update_data(trade_id=trade_id, trade_lang=lang)

    # Убираем сообщение с выбором предметов
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Отправляем два сообщения: сводку + управление
    trade = db.get_item_trade(trade_id)
    bot_id = (await callback.bot.get_me()).id
    review_msg_id, controls_msg_id = await _send_review_messages(
        callback.bot, user_id, trade, lang,
        storage=state.storage, bot_id=bot_id
    )
    await state.update_data(review_msg_id=review_msg_id, controls_msg_id=controls_msg_id)

    # Уведомляем партнёра
    await _notify_partner_selection_done(
        callback.bot, state.storage, bot_id, trade, user_id
    )

    await callback.answer()


# ========== ПОДТВЕРЖДЕНИЕ И ВЫПОЛНЕНИЕ ОБМЕНА ==========

@router.callback_query(F.data.startswith("itr_confirm_"))
async def item_trade_confirm(callback: CallbackQuery, state: FSMContext):
    """Участник нажал «Подтвердить» — переходим к финальному экрану."""
    trade_id = int(callback.data.split("_")[2])
    trade = db.get_item_trade(trade_id)

    if not trade or trade['status'] not in ('selecting', 'confirming'):
        await callback.answer("❌ Обмен недоступен", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id not in (trade['initiator_id'], trade['partner_id']):
        await callback.answer("⛔ Не ваш обмен", show_alert=True)
        return

    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"
    lc = "ru" if lang == "RUS" else "en"

    data = await state.get_data()
    controls_msg_id = data.get("controls_msg_id")

    # Убираем кнопку подтверждения — показываем ожидание
    waiting_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⏳ Ожидаем партнёра…" if lang == "RUS" else "⏳ Waiting for partner…",
            callback_data="itr_noop"
        )],
        [InlineKeyboardButton(
            text="✏️ Изменить предложение" if lang == "RUS" else "✏️ Edit offer",
            callback_data=f"itr_edit_{trade_id}"
        )],
        [InlineKeyboardButton(
            text="❌ Отменить обмен" if lang == "RUS" else "❌ Cancel trade",
            callback_data=f"itr_cancel_{trade_id}"
        )],
    ])
    try:
        await callback.message.edit_reply_markup(reply_markup=waiting_kb)
    except Exception:
        pass

    await callback.answer(locale_manager.get_text(lc, "item_trade.you_confirmed"))

    # Отправляем финальный экран подтверждения этому пользователю
    bot_id = (await callback.bot.get_me()).id
    final_text = await _final_confirm_text(trade, user_id, lang)
    final_kb = _final_confirm_keyboard(trade_id, False, lang)
    try:
        final_msg = await callback.bot.send_message(user_id, final_text, parse_mode="HTML", reply_markup=final_kb)
        await state.update_data(final_msg_id=final_msg.message_id)
    except Exception as e:
        logger.warning(f"Could not send final confirm to {user_id}: {e}")

    # Уведомляем партнёра — обновляем его сводку и тоже отправляем финальный экран
    partner_id = trade['partner_id'] if trade['initiator_id'] == user_id else trade['initiator_id']
    partner = db.get_user(partner_id)
    partner_lang = partner.get("language", "RUS") if partner else "RUS"
    partner_lc = "ru" if partner_lang == "RUS" else "en"

    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.context import FSMContext as FSMCtx
    key = StorageKey(bot_id=bot_id, chat_id=partner_id, user_id=partner_id)
    partner_fsm = FSMCtx(storage=state.storage, key=key)
    partner_data = await partner_fsm.get_data()
    partner_state = await partner_fsm.get_state()

    user_display = _user_display(user)
    notify = locale_manager.get_text(partner_lc, "item_trade.partner_confirmed").format(user=user_display)

    if partner_state in (ItemTradeStates.reviewing, ItemTradeStates.final_confirm):
        # Партнёр уже в режиме просмотра — обновляем его сводку и отправляем финальный экран
        p_review_msg_id = partner_data.get("review_msg_id")
        p_controls_msg_id = partner_data.get("controls_msg_id")
        p_final_msg_id = partner_data.get("final_msg_id")

        # Обновляем сводку партнёра (статус изменился)
        p_review_text = await _review_text(trade, partner_id, partner_lang)
        if p_review_msg_id:
            try:
                await callback.bot.edit_message_text(
                    chat_id=partner_id, message_id=p_review_msg_id,
                    text=p_review_text, parse_mode="HTML"
                )
            except Exception:
                pass

        # Если финального экрана ещё нет — отправляем
        if not p_final_msg_id:
            p_final_text = await _final_confirm_text(trade, partner_id, partner_lang)
            p_final_kb = _final_confirm_keyboard(trade_id, False, partner_lang)
            try:
                pfm = await callback.bot.send_message(
                    partner_id,
                    f"{notify}\n\n{p_final_text}",
                    parse_mode="HTML",
                    reply_markup=p_final_kb
                )
                await partner_fsm.update_data(final_msg_id=pfm.message_id)
                await partner_fsm.set_state(ItemTradeStates.final_confirm)
            except Exception as e:
                logger.warning(f"Could not send final confirm to partner {partner_id}: {e}")
    else:
        # Партнёр ещё выбирает — просто уведомляем
        try:
            await callback.bot.send_message(partner_id, notify, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Could not notify partner {partner_id}: {e}")

    await state.set_state(ItemTradeStates.final_confirm)


@router.callback_query(F.data.startswith("itr_final_"))
async def item_trade_final_confirm(callback: CallbackQuery, state: FSMContext):
    """Финальное подтверждение — оба нажали, выполняем обмен."""
    trade_id = int(callback.data.split("_")[2])
    trade = db.get_item_trade(trade_id)

    if not trade or trade['status'] not in ('selecting', 'confirming'):
        await callback.answer("❌ Обмен недоступен", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id not in (trade['initiator_id'], trade['partner_id']):
        await callback.answer("⛔ Не ваш обмен", show_alert=True)
        return

    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"
    lc = "ru" if lang == "RUS" else "en"

    # Атомарно фиксируем подтверждение
    trade = db.set_item_trade_confirmed(trade_id, user_id)

    # Показываем ожидание на финальном экране
    waiting_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⏳ Ожидаем партнёра…" if lang == "RUS" else "⏳ Waiting for partner…",
            callback_data="itr_noop"
        )],
        [InlineKeyboardButton(
            text="❌ Отменить обмен" if lang == "RUS" else "❌ Cancel trade",
            callback_data=f"itr_cancel_{trade_id}"
        )],
    ])
    try:
        await callback.message.edit_reply_markup(reply_markup=waiting_kb)
    except Exception:
        pass
    await callback.answer(locale_manager.get_text(lc, "item_trade.you_confirmed"))

    # Проверяем оба ли подтвердили финально
    if not (trade['initiator_confirmed'] and trade['partner_confirmed']):
        # Только один подтвердил — уведомляем партнёра
        partner_id = trade['partner_id'] if trade['initiator_id'] == user_id else trade['initiator_id']
        partner = db.get_user(partner_id)
        partner_lang = partner.get("language", "RUS") if partner else "RUS"
        partner_lc = "ru" if partner_lang == "RUS" else "en"
        user_display = _user_display(user)
        notify = locale_manager.get_text(partner_lc, "item_trade.partner_confirmed").format(user=user_display)

        bot_id = (await callback.bot.get_me()).id
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.context import FSMContext as FSMCtx
        key = StorageKey(bot_id=bot_id, chat_id=partner_id, user_id=partner_id)
        partner_fsm = FSMCtx(storage=state.storage, key=key)
        partner_data = await partner_fsm.get_data()
        p_final_msg_id = partner_data.get("final_msg_id")

        if p_final_msg_id:
            # Обновляем финальный экран партнёра — убираем кнопку подтверждения
            p_final_text = await _final_confirm_text(trade, partner_id, partner_lang)
            p_final_kb = _final_confirm_keyboard(trade_id, False, partner_lang)
            try:
                await callback.bot.edit_message_text(
                    chat_id=partner_id, message_id=p_final_msg_id,
                    text=f"{notify}\n\n{p_final_text}",
                    parse_mode="HTML", reply_markup=p_final_kb
                )
            except Exception:
                try:
                    await callback.bot.send_message(partner_id, f"{notify}\n\n{p_final_text}",
                                                    parse_mode="HTML", reply_markup=p_final_kb)
                except Exception:
                    pass
        else:
            try:
                await callback.bot.send_message(partner_id, notify, parse_mode="HTML")
            except Exception:
                pass
        return

    # Оба подтвердили — выполняем обмен
    success = db.execute_item_trade(trade_id)
    if success:
        # Лог
        _init = db.get_user(trade['initiator_id'])
        _part = db.get_user(trade['partner_id'])
        _init_items = ", ".join(
            f"{db.get_inventory_item(iid).get('name', str(iid)) if db.get_inventory_item(iid) else str(iid)}"
            for iid in (trade.get('initiator_items') or [])
        ) or "—"
        _part_items = ", ".join(
            f"{db.get_inventory_item(iid).get('name', str(iid)) if db.get_inventory_item(iid) else str(iid)}"
            for iid in (trade.get('partner_items') or [])
        ) or "—"
        await log_item_trade_complete(
            callback.bot,
            initiator_id=trade['initiator_id'],
            initiator_name=_user_display(_init),
            partner_id=trade['partner_id'],
            partner_name=_user_display(_part),
            initiator_items=_init_items,
            partner_items=_part_items,
        )
        await _notify_trade_success(callback.bot, trade)
        # Обновляем финальный экран у обоих
        try:
            await callback.message.edit_text(
                locale_manager.get_text(lc, "item_trade.trade_success"),
                parse_mode="HTML", reply_markup=None
            )
        except Exception:
            pass
        # Обновляем у партнёра
        partner_id = trade['partner_id'] if trade['initiator_id'] == user_id else trade['initiator_id']
        partner = db.get_user(partner_id)
        partner_lang = partner.get("language", "RUS") if partner else "RUS"
        partner_lc = "ru" if partner_lang == "RUS" else "en"
        bot_id = (await callback.bot.get_me()).id
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.context import FSMContext as FSMCtx
        key = StorageKey(bot_id=bot_id, chat_id=partner_id, user_id=partner_id)
        partner_fsm = FSMCtx(storage=state.storage, key=key)
        partner_data = await partner_fsm.get_data()
        p_final_msg_id = partner_data.get("final_msg_id")
        if p_final_msg_id:
            try:
                await callback.bot.edit_message_text(
                    chat_id=partner_id, message_id=p_final_msg_id,
                    text=locale_manager.get_text(partner_lc, "item_trade.trade_success"),
                    parse_mode="HTML", reply_markup=None
                )
            except Exception:
                pass
        await state.clear()
    else:
        db.cancel_item_trade(trade_id)
        try:
            await callback.message.edit_text(
                locale_manager.get_text(lc, "item_trade.trade_error"),
                parse_mode="HTML", reply_markup=None
            )
        except Exception:
            pass
        await _cancel_trade_notify(callback.bot, trade, user_id)


async def _notify_trade_success(bot, trade: dict):
    """Уведомить обоих участников об успешном обмене."""
    for uid in (trade['initiator_id'], trade['partner_id']):
        user = db.get_user(uid)
        lang = user.get("language", "RUS") if user else "RUS"
        partner_id = trade['partner_id'] if uid == trade['initiator_id'] else trade['initiator_id']
        partner = db.get_user(partner_id)
        partner_display = _user_display(partner)
        lc_uid = "ru" if lang == "RUS" else "en"
        text = locale_manager.get_text(lc_uid, "item_trade.trade_success_notify").format(partner=partner_display)
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
        except Exception:
            pass


# ========== ИЗМЕНЕНИЕ ПРЕДЛОЖЕНИЯ ==========

@router.callback_query(F.data.startswith("itr_edit_"))
async def item_trade_edit(callback: CallbackQuery, state: FSMContext):
    """Участник хочет изменить своё предложение."""
    trade_id = int(callback.data.split("_")[2])
    trade = db.get_item_trade(trade_id)

    if not trade or trade['status'] not in ('selecting', 'confirming'):
        await callback.answer("❌ Обмен недоступен", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id not in (trade['initiator_id'], trade['partner_id']):
        await callback.answer("⛔ Не ваш обмен", show_alert=True)
        return

    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"

    # Снимаем блокировку предметов этого участника
    is_init = trade['initiator_id'] == user_id
    old_ids = trade['initiator_items'] if is_init else trade['partner_items']
    db.unlock_items_for_trade(old_ids)
    db.update_item_trade_offer(trade_id, user_id, [], {})

    data = await state.get_data()
    await state.set_state(ItemTradeStates.selecting_own_items)
    await state.update_data(
        trade_id=trade_id, trade_lang=lang, select_page=0,
        selected_ids=[], qty_map={},
        review_msg_id=None, controls_msg_id=None, final_msg_id=None
    )

    # Удаляем старые сообщения сводки/управления/финального подтверждения
    for key_name in ("review_msg_id", "controls_msg_id", "final_msg_id"):
        mid = data.get(key_name)
        if mid:
            try:
                await callback.bot.delete_message(chat_id=user_id, message_id=mid)
            except Exception:
                pass

    items = db.get_unlocked_inventory(user_id)
    lc = "ru" if lang == "RUS" else "en"
    text = locale_manager.get_text(lc, "item_trade.edit_offer")

    keyboard = _build_select_keyboard(items, [], {}, trade_id, 0, lang, allow_empty=True)
    # Удаляем текущее сообщение (с кнопками) и отправляем новое
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)

    # Уведомляем партнёра что предложение изменяется
    partner_id = trade['partner_id'] if is_init else trade['initiator_id']
    partner = db.get_user(partner_id)
    partner_lang = partner.get("language", "RUS") if partner else "RUS"
    user_display = _user_display(user)
    partner_lc = "ru" if partner_lang == "RUS" else "en"
    notify = locale_manager.get_text(partner_lc, "item_trade.partner_editing").format(user=user_display)

    # Также удаляем финальный экран у партнёра если был
    bot_id = (await callback.bot.get_me()).id
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.context import FSMContext as FSMCtx
    p_key = StorageKey(bot_id=bot_id, chat_id=partner_id, user_id=partner_id)
    partner_fsm = FSMCtx(storage=state.storage, key=p_key)
    partner_data = await partner_fsm.get_data()
    for key_name in ("final_msg_id",):
        mid = partner_data.get(key_name)
        if mid:
            try:
                await callback.bot.delete_message(chat_id=partner_id, message_id=mid)
            except Exception:
                pass
    await partner_fsm.update_data(final_msg_id=None)

    try:
        await callback.bot.send_message(partner_id, notify, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


# ========== ОТМЕНА ОБМЕНА ==========

@router.callback_query(F.data.startswith("itr_cancel_"))
async def item_trade_cancel(callback: CallbackQuery, state: FSMContext):
    """Участник отменяет обмен."""
    trade_id = int(callback.data.split("_")[2])
    trade = db.get_item_trade(trade_id)

    if not trade:
        await callback.answer()
        return

    user_id = callback.from_user.id
    if user_id not in (trade['initiator_id'], trade['partner_id']):
        await callback.answer("⛔ Не ваш обмен", show_alert=True)
        return

    db.cancel_item_trade(trade_id)
    await state.clear()

    user = db.get_user(user_id)
    lang = user.get("language", "RUS") if user else "RUS"
    lc = "ru" if lang == "RUS" else "en"

    # Лог: отмена обмена
    other_id = trade['partner_id'] if trade['initiator_id'] == user_id else trade['initiator_id']
    other = db.get_user(other_id)
    await log_item_trade_cancel(
        callback.bot,
        cancelled_by_id=user_id,
        cancelled_by_name=_user_display(user),
        other_id=other_id,
        other_name=_user_display(other),
    )

    try:
        await callback.message.edit_text(
            locale_manager.get_text(lc, "item_trade.cancelled_message"),
            parse_mode="HTML",
            reply_markup=None
        )
    except Exception:
        pass
    await callback.answer(locale_manager.get_text(lc, "item_trade.cancelled_alert"))

    await _cancel_trade_notify(callback.bot, trade, user_id)


@router.callback_query(F.data == "itr_noop")
async def item_trade_noop(callback: CallbackQuery):
    await callback.answer()
