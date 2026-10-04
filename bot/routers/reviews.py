"""Reviews branch: slot list, card, interactive create/edit wizard, mutations."""

from __future__ import annotations

import html
import logging
from datetime import date, timedelta
from typing import Awaitable, Callable, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.core.utils import TASHKENT_TZ, escape_md, format_datetime, to_tashkent, utc_iso, utc_now
from bot.database.db import Database
from bot.database.models import (
    DEFAULT_LANGUAGE,
    EVENT_TYPE_PEER_REVIEW,
    EVENT_TYPE_SLOT,
    LANG_EN,
    LANG_RU,
    LANG_UZ,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_CANCELED,
    STATUS_OPEN,
    TrackedEvent,
)
from bot.keyboards.menu import normalize_language
from bot.keyboards.reviews import (
    SlotWizardCB,
    build_date_picker_kb,
    build_empty_slots_kb,
    build_hour_picker_kb,
    build_minute_picker_kb,
    build_slot_card_kb,
    build_slots_list_kb,
    compose_slot_datetimes,
    day_offset_from_start,
    is_slot_start_allowed,
    wizard_date_label,
)
from bot.routers.helpers import (
    AuthRequiredError,
    PlatformNetworkError,
    get_user_token,
    require_user,
)
from bot.services.crypto import CryptoService
from bot.services.s21_api import S21ApiClient, S21ApiError, S21NetworkError
from bot.states.reviews import SlotFSM

logger = logging.getLogger(__name__)

ForcePollFn = Callable[[int], Awaitable[None]]

SLOT_TYPES = frozenset({EVENT_TYPE_SLOT, EVENT_TYPE_PEER_REVIEW})

