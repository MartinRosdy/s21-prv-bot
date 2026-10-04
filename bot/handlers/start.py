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
    "Привет! 👋\n"
    "Я слежу за Peer-проверками в Школе 21 и напоминаю о записях\n\n"
    "Для начала работы необходимо авторизоваться:\n"
    "<b><u>/login</u></b>\n\n"
    "<i><b>⚠️ Требуется именно логин и пароль от платформы School 21</b></i>\n"
    "<i>🔒 Пароль надежно шифруется ключом AES-128 и нигде не сохраняется в открытом виде</i>\n\n"
    "Узнать больше о функционале: /help\n\n"
    "Выберите язык / Choose language / Tilni tanlang"
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
        "📖 *Help — S21 Peer-Review Assistant*\n\n"
        "🤖 *About the Bot:*\n"
        "The bot continuously monitors your School 21 platform calendar every 30 seconds, "
        "tracks peer-review duty slots, sends precision reminders, and manages your schedule.\n\n"
        "🧭 *Role Emojis:*\n"
        "• 🔍 — *I am checking (Evaluator):* your open duty slots and reviews where you evaluate another peer's project.\n"
        "• 📖 — *Being checked (Evaluated):* bookings where another peer reviews your project.\n\n"
        "⏳ *15-Minute Rule:*\n"
        "• Slot creation and booking are available at least 15 minutes before the start time.\n"
        "• In today's time grid, past hours/minutes or those within 15 minutes are replaced by `.` and disabled.\n\n"
        "🏢 *Review Formats:*\n"
        "• 🏢 *Offline:* on-campus review at the workstation.\n"
        "• 🌐 *Online:* remote review with video call link.\n"
        "Open slots have no format; change booked reviews on the School 21 platform.\n\n"
        "⚡️ *Automatic Slot Splitting:*\n"
        "If you have an open duty slot (e.g. 19:00 - 22:00) and sign up for a review at 19:30, "
        "the bot automatically splits your schedule:\n"
        "  a) 🔍 Evaluator: 19:00 - 19:30\n"
        "  b) 📖 Evaluated: 19:30 - 20:00\n"
        "  c) 🔍 Evaluator: 20:00 - 22:00\n\n"
        "🔔 *4 Notification Triggers:*\n"
        "1. 🔥 *Instant:* when anyone books your slot or you book a slot.\n"
        "2. ⏳ *T-15 min:* reminder with peer nickname, role, format, and time.\n"
        "3. 🔔 *T-2 min:* short readiness reminder.\n"
        "4. 🚀 *T-0 (Start):* alert that the review begins right now.\n"
        "*Notifications are sent symmetrically to both evaluator and evaluated!*\n\n"
        "⌨️ *Commands:*\n"
        "• /start — Main menu & onboarding\n"
        "• /help — This detailed guide\n"
        "• /lang — Switch language (English / Русский / O'zbekcha)\n"
        "• /login — Step-by-step sign-in (School 21 platform credentials)\n"
        "• /logout — Sign out and clear credentials\n"
        "• /cancel — Cancel current action\n\n"
        f"{_SUPPORT_BLOCK[LANG_EN]}"
    ),
    LANG_RU: (
        "📖 *Справка — S21 Peer-Review Assistant*\n\n"
        "🤖 *О боте:*\n"
        "Бот непрерывно опрашивает календарь платформы Школы 21 каждые 30 секунд, "
        "отслеживает слоты дежурств, присылает точечные уведомления и помогает управлять расписанием проверок.\n\n"
        "🧭 *Значения эмодзи и роли:*\n"
        "• 🔍 — *Я проверяющий (Evaluator):* твои свободные слоты дежурств и проверки, где ты оцениваешь чужой проект.\n"
        "• 📖 — *Меня проверяют (Evaluated):* записи на ревью, где другой пир проверяет твой проект.\n\n"
        "⏳ *Правило 15 минут (15-Min Rule):*\n"
        "• Записаться на проверку или создать слот можно минимум за 15 минут до ее начала.\n"
        "• В сетке выбора времени на сегодня прошедшие или недоступные ячейки заменяются точкой `.` и становятся неактивными.\n\n"
        "🏢 *Форматы проверок:*\n"
        "• 🏢 *Офлайн:* очная проверка в кампусе за рабочей станцией.\n"
        "• 🌐 *Онлайн:* дистанционная проверка со ссылкой на видеоконференцию.\n"
        "У пустого слота нет формата; формат занятого ревью меняется на платформе Школы 21.\n\n"
        "⚡️ *Умное разделение слотов:*\n"
        "Если у тебя открыт длинный слот (например, 19:00 - 22:00) и ты сам записываешься на проверку в 19:30, "
        "система автоматически разделит твой график:\n"
        "  a) 🔍 Проверяющий: 19:00 - 19:30\n"
        "  b) 📖 Проверяемый: 19:30 - 20:00\n"
        "  c) 🔍 Проверяющий: 20:00 - 22:00\n\n"
        "🔔 *4 этапа уведомлений:*\n"
        "1. 🔥 *Моментально:* при любой новой записи (к тебе или твоей к пиру).\n"
        "2. ⏳ *За 15 минут:* напоминание с никнеймом пира, ролью и форматом.\n"
        "3. 🔔 *За 2 минуты:* короткое напоминание о готовности.\n"
        "4. 🚀 *0 минут (Старт):* сигнал о начале проверки прямо сейчас.\n"
        "*Уведомления приходят зеркально проверяющему и проверяемому!*\n\n"
        "⌨️ *Команды:*\n"
        "• /start — Главное меню и статус\n"
        "• /help — Эта подробная справка\n"
        "• /lang — Выбор языка (Русский / English / O'zbekcha)\n"
        "• /login — Авторизация (логин и пароль от платформы Школы 21)\n"
        "• /logout — Выход из аккаунта и удаление данных\n"
        "• /cancel — Отмена текущего действия\n\n"
        f"{_SUPPORT_BLOCK[LANG_RU]}"
    ),
    LANG_UZ: (
        "📖 *Yordam — S21 Peer-Review Assistant*\n\n"
        "🤖 *Bot haqida:*\n"
        "Bot har 30 soniyada School 21 platformasi kalendarini tekshirib boradi, "
        "navbatchilik slotlarini kuzatadi, o‘z vaqtida eslatmalar yuboradi va tekshiruvlar jadvalini boshqarishga yordam beradi.\n\n"
        "🧭 *Emojilar va rollar:*\n"
        "• 🔍 — *Men tekshiruvchiman (Evaluator):* bo‘sh slotlaringiz va boshqa peer loyihasini tekshiradigan holatlar.\n"
        "• 📖 — *Meni tekshirishadi (Evaluated):* boshqa peer sizning loyihangizni tekshiradigan yozuvlar.\n\n"
        "⏳ *15 daqiqa qoidasi:*\n"
        "• Slot yaratish yoki yozilish kamida 15 daqiqa oldin amalga oshirilishi mumkin.\n"
        "• Bugungi vaqt kataklarida o‘tgan yoki 15 daqiqadan kam vaqtlar `.` belgisi bilan belgilanadi va faol bo‘lmaydi.\n\n"
        "🏢 *Tekshiruv formatlari:*\n"
        "• 🏢 *Offline:* kampusda bevosita ish stantsiyasida tekshiruv.\n"
        "• 🌐 *Online:* videoaloqa havolasi orqali masofaviy tekshiruv.\n"
        "Bo‘sh slotda format yo‘q; band review formatini School 21 platformasida o‘zgartiring.\n\n"
        "⚡️ *Slotlarni avtomatik bo‘lish:*\n"
        "Agar sizda uzun slot ochilgan bo‘lsa (masalan, 19:00 - 22:00) va 19:30 ga tekshiruvga yozilsangiz, "
        "tizim jadvalingizni avtomatik ravishda ajratadi:\n"
        "  a) 🔍 Tekshiruvchi: 19:00 - 19:30\n"
        "  b) 📖 Tekshiriluvchi: 19:30 - 20:00\n"
        "  c) 🔍 Tekshiruvchi: 20:00 - 22:00\n\n"
        "🔔 *4 bosqichli bildirishnomalar:*\n"
        "1. 🔥 *Tezkor:* har qanday yangi yozilishda (sizga yoki siz tomondan).\n"
        "2. ⏳ *15 daqiqa oldin:* peer nickname, rol va format bilan eslatma.\n"
        "3. 🔔 *2 daqiqa oldin:* tayyorgarlik haqida qisqa eslatma.\n"
        "4. 🚀 *0 daqiqa (Start):* tekshiruv boshlanganligi haqida signal.\n"
        "*Xabarlar tekshiruvchi va tekshiriluvchiga bir xil tarzda yuboriladi!*\n\n"
        "⌨️ *Buyruqlar:*\n"
        "• /start — Asosiy menyu va holat\n"
        "• /help — Ushbu batafsil yordam\n"
        "• /lang — Tilni tanlash (English / Русский / O'zbekcha)\n"
        "• /login — Avtorizatsiya (School 21 platformasi login va paroli)\n"
        "• /logout — Hisobdan chiqish va ma’lumotlarni o‘chirish\n"
        "• /cancel — Joriy amalni bekor qilish\n\n"
        f"{_SUPPORT_BLOCK[LANG_UZ]}"
    ),
}

