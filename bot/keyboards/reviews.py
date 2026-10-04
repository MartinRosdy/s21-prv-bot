"""Inline keyboards for the Reviews (slots) branch."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.utils import TASHKENT_TZ, format_datetime, to_tashkent, utc_now
from bot.database.models import (
    LANG_EN,
    LANG_RU,
    LANG_UZ,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_OPEN,
    TrackedEvent,
)
from bot.keyboards.menu import normalize_language

# Minimum advance notice for booking or creating slots on the current day
MIN_BOOKING_LEAD_MINUTES: int = 15


class SlotWizardCB(CallbackData, prefix="sw"):
    """
    Compact wizard callbacks.

    ``act``: date | sh | sm | eh | em | fmt | disabled | back | cancel
    ``val``: day offset (0..6), hour (8..23), minute (0/15/30/45), or format (0=offline, 1=online)
    """

    act: str
    val: int = 0


_WEEKDAYS = {
    LANG_EN: ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    LANG_RU: ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"),
    LANG_UZ: ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"),
}

_MONTHS_SHORT = {
    LANG_EN: (
        "", "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec",
    ),
    LANG_RU: (
        "", "янв", "фев", "мар", "апр", "мая", "июн",
        "июл", "авг", "сен", "окт", "ноя", "дек",
    ),
    LANG_UZ: (
        "", "yan", "fev", "mar", "apr", "may", "iyn",
        "iyl", "avg", "sen", "okt", "noy", "dek",
    ),
}

_LABELS = {
    LANG_EN: {
        "create_slot": "➕ Create slot",
        "edit_time": "🔄 Change time",
        "delete": "❌ Delete",
        "back": "⬅️ Back",
        "back_menu": "🔙 Back to menu",
        "back_step": "⬅️ Back",
        "cancel": "❌ Cancel",
        "today": "📅 Today",
        "tomorrow": "📅 Tomorrow",
        "make_offline": "🏢 Switch to Offline",
        "make_online": "🌐 Switch to Online",
        "format_online": "🌐 Online",
        "format_offline": "🏢 Offline",
        "category_evaluator": "🔍 I am checking",
        "category_evaluated": "📖 Being checked",
        "time_unavailable": "Time unavailable",
        "status_free": "⏳ Free",
        "free_slots": "📋 Free slots",
    },
    LANG_RU: {
        "create_slot": "➕ Создать слот",
        "edit_time": "🔄 Изменить время",
        "delete": "❌ Удалить",
        "back": "⬅️ Назад",
        "back_menu": "🔙 Назад в меню",
        "back_step": "⬅️ Назад",
        "cancel": "❌ Отмена",
        "today": "📅 Сегодня",
        "tomorrow": "📅 Завтра",
        "make_offline": "🏢 Переключить на Офлайн",
        "make_online": "🌐 Переключить на Онлайн",
        "format_online": "🌐 Онлайн",
        "format_offline": "🏢 Офлайн",
        "category_evaluator": "🔍 Я проверяющий",
        "category_evaluated": "📖 Меня проверяют",
        "time_unavailable": "Время недоступно",
        "status_free": "⏳ Свободен",
        "free_slots": "📋 Свободные слоты",
    },
    LANG_UZ: {
        "create_slot": "➕ Slot yaratish",
        "edit_time": "🔄 Vaqtni o‘zgartirish",
        "delete": "❌ O‘chirish",
        "back": "⬅️ Orqaga",
        "back_menu": "🔙 Menyuga qaytish",
        "back_step": "⬅️ Orqaga",
        "cancel": "❌ Bekor qilish",
        "today": "📅 Bugun",
        "tomorrow": "📅 Ertaga",
        "make_offline": "🏢 Offlinega o‘tkazish",
        "make_online": "🌐 Onlinega o‘tkazish",
        "format_online": "🌐 Online",
        "format_offline": "🏢 Offline",
        "category_evaluator": "🔍 Men tekshiruvchiman",
        "category_evaluated": "📖 Meni tekshirishadi",
        "time_unavailable": "Vaqt mavjud emas",
        "status_free": "⏳ Bo‘sh",
        "free_slots": "📋 Bo‘sh slotlar",
    },
}


def _t(language: str | None, key: str) -> str:
    return _LABELS[normalize_language(language)][key]


def build_empty_slots_kb(
    language: str | None = None,
) -> InlineKeyboardMarkup:
    """Shown when the user has no active review slots."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=_t(language, "create_slot"),
            callback_data="slot_create",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_t(language, "back_menu"),
            callback_data="menu_home",
        )
    )
    return builder.as_markup()


