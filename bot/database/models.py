"""Lightweight data models for DB rows and calendar sync."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Event kinds stored in ``events.type``
EVENT_TYPE_SLOT = "SLOT"
EVENT_TYPE_PEER_REVIEW = "PEER_REVIEW"

# Peer-review kinds the poller tracks (campus activities / exams are ignored).
REVIEW_EVENT_TYPES = frozenset({EVENT_TYPE_SLOT, EVENT_TYPE_PEER_REVIEW})

# User role relative to a peer-review booking / slot.
ROLE_EVALUATOR = "evaluator"  # I check someone
ROLE_EVALUATED = "evaluated"  # Someone checks me
DEFAULT_ROLE = ROLE_EVALUATOR

# Lifecycle statuses stored in ``events.status``
STATUS_OPEN = "OPEN"
STATUS_BOOKED = "BOOKED"
STATUS_CANCELED = "CANCELED"
STATUS_COMPLETED = "COMPLETED"

# Reminder job must skip sending when status is terminal / gone.
REMINDER_BLOCK_STATUSES = frozenset({STATUS_CANCELED, STATUS_COMPLETED})

TERMINAL_BOOKING_STATUSES = frozenset(
    {
        "COMPLETED",
        "CANCELED",
        "CANCELLED",
        "VERIFIER_WAS_ABSENT",
        "STUDENT_WAS_ABSENT",
    }
)


# Supported UI languages (ISO-ish codes stored in ``users.language``).
LANG_EN = "en"
LANG_RU = "ru"
LANG_UZ = "uz"
SUPPORTED_LANGUAGES = frozenset({LANG_EN, LANG_RU, LANG_UZ})
DEFAULT_LANGUAGE = LANG_RU


@dataclass
class User:
    """Bot user row; credentials may be cleared after /logout."""

    telegram_chat_id: int
    s21_login: Optional[str]
    encrypted_password: Optional[str]
    language: str = DEFAULT_LANGUAGE
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def is_linked(self) -> bool:
        """True when School 21 login/password are present."""
        return bool(self.s21_login and self.encrypted_password)


@dataclass
class TrackedEvent:
    """
    Persistent snapshot of one calendar item for a user.

    ``user_id`` is the Telegram chat id (FK → users.telegram_chat_id).
    ``role`` is ``evaluator`` / ``evaluated`` (who am I in this slot).
    ``data`` holds extra payload: peer login, online flag, goal name, etc.
    """

    id: Optional[int]
    user_id: int
    s21_event_id: str
    type: str
    status: str
    start_time: str
    end_time: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)
    role: Optional[str] = None
    is_notified: bool = False
    notified_booking_id: Optional[str] = None

    @property
    def effective_role(self) -> str:
        """Resolve role from column, then ``data``, defaulting to evaluator."""
        if self.role in {ROLE_EVALUATOR, ROLE_EVALUATED}:
            return self.role
        from_data = (self.data or {}).get("role")
        if from_data in {ROLE_EVALUATOR, ROLE_EVALUATED}:
            return str(from_data)
        return DEFAULT_ROLE


@dataclass
class CalendarSnapshotItem:
    """
    Normalized item derived from a single GraphQL calendar event.

    Used by the poller to diff against ``events`` rows.
    """

    s21_event_id: str
    type: str
    status: str
    start_time: str
    end_time: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)
    role: Optional[str] = None