_HELP_HTML = {
    LANG_RU: (
        "📖 <b>Справка — S21 Peer-Review Notifier</b>\n\n"
        "🤖 <b>О боте:</b>\n"
        "Бот непрерывно опрашивает календарь платформы Школы 21 каждые 30 секунд, "
        "отслеживает слоты, присылает точечные уведомления и помогает управлять расписанием проверок\n\n"
        "🧭 <b>Значения эмодзи и роли:</b>\n"
        "• 🔍 — Я проверяющий (Evaluator): твои свободные слоты дежурств и проверки, "
        "где ты оцениваешь чужой проект\n"
        "• 📖 — Меня проверяют (Evaluated): записи на ревью, где другой пир проверяет твой проект\n\n"
        "⏳ <b>Правило 15 минут (15-Min Rule):</b>\n"
        "• Записаться на проверку или создать слот можно минимум за 15 минут до ее начала\n\n"
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
        "The bot tracks your School 21 peer reviews and sends booking and start reminders.\n"
        "Create or change a slot at least 15 minutes before it starts.\n"
        "🔍 Checking — you evaluate a peer; 📖 Being checked — a peer evaluates you.\n"
        "A booked review has a format button at T-15; changing format currently requires the platform.\n\n"
        "Commands: /start, /help, /lang, /login, /logout, /cancel\n"
        "Support: @A_Martin_Rosdy"
    ),
    LANG_UZ: (
        "📖 <b>Yordam — S21 Peer-Review Notifier</b>\n\n"
        "Bot School 21 peer-review slotlarini kuzatadi va eslatmalar yuboradi.\n"
        "Slot boshlanishidan kamida 15 daqiqa oldin yaratiladi yoki o‘zgartiriladi.\n"
        "🔍 Tekshiruvchi — siz tekshirasiz; 📖 Tekshiriluvchi — sizni tekshirishadi.\n"
        "Band slot formatini hozircha faqat platformada o‘zgartirish mumkin.\n\n"
        "Buyruqlar: /start, /help, /lang, /login, /logout, /cancel\n"
        "Yordam: @A_Martin_Rosdy"
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
