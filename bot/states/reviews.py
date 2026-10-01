"""FSM states for interactive slot create / reschedule wizard."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class SlotFSM(StatesGroup):
    """Step-by-step date/time picker (inline buttons, no free text)."""

    picking_date = State()
    picking_start_hour = State()
    picking_start_minute = State()
    picking_end_hour = State()
    picking_end_minute = State()
    picking_format = State()