_TEXTS = {
    LANG_EN: {
        "no_slots": "No active slots",
        "your_slots": "Your peer-review slots:",
        "create_title": "➕ *Create slot*",
        "edit_title": "🔄 *Change slot time*",
        "step_date": "Step 1/5 — choose a *date*:",
        "step_date_new": "Step 1/5 — choose a *new date*:",
        "step_start_hour": "Step 2/5 — *start* hour:",
        "step_start_minute": "Step 3/5 — *start* minutes:",
        "step_end_hour": "Step 4/5 — *end* hour:",
        "step_end_minute": "Step 5/5 — *end* minutes:",
        "date_line": "Date: *{date}*",
        "start_partial": "Start: *{hh}:??*",
        "start_full": "Start: *{hh}:{mm}*",
        "end_partial": "End: *{hh}:??*",
        "end_full": "End: *{hh}:{mm}*",
        "current_prefix": "ℹ️ Current slot time: {when}\n\n",
        "card_title": "🔍 *Slot card*",
        "time_line": "Time: {when}",
        "status_open": "Status: ⏳ *OPEN* (waiting for peer)",
        "status_booked": "Status: 🔥 *BOOKED*",
        "peer_none": "Booked: —",
        "peer_line": "Booked: *{peer}* ({fmt})",
        "peer_evaluatee": "Evaluatee: *{peer}* ({fmt})",
        "peer_evaluator": "Evaluator: *{peer}* ({fmt})",
        "peer_unknown": "unknown peer",
        "role_evaluator": "Role: 🔍 *Checking*",
        "role_evaluated": "Role: 📖 *Being checked*",
        "fmt_online": "Online",
        "fmt_offline": "Offline",
        "fmt_line": "Format: {icon} *{fmt}*",
        "goal_line": "\nProject: {goal}",
        "cancelled": "Action cancelled",
        "created_ok": "Slot created successfully!",
        "updated_ok": "Slot time updated!",
        "deleted_ok": "Slot deleted!",
        "need_auth": "Sign in required",
        "need_auth_login": "Sign in required: /login",
        "session_reset": "Session reset, start over",
        "edit_session_reset": "Edit session reset",
        "network": "Network unavailable, try later",
        "platform_reject": "Platform rejected the operation",
        "bad_slot": "Invalid slot",
        "not_found": "Slot not found",
        "edit_open_only": "Only an empty slot can be changed",
        "delete_open_only": "Only an empty slot can be deleted",
        "auth_platform": "Platform auth error",
        "delete_reject": "Platform rejected deletion",
        "booked_only": "Available only for a booked slot",
        "online": "Online",
        "offline": "Offline",
        "session_expired": "Session expired, start over",
        "evaluator_only": "Only the evaluator can manage this slot",
        "time_unavailable": "Time unavailable (15-min rule or in past)",
        "category_evaluator": "I am checking",
        "category_evaluated": "Being checked",
    },
    LANG_RU: {
        "no_slots": "Нет активных слотов",
        "your_slots": "Твои слоты на пир-ревью:",
        "create_title": "➕ *Создание слота*",
        "edit_title": "🔄 *Изменение времени слота*",
        "step_date": "Шаг 1/5 — выбери *дату*:",
        "step_date_new": "Шаг 1/5 — выбери *новую дату*:",
        "step_start_hour": "Шаг 2/5 — час *начала*:",
        "step_start_minute": "Шаг 3/5 — минуты *начала*:",
        "step_end_hour": "Шаг 4/5 — час *конца*:",
        "step_end_minute": "Шаг 5/5 — минуты *конца*:",
        "date_line": "Дата: *{date}*",
        "start_partial": "Начало: *{hh}:??*",
        "start_full": "Начало: *{hh}:{mm}*",
        "end_partial": "Конец: *{hh}:??*",
        "end_full": "Конец: *{hh}:{mm}*",
        "current_prefix": "ℹ️ Сейчас слот стоит на: {when}\n\n",
        "card_title": "🔍 *Карточка слота*",
        "time_line": "Время: {when}",
        "status_open": "Статус: ⏳ *OPEN* (ждём пира)",
        "status_booked": "Статус: 🔥 *BOOKED*",
        "peer_none": "Записан: —",
        "peer_line": "Записан: *{peer}* ({fmt})",
        "peer_evaluatee": "Проверяемый: *{peer}* ({fmt})",
        "peer_evaluator": "Проверяющий: *{peer}* ({fmt})",
        "peer_unknown": "неизвестный пир",
        "role_evaluator": "Роль: 🔍 *Проверяющий*",
        "role_evaluated": "Роль: 📖 *Проверяемый*",
        "fmt_online": "Онлайн",
        "fmt_offline": "Офлайн",
        "fmt_line": "Формат: {icon} *{fmt}*",
        "goal_line": "\nПроект: {goal}",
        "cancelled": "Действие отменено",
        "created_ok": "Слот успешно создан!",
        "updated_ok": "Время слота изменено!",
        "deleted_ok": "Слот удален!",
        "need_auth": "Нужна авторизация",
        "need_auth_login": "Нужна авторизация: /login",
        "session_reset": "Сессия сброшена, начни заново",
        "edit_session_reset": "Сессия редактирования сброшена",
        "network": "Сеть недоступна, попробуй позже",
        "platform_reject": "Платформа отклонила операцию",
        "bad_slot": "Некорректный слот",
        "not_found": "Слот не найден",
        "edit_open_only": "Можно менять только пустой слот",
        "delete_open_only": "Удалить можно только пустой слот",
        "auth_platform": "Ошибка авторизации на платформе",
        "delete_reject": "Платформа отклонила удаление",
        "booked_only": "Доступно только для занятого слота",
        "online": "Онлайн",
        "offline": "Офлайн",
        "session_expired": "Сессия истекла, начни заново",
        "evaluator_only": "Управлять слотом может только проверяющий",
        "time_unavailable": "Время недоступно (правило 15 минут или прошло)",
        "category_evaluator": "Я проверяющий",
        "category_evaluated": "Меня проверяют",
    },
    LANG_UZ: {
        "no_slots": "Faol slotlar yo‘q",
        "your_slots": "Peer-review slotlaringiz:",
        "create_title": "➕ *Slot yaratish*",
        "edit_title": "🔄 *Slot vaqtini o‘zgartirish*",
        "step_date": "1/5-qadam — *sanani* tanlang:",
        "step_date_new": "1/5-qadam — *yangi sanani* tanlang:",
        "step_start_hour": "2/5-qadam — *boshlanish* soati:",
        "step_start_minute": "3/5-qadam — *boshlanish* daqiqalari:",
        "step_end_hour": "4/5-qadam — *tugash* soati:",
        "step_end_minute": "5/5-qadam — *tugash* daqiqalari:",
        "date_line": "Sana: *{date}*",
        "start_partial": "Boshlanish: *{hh}:??*",
        "start_full": "Boshlanish: *{hh}:{mm}*",
        "end_partial": "Tugash: *{hh}:??*",
        "end_full": "Tugash: *{hh}:{mm}*",
        "current_prefix": "ℹ️ Hozirgi slot vaqti: {when}\n\n",
        "card_title": "🔍 *Slot kartasi*",
        "time_line": "Vaqt: {when}",
        "status_open": "Holat: ⏳ *OPEN* (peer kutilmoqda)",
        "status_booked": "Holat: 🔥 *BOOKED*",
        "peer_none": "Yozilgan: —",
        "peer_line": "Yozilgan: *{peer}* ({fmt})",
        "peer_evaluatee": "Tekshiriluvchi: *{peer}* ({fmt})",
        "peer_evaluator": "Tekshiruvchi: *{peer}* ({fmt})",
        "peer_unknown": "noma’lum peer",
        "role_evaluator": "Rol: 🔍 *Tekshiruvchi*",
        "role_evaluated": "Rol: 📖 *Tekshiriluvchi*",
        "fmt_online": "Online",
        "fmt_offline": "Offline",
        "fmt_line": "Format: {icon} *{fmt}*",
        "goal_line": "\nLoyiha: {goal}",
        "cancelled": "Amal bekor qilindi",
        "created_ok": "Slot muvaffaqiyatli yaratildi!",
        "updated_ok": "Slot vaqti o‘zgartirildi!",
        "deleted_ok": "Slot o‘chirildi!",
        "need_auth": "Avtorizatsiya kerak",
        "need_auth_login": "Avtorizatsiya kerak: /login",
        "session_reset": "Sessiya tiklandi, qaytadan boshlang",
        "edit_session_reset": "Tahrirlash sessiyasi tiklandi",
        "network": "Tarmoq mavjud emas, keyinroq urinib ko‘ring",
        "platform_reject": "Platforma amalni rad etdi",
        "bad_slot": "Noto‘g‘ri slot",
        "not_found": "Slot topilmadi",
        "edit_open_only": "Faqat bo‘sh slotni o‘zgartirish mumkin",
        "delete_open_only": "Faqat bo‘sh slotni o‘chirish mumkin",
        "auth_platform": "Platformada avtorizatsiya xatosi",
        "delete_reject": "Platforma o‘chirishni rad etdi",
        "booked_only": "Faqat band slot uchun mavjud",
        "online": "Online",
        "offline": "Offline",
        "session_expired": "Sessiya tugadi, qaytadan boshlang",
        "evaluator_only": "Slotni faqat tekshiruvchi boshqara oladi",
        "time_unavailable": "Vaqt mavjud emas (15 daqiqa qoidasi yoki o‘tgan)",
        "category_evaluator": "Men tekshiruvchiman",
        "category_evaluated": "Meni tekshirishadi",
    },
}


