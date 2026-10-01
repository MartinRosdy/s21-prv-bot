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
    STATUS_BOOKED,
    STATUS_OPEN,
    TrackedEvent,
)
from bot.keyboards.menu import normalize_language


class SlotWizardCB(CallbackData, prefix="sw"):
    """
    Compact wizard callbacks.

    ``act``: date | sh | sm | eh | em | back | cancel
    ``val``: day offset (0..6), hour (8..23), or minute (0/15/30/45)
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
        "make_offline": "🌐 Make Offline",
        "make_online": "🌐 Make Online",
        "role_evaluator": "👨‍🏫 (Checking)",
        "role_evaluated": "👨‍🎓 (Being checked)",
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
        "make_offline": "🌐 Сделать Офлайн",
        "make_online": "🌐 Сделать Онлайн",
        "role_evaluator": "👨‍🏫 (Проверяю)",
        "role_evaluated": "👨‍🎓 (Проверяют меня)",
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
        "make_offline": "🌐 Offline qilish",
        "make_online": "🌐 Online qilish",
        "role_evaluator": "👨‍🏫 (Tekshiraman)",
        "role_evaluated": "👨‍🎓 (Meni tekshiradi)",
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
) -> InlineKeyboardMarkup:
    """List of active slots as date/time interval buttons with role markers."""
    builder = InlineKeyboardBuilder()
    lang = normalize_language(language)
    for slot in slots:
        if slot.id is None:
            continue
        when = format_datetime(slot.start_time, slot.end_time, language=lang)
        if slot.effective_role == ROLE_EVALUATED:
            prefix = _t(lang, "role_evaluated")
        else:
            prefix = _t(lang, "role_evaluator")
        builder.row(
            InlineKeyboardButton(
                text=f"{prefix} {when}",
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
    Detail actions depending on OPEN vs BOOKED and user role.

    Evaluated users cannot manage slot time — only Back is shown.
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

    # Evaluated (being checked) cannot delete / reschedule / toggle.
    if slot.effective_role == ROLE_EVALUATED:
        builder.row(
            InlineKeyboardButton(
                text=_t(language, "back"),
                callback_data="menu_reviews",
            )
        )
        return builder.as_markup()

    if slot.status == STATUS_OPEN:
        builder.row(
            InlineKeyboardButton(
                text=_t(language, "delete"),
                callback_data=f"slot_delete:{slot.id}",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=_t(language, "edit_time"),
                callback_data=f"slot_edit:{slot.id}",
            )
        )
    elif slot.status == STATUS_BOOKED:
        is_online = bool(slot.data.get("is_online"))
        toggle_key = "make_offline" if is_online else "make_online"
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
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_t(language, "back_menu"),
            callback_data="menu_home",
        )
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
    """
    Step 1: Today, Tomorrow, and 5 more days ahead.

    ``highlight_offset`` marks the slot's current day when editing.
    No Back button on this step — only Cancel.
    """
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


def build_hour_picker_kb(
    *,
    which: str,
    language: str | None = None,
    highlight_hour: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """
    Step 2 / 4: hours 08–23 as plain digits in a 4-column grid.

    ``which`` is ``sh`` (start hour) or ``eh`` (end hour).
    """
    builder = InlineKeyboardBuilder()
    buttons: list[InlineKeyboardButton] = []
    for hour in range(8, 24):
        label = _mark_current(
            f"{hour:02d}",
            is_current=highlight_hour is not None and hour == highlight_hour,
        )
        buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=SlotWizardCB(act=which, val=hour).pack(),
            )
        )
    for i in range(0, len(buttons), 4):
        builder.row(*buttons[i : i + 4])
    _append_nav(builder, language=language, with_back=True)
    return builder.as_markup()


def build_minute_picker_kb(
    *,
    which: str,
    language: str | None = None,
    highlight_minute: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """
    Step 3 / 5: minutes [00] [15] [30] [45].

    ``which`` is ``sm`` (start minute) or ``em`` (end minute).
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        *[
            InlineKeyboardButton(
                text=_mark_current(
                    f"{minute:02d}",
                    is_current=(
                        highlight_minute is not None and minute == highlight_minute
                    ),
                ),
                callback_data=SlotWizardCB(act=which, val=minute).pack(),
            )
            for minute in (0, 15, 30, 45)
        ]
    )
    _append_nav(builder, language=language, with_back=True)
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
    """
    Day offset (from today, Tashkent) for an existing slot start.

    Clamped to 0..6 so the date picker can highlight it.
    """
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
        local_end = local_end + timedelta(days=1)
    utc = timezone.utc
    return local_start.astimezone(utc), local_end.astimezone(utc)
