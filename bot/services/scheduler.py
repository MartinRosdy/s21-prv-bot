"""APScheduler: calendar poller (diff) + precise one-shot reminders."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import timedelta
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot.core.utils import format_datetime, parse_iso_utc, utc_now
from bot.database.db import Database
from bot.database.models import (
    DEFAULT_LANGUAGE,
    EVENT_TYPE_PEER_REVIEW,
    EVENT_TYPE_SLOT,
    REMINDER_BLOCK_STATUSES,
    REVIEW_EVENT_TYPES,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_CANCELED,
    STATUS_COMPLETED,
    STATUS_OPEN,
    CalendarSnapshotItem,
    TrackedEvent,
    User,
)
from bot.services.crypto import CryptoService
from bot.services.s21_api import S21ApiClient, S21ApiError, S21AuthError, S21NetworkError
from bot.services.slot_splitter import calculate_slot_splits

logger = logging.getLogger(__name__)

# Reminder offsets relative to start_time (minutes before).
# Trigger 2 (T-15), Trigger 3 (T-2), Trigger 4 (T-0)
REMINDER_OFFSETS_MINUTES: tuple[int, ...] = (15, 2, 0)


def _split_interval(when: str) -> tuple[str, str]:
    """Split ``format_datetime`` interval into start / end display parts."""
    if " - " in when:
        start_str, end_str = when.split(" - ", 1)
        return start_str, end_str
    return when, "—"


def _peer_display(raw: object | None) -> str:
    """Human-readable peer login for notifications."""
    value = str(raw).strip() if raw else ""
    return value or "неизвестный пир"


def _peer_code_html(raw: object | None) -> str:
    """HTML ``<code>`` wrapper for one-tap copy of the peer login."""
    return f"<code>{html.escape(_peer_display(raw))}</code>"


def _role_peer_line_html(role: str, peer_raw: object | None) -> str:
    """Role-aware peer line with copyable login."""
    code = _peer_code_html(peer_raw)
    if role == ROLE_EVALUATED:
        return f"Твой проверяющий: {code}"
    return f"Твой проверяемый: {code}"


def _build_booked_instant_text(
    role: str,
    peer_raw: object | None,
    when: str,
    is_online: bool | None,
    *,
    language: str | None = None,
) -> str:
    """TRIGGER 1: Instant booking notification for evaluator or evaluated."""
    fmt_str = "🌐 Онлайн" if is_online else "🏢 Офлайн"
    peer_code = _peer_code_html(peer_raw)

    if role == ROLE_EVALUATED:
        return (
            "🔥 <b>Ты записался на проверку!</b>\n\n"
            "Роль: 📖 <b>Проверяемый</b>\n"
            f"Твой проверяющий: {peer_code}\n"
            f"Формат: {fmt_str}\n"
            f"Время: {html.escape(when)}"
        )
    return (
        "🔥 <b>К тебе записались на проверку!</b>\n\n"
        "Роль: 🔍 <b>Проверяющий</b>\n"
        f"Твой проверяемый: {peer_code}\n"
        f"Формат: {fmt_str}\n"
        f"Время: {html.escape(when)}"
    )


def _resolve_role(item: CalendarSnapshotItem | TrackedEvent) -> str:
    if isinstance(item, TrackedEvent):
        return item.effective_role
    role = item.role or (item.data or {}).get("role")
    if role == ROLE_EVALUATED:
        return ROLE_EVALUATED
    return ROLE_EVALUATOR


class PeerReviewScheduler:
    """
    Two responsibilities:

    1. **Poller** (every N seconds) — auth → fetch calendar → slot split check
       → diff against ``events`` table → notify on create / book / cancel.
    2. **Reminders** — when a row becomes BOOKED, schedule one-shot ``date``
       jobs at T-15 / T-2 / T-0 (mirroring both roles).
    """

    def __init__(
        self,
        *,
        bot: Bot,
        db: Database,
        crypto: CryptoService,
        api: S21ApiClient,
        poll_interval_seconds: int = 30,
    ) -> None:
        self.bot = bot
        self._db = db
        self._crypto = crypto
        self._api = api
        self._poll_interval = poll_interval_seconds
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        # Cap concurrent School 21 API calls to avoid HTTP 429.
        self._api_semaphore = asyncio.Semaphore(3)

    def start(self) -> None:
        self._scheduler.add_job(
            self.poll_all_users,
            trigger=IntervalTrigger(seconds=self._poll_interval),
            id="calendar_poll",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        logger.info("Scheduler started (poll interval=%ss)", self._poll_interval)

    async def poll_user(self, chat_id: int) -> None:
        """Force a single-user calendar sync (e.g. after create/delete slot)."""
        user = await self._db.get_user(chat_id)
        if user is None or not user.is_linked:
            return
        try:
            await self._process_user(user)
        except Exception:
            logger.exception(
                "Forced poll failed for chat_id=%s login=%s",
                user.telegram_chat_id,
                user.s21_login,
            )

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    # ------------------------------------------------------------------ poll

    async def poll_all_users(self) -> None:
        """Entry point for the interval poll job."""
        users = await self._db.get_all_users()
        if not users:
            logger.debug("Poll skipped: no registered users")
            return

        logger.info("Polling calendar for %s user(s)", len(users))

        async def _safe_poll(user: User) -> None:
            try:
                await self._process_user(user)
            except Exception:
                logger.exception(
                    "Unhandled error while processing chat_id=%s login=%s",
                    user.telegram_chat_id,
                    user.s21_login,
                )

        # Concurrent per-user polls; semaphore limits API fan-out to 3.
        await asyncio.gather(*(_safe_poll(user) for user in users))

    async def _process_user(self, user: User) -> None:
        password: Optional[str] = None
        snapshot = None
        token: Optional[str] = None

        # Auth + calendarGetEvents share one semaphore slot (max 3 in flight).
        async with self._api_semaphore:
            try:
                password = self._crypto.decrypt(user.encrypted_password)
                token = await self._api.get_access_token(user.s21_login, password)
            except S21NetworkError as exc:
                logger.warning(
                    "Auth network blip for chat_id=%s: %s",
                    user.telegram_chat_id,
                    exc,
                )
                return
            except (ValueError, S21AuthError) as exc:
                logger.warning(
                    "Auth failed for chat_id=%s: %s",
                    user.telegram_chat_id,
                    exc,
                )
                await self._safe_send(
                    user.telegram_chat_id,
                    "⚠️ Не удалось войти в платформу Школы 21. "
                    "Проверь логин и пароль от платформы Школы 21 командой /login",
                )
                return
            finally:
                password = None
                del password

            try:
                snapshot = await self._api.fetch_calendar_events(token)
            except S21ApiError as exc:
                logger.warning(
                    "Calendar fetch failed for chat_id=%s: %s",
                    user.telegram_chat_id,
                    exc,
                )
                return

        # Transient network error — skip this cycle quietly.
        if snapshot is None:
            logger.warning(
                "Skipping reconcile for chat_id=%s (network blip)",
                user.telegram_chat_id,
            )
            return

        # Slot splitting: if user opened a long slot and has an evaluated booking colliding with it
        plan = calculate_slot_splits(snapshot)
        if plan.slots_to_update or plan.slots_to_create or plan.slots_to_delete:
            logger.info(
                "Slot splitting detected for user %s: %s update(s), %s create(s), %s delete(s)",
                user.telegram_chat_id,
                len(plan.slots_to_update),
                len(plan.slots_to_create),
                len(plan.slots_to_delete),
            )
            if token:
                for u in plan.slots_to_update:
                    try:
                        await self._api.update_slot(
                            token,
                            str(u["event_slot_id"]),
                            u["new_start_utc"],
                            u["new_end_utc"],
                        )
                    except Exception as exc:
                        logger.warning("Failed to sync updated split slot on API: %s", exc)

                for c in plan.slots_to_create:
                    try:
                        await self._api.create_slot(
                            token,
                            c["new_start_utc"],
                            c["new_end_utc"],
                        )
                    except Exception as exc:
                        logger.warning("Failed to sync created split slot on API: %s", exc)

                for d in plan.slots_to_delete:
                    try:
                        await self._api.delete_slot(token, str(d["event_slot_id"]))
                    except Exception as exc:
                        logger.warning("Failed to delete split slot on API: %s", exc)

        await self._reconcile(user, plan.normalized_items)

    async def _reconcile(
        self,
        user: User,
        snapshot: list[CalendarSnapshotItem],
    ) -> None:
        """Compare API snapshot with local peer-review rows and apply transitions."""
        user_id = user.telegram_chat_id

        # Silently retire leftover campus events from the removed Events feature.
        for row in await self._db.get_events(user_id, active_only=True):
            if row.type not in REVIEW_EVENT_TYPES and row.id is not None:
                await self._db.update_event_status(row.id, STATUS_CANCELED)
                self._remove_reminder_jobs(row.id)
                logger.info(
                    "Retired legacy campus event user=%s db_id=%s type=%s",
                    user_id,
                    row.id,
                    row.type,
                )

        known = {
            e.s21_event_id: e
            for e in await self._db.get_events(user_id, active_only=True)
            if e.type in REVIEW_EVENT_TYPES
        }
        api_by_id = {item.s21_event_id: item for item in snapshot}
        seen: set[str] = set()
        lang = user.language or DEFAULT_LANGUAGE

        for item in snapshot:
            if item.type not in REVIEW_EVENT_TYPES:
                continue
            seen.add(item.s21_event_id)
            existing = known.get(item.s21_event_id)
            await self._apply_item(user, item, existing)

        # Disappeared from API → cancelled by platform.
        for s21_id, row in known.items():
            if s21_id in seen:
                continue
            logger.info(
                "Slot vanished from API: user=%s s21_id=%s db_id=%s",
                user_id,
                s21_id,
                row.id,
            )
            if row.id is not None:
                await self._db.update_event_status(row.id, STATUS_CANCELED)
                self._remove_reminder_jobs(row.id)
            start_str, end_str = _split_interval(
                format_datetime(row.start_time, row.end_time, language=lang)
            )
            await self._safe_send(
                user.telegram_chat_id,
                f"🗑 Слот на {start_str} - {end_str} был удален",
                parse_mode=None,
            )

        await self._complete_past_events(user_id, api_by_id)

    def _tracked_from_item(
        self,
        user_id: int,
        item: CalendarSnapshotItem,
        *,
        event_id: Optional[int] = None,
        event_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> TrackedEvent:
        role = _resolve_role(item)
        data = dict(item.data or {})
        data["role"] = role
        return TrackedEvent(
            id=event_id,
            user_id=user_id,
            s21_event_id=item.s21_event_id,
            type=event_type or item.type,
            status=status or item.status,
            start_time=item.start_time,
            end_time=item.end_time,
            data=data,
            role=role,
        )

    async def _apply_item(
        self,
        user: User,
        item: CalendarSnapshotItem,
        existing: Optional[TrackedEvent],
    ) -> None:
        user_id = user.telegram_chat_id
        lang = user.language or DEFAULT_LANGUAGE
        when = format_datetime(item.start_time, item.end_time, language=lang)
        role = _resolve_role(item)
        is_online = item.data.get("is_online")

        # --- brand-new open slot -------------------------------------------
        if existing is None and item.type == EVENT_TYPE_SLOT and item.status == STATUS_OPEN:
            saved = await self._db.upsert_event(
                self._tracked_from_item(user_id, item)
            )
            logger.info(
                "New OPEN slot user=%s s21_id=%s db_id=%s role=%s",
                user_id,
                item.s21_event_id,
                saved.id,
                role,
            )
            start_str, end_str = _split_interval(when)
            fmt_str = "🌐 Онлайн" if is_online else "🏢 Офлайн"
            await self._safe_send(
                user.telegram_chat_id,
                f"⏳ Создан новый слот ({fmt_str}): {start_str} - {end_str}. Ждем пира",
                parse_mode=None,
            )
            return

        # --- TRIGGER 1: OPEN → BOOKED (peer signed up) ---------------------
        if (
            existing is not None
            and existing.status == STATUS_OPEN
            and item.status == STATUS_BOOKED
            and item.type == EVENT_TYPE_PEER_REVIEW
        ):
            peer_raw = item.data.get("peer_login")
            saved = await self._db.upsert_event(
                self._tracked_from_item(
                    user_id,
                    item,
                    event_id=existing.id,
                    event_type=EVENT_TYPE_PEER_REVIEW,
                    status=STATUS_BOOKED,
                )
            )
            logger.info(
                "Slot BOOKED user=%s s21_id=%s peer=%s role=%s",
                user_id,
                item.s21_event_id,
                peer_raw,
                role,
            )
            start_str, end_str = _split_interval(when)
            text = _build_booked_instant_text(
                role,
                peer_raw,
                f"{start_str} - {end_str}",
                is_online,
                language=lang,
            )
            await self._safe_send(user_id, text, parse_mode="HTML")
            self._schedule_reminders(saved)
            return

        # --- TRIGGER 1: already-booked peer review first seen --------------
        if (
            existing is None
            and item.type == EVENT_TYPE_PEER_REVIEW
            and item.status == STATUS_BOOKED
        ):
            peer_raw = item.data.get("peer_login")
            saved = await self._db.upsert_event(
                self._tracked_from_item(user_id, item)
            )
            logger.info(
                "New BOOKED peer-review user=%s s21_id=%s role=%s",
                user_id,
                item.s21_event_id,
                role,
            )
            start_str, end_str = _split_interval(when)
            text = _build_booked_instant_text(
                role,
                peer_raw,
                f"{start_str} - {end_str}",
                is_online,
                language=lang,
            )
            await self._safe_send(user_id, text, parse_mode="HTML")
            self._schedule_reminders(saved)
            return

        # --- refresh metadata for already-tracked active rows --------------
        if existing is not None and existing.status in {STATUS_OPEN, STATUS_BOOKED}:
            time_changed = (
                existing.start_time != item.start_time
                or existing.end_time != item.end_time
            )
            meta_changed = (
                existing.data != item.data
                or existing.type != item.type
                or existing.status != item.status
                or existing.effective_role != role
            )
            if time_changed or meta_changed:
                saved = await self._db.upsert_event(
                    self._tracked_from_item(
                        user_id,
                        item,
                        event_id=existing.id,
                    )
                )
                if time_changed:
                    logger.info(
                        "Slot time changed user=%s s21_id=%s db_id=%s "
                        "old_start=%s new_start=%s",
                        user_id,
                        item.s21_event_id,
                        existing.id,
                        existing.start_time,
                        item.start_time,
                    )
                    new_start, new_end = _split_interval(when)
                    await self._safe_send(
                        user_id,
                        "🔄 Время слота было изменено на платформе.\n"
                        f"Новое время: {new_start} - {new_end}",
                        parse_mode=None,
                    )
                    # Reschedule reminders if the booked slot moved.
                    if saved.status == STATUS_BOOKED and saved.id is not None:
                        self._remove_reminder_jobs(saved.id)
                        self._schedule_reminders(saved)

    def _remove_reminder_jobs(self, event_db_id: int) -> None:
        """Drop all one-shot reminder jobs for a vanished / canceled event."""
        for minutes_before in REMINDER_OFFSETS_MINUTES:
            job_id = f"reminder_{event_db_id}_{minutes_before}"
            try:
                self._scheduler.remove_job(job_id)
                logger.info("Removed reminder job_id=%s", job_id)
            except JobLookupError:
                pass

    async def _complete_past_events(
        self,
        user_id: int,
        api_by_id: dict[str, CalendarSnapshotItem],
    ) -> None:
        """Mark BOOKED rows whose start_time is in the past as COMPLETED."""
        now = utc_now()
        active = await self._db.get_events(user_id, active_only=True)
        for row in active:
            if row.status != STATUS_BOOKED or row.id is None:
                continue
            if row.type not in REVIEW_EVENT_TYPES:
                continue
            try:
                start = parse_iso_utc(row.start_time)
            except ValueError:
                continue
            if start + timedelta(minutes=1) < now and row.s21_event_id in api_by_id:
                await self._db.update_event_status(row.id, STATUS_COMPLETED)
                logger.info(
                    "Marked COMPLETED user=%s db_id=%s (start passed)",
                    user_id,
                    row.id,
                )

    # -------------------------------------------------------------- reminders

    def _schedule_reminders(self, event: TrackedEvent) -> None:
        """Register up to 3 one-shot reminder jobs for a BOOKED row."""
        if event.id is None:
            logger.warning("Cannot schedule reminders without DB id")
            return

        try:
            start = parse_iso_utc(event.start_time)
        except ValueError:
            logger.warning(
                "Bad start_time for reminders db_id=%s value=%r",
                event.id,
                event.start_time,
            )
            return

        now = utc_now()
        for minutes_before in REMINDER_OFFSETS_MINUTES:
            run_at = start - timedelta(minutes=minutes_before)
            if run_at <= now:
                logger.info(
                    "Skip reminder db_id=%s T-%s (already past)",
                    event.id,
                    minutes_before,
                )
                continue

            job_id = f"reminder_{event.id}_{minutes_before}"
            self._scheduler.add_job(
                self.send_reminder,
                trigger=DateTrigger(run_date=run_at),
                id=job_id,
                replace_existing=True,
                kwargs={
                    "event_db_id": event.id,
                    "minutes_before": minutes_before,
                },
                misfire_grace_time=120,
            )
            logger.info(
                "Scheduled reminder job_id=%s run_at=%s",
                job_id,
                run_at.isoformat(),
            )

    async def send_reminder(self, event_db_id: int, minutes_before: int) -> None:
        """
        One-shot reminder handler for T-15, T-2, T-0.

        Phantom guard: re-read DB; skip if missing / CANCELED / COMPLETED.
        """
        row = await self._db.get_event_by_id(event_db_id)
        if row is None:
            logger.info("Reminder skipped: event %s deleted", event_db_id)
            return
        if row.status in REMINDER_BLOCK_STATUSES:
            logger.info(
                "Reminder skipped: event %s status=%s",
                event_db_id,
                row.status,
            )
            return

        user = await self._db.get_user(row.user_id)
        lang = (user.language if user else None) or DEFAULT_LANGUAGE
        text = self._build_reminder_text(row, minutes_before, language=lang)
        await self._safe_send(row.user_id, text, parse_mode="HTML")
        logger.info(
            "Reminder sent user=%s db_id=%s T-%s",
            row.user_id,
            event_db_id,
            minutes_before,
        )

        if minutes_before == 0 and row.id is not None:
            await self._db.update_event_status(row.id, STATUS_COMPLETED)

    def _build_reminder_text(
        self,
        row: TrackedEvent,
        minutes_before: int,
        *,
        language: str | None = None,
    ) -> str:
        """
        Compose reminder copy depending on event type, offset, and role.
        Symmetrical for both evaluator (🔍) and evaluated (📖).
        """
        lang = language or DEFAULT_LANGUAGE
        role = row.effective_role
        when = html.escape(
            format_datetime(row.start_time, row.end_time, language=lang)
        )
        peer_raw = row.data.get("peer_login")
        peer_code = _peer_code_html(peer_raw)
        is_online = bool(row.data.get("is_online"))
        fmt_str = "🌐 Онлайн" if is_online else "🏢 Офлайн"

        if role == ROLE_EVALUATED:
            role_icon = "📖"
            role_name = "Проверяемый"
            counterpart = f"Твой проверяющий: {peer_code}"
        else:
            role_icon = "🔍"
            role_name = "Проверяющий"
            counterpart = f"Твой проверяемый: {peer_code}"

        role_line = f"Роль: {role_icon} <b>{role_name}</b>"

        # TRIGGER 2: T-15 minutes
        if minutes_before == 15:
            return (
                "⏳ <b>Пир-ревью начнется через 15 минут!</b>\n\n"
                f"{role_line}\n"
                f"{counterpart}\n"
                f"Формат: {fmt_str}\n"
                f"Время: {when}"
            )

        # TRIGGER 3: T-2 minutes
        if minutes_before == 2:
            return (
                "🔔 <b>Напоминание: Пир-Ревью через 2 минуты!</b>\n\n"
                f"{role_line}\n"
                f"{counterpart}\n"
                f"Формат: {fmt_str}\n"
                f"Время: {when}"
            )

        # TRIGGER 4: T-0 minutes (Start moment)
        if minutes_before == 0:
            return (
                "🚀 <b>Пир-Ревью начинается прямо сейчас!</b>\n\n"
                f"{role_line}\n"
                f"{counterpart}\n"
                f"Формат: {fmt_str}\n"
                f"Время: {when}"
            )

        return (
            f"🔔 <b>Напоминание: Пир-Ревью через {minutes_before} минут!</b>\n\n"
            f"{role_line}\n"
            f"{counterpart}\n"
            f"Формат: {fmt_str}\n"
            f"Время: {when}"
        )

    # ------------------------------------------------------------------ send

    async def _safe_send(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: Optional[str] = "Markdown",
    ) -> None:
        try:
            await self.bot.send_message(
                chat_id,
                text,
                parse_mode=parse_mode,
            )
        except TelegramAPIError as exc:
            logger.warning("Failed to send message to %s: %s", chat_id, exc)