def _tr(language: str | None, key: str, **kwargs: object) -> str:
    text = _TEXTS[normalize_language(language)][key]
    if kwargs:
        return text.format(**kwargs)
    return text


def _edit_prefix(data: dict, language: str | None) -> str:
    """Prefix for update-mode wizard steps with the previous slot time."""
    if (data.get("wizard_mode") or "create") != "edit":
        return ""
    when = data.get("current_label")
    if not when:
        return ""
    return _tr(language, "current_prefix", when=when)


def _create_date_prompt(language: str | None) -> str:
    return (
        f"{_tr(language, 'create_title')}\n\n"
        f"{_tr(language, 'step_date')}"
    )


def _edit_date_prompt(language: str | None, current_label: str) -> str:
    return (
        f"{_tr(language, 'current_prefix', when=current_label)}"
        f"{_tr(language, 'edit_title')}\n\n"
        f"{_tr(language, 'step_date_new')}"
    )


def _platform_slot_id(slot: TrackedEvent) -> str:
    """Prefer eventSlotId from payload; fall back to calendar event id."""
    raw = slot.data.get("event_slot_id") or slot.s21_event_id
    return str(raw)


def _is_review_slot(event: TrackedEvent) -> bool:
    return event.type in SLOT_TYPES and event.status in {STATUS_OPEN, STATUS_BOOKED}


def _slot_card_text(slot: TrackedEvent, language: str | None = None) -> str:
    lang = normalize_language(language)
    when = format_datetime(slot.start_time, slot.end_time, language=lang)
    role = slot.effective_role
    role_line = (
        _tr(lang, "role_evaluated")
        if role == ROLE_EVALUATED
        else _tr(lang, "role_evaluator")
    )
    is_online = bool(slot.data.get("is_online"))
    fmt_icon = "🌐" if is_online else "🏢"
    fmt_name = _tr(lang, "fmt_online") if is_online else _tr(lang, "fmt_offline")
    fmt_line = _tr(lang, "fmt_line", icon=fmt_icon, fmt=fmt_name)

    if slot.status == STATUS_OPEN:
        status_line = _tr(lang, "status_open")
        peer_line = _tr(lang, "peer_none")
    else:
        peer = escape_md(
            str(slot.data.get("peer_login") or _tr(lang, "peer_unknown"))
        )
        status_line = _tr(lang, "status_booked")
        peer_key = (
            "peer_evaluator" if role == ROLE_EVALUATED else "peer_evaluatee"
        )
        peer_line = _tr(lang, peer_key, peer=peer, fmt=fmt_name)

    goal = slot.data.get("goal_name")
    goal_line = (
        _tr(lang, "goal_line", goal=escape_md(str(goal))) if goal else ""
    )
    format_line = f"{fmt_line}\n" if slot.status == STATUS_BOOKED else ""

    return (
        f"{_tr(lang, 'card_title')}\n\n"
        f"{role_line}\n"
        f"{_tr(lang, 'time_line', when=when)}\n"
        f"{format_line}"
        f"{status_line}\n"
        f"{peer_line}"
        f"{goal_line}"
    )


