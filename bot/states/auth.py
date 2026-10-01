"""FSM states for stepwise /login."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AuthStates(StatesGroup):
    """Interactive login: ask login, then password separately."""

    waiting_for_login = State()
    waiting_for_password = State()
