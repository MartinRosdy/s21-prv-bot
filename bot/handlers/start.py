"""Handler registration and /start /help commands."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.database.db import Database
from bot.database.models import (
    DEFAULT_LANGUAGE,
    LANG_EN,
    LANG_RU,
    LANG_UZ,
)
from bot.keyboards.menu import (
    build_lang_kb,
    build_main_menu_kb,
    normalize_language,
    reviews_desc,
)
from bot.routers.menu import send_main_menu


FIRST_START_PROMPT = (
    "🇷🇺 Привет! Выбери язык интерфейса\n"
    "🇬🇧 Hi! Choose your language\n"
    "🇺🇿 Salom! Tilni tanlang"
)

_SUPPORT_BLOCK = {
    LANG_EN: (
        "👨‍💻 *Support and feedback:*\n"
        "If you found a bug or want to suggest an idea — "
        "message the creator: @A\\_Martin\\_Rosdy"
    ),
    LANG_RU: (
        "👨‍💻 *Поддержка и обратная связь:*\n"
        "Если вы нашли баг или хотите предложить идею — "
        "пишите создателю: @A\\_Martin\\_Rosdy"
    ),
    LANG_UZ: (
        "👨‍💻 *Qo‘llab-quvvatlash va fikr-mulohaza:*\n"
        "Agar xato topsangiz yoki g‘oya taklif qilmoqchi bo‘lsangiz — "
        "yaratuvchiga yozing: @A\\_Martin\\_Rosdy"
    ),
}

_HELP_TEXT = {
    LANG_EN: (
        "📖 *Help — S21 Peer-Review Notifier*\n\n"
        "*Monitoring*\n"
        "The bot polls the platform calendar every *30 seconds*: "
        "new slots and peer-review bookings\n\n"
        "*Reminders*\n"
        "Notifications arrive *15, 2 minutes before and at the start moment*. "
        "At T-15 the bot reveals the peer login "
        "(your evaluatee or evaluator) and format (online/offline)\n\n"
        f"*🔍 Reviews*\n{reviews_desc(LANG_EN)}\n\n"
        "*Commands*\n"
        "• /start — onboarding / language\n"
        "• /help — this help\n"
        "• /lang — language (English / Русский / O'zbekcha)\n"
        "• /login — sign in (step by step)\n"
        "• /logout — sign out and delete data\n"
        "• /cancel — cancel current action\n\n"
        f"{_SUPPORT_BLOCK[LANG_EN]}"
    ),
    LANG_RU: (
        "📖 *Справка — S21 Peer-Review Notifier*\n\n"
        "*Мониторинг*\n"
        "Бот опрашивает календарь платформы каждые *30 секунд*: "
        "новые слоты и записи на пир-ревью\n\n"
        "*Напоминания*\n"
        "Уведомления приходят *за 15, 2 и в минуту старта*. "
        "За 15 минут бот раскрывает логин пира "
        "(проверяемого или проверяющего) и формат (онлайн/офлайн)\n\n"
        f"*🔍 Проверки*\n{reviews_desc(LANG_RU)}\n\n"
        "*Команды*\n"
        "• /start — онбординг / язык\n"
        "• /help — эта справка\n"
        "• /lang — выбор языка (English / Русский / O'zbekcha)\n"
        "• /login — авторизация (пошагово)\n"
        "• /logout — выход и удаление данных\n"
        "• /cancel — отменить текущее действие\n\n"
        f"{_SUPPORT_BLOCK[LANG_RU]}"
    ),
    LANG_UZ: (
        "📖 *Yordam — S21 Peer-Review Notifier*\n\n"
        "*Monitoring*\n"
        "Bot platforma kalendarini har *30 soniyada* tekshiradi: "
        "yangi slotlar va peer-review yozuvlari\n\n"
        "*Eslatmalar*\n"
        "Xabarlar *15, 2 daqiqa oldin va boshlanish daqiqasida* keladi. "
        "T-15 da peer login "
        "(tekshiriluvchi yoki tekshiruvchi) va format (online/offline) "
        "ko‘rsatiladi\n\n"
        f"*🔍 Tekshiruvlar*\n{reviews_desc(LANG_UZ)}\n\n"
        "*Buyruqlar*\n"
        "• /start — onboarding / til\n"
        "• /help — ushbu yordam\n"
        "• /lang — til (English / Русский / O'zbekcha)\n"
        "• /login — avtorizatsiya (bosqichma-bosqich)\n"
        "• /logout — chiqish va ma’lumotlarni o‘chirish\n"
        "• /cancel — joriy amalni bekor qilish\n\n"
        f"{_SUPPORT_BLOCK[LANG_UZ]}"
    ),
}

_CANCEL_DONE = {
    LANG_EN: "Action cancelled. Back: /start",
    LANG_RU: "Действие отменено. Вернуться: /start",
    LANG_UZ: "Amal bekor qilindi. Qaytish: /start",
}

_CANCEL_NOTHING = {
    LANG_EN: "Nothing to cancel",
    LANG_RU: "Нечего отменять",
    LANG_UZ: "Bekor qilish uchun hech narsa yo‘q",
}


def help_text(language: str | None = None) -> str:
    """Localized /help body."""
    return _HELP_TEXT[normalize_language(language)]


def get_start_router(db: Database) -> Router:
    """Build a router with DB-aware /start and /help handlers."""
    router = Router(name="start")

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        """
        Smart /start routing by auth state.

        Linked users (``s21_login`` + password in DB) get the main menu.
        Guests / after ``/logout`` get the language picker + onboarding.
        """
        await state.clear()
        user = await db.get_user(message.chat.id)

        if user and user.is_linked:
            await send_main_menu(message, language=user.language)
            return

        await state.update_data(from_start=True)
        await message.answer(
            FIRST_START_PROMPT,
            reply_markup=build_lang_kb(with_back=False),
        )

    @router.message(Command("help"))
    async def cmd_help(message: Message, state: FSMContext) -> None:
        user = await db.get_user(message.chat.id)
        fsm_data = await state.get_data()
        pending = fsm_data.get("pending_language")
        from_start = fsm_data.get("from_start")
        # Keep pending_language / from_start for first-start; only drop wizard.
        current = await state.get_state()
        if current is not None:
            await state.set_state(None)
            restore: dict = {}
            if pending:
                restore["pending_language"] = pending
            if from_start:
                restore["from_start"] = True
            if restore:
                await state.update_data(**restore)
        lang = (
            (user.language if user else None)
            or pending
            or DEFAULT_LANGUAGE
        )
        markup = build_main_menu_kb(lang) if (user and user.is_linked) else None
        await message.answer(
            help_text(lang),
            reply_markup=markup,
            parse_mode="Markdown",
        )

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        user = await db.get_user(message.chat.id)
        fsm_data = await state.get_data()
        pending = fsm_data.get("pending_language")
        from_start = fsm_data.get("from_start")
        lang = (
            (user.language if user else None)
            or pending
            or DEFAULT_LANGUAGE
        )
        current = await state.get_state()
        if current is None:
            await message.answer(_CANCEL_NOTHING[normalize_language(lang)])
            return
        await state.clear()
        # Preserve language / onboarding flags chosen before first /login.
        restore: dict = {}
        if pending and (user is None or not user.is_linked):
            restore["pending_language"] = pending
        if from_start:
            restore["from_start"] = True
        if restore:
            await state.update_data(**restore)
        await message.answer(
            _CANCEL_DONE[normalize_language(lang)],
            parse_mode="Markdown",
        )

    return router