async def _load_slots(db: Database, user_id: int) -> list[TrackedEvent]:
    events = await db.get_events(user_id, active_only=True)
    slots = [e for e in events if _is_review_slot(e)]
    slots.sort(key=lambda e: e.start_time)
    return slots


async def show_reviews_list(
    message: Message,
    db: Database,
    user_id: int,
    *,
    language: str | None = None,
    category_filter: Optional[str] = None,
    edit: bool = True,
) -> None:
    lang = normalize_language(language)
    slots = await _load_slots(db, user_id)
    if not slots:
        text = _tr(lang, "no_slots")
        markup = build_empty_slots_kb(lang)
    else:
        evaluator_count = sum(1 for s in slots if s.effective_role == ROLE_EVALUATOR)
        evaluated_count = sum(1 for s in slots if s.effective_role == ROLE_EVALUATED)
        text = (
            f"{_tr(lang, 'your_slots')}\n\n"
            f"🔍 *{_tr(lang, 'category_evaluator')}:* {evaluator_count}\n"
            f"📖 *{_tr(lang, 'category_evaluated')}:* {evaluated_count}"
        )
        markup = build_slots_list_kb(slots, lang, category_filter=category_filter)

    if edit:
        await message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await message.answer(text, reply_markup=markup, parse_mode="Markdown")


