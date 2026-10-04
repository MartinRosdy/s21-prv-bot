"""Main menu and settings keyboards."""

from __future__ import annotations

import html

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
        "one — switch it to online format"
    ),
    LANG_RU: (
        "Список твоих слотов на пир-ревью. Можно создать слот через "
        "пошаговый выбор даты и времени, изменить время или удалить слот, "
        "а для занятого — переключить на онлайн формат"
    ),
    LANG_UZ: (
        "Peer-review slotlaringiz ro‘yxati. Slotni sana/vaqt tanlash "
        "orqali yarating, vaqtini o‘zgartiring yoki o‘chiring, band "
        "bo‘lsa — onlayn formatga o‘tkazing"
    ),
}

_MAIN_MENU_TEXT = {
    LANG_EN: (
        "✅ Language set: English\n\n"
        "Current account: {login}\n\n"
        "🏠 Main menu\n\n"
        "🔍 I am checking: {evaluator_count} | 📖 Being checked: {evaluated_count}\n\n"
        "📋 My slots — Your peer-review slots\n"
        "Create a slot by choosing a date and time, change or delete an open slot\n"
        "For a booked slot — switch it to online format\n\n"
        "ℹ️ Use /help to see all commands"
    ),
    LANG_RU: (
        "✅ Язык установлен: Русский\n\n"
        "Текущая авторизация: {login}\n\n"
        "🏠 Главное меню\n\n"
        "🔍 Я проверяющий: {evaluator_count} | 📖 Меня проверяют: {evaluated_count}\n\n"
        "📋 Мои слоты — Список твоих слотов на пир-ревью\n"
        "Можно создать слот через пошаговый выбор даты и времени, изменить время или удалить слот\n"
        "А для занятого — переключить на онлайн формат\n\n"
        "ℹ️ Используй /help для просмотра всех команд"
    ),
    LANG_UZ: (
        "✅ Til tanlandi: O'zbekcha\n\n"
        "Joriy akkaunt: {login}\n\n"
        "🏠 Asosiy menyu\n\n"
        "🔍 Men tekshiruvchiman: {evaluator_count} | 📖 Meni tekshirishadi: {evaluated_count}\n\n"
        "📋 Mening slotlarim — Peer-review slotlaringiz ro‘yxati\n"
        "Sana va vaqtni tanlab slot yaratish, o‘zgartirish yoki o‘chirish mumkin\n"
        "Band slotni onlayn formatga o‘tkazish mumkin\n\n"
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


def main_menu_text(
    language: str | None = None,
    login: str | None = None,
    *,
    evaluator_count: int = 0,
    evaluated_count: int = 0,
) -> str:
    """Localized main-menu caption."""
    return _MAIN_MENU_TEXT[normalize_language(language)].format(
        login=f"<code>{html.escape(login or '—')}</code>",
        evaluator_count=max(0, evaluator_count),
        evaluated_count=max(0, evaluated_count),
    )


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
