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
    build_help_kb,
    build_lang_kb,
    normalize_language,
)
from bot.routers.menu import send_main_menu


FIRST_START_PROMPT = "Выберите язык / Choose language / Tilni tanlang"

_HELP_HTML = {
    LANG_RU: (
        "📖 <b>Справка — S21 Peer-Review Notifier</b>\n\n"
        "🤖 <b>О боте:</b>\n"
        "Бот непрерывно опрашивает календарь платформы Школы 21 каждые 30 секунд, "
        "отслеживает слоты, присылает точечные уведомления и помогает управлять расписанием проверок\n\n"
        "🧭 <b>Значения эмодзи и роли:</b>\n"
        "• 🔍 Я проверяющий (Evaluator): твои открытые ревью слоты, где ты оцениваешь чужой проект\n"
        "• 📖 Меня проверяют (Evaluated): записи на ревью, где другой пир проверяет твой проект\n\n"
        "⏳ <b>Правило 15 минут (15-Min Rule):</b>\n"
        "• Записаться на проверку или создать слот можно минимум за 15 минут до ее начала\n\n"
        "🌐 <b>Формат проверки:</b>\n"
        "• Для занятого слота можно сменить формат на онлайн\n\n"
        "🔔 <b>4 этапа уведомлений:</b>\n"
        "1. 🔥 Моментально: при любой новой записи\n"
        "2. ⏳ За 15 минут: напоминание с никнеймом пира, ролью и форматом\n"
        "3. 🔔 За 2 минуты: короткое напоминание о готовности\n"
        "4. 🚀 0 минут (Старт): сигнал о начале проверки прямо сейчас\n\n"
        "⌨️ <b>Команды:</b>\n"
        "• /start — Главное меню и статус\n"
        "• /help — Эта подробная справка\n"
        "• /lang — Выбор языка (Русский / English / O'zbekcha)\n"
        "• /login — Авторизация (логин и пароль от платформы Школы 21)\n"
        "• /logout — Выход из аккаунта и удаление данных\n"
        "• /cancel — Отмена текущего действия\n\n"
        "👨‍💻 <b>Поддержка и обратная связь:</b>\n"
        "Если вы нашли баг или хотите предложить идею — пишите создателю: @A_Martin_Rosdy"
    ),
    LANG_EN: (
        "📖 <b>Help — S21 Peer-Review Notifier</b>\n\n"
        "🤖 <b>About the bot:</b>\n"
        "The bot polls the School 21 platform calendar every 30 seconds, tracks slots, sends precise notifications, and helps manage your review schedule\n\n"
        "🧭 <b>Emoji meanings and roles:</b>\n"
        "• 🔍 I am checking (Evaluator): your open review slots where you evaluate another peer's project\n"
        "• 📖 Being checked (Evaluated): review bookings where another peer evaluates your project\n\n"
        "⏳ <b>15-Minute Rule:</b>\n"
        "• You can book a review or create a slot at least 15 minutes before it starts\n\n"
        "🌐 <b>Review format:</b>\n"
        "• For a booked slot, you can switch the format to online\n\n"
        "🔔 <b>4 notification stages:</b>\n"
        "1. 🔥 Instantly: on every new booking\n"
        "2. ⏳ 15 minutes before: reminder with peer nickname, role, and format\n"
        "3. 🔔 2 minutes before: short readiness reminder\n"
        "4. 🚀 0 minutes (Start): signal that the review starts now\n\n"
        "⌨️ <b>Commands:</b>\n"
        "• /start — Main menu and status\n"
        "• /help — This detailed help\n"
        "• /lang — Choose language (Russian / English / O'zbekcha)\n"
        "• /login — Sign in (School 21 platform login and password)\n"
        "• /logout — Sign out and delete data\n"
        "• /cancel — Cancel the current action\n\n"
        "👨‍💻 <b>Support and feedback:</b>\n"
        "If you found a bug or want to suggest an idea, message the creator: @A_Martin_Rosdy"
    ),
    LANG_UZ: (
        "📖 <b>Yordam — S21 Peer-Review Notifier</b>\n\n"
        "🤖 <b>Bot haqida:</b>\n"
        "Bot School 21 platformasi kalendarini har 30 soniyada tekshiradi, slotlarni kuzatadi, aniq bildirishnomalar yuboradi va tekshiruv jadvalini boshqarishga yordam beradi\n\n"
        "🧭 <b>Emoji va rollarning ma’nosi:</b>\n"
        "• 🔍 Men tekshiruvchiman (Evaluator): boshqa peer loyihasini baholaydigan ochiq review slotlaringiz\n"
        "• 📖 Meni tekshirishadi (Evaluated): boshqa peer loyihangizni tekshiradigan review yozuvlari\n\n"
        "⏳ <b>15 daqiqa qoidasi:</b>\n"
        "• Tekshiruvga yozilish yoki slot yaratish boshlanishidan kamida 15 daqiqa oldin mumkin\n\n"
        "🌐 <b>Tekshiruv formati:</b>\n"
        "• Band qilingan slot formatini onlaynga o‘zgartirish mumkin\n\n"
        "🔔 <b>Bildirishnomalarning 4 bosqichi:</b>\n"
        "1. 🔥 Darhol: har bir yangi yozilishda\n"
        "2. ⏳ 15 daqiqa oldin: peer nickname, rol va format bilan eslatma\n"
        "3. 🔔 2 daqiqa oldin: tayyorgarlik haqida qisqa eslatma\n"
        "4. 🚀 0 daqiqa (Start): tekshiruv ayni paytda boshlanishi haqida signal\n\n"
        "⌨️ <b>Buyruqlar:</b>\n"
        "• /start — Asosiy menyu va holat\n"
        "• /help — Ushbu batafsil yordam\n"
        "• /lang — Tilni tanlash (Ruscha / English / O'zbekcha)\n"
        "• /login — Avtorizatsiya (School 21 platformasi login va paroli)\n"
        "• /logout — Hisobdan chiqish va ma’lumotlarni o‘chirish\n"
        "• /cancel — Joriy amalni bekor qilish\n\n"
        "👨‍💻 <b>Yordam va fikr-mulohaza:</b>\n"
        "Agar xato topsangiz yoki g‘oya taklif qilmoqchi bo‘lsangiz, yaratuvchiga yozing: @A_Martin_Rosdy"
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
    return _HELP_HTML[normalize_language(language)]


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
            await send_main_menu(
                message,
                db=db,
                user_id=message.chat.id,
                language=user.language,
                login=user.s21_login,
            )
            return

        await state.update_data(from_start=True)
        await message.answer(
            FIRST_START_PROMPT,
            reply_markup=build_lang_kb(with_back=False),
            parse_mode="HTML",
        )

    @router.message(Command("help"))
    async def cmd_help(message: Message, state: FSMContext) -> None:
        user = await db.get_user(message.chat.id)
        fsm_data = await state.get_data()
        pending = fsm_data.get("pending_language")
        from_start = fsm_data.get("from_start")
        # Drop any active auth/slot flow, preserving onboarding language only.
        current = await state.get_state()
        if current is not None:
            restore: dict = {}
            if pending:
                restore["pending_language"] = pending
            if from_start:
                restore["from_start"] = True
            await state.clear()
            if restore:
                await state.update_data(**restore)
        lang = (
            (user.language if user else None)
            or pending
            or DEFAULT_LANGUAGE
        )
        await message.answer(
            help_text(lang),
            reply_markup=build_help_kb(lang),
            parse_mode="HTML",
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