def get_reviews_router(
    db: Database,
    crypto: CryptoService,
    api: S21ApiClient,
    *,
    force_poll: Optional[ForcePollFn] = None,
) -> Router:
    router = Router(name="reviews")

    async def _user_lang(user_id: int, data: Optional[dict] = None) -> str:
        if data and data.get("language"):
            return normalize_language(str(data["language"]))
        try:
            user = await require_user(db, user_id)
            return normalize_language(user.language)
        except AuthRequiredError:
            return DEFAULT_LANGUAGE

    async def _maybe_force_poll(chat_id: int) -> None:
        if force_poll is None:
            return
        try:
            await force_poll(chat_id)
        except Exception:
            logger.exception("force_poll failed for chat_id=%s", chat_id)

    async def _start_wizard(
        callback: CallbackQuery,
        state: FSMContext,
        *,
        mode: str,
        prompt: str,
        language: str,
        edit_slot_id: Optional[int] = None,
        edit_s21_id: Optional[str] = None,
        highlight_offset: Optional[int] = None,
        current_label: Optional[str] = None,
        highlight_start_hour: Optional[int] = None,
        highlight_start_minute: Optional[int] = None,
        highlight_end_hour: Optional[int] = None,
        highlight_end_minute: Optional[int] = None,
    ) -> None:
        await state.set_state(SlotFSM.picking_date)
        await state.update_data(
            wizard_mode=mode,
            language=language,
            edit_slot_id=edit_slot_id,
            edit_s21_id=edit_s21_id,
            highlight_offset=highlight_offset,
            current_label=current_label,
            highlight_start_hour=highlight_start_hour,
            highlight_start_minute=highlight_start_minute,
            highlight_end_hour=highlight_end_hour,
            highlight_end_minute=highlight_end_minute,
            day_offset=None,
            selected_date=None,
            start_hour=None,
            start_minute=None,
            end_hour=None,
            end_minute=None,
        )
        if callback.message:
            await callback.message.edit_text(
                prompt,
                reply_markup=build_date_picker_kb(
                    language=language,
                    highlight_offset=highlight_offset,
                ),
                parse_mode="Markdown",
            )

    async def _cancel_wizard(
        callback: CallbackQuery,
        state: FSMContext,
    ) -> None:
        data = await state.get_data()
        lang = await _user_lang(callback.from_user.id, data)
        await state.clear()
        if not callback.message:
            await callback.answer()
            return
        await callback.message.edit_text(_tr(lang, "cancelled"))
        try:
            user = await require_user(db, callback.from_user.id)
            await show_reviews_list(
                callback.message,
                db,
                callback.from_user.id,
                language=user.language,
                edit=False,
            )
        except AuthRequiredError:
            pass
        await callback.answer()

    async def _render_date_step(
        callback: CallbackQuery,
        state: FSMContext,
        data: dict,
    ) -> None:
        lang = normalize_language(data.get("language"))
        mode = data.get("wizard_mode") or "create"
        highlight = data.get("highlight_offset")
        highlight_int = int(highlight) if highlight is not None else None
        current_label = data.get("current_label") or ""
        if mode == "edit" and current_label:
            prompt = _edit_date_prompt(lang, str(current_label))
        else:
            prompt = _create_date_prompt(lang)
        await state.set_state(SlotFSM.picking_date)
        if callback.message:
            await callback.message.edit_text(
                prompt,
                reply_markup=build_date_picker_kb(
                    language=lang,
                    highlight_offset=highlight_int,
                ),
                parse_mode="Markdown",
            )

    async def _render_start_hour(
        callback: CallbackQuery,
        state: FSMContext,
        data: dict,
    ) -> None:
        lang = normalize_language(data.get("language"))
        mode = data.get("wizard_mode") or "create"
        title_key = "create_title" if mode == "create" else "edit_title"
        day_offset = int(data.get("day_offset") or 0)
        date_label = wizard_date_label(day_offset, lang)
        highlight = data.get("highlight_start_hour")
        await state.set_state(SlotFSM.picking_start_hour)
        if callback.message:
            await callback.message.edit_text(
                f"{_edit_prefix(data, lang)}"
                f"{_tr(lang, title_key)}\n\n"
                f"{_tr(lang, 'date_line', date=date_label)}\n"
                f"{_tr(lang, 'step_start_hour')}",
                reply_markup=build_hour_picker_kb(
                    which="sh",
                    language=lang,
                    day_offset=day_offset,
                    highlight_hour=int(highlight) if highlight is not None else None,
                ),
                parse_mode="Markdown",
            )

    async def _render_start_minute(
        callback: CallbackQuery,
        state: FSMContext,
        data: dict,
    ) -> None:
        lang = normalize_language(data.get("language"))
        mode = data.get("wizard_mode") or "create"
        title_key = "create_title" if mode == "create" else "edit_title"
        day_offset = int(data.get("day_offset") or 0)
        date_label = wizard_date_label(day_offset, lang)
        start_h = int(data.get("start_hour") or 0)
        highlight = data.get("highlight_start_minute")
        await state.set_state(SlotFSM.picking_start_minute)
        if callback.message:
            await callback.message.edit_text(
                f"{_edit_prefix(data, lang)}"
                f"{_tr(lang, title_key)}\n\n"
                f"{_tr(lang, 'date_line', date=date_label)}\n"
                f"{_tr(lang, 'start_partial', hh=f'{start_h:02d}')}\n"
                f"{_tr(lang, 'step_start_minute')}",
                reply_markup=build_minute_picker_kb(
                    which="sm",
                    language=lang,
                    day_offset=day_offset,
                    hour=start_h,
                    highlight_minute=(
                        int(highlight) if highlight is not None else None
                    ),
                ),
                parse_mode="Markdown",
            )

    async def _render_end_hour(
        callback: CallbackQuery,
        state: FSMContext,
        data: dict,
    ) -> None:
        lang = normalize_language(data.get("language"))
        mode = data.get("wizard_mode") or "create"
        title_key = "create_title" if mode == "create" else "edit_title"
        day_offset = int(data.get("day_offset") or 0)
        date_label = wizard_date_label(day_offset, lang)
        start_h = int(data.get("start_hour") or 0)
        start_m = int(data.get("start_minute") or 0)
        highlight = data.get("highlight_end_hour")
        await state.set_state(SlotFSM.picking_end_hour)
        if callback.message:
            await callback.message.edit_text(
                f"{_edit_prefix(data, lang)}"
                f"{_tr(lang, title_key)}\n\n"
                f"{_tr(lang, 'date_line', date=date_label)}\n"
                f"{_tr(lang, 'start_full', hh=f'{start_h:02d}', mm=f'{start_m:02d}')}\n"
                f"{_tr(lang, 'step_end_hour')}",
                reply_markup=build_hour_picker_kb(
                    which="eh",
                    language=lang,
                    day_offset=day_offset,
                    highlight_hour=int(highlight) if highlight is not None else None,
                    start_hour=start_h,
                    start_minute=start_m,
                ),
                parse_mode="Markdown",
            )

    async def _render_end_minute(
        callback: CallbackQuery,
        state: FSMContext,
        data: dict,
    ) -> None:
        lang = normalize_language(data.get("language"))
        mode = data.get("wizard_mode") or "create"
        title_key = "create_title" if mode == "create" else "edit_title"
        day_offset = int(data.get("day_offset") or 0)
        date_label = wizard_date_label(day_offset, lang)
        start_h = int(data.get("start_hour") or 0)
        start_m = int(data.get("start_minute") or 0)
        end_h = int(data.get("end_hour") or 0)
        highlight = data.get("highlight_end_minute")
        await state.set_state(SlotFSM.picking_end_minute)
        if callback.message:
            await callback.message.edit_text(
                f"{_edit_prefix(data, lang)}"
                f"{_tr(lang, title_key)}\n\n"
                f"{_tr(lang, 'date_line', date=date_label)}\n"
                f"{_tr(lang, 'start_full', hh=f'{start_h:02d}', mm=f'{start_m:02d}')}\n"
                f"{_tr(lang, 'end_partial', hh=f'{end_h:02d}')}\n"
                f"{_tr(lang, 'step_end_minute')}",
                reply_markup=build_minute_picker_kb(
                    which="em",
                    language=lang,
                    day_offset=day_offset,
                    hour=end_h,
                    highlight_minute=(
                        int(highlight) if highlight is not None else None
                    ),
                    start_hour=start_h,
                    start_minute=start_m,
                ),
                parse_mode="Markdown",
            )

    async def _wizard_back(
        callback: CallbackQuery,
        state: FSMContext,
    ) -> None:
        """Step back to the previous wizard state and redraw its keyboard."""
        current = await state.get_state()
        data = await state.get_data()

        # Date step has no Back button; if somehow triggered — stay / ignore.
        if current == SlotFSM.picking_date.state:
            await callback.answer()
            return

        if current == SlotFSM.picking_start_hour.state:
            await _render_date_step(callback, state, data)
            await callback.answer()
            return

        if current == SlotFSM.picking_start_minute.state:
            await _render_start_hour(callback, state, data)
            await callback.answer()
            return

        if current == SlotFSM.picking_end_hour.state:
            await _render_start_minute(callback, state, data)
            await callback.answer()
            return

        if current == SlotFSM.picking_end_minute.state:
            await _render_end_hour(callback, state, data)
            await callback.answer()
            return

        await callback.answer()

    async def _finish_wizard(
        callback: CallbackQuery,
        state: FSMContext,
        data: dict,
    ) -> None:
        """Validate selections, call create/update mutation, refresh list."""
        lang = normalize_language(data.get("language"))
        try:
            day_offset = int(data["day_offset"])
            start_hour = int(data["start_hour"])
            start_minute = int(data["start_minute"])
            end_hour = int(data["end_hour"])
            end_minute = int(data["end_minute"])
        except (KeyError, TypeError, ValueError):
            await state.clear()
            await callback.answer(_tr(lang, "session_reset"), show_alert=True)
            return

        try:
            start_dt, end_dt = compose_slot_datetimes(
                day_offset=day_offset,
                start_hour=start_hour,
                start_minute=start_minute,
                end_hour=end_hour,
                end_minute=end_minute,
                selected_date=date.fromisoformat(str(data["selected_date"])),
            )
        except (KeyError, ValueError):
            await callback.answer(
                _tr(lang, "time_unavailable"),
                show_alert=True,
            )
            return
        if not is_slot_start_allowed(start_dt):
            await callback.answer(
                _tr(lang, "time_unavailable"),
                show_alert=True,
            )
            return
        mode = data.get("wizard_mode") or "create"

        try:
            user = await require_user(db, callback.from_user.id)
            lang = normalize_language(user.language)
            token = await get_user_token(user, crypto=crypto, api=api)
            if mode == "edit":
                s21_id = data.get("edit_s21_id")
                slot_id = data.get("edit_slot_id")
                if not s21_id or slot_id is None:
                    await state.clear()
                    await callback.answer(
                        _tr(lang, "edit_session_reset"),
                        show_alert=True,
                    )
                    return
                existing_ev = await db.get_event_by_id(int(slot_id))
                if (
                    existing_ev is None
                    or existing_ev.user_id != callback.from_user.id
                    or existing_ev.status != STATUS_OPEN
                    or existing_ev.effective_role != ROLE_EVALUATOR
                ):
                    await callback.answer(_tr(lang, "edit_open_only"), show_alert=True)
                    return
                await api.update_slot(
                    token,
                    str(s21_id),
                    utc_iso(start_dt),
                    utc_iso(end_dt),
                )
                await db.update_event_status(
                    int(slot_id),
                    STATUS_OPEN,
                    start_time=utc_iso(start_dt),
                    end_time=utc_iso(end_dt),
                )
                ok_text = _tr(lang, "updated_ok")
            else:
                await api.create_slot(
                    token,
                    utc_iso(start_dt),
                    utc_iso(end_dt),
                )
                ok_text = _tr(lang, "created_ok")
        except AuthRequiredError:
            await state.clear()
            await callback.answer(_tr(lang, "need_auth_login"), show_alert=True)
            return
        except (PlatformNetworkError, S21NetworkError):
            await callback.answer(_tr(lang, "network"), show_alert=True)
            return
        except S21ApiError as exc:
            logger.warning("slot wizard mutation failed: %s", exc)
            await callback.answer(_tr(lang, "platform_reject"), show_alert=True)
            return

        await state.clear()
        if callback.message:
            await callback.message.edit_text(ok_text)
            await _maybe_force_poll(callback.from_user.id)
            await show_reviews_list(
                callback.message,
                db,
                callback.from_user.id,
                language=lang,
                edit=False,
            )
        await callback.answer(ok_text)

    # ------------------------------------------------------------------ menu

    @router.callback_query(F.data == "menu_reviews")
    async def cb_menu_reviews(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if not callback.message:
            await callback.answer()
            return
        try:
            user = await require_user(db, callback.from_user.id)
        except AuthRequiredError:
            await callback.answer(_tr(DEFAULT_LANGUAGE, "need_auth"), show_alert=True)
            return

        await show_reviews_list(
            callback.message,
            db,
            callback.from_user.id,
            language=user.language,
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("slot_cat:"))
    async def cb_slot_cat(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if not callback.message or not callback.data:
            await callback.answer()
            return
        cat = callback.data.split(":", 1)[1]
        category_filter = cat if cat in {"evaluator", "evaluated", "open"} else None
        try:
            user = await require_user(db, callback.from_user.id)
        except AuthRequiredError:
            await callback.answer(_tr(DEFAULT_LANGUAGE, "need_auth"), show_alert=True)
            return

        await show_reviews_list(
            callback.message,
            db,
            callback.from_user.id,
            language=user.language,
            category_filter=category_filter,
            edit=True,
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("slot_view:"))
    async def cb_slot_view(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if not callback.message or not callback.data:
            await callback.answer()
            return
        try:
            user = await require_user(db, callback.from_user.id)
        except AuthRequiredError:
            await callback.answer(_tr(DEFAULT_LANGUAGE, "need_auth"), show_alert=True)
            return

        lang = normalize_language(user.language)
        try:
            slot_id = int(callback.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer(_tr(lang, "bad_slot"), show_alert=True)
            return

        slot = await db.get_event_by_id(slot_id)
        if (
            slot is None
            or slot.user_id != callback.from_user.id
            or not _is_review_slot(slot)
        ):
            await callback.answer(_tr(lang, "not_found"), show_alert=True)
            await show_reviews_list(
                callback.message,
                db,
                callback.from_user.id,
                language=lang,
            )
            return

        await callback.message.edit_text(
            _slot_card_text(slot, lang),
            reply_markup=build_slot_card_kb(slot, lang),
            parse_mode="Markdown",
        )
        await callback.answer()

    @router.callback_query(F.data == "slot_create")
    async def cb_slot_create(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            await callback.answer()
            return
        try:
            user = await require_user(db, callback.from_user.id)
        except AuthRequiredError:
            await callback.answer(_tr(DEFAULT_LANGUAGE, "need_auth"), show_alert=True)
            return

        lang = normalize_language(user.language)
        await _start_wizard(
            callback,
            state,
            mode="create",
            language=lang,
            prompt=_create_date_prompt(lang),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("slot_edit:"))
    async def cb_slot_edit(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not callback.data:
            await callback.answer()
            return
        try:
            user = await require_user(db, callback.from_user.id)
        except AuthRequiredError:
            await callback.answer(_tr(DEFAULT_LANGUAGE, "need_auth"), show_alert=True)
            return

        lang = normalize_language(user.language)
        try:
            slot_id = int(callback.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer(_tr(lang, "bad_slot"), show_alert=True)
            return

        slot = await db.get_event_by_id(slot_id)
        if (
            slot is None
            or slot.user_id != callback.from_user.id
            or slot.status != STATUS_OPEN
        ):
            await callback.answer(_tr(lang, "edit_open_only"), show_alert=True)
            return
        if slot.effective_role == ROLE_EVALUATED:
            await callback.answer(_tr(lang, "evaluator_only"), show_alert=True)
            return

        offset = day_offset_from_start(slot.start_time)
        current_label = format_datetime(
            slot.start_time, slot.end_time, language=lang
        )
        local_start = to_tashkent(slot.start_time)
        local_end = to_tashkent(slot.end_time) if slot.end_time else None

        await _start_wizard(
            callback,
            state,
            mode="edit",
            language=lang,
            prompt=_edit_date_prompt(lang, current_label),
            edit_slot_id=slot_id,
            edit_s21_id=_platform_slot_id(slot),
            highlight_offset=offset,
            current_label=current_label,
            highlight_start_hour=local_start.hour,
            highlight_start_minute=local_start.minute,
            highlight_end_hour=local_end.hour if local_end else None,
            highlight_end_minute=local_end.minute if local_end else None,
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("slot_delete:"))
    async def cb_slot_delete(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if not callback.message or not callback.data:
            await callback.answer()
            return
        try:
            user = await require_user(db, callback.from_user.id)
        except AuthRequiredError:
            await callback.answer(_tr(DEFAULT_LANGUAGE, "need_auth"), show_alert=True)
            return

        lang = normalize_language(user.language)
        try:
            slot_id = int(callback.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer(_tr(lang, "bad_slot"), show_alert=True)
            return

        slot = await db.get_event_by_id(slot_id)
        if (
            slot is None
            or slot.user_id != callback.from_user.id
            or slot.status != STATUS_OPEN
        ):
            await callback.answer(_tr(lang, "delete_open_only"), show_alert=True)
            return
        if slot.effective_role == ROLE_EVALUATED:
            await callback.answer(_tr(lang, "evaluator_only"), show_alert=True)
            return

        try:
            token = await get_user_token(user, crypto=crypto, api=api)
            await api.delete_slot(token, _platform_slot_id(slot))
        except AuthRequiredError:
            await callback.answer(_tr(lang, "auth_platform"), show_alert=True)
            return
        except (PlatformNetworkError, S21NetworkError):
            await callback.answer(_tr(lang, "network"), show_alert=True)
            return
        except S21ApiError as exc:
            logger.warning("delete_slot failed: %s", exc)
            await callback.answer(_tr(lang, "delete_reject"), show_alert=True)
            return

        if slot.id is not None:
            await db.update_event_status(slot.id, STATUS_CANCELED)

        ok_text = _tr(lang, "deleted_ok")
        await callback.message.answer(ok_text)
        await callback.answer(ok_text)
        await _maybe_force_poll(callback.from_user.id)
        await show_reviews_list(
            callback.message,
            db,
            callback.from_user.id,
            language=lang,
        )

    @router.callback_query(F.data.startswith("slot_toggle_online:"))
    async def cb_slot_toggle_online(
        callback: CallbackQuery,
        state: FSMContext,
    ) -> None:
        await state.clear()
        if not callback.message or not callback.data:
            await callback.answer()
            return
        try:
            user = await require_user(db, callback.from_user.id)
        except AuthRequiredError:
            await callback.answer(_tr(DEFAULT_LANGUAGE, "need_auth"), show_alert=True)
            return

        lang = normalize_language(user.language)
        try:
            slot_id = int(callback.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer(_tr(lang, "bad_slot"), show_alert=True)
            return

        slot = await db.get_event_by_id(slot_id)
        if (
            slot is None
            or slot.user_id != callback.from_user.id
            or not _is_review_slot(slot)
        ):
            await callback.answer(_tr(lang, "not_found"), show_alert=True)
            return

        if slot.status != STATUS_BOOKED:
            await callback.answer(_tr(lang, "booked_only"), show_alert=True)
            return
        await callback.answer(
            "Смена формата пока доступна только через платформу Школы 21",
            show_alert=True,
        )

    # ---------------------------------------------------------- time wizard

    @router.callback_query(SlotWizardCB.filter())
    async def cb_slot_wizard(
        callback: CallbackQuery,
        callback_data: SlotWizardCB,
        state: FSMContext,
    ) -> None:
        if not callback.message:
            await callback.answer()
            return

        data = await state.get_data()
        lang = normalize_language(data.get("language"))
        current = await state.get_state()
        expected_states = {
            "date": SlotFSM.picking_date.state,
            "sh": SlotFSM.picking_start_hour.state,
            "sm": SlotFSM.picking_start_minute.state,
            "eh": SlotFSM.picking_end_hour.state,
            "em": SlotFSM.picking_end_minute.state,
        }
        if (
            callback_data.act in expected_states
            and current != expected_states[callback_data.act]
        ):
            await callback.answer(_tr(lang, "session_expired"), show_alert=True)
            return

        if callback_data.act == "disabled":
            await callback.answer(_tr(lang, "time_unavailable"), show_alert=False)
            return

        if callback_data.act == "cancel":
            await _cancel_wizard(callback, state)
            return

        if callback_data.act == "back":
            if current is None:
                await callback.answer(_tr(lang, "session_expired"), show_alert=True)
                return
            await _wizard_back(callback, state)
            return

        if callback_data.act in {"sh", "eh"} and callback_data.val not in range(24):
            await callback.answer(_tr(lang, "time_unavailable"), show_alert=True)
            return
        if callback_data.act in {"sm", "em"} and callback_data.val not in {0, 15, 30, 45}:
            await callback.answer(_tr(lang, "time_unavailable"), show_alert=True)
            return

        if callback_data.act == "date":
            if callback_data.val not in range(7):
                await callback.answer(_tr(lang, "time_unavailable"), show_alert=True)
                return
            selected = utc_now().astimezone(TASHKENT_TZ).date() + timedelta(days=callback_data.val)
            await state.update_data(day_offset=callback_data.val, selected_date=selected.isoformat())
            fresh = await state.get_data()
            await _render_start_hour(callback, state, fresh)
            await callback.answer()
            return

        if callback_data.act == "sh":
            await state.update_data(start_hour=callback_data.val)
            fresh = await state.get_data()
            await _render_start_minute(callback, state, fresh)
            await callback.answer()
            return

        if callback_data.act == "sm":
            await state.update_data(start_minute=callback_data.val)
            fresh = await state.get_data()
            await _render_end_hour(callback, state, fresh)
            await callback.answer()
            return

        if callback_data.act == "eh":
            await state.update_data(end_hour=callback_data.val)
            fresh = await state.get_data()
            await _render_end_minute(callback, state, fresh)
            await callback.answer()
            return

        if callback_data.act == "em":
            await state.update_data(end_minute=callback_data.val)
            fresh = await state.get_data()
            await _finish_wizard(callback, state, fresh)
            return

        await callback.answer()

    return router