def build_slots_list_kb(
    slots: list[TrackedEvent],
    language: str | None = None,
    category_filter: Optional[str] = None,
) -> InlineKeyboardMarkup:
    """
    Categorized slots list:
    1. 🔍 Evaluator (I check)
    2. 📖 Evaluated (Being checked)

    No textual '(Проверяю)' suffixes. Clear navigation and filters.
    """
    builder = InlineKeyboardBuilder()
    lang = normalize_language(language)

    evaluator_slots = [s for s in slots if s.effective_role == ROLE_EVALUATOR]
    evaluated_slots = [s for s in slots if s.effective_role == ROLE_EVALUATED]
    free_slots = [s for s in evaluator_slots if s.status == STATUS_OPEN]

    # Category switcher buttons at the top
    evaluator_count = len(evaluator_slots)
    evaluated_count = len(evaluated_slots)

    builder.row(
        InlineKeyboardButton(
            text=f"{_t(lang, 'category_evaluator')} ({evaluator_count})",
            callback_data="slot_cat:evaluator",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{_t(lang, 'category_evaluated')} ({evaluated_count})",
            callback_data="slot_cat:evaluated",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{_t(lang, 'free_slots')} ({len(free_slots)})",
            callback_data="slot_cat:open",
        ),
    )

    # Determine which slots to show based on filter
    slots_to_render = slots
    if category_filter == "evaluator":
        slots_to_render = evaluator_slots
    elif category_filter == "evaluated":
        slots_to_render = evaluated_slots
    elif category_filter == "open":
        slots_to_render = free_slots

    for slot in slots_to_render:
        if slot.id is None:
            continue
        when = format_datetime(slot.start_time, slot.end_time, language=lang)
        is_online = bool(slot.data.get("is_online"))
        fmt_str = "🌐" if is_online else "🏢"

        if slot.effective_role == ROLE_EVALUATED:
            peer = slot.data.get("peer_login") or "..."
            label = f"📖 {when} · {peer} {fmt_str}"
        else:
            if slot.status == STATUS_OPEN:
                label = f"🔍 {when} · ⏳ {fmt_str}"
            else:
                peer = slot.data.get("peer_login") or "..."
                label = f"🔍 {when} · {peer} {fmt_str}"

        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=f"slot_view:{slot.id}",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=_t(language, "create_slot"),
            callback_data="slot_create",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_t(language, "back_menu"),
            callback_data="menu_home",
        )
    )
    return builder.as_markup()


def build_slot_card_kb(
    slot: TrackedEvent,
    language: str | None = None,
) -> InlineKeyboardMarkup:
    """
    Slot card detail actions with format toggle and clear navigation.
    """
    builder = InlineKeyboardBuilder()
    if slot.id is None:
        builder.row(
            InlineKeyboardButton(
                text=_t(language, "back"),
                callback_data="menu_reviews",
            )
        )
        return builder.as_markup()

    is_online = bool(slot.data.get("is_online"))
    toggle_key = "make_offline" if is_online else "make_online"

    # The existing platform mutation changes open evaluator slots. Do not
    # pretend that a booked/evaluated review was changed only in local state.
    if slot.effective_role == ROLE_EVALUATOR:
        if slot.status == STATUS_OPEN:
            builder.row(
                InlineKeyboardButton(
                    text=_t(language, "delete"),
                    callback_data=f"slot_delete:{slot.id}",
                ),
                InlineKeyboardButton(
                    text=_t(language, "edit_time"),
                    callback_data=f"slot_edit:{slot.id}",
                ),
            )
        builder.row(
            InlineKeyboardButton(
                text=_t(language, toggle_key),
                callback_data=f"slot_toggle_online:{slot.id}",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=_t(language, "back"),
            callback_data="menu_reviews",
        ),
        InlineKeyboardButton(
            text=_t(language, "back_menu"),
            callback_data="menu_home",
        ),
    )
    return builder.as_markup()


def _append_nav(
    builder: InlineKeyboardBuilder,
    *,
    language: str | None = None,
    with_back: bool = True,
) -> None:
    """Back (optional) + Cancel on every wizard step."""
    if with_back:
        builder.row(
            InlineKeyboardButton(
                text=_t(language, "back_step"),
                callback_data=SlotWizardCB(act="back").pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=_t(language, "cancel"),
            callback_data=SlotWizardCB(act="cancel").pack(),
        )
    )


