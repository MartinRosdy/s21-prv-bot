"""Shared helpers: datetime parsing and localized formatting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# Month names keyed by UI language (1-indexed; index 0 unused).
# Never use strftime %B — it follows the OS locale, not user.language.
MONTHS: dict[str, tuple[str, ...]] = {
    "en": (
        "",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ),
    "ru": (
        "",
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ),
    "uz": (
        "",
        "yanvar",
        "fevral",
        "mart",
        "aprel",
        "may",
        "iyun",
        "iyul",
        "avgust",
        "sentabr",
        "oktabr",
        "noyabr",
        "dekabr",
    ),
}
_DEFAULT_LANG = "ru"

TASHKENT_TZ = ZoneInfo("Asia/Tashkent")
UTC = timezone.utc


def _months_for(language: str | None) -> tuple[str, ...]:
    lang = language if language in MONTHS else _DEFAULT_LANG
    return MONTHS[lang]



def parse_iso_utc(value: str | datetime) -> datetime:
    """
    Parse an ISO 8601 UTC timestamp into an aware datetime.

    Accepts strings like ``2026-09-30T08:30:00Z`` / ``2026-09-30T08:30:00.000Z``
    or an already-parsed datetime.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        normalized = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_tashkent(dt: datetime | str) -> datetime:
    """Convert a UTC (or naive-as-UTC) datetime to Asia/Tashkent."""
    return parse_iso_utc(dt).astimezone(TASHKENT_TZ)


def format_datetime(
    value: str | datetime,
    end: Optional[str | datetime] = None,
    *,
    language: str | None = None,
) -> str:
    """
    Format an API UTC timestamp (optionally an interval) for the user.

    Month name follows ``language`` (ru / en / uz). Always lowercase.
    Examples (Asia/Tashkent):
    - ``1 октября, 14:00`` / ``1 october, 14:00`` / ``1 oktabr, 14:00``
    - same day → ``1 октября, 14:30 - 18:30``
    - spanning midnight → ``1 октября, 23:00 - 2 октября, 01:00``
    """
    months = _months_for(language)
    local = to_tashkent(value)
    month = months[local.month].lower()
    start_part = f"{local.day} {month}, {local.hour:02d}:{local.minute:02d}"

    if end is None:
        return start_part

    local_end = to_tashkent(end)
    end_clock = f"{local_end.hour:02d}:{local_end.minute:02d}"

    # Same calendar day → keep the date once.
    if local.date() == local_end.date():
        return f"{start_part} - {end_clock}"

    # Different dates → repeat the date on the end side.
    end_month = months[local_end.month].lower()
    return (
        f"{start_part} - "
        f"{local_end.day} {end_month}, {end_clock}"
    )


# Backwards-compatible alias (defaults to Russian).
def format_datetime_ru(
    value: str | datetime,
    end: Optional[str | datetime] = None,
) -> str:
    return format_datetime(value, end, language="ru")


def utc_now() -> datetime:
    """Current time in UTC (timezone-aware)."""
    return datetime.now(UTC)


def utc_iso(dt: datetime | None = None) -> str:
    """
    Serialize a datetime as ISO 8601 UTC with millisecond precision.

    Example: ``2026-09-30T08:30:00.000Z`` (required by School 21 GraphQL).
    """
    value = (dt or utc_now()).astimezone(UTC).replace(microsecond=0)
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def days_from_now(days: int) -> datetime:
    """UTC datetime offset by ``days`` from now."""
    return utc_now() + timedelta(days=days)


def escape_md(text: str) -> str:
    """Escape characters that break Telegram classic Markdown."""
    return (
        text.replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )
