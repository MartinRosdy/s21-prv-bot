"""Main menu and settings keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import DEFAULT_LANGUAGE, LANG_EN, LANG_RU, LANG_UZ


_MENU_BUTTONS = {
    LANG_EN: {
        "reviews": "📋 My slots",
    },
    LANG_RU: {
        "reviews": "📋 Мои слоты",
    },
    LANG_UZ: {
        "reviews": "📋 Mening slotlarim",
    },
}

_REVIEWS_DESC = {
    LANG_EN: (
        "Your peer-review slots list. Create a slot via step-by-step "
        "date/time picker, reschedule or delete a slot, and for a booked "
        "one — switch to online"
    ),
    LANG_RU: (
        "Список твоих слотов на пир-ревью. Можно создать слот через "
        "пошаговый выбор даты и времени, изменить время или удалить слот, "
        "а для занятого — переключить на онлайн"
    ),
    LANG_UZ: (
        "Peer-review slotlaringiz ro‘yxati. Slotni sana/vaqt tanlash "
        "orqali yarating, vaqtini o‘zgartiring yoki o‘chiring, band "
        "bo‘lsa — onlaynga o‘tkazing"
    ),
}

_MAIN_MENU_TEXT = {
    LANG_EN: (
        "🏠 *Main menu*\n\n"
        "Choose a section:\n\n"
        f"📋 *My slots* — {_REVIEWS_DESC[LANG_EN]}\n\n"
        "ℹ️ Use /help to see all commands"
    ),
    LANG_RU: (
        "🏠 *Главное меню*\n\n"
        "Выбери раздел:\n\n"
        f"📋 *Мои слоты* — {_REVIEWS_DESC[LANG_RU]}\n\n"
        "ℹ️ Используй /help для просмотра всех команд"
    ),
    LANG_UZ: (
        "🏠 *Asosiy menyu*\n\n"
        "Bo‘limni tanlang:\n\n"
        f"📋 *Mening slotlarim* — {_REVIEWS_DESC[LANG_UZ]}\n\n"
        "ℹ️ Barcha buyruqlarni ko‘rish uchun /help dan foydalaning"
    ),
}

_BACK_TO_MENU = {
    LANG_EN: "🔙 Back to menu",
    LANG_RU: "🔙 Назад в меню",
    LANG_UZ: "🔙 Menyuga qaytish",
}

# Backwards-compatible default (Russian).
BACK_TO_MENU_LABEL = _BACK_TO_MENU[LANG_RU]


def normalize_language(language: str | None) -> str:
    """Return a supported language code, falling back to default."""
    if language in _MAIN_MENU_TEXT:
        return language  # type: ignore[return-value]
    return DEFAULT_LANGUAGE


def main_menu_text(language: str | None = None) -> str:
    """Localized main-menu caption."""
    return _MAIN_MENU_TEXT[normalize_language(language)]


def reviews_desc(language: str | None = None) -> str:
    """Localized Reviews section description (help / menu)."""
    return _REVIEWS_DESC[normalize_language(language)]


def build_main_menu_kb(language: str | None = None) -> InlineKeyboardMarkup:
    """Primary navigation for authenticated users."""
    labels = _MENU_BUTTONS[normalize_language(language)]
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=labels["reviews"],
            callback_data="menu_reviews",
        )
    )
    return builder.as_markup()


def build_lang_kb(
    *,
    with_back: bool = True,
    language: str | None = None,
) -> InlineKeyboardMarkup:
    """Language picker; optionally include back-to-menu (for /lang)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")
    )
    builder.row(
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")
    )
    builder.row(
        InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang_uz")
    )
    if with_back:
        back = _BACK_TO_MENU[normalize_language(language)]
        builder.row(
            InlineKeyboardButton(text=back, callback_data="menu_home")
        )
    return builder.as_markup()


def build_settings_kb(language: str | None = None) -> InlineKeyboardMarkup:
    """Language picker + back to menu (authenticated /lang flow)."""
    return build_lang_kb(with_back=True, language=language)


def build_back_to_menu_kb(language: str | None = None) -> InlineKeyboardMarkup:
    """Single back-to-main-menu button."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=_BACK_TO_MENU[normalize_language(language)],
            callback_data="menu_home",
        )
    )
    return builder.as_markup()