def _mark_current(label: str, *, is_current: bool) -> str:
    """Prefix current value with ✅ (no textual '(current)' suffix)."""
    if is_current:
        return f"✅ {label}"
    return label


def build_date_picker_kb(
    *,
    language: str | None = None,
    highlight_offset: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """Step 1: Today, Tomorrow, and 5 more days ahead."""
    lang = normalize_language(language)
    builder = InlineKeyboardBuilder()
    now_local = utc_now().astimezone(TASHKENT_TZ)
    weekdays = _WEEKDAYS[lang]
    months = _MONTHS_SHORT[lang]

    def _date_label(offset: int, base: str) -> str:
        return _mark_current(
            base,
            is_current=highlight_offset is not None and offset == highlight_offset,
        )

    builder.row(
        InlineKeyboardButton(
            text=_date_label(0, _t(lang, "today")),
            callback_data=SlotWizardCB(act="date", val=0).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_date_label(1, _t(lang, "tomorrow")),
            callback_data=SlotWizardCB(act="date", val=1).pack(),
        )
    )

    for offset in range(2, 7):
        day = (now_local + timedelta(days=offset)).date()
        wd = weekdays[day.weekday()]
        mon = months[day.month]
        label = _date_label(offset, f"{wd}, {day.day} {mon}")
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=SlotWizardCB(act="date", val=offset).pack(),
            )
        )

    _append_nav(builder, language=lang, with_back=False)
    return builder.as_markup()


def _is_slot_time_valid_today(
    hour: int,
    minute: int,
    now_local: datetime,
    lead_minutes: int = MIN_BOOKING_LEAD_MINUTES,
) -> bool:
    """Check if the given hour:minute on today meets the minimum lead time."""
    candidate = datetime(
        now_local.year,
        now_local.month,
        now_local.day,
        hour,
        minute,
        tzinfo=TASHKENT_TZ,
    )
    return (candidate - now_local) >= timedelta(minutes=lead_minutes)


