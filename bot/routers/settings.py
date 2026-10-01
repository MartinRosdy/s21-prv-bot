"""Settings router: /lang language selection (also first-start)."""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    Message,
)

from bot.database.db import Database
from bot.database.models import (
    DEFAULT_LANGUAGE,
    LANG_EN,
    LANG_RU,
    LANG_UZ,
    SUPPORTED_LANGUAGES,
)
from bot.keyboards.menu import (
    build_lang_kb,
    build_main_menu_kb,
    main_menu_text,
    normalize_language,
)

logger = logging.getLogger(__name__)

SETTINGS_PROMPT = (
    "⚙️ Choose language / Выберите язык / Tilni tanlang"
)

_START_WELCOME = {
    LANG_EN: (
        "Hi! 👋\n"
        "I am your personal Peer-Review assistant for School 21. "
        "I monitor your calendar every 30 seconds\n\n"
        "⏰ Reminders: I will notify you 15, 2 minutes before and at the start moment\n"
        "🕵️‍♂️ Peer nickname: Exactly 15 minutes before start "
        "I will reveal the nickname of your evaluatee or evaluator!\n\n"
        "To get started, sign in:\n"
        "/login\n\n"
        "*(Your data is fully secure. Passwords are encrypted)*\n\n"
        "Learn more about features: /help"
    ),
    LANG_RU: (
        "Привет! 👋\n"
        "Я — твой персональный ассистент по Peer-проверкам в Школе 21. "
        "Я мониторю твой календарь каждые 30 секунд\n\n"
        "⏰ Напоминания: Я пришлю уведомления за 15, 2 и в минуту старта\n"
        "🕵️‍♂️ Никнейм пира: Ровно за 15 минут до старта "
        "я раскрою никнейм твоего проверяемого или проверяющего!\n\n"
        "Для начала работы необходимо авторизоваться:\n"
        "/login\n\n"
        "*(Твои данные в полной безопасности. Пароли шифруются)*\n\n"
        "Узнать больше о функционале: /help"
    ),
    LANG_UZ: (
        "Salom! 👋\n"
        "Men School 21 Peer-tekshiruvlari bo‘yicha shaxsiy yordamchingizman. "
        "Kalendaringizni har 30 soniyada kuzataman\n\n"
        "⏰ Eslatmalar: 15, 2 daqiqa oldin va boshlanish daqiqasida "
        "xabar yuboraman\n"
        "🕵️‍♂️ Peer nickname: Boshlanishidan roppa-rosa 15 daqiqa oldin "
        "tekshiriluvchi yoki tekshiruvchingiz nicknameini ochaman!\n\n"
        "Ishni boshlash uchun avtorizatsiyadan o‘ting:\n"
        "/login\n\n"
        "*(Ma’lumotlaringiz to‘liq xavfsiz. Parollar shifrlanadi)*\n\n"
        "Imkoniyatlar haqida batafsil: /help"
    ),
}


def start_welcome_text(language: str | None = None) -> str:
    """Localized /start welcome for guests (not signed in)."""
    return _START_WELCOME[normalize_language(language)]


def post_lang_auth_hint(language: str | None = None) -> str:
    """Localized auth instruction shown after first-start language pick."""
    return start_welcome_text(language)


_LANG_CONFIRM = {
    LANG_EN: "✅ Language set to *English*",
    LANG_RU: "✅ Язык установлен: *Русский*",
    LANG_UZ: "✅ Til tanlandi: *O'zbekcha*",
}

_CALLBACK_TO_LANG = {
    "lang_en": LANG_EN,
    "lang_ru": LANG_RU,
    "lang_uz": LANG_UZ,
}

# Telegram Bot API language_code for setMyCommands.
_TG_LANG_CODE = {
    LANG_EN: "en",
    LANG_RU: "ru",
    LANG_UZ: "uz",
}

