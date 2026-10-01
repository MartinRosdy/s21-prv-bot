"""Main menu router: home / reviews entrypoints."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.db import Database
from bot.database.models import DEFAULT_LANGUAGE, LANG_EN, LANG_RU, LANG_UZ
from bot.keyboards.menu import build_main_menu_kb, main_menu_text, normalize_language
from bot.routers.helpers import AuthRequiredError, require_user
from bot.routers.settings import post_lang_auth_hint


_NEED_AUTH = {
    LANG_EN: "Sign in required",
    LANG_RU: "Нужна авторизация",
    LANG_UZ: "Avtorizatsiya kerak",
}


async def send_main_menu(
    message: Message,
    *,
    language: str | None = None,
    edit: bool = False,
) -> None:
    """Render the main menu as a new message or an edited one."""
    text = main_menu_text(language)
    markup = build_main_menu_kb(language)
    if edit:
        await message.edit_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )
    else:
        await message.answer(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )


def get_menu_router(db: Database) -> Router:
    router = Router(name="menu")

    @router.callback_query(F.data == "menu_home")
    async def cb_menu_home(callback: CallbackQuery, state: FSMContext) -> None:
        """Delete nested message and recreate /start main menu."""
        fsm_data = await state.get_data()
        pending_language = fsm_data.get("pending_language")
        await state.clear()
        if not callback.message:
            await callback.answer()
            return
        try:
            user = await require_user(db, callback.from_user.id)
        except AuthRequiredError:
            lang = normalize_language(pending_language or DEFAULT_LANGUAGE)
            hint = post_lang_auth_hint(lang)
            await callback.answer(_NEED_AUTH[lang], show_alert=True)
            try:
                await callback.message.edit_text(hint, parse_mode="Markdown")
            except Exception:
                await callback.message.answer(hint, parse_mode="Markdown")
            return

        try:
            await callback.message.delete()
        except Exception:
            pass

        await send_main_menu(
            callback.message,
            language=user.language,
            edit=False,
        )
        await callback.answer()

    return router
