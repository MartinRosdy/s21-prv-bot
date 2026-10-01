"""Shared helpers for interactive routers."""

from __future__ import annotations

from typing import Optional

from bot.database.db import Database
from bot.database.models import User
from bot.services.crypto import CryptoService
from bot.services.s21_api import S21ApiClient, S21AuthError, S21NetworkError


class AuthRequiredError(Exception):
    """User is not linked or credentials are invalid."""


class PlatformNetworkError(Exception):
    """Transient platform network failure while obtaining a token."""


async def require_user(db: Database, chat_id: int) -> User:
    user = await db.get_user(chat_id)
    if user is None or not user.is_linked:
        raise AuthRequiredError("not_registered")
    return user


async def get_user_token(
    user: User,
    *,
    crypto: CryptoService,
    api: S21ApiClient,
) -> str:
    """Decrypt password, exchange for Keycloak token, wipe plaintext."""
    password: Optional[str] = None
    try:
        password = crypto.decrypt(user.encrypted_password)
        return await api.get_access_token(user.s21_login, password)
    except S21NetworkError as exc:
        raise PlatformNetworkError(str(exc)) from exc
    except (ValueError, S21AuthError) as exc:
        raise AuthRequiredError(str(exc)) from exc
    finally:
        password = None
        del password