_COMMANDS_BY_LANG = {
    LANG_EN: [
        BotCommand(command="start", description="Main menu"),
        BotCommand(command="help", description="Help and feedback"),
        BotCommand(command="lang", description="Choose language"),
        BotCommand(command="login", description="Sign in"),
        BotCommand(command="logout", description="Sign out"),
        BotCommand(command="cancel", description="Cancel current action"),
    ],
    LANG_RU: [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="Справка и обратная связь"),
        BotCommand(command="lang", description="Выбор языка"),
        BotCommand(command="login", description="Авторизация"),
        BotCommand(command="logout", description="Выход из аккаунта"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ],
    LANG_UZ: [
        BotCommand(command="start", description="Asosiy menyu"),
        BotCommand(command="help", description="Yordam va fikr-mulohaza"),
        BotCommand(command="lang", description="Tilni tanlash"),
        BotCommand(command="login", description="Avtorizatsiya"),
        BotCommand(command="logout", description="Hisobdan chiqish"),
        BotCommand(command="cancel", description="Joriy amalni bekor qilish"),
    ],
}


def bot_commands_for(language: str | None = None) -> list[BotCommand]:
    """Return localized BotCommand list (default Russian)."""
    lang = language if language in _COMMANDS_BY_LANG else LANG_RU
    return list(_COMMANDS_BY_LANG[lang])


async def sync_user_bot_commands(
    bot: Bot,
    chat_id: int,
    language: str,
) -> None:
    """
    Sync the left-side command menu for a specific chat.

    Telegram does not auto-bind UI language to DB preference, so we set
    ``BotCommandScopeChat`` for this user. Also refresh the language_code
    default when possible.
    """
    commands = bot_commands_for(language)
    try:
        await bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(chat_id=chat_id),
        )
    except Exception:
        logger.warning(
            "Failed to set chat-scoped commands for chat_id=%s",
            chat_id,
            exc_info=True,
        )
    tg_code = _TG_LANG_CODE.get(language)
    if tg_code:
        try:
            await bot.set_my_commands(commands, language_code=tg_code)
        except Exception:
            logger.debug(
                "set_my_commands(language_code=%s) failed",
                tg_code,
                exc_info=True,
            )


def get_settings_router(db: Database, bot: Optional[Bot] = None) -> Router:
    router = Router(name="settings")

    async def _show_lang_picker(
        target: Message,
        *,
        edit: bool = False,
        with_back: bool = True,
        language: str | None = None,
    ) -> None:
        markup = build_lang_kb(with_back=with_back, language=language)
        if edit:
            await target.edit_text(
                SETTINGS_PROMPT,
                reply_markup=markup,
                parse_mode="Markdown",
            )
        else:
            await target.answer(
                SETTINGS_PROMPT,
                reply_markup=markup,
                parse_mode="Markdown",
            )

    @router.message(Command("lang"))
    async def cmd_lang(message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await db.get_user(message.chat.id)
        # Allow language pick before login (first-start / guests).
        linked = bool(user and user.is_linked)
        await _show_lang_picker(
            message,
            edit=False,
            with_back=linked,
            language=user.language if user else None,
        )

    @router.callback_query(F.data == "menu_settings")
    async def cb_menu_settings(callback: CallbackQuery, state: FSMContext) -> None:
        """Legacy callback — kept for old inline messages."""
        await state.clear()
        if not callback.message:
            await callback.answer()
            return
        user = await db.get_user(callback.from_user.id)
        linked = bool(user and user.is_linked)
        await _show_lang_picker(
            callback.message,
            edit=True,
            with_back=linked,
            language=user.language if user else None,
        )
        await callback.answer()

    @router.callback_query(F.data.in_(_CALLBACK_TO_LANG))
    async def cb_set_language(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not callback.data:
            await callback.answer()
            return

        language = _CALLBACK_TO_LANG[callback.data]
        if language not in SUPPORTED_LANGUAGES:
            await callback.answer("Unsupported language", show_alert=True)
            return

        user = await db.get_user(callback.from_user.id)
        confirm = _LANG_CONFIRM[language]
        active_bot = bot or callback.bot

        # Always persist language in DB (creates soft row for brand-new guests).
        await db.upsert_user_language(callback.from_user.id, language)
        await sync_user_bot_commands(
            active_bot,
            callback.from_user.id,
            language,
        )

        linked = bool(user and user.is_linked)

        # Guest (/start onboarding or /lang before login): delete picker → welcome.
        if not linked:
            await state.clear()
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(
                start_welcome_text(language),
                parse_mode="Markdown",
            )
            await callback.answer()
            return

        # Authenticated /lang from settings: confirm + main menu.
        await state.clear()
        await callback.message.edit_text(
            f"{confirm}\n\n{main_menu_text(language)}",
            reply_markup=build_main_menu_kb(language),
            parse_mode="Markdown",
        )
        await callback.answer()

    return router