def build_hour_picker_kb(
    *,
    which: str,
    language: str | None = None,
    day_offset: int = 0,
    highlight_hour: Optional[int] = None,
    start_hour: Optional[int] = None,
    start_minute: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """
    Step 2 / 4: hours 08–23 in a 4-column grid.

    15-Min Rule:
    For today (day_offset == 0), if all 4 minutes of an hour fail the 15-min rule
    (or for end hour, are not after start_time), the cell is replaced by '.'
    and clicking returns a disabled callback.
    """
    builder = InlineKeyboardBuilder()
    buttons: list[InlineKeyboardButton] = []
    now_local = utc_now().astimezone(TASHKENT_TZ)

    for hour in range(8, 24):
        is_valid = True

        if which == "sh" and day_offset == 0:
                # Start hour is valid if at least one minute (:00, :15, :30, :45) is valid
            is_valid = any(
                _is_slot_time_valid_today(hour, m, now_local)
                for m in (0, 15, 30, 45)
            )
        elif which == "eh" and start_hour is not None:
            # End must be later than start for every selected date, not only today.
            if hour < start_hour:
                is_valid = False
            elif hour == start_hour and (start_minute or 0) >= 45:
                is_valid = False

        if is_valid:
            label = _mark_current(
                f"{hour:02d}",
                is_current=highlight_hour is not None and hour == highlight_hour,
            )
            cb = SlotWizardCB(act=which, val=hour).pack()
        else:
            label = "."
            cb = SlotWizardCB(act="disabled", val=0).pack()

        buttons.append(InlineKeyboardButton(text=label, callback_data=cb))

    for i in range(0, len(buttons), 4):
        builder.row(*buttons[i : i + 4])
    _append_nav(builder, language=language, with_back=True)
    return builder.as_markup()


def build_minute_picker_kb(
    *,
    which: str,
    language: str | None = None,
    day_offset: int = 0,
    hour: int = 8,
    highlight_minute: Optional[int] = None,
    start_hour: Optional[int] = None,
    start_minute: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """
    Step 3 / 5: minutes [00] [15] [30] [45].

    15-Min Rule:
    For today (day_offset == 0), minutes that fail the 15-min rule (or end minutes <= start)
    are replaced by '.' and clicking returns a disabled callback.
    """
    builder = InlineKeyboardBuilder()
    now_local = utc_now().astimezone(TASHKENT_TZ)
    buttons: list[InlineKeyboardButton] = []

    for minute in (0, 15, 30, 45):
        is_valid = True

        if which == "sm":
            if day_offset == 0 and not _is_slot_time_valid_today(
                hour, minute, now_local
            ):
                is_valid = False
            # The wizard creates same-day slots; 23:45 has no valid end value.
            if hour == 23 and minute == 45:
                is_valid = False
        elif which == "em" and start_hour is not None:
            if hour < start_hour:
                is_valid = False
            elif hour == start_hour and minute <= (start_minute or 0):
                is_valid = False

        if is_valid:
            label = _mark_current(
                f"{minute:02d}",
                is_current=highlight_minute is not None and minute == highlight_minute,
            )
            cb = SlotWizardCB(act=which, val=minute).pack()
        else:
            label = "."
            cb = SlotWizardCB(act="disabled", val=0).pack()

        buttons.append(InlineKeyboardButton(text=label, callback_data=cb))

    builder.row(*buttons)
    _append_nav(builder, language=language, with_back=True)
    return builder.as_markup()


def build_format_picker_kb(
    language: str | None = None,
    current_is_online: Optional[bool] = None,
) -> InlineKeyboardMarkup:
    """Step 6: format selection (Offline / Online)."""
    lang = normalize_language(language)
    builder = InlineKeyboardBuilder()

    offline_label = _mark_current(
        _t(lang, "format_offline"),
        is_current=current_is_online is False,
    )
    online_label = _mark_current(
        _t(lang, "format_online"),
        is_current=current_is_online is True,
    )

    builder.row(
        InlineKeyboardButton(
            text=offline_label,
            callback_data=SlotWizardCB(act="fmt", val=0).pack(),
        ),
        InlineKeyboardButton(
            text=online_label,
            callback_data=SlotWizardCB(act="fmt", val=1).pack(),
        ),
    )
    _append_nav(builder, language=lang, with_back=True)
    return builder.as_markup()


def wizard_date_label(day_offset: int, language: str | None = None) -> str:
    """Human-readable date label for wizard prompts."""
    lang = normalize_language(language)
    local = utc_now().astimezone(TASHKENT_TZ) + timedelta(days=day_offset)
    if day_offset == 0:
        prefix = _t(lang, "today").replace("📅 ", "")
    elif day_offset == 1:
        prefix = _t(lang, "tomorrow").replace("📅 ", "")
    else:
        prefix = _WEEKDAYS[lang][local.weekday()]
    mon = _MONTHS_SHORT[lang][local.month]
    return f"{prefix}, {local.day} {mon}"


def day_offset_from_start(start_time: str | datetime) -> int:
    """Day offset (from today, Tashkent) for an existing slot start."""
    local_date = to_tashkent(start_time).date()
    today = utc_now().astimezone(TASHKENT_TZ).date()
    return max(0, min(6, (local_date - today).days))


def compose_slot_datetimes(
    *,
    day_offset: int,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
) -> tuple[datetime, datetime]:
    """Build timezone-aware UTC datetimes from wizard selections (Tashkent local)."""
    if day_offset not in range(0, 7):
        raise ValueError("day_offset must be between 0 and 6")
    if start_hour not in range(8, 24) or end_hour not in range(8, 24):
        raise ValueError("slot hours must be between 08 and 23")
    if start_minute not in {0, 15, 30, 45} or end_minute not in {0, 15, 30, 45}:
        raise ValueError("slot minutes must use the 15-minute grid")
    base = (utc_now().astimezone(TASHKENT_TZ) + timedelta(days=day_offset)).date()
    local_start = datetime(
        base.year,
        base.month,
        base.day,
        start_hour,
        start_minute,
        tzinfo=TASHKENT_TZ,
    )
    local_end = datetime(
        base.year,
        base.month,
        base.day,
        end_hour,
        end_minute,
        tzinfo=TASHKENT_TZ,
    )
    if local_end <= local_start:
        raise ValueError("slot end must be later than slot start")
    utc = timezone.utc
    return local_start.astimezone(utc), local_end.astimezone(utc)


def is_slot_start_allowed(
    start: datetime,
    *,
    now: Optional[datetime] = None,
    lead_minutes: int = MIN_BOOKING_LEAD_MINUTES,
) -> bool:
    """Server-side guard against stale or forged wizard callbacks."""
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return start >= current.astimezone(timezone.utc) + timedelta(
        minutes=lead_minutes
    )
