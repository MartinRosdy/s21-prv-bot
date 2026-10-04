"""/login and /logout handlers (stepwise FSM auth)."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.database.db import Database
from bot.database.models import DEFAULT_LANGUAGE, LANG_EN, LANG_RU, LANG_UZ, SUPPORTED_LANGUAGES
from bot.keyboards.menu import build_main_menu_kb, main_menu_text, normalize_language
from bot.routers.settings import sync_user_bot_commands
from bot.services.crypto import CryptoService
from bot.services.s21_api import S21ApiClient, S21AuthError, S21NetworkError
from bot.states.auth import AuthStates

logger = logging.getLogger(__name__)

_ALREADY_AUTH = {
    LANG_EN: (
        "⚠️ You are already signed in. "
        "To switch accounts, use /logout first"
    ),
    LANG_RU: (
        "⚠️ Вы уже авторизованы в системе. "
        "Если хотите сменить аккаунт, сначала используйте команду /logout"
    ),
    LANG_UZ: (
        "⚠️ Siz allaqachon avtorizatsiyadan o‘tgansiz. "
        "Akkountni almashtirish uchun avval /logout dan foydalaning"
    ),
}

_ASK_LOGIN = {
    LANG_EN: "Enter your School 21 login (e.g. `lomasadr`):",
    LANG_RU: "Введите ваш логин от Школы 21 (например, `lomasadr`):",
    LANG_UZ: "School 21 loginingizni kiriting (masalan, `lomasadr`):",
}

_ASK_PASSWORD = {
    LANG_EN: "Enter your School 21 platform password (it is securely encrypted):",
    LANG_RU: "Введите пароль от платформы Школы 21 (он безопасно шифруется):",
    LANG_UZ: "School 21 platformasi parolini kiriting (u xavfsiz shifrlanadi):",
}


_CHECKING = {
    LANG_EN: "🔐 Checking platform access…",
    LANG_RU: "🔐 Проверяю доступ к платформе…",
    LANG_UZ: "🔐 Platformaga kirish tekshirilmoqda…",
}

_NETWORK_ERR = {
    LANG_EN: (
        "⚠️ Network/platform temporarily unavailable — "
        "try /login again in a minute"
    ),
    LANG_RU: (
        "⚠️ Сеть/платформа временно недоступны — "
        "попробуй /login ещё раз через минуту"
    ),
    LANG_UZ: (
        "⚠️ Tarmoq/platforma vaqtincha mavjud emas — "
        "bir daqiqadan so‘ng /login ni qayta urinib ko‘ring"
    ),
}

_AUTH_ERR = {
    LANG_EN: (
        "❌ Sign-in failed — check your School 21 login and password"
    ),
    LANG_RU: (
        "❌ Не удалось войти — проверь логин и пароль от платформы Школы 21"
    ),
    LANG_UZ: (
        "❌ Kirib bo‘lmadi — School 21 login va parolini tekshiring"
    ),
}

_ENCRYPT_ERR = {
    LANG_EN: "❌ Encryption error — check ENCRYPTION_KEY",
    LANG_RU: "❌ Ошибка шифрования — проверь ENCRYPTION_KEY",
    LANG_UZ: "❌ Shifrlash xatosi — ENCRYPTION_KEY ni tekshiring",
}

_PRIVATE_ONLY = {
    LANG_EN: "Sign-in is only available in private messages",
    LANG_RU: "Авторизация доступна только в личных сообщениях",
    LANG_UZ: "Avtorizatsiya faqat shaxsiy xabarlarda mavjud",
}

_EMPTY_LOGIN = {
    LANG_EN: "Login cannot be empty. Enter your School 21 login:",
    LANG_RU: "Логин не может быть пустым. Введи логин от Школы 21:",
    LANG_UZ: "Login bo‘sh bo‘lishi mumkin emas. School 21 loginini kiriting:",
}

_EMPTY_PASSWORD = {
    LANG_EN: "Password cannot be empty. Enter your password:",
    LANG_RU: "Пароль не может быть пустым. Введи пароль:",
    LANG_UZ: "Parol bo‘sh bo‘lishi mumkin emas. Parolingizni kiriting:",
}

_LOGIN_OK = {
    LANG_EN: (
        "✅ Sign-in complete! I am now monitoring your calendar"
    ),
    LANG_RU: (
        "✅ Авторизация успешно завершена! Теперь я мониторю ваш календарь"
    ),
    LANG_UZ: (
        "✅ Avtorizatsiya muvaffaqiyatli yakunlandi! "
        "Endi kalendaringizni kuzataman"
    ),
}

_LOGOUT_OK = {
    LANG_EN: (
        "👋 Data deleted — monitoring stopped\n"
        "You can return with /login"
    ),
    LANG_RU: (
        "👋 Данные удалены — мониторинг остановлен\n"
        "Вернуться можно командой /login"
    ),
    LANG_UZ: (
        "👋 Ma’lumotlar o‘chirildi — monitoring to‘xtatildi\n"
        "/login orqali qaytishingiz mumkin"
    ),
}

_LOGOUT_NONE = {
    LANG_EN: "You are not signed in — use /login",
    LANG_RU: "Ты и так не авторизован — используй /login",
    LANG_UZ: "Siz avtorizatsiyadan o‘tmagansiz — /login dan foydalaning",
}


async def _resolve_lang(db: Database, message: Message, state: FSMContext) -> str:
    fsm_data = await state.get_data()
    pending = fsm_data.get("pending_language") or fsm_data.get("auth_language")
    if pending in SUPPORTED_LANGUAGES:
        return normalize_language(str(pending))
    user = await db.get_user(message.chat.id)
    return normalize_language(user.language if user else DEFAULT_LANGUAGE)


def get_auth_router(
    db: Database,
    crypto: CryptoService,
    api: S21ApiClient,
) -> Router:
    """Build auth router with injected dependencies."""
    router = Router(name="auth")

    @router.message(Command("login"))
    async def cmd_login(message: Message, state: FSMContext) -> None:
        """Start stepwise login: ask for login, then password via FSM."""
        if message.chat.type != "private":
            await message.answer(_PRIVATE_ONLY[LANG_RU])
            return

        lang = await _resolve_lang(db, message, state)
        existing = await db.get_user(message.chat.id)
        # Soft-logged-out rows keep language but have no credentials —
        # allow /login to UPDATE them instead of treating as signed in.
        if existing is not None and existing.is_linked:
            await message.answer(
                f"{_ALREADY_AUTH[normalize_language(lang)]}\n\n"
                f"{main_menu_text(lang)}",
                reply_markup=build_main_menu_kb(lang),
                parse_mode="Markdown",
            )
            return

        fsm_data = await state.get_data()
        pending_language = fsm_data.get("pending_language")

        # Preserve pending_language across FSM restart.
        await state.set_state(AuthStates.waiting_for_login)
        await state.update_data(
            auth_language=lang,
            pending_language=pending_language,
            auth_login=None,
        )
        await message.answer(
            _ASK_LOGIN[normalize_language(lang)],
            parse_mode="Markdown",
        )

    @router.message(AuthStates.waiting_for_login, F.text, ~F.text.startswith("/"))
    async def process_login(message: Message, state: FSMContext) -> None:
        lang = await _resolve_lang(db, message, state)
        login = (message.text or "").strip()
        if not login:
            await message.answer(_EMPTY_LOGIN[normalize_language(lang)])
            return

        await state.update_data(auth_login=login)
        await state.set_state(AuthStates.waiting_for_password)
        await message.answer(
            _ASK_PASSWORD[normalize_language(lang)],
            parse_mode="Markdown",
        )

    @router.message(AuthStates.waiting_for_password, F.text, ~F.text.startswith("/"))
    async def process_password(message: Message, state: FSMContext) -> None:
        fsm_data = await state.get_data()
        lang = normalize_language(
            fsm_data.get("auth_language")
            or fsm_data.get("pending_language")
            or DEFAULT_LANGUAGE
        )
        login = str(fsm_data.get("auth_login") or "").strip()
        password = (message.text or "").strip()

        if not login:
            await state.set_state(AuthStates.waiting_for_login)
            await message.answer(_ASK_LOGIN[lang], parse_mode="Markdown")
            return

        if not password:
            await message.answer(_EMPTY_PASSWORD[lang])
            return

        chat_id = message.chat.id
        pending_language = fsm_data.get("pending_language")
        status_msg = await message.answer(_CHECKING[lang])

        # Best-effort: remove the message that contains the password.
        try:
            await message.delete()
        except Exception:
            logger.debug("Could not delete password message in chat %s", chat_id)

        try:
            await api.get_access_token(login, password)
            encrypted = crypto.encrypt(password)
        except S21NetworkError as exc:
            logger.warning("Login network error for chat_id=%s: %s", chat_id, exc)
            await status_msg.edit_text(_NETWORK_ERR[lang])
            await state.set_state(AuthStates.waiting_for_password)
            return
        except S21AuthError as exc:
            logger.info("Login rejected for chat_id=%s: %s", chat_id, exc)
            await status_msg.edit_text(_AUTH_ERR[lang])
            await state.set_state(AuthStates.waiting_for_password)
            return
        except ValueError as exc:
            logger.error("Encryption error for chat_id=%s: %s", chat_id, exc)
            await status_msg.edit_text(_ENCRYPT_ERR[lang])
            await state.clear()
            return
        finally:
            # Wipe plaintext from this frame as soon as possible.
            password = ""  # noqa: F841

        await db.upsert_user(chat_id, login, encrypted)
        if pending_language in SUPPORTED_LANGUAGES:
            await db.update_user_language(chat_id, pending_language)
        await state.clear()

        saved = await db.get_user(chat_id)
        lang = (saved.language if saved else None) or DEFAULT_LANGUAGE
        lang = normalize_language(lang)
        try:
            await sync_user_bot_commands(message.bot, chat_id, lang)
        except Exception:
            logger.debug("sync_user_bot_commands after login failed", exc_info=True)

        await status_msg.edit_text(
            f"{_LOGIN_OK[lang]}\n\n{main_menu_text(lang)}",
            reply_markup=build_main_menu_kb(lang),
            parse_mode="Markdown",
        )
        logger.info("User linked: chat_id=%s login=%s", chat_id, login)

    @router.message(Command("logout"))
    async def cmd_logout(message: Message, state: FSMContext) -> None:
        await state.clear()
        chat_id = message.chat.id
        user = await db.get_user(chat_id)
        lang = normalize_language(user.language if user else DEFAULT_LANGUAGE)
        # Keep the users row (and language); only wipe School 21 credentials.
        cleared = await db.clear_user_credentials(chat_id)
        if cleared:
            await message.answer(
                _LOGOUT_OK[lang],
                parse_mode="Markdown",
            )
            logger.info("User logged out: chat_id=%s", chat_id)
        else:
            await message.answer(_LOGOUT_NONE[lang])

    return router
