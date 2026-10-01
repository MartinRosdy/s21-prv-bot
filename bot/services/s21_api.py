"""Async School 21 API client: Keycloak auth + GraphQL calendar."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import aiohttp

from bot.core.utils import days_from_now, utc_iso, utc_now
from bot.database.models import (
    DEFAULT_ROLE,
    EVENT_TYPE_PEER_REVIEW,
    EVENT_TYPE_SLOT,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_OPEN,
    TERMINAL_BOOKING_STATUSES,
    CalendarSnapshotItem,
)

logger = logging.getLogger(__name__)

# Browser-like UA required by the platform WAF / CDN.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Strict GraphQL URL — School 21 balancer validates operation query param.
CALENDAR_OPERATION_URL = (
    "https://platform.21-school.ru/services/graphql"
    "?operation=calendarGetEvents"
)

# ---------------------------------------------------------------------------
# GraphQL fragments — MUST match the School 21 frontend query string exactly.
# Server returns HTTP 400 if activity / exam / penalty fields are stripped.
# Python parsers ignore unused fields; the wire payload must still include them.
# ---------------------------------------------------------------------------
GRAPHQL_FRAGMENTS = """
fragment CalendarEvent on CalendarEvent {
  id
  start
  end
  description
  eventType
  eventCode
  eventSlots { id type start end event { eventUserRole __typename } school { shortName __typename } __typename }
  bookings { ...CalendarReviewBooking __typename }
  exam { ...CalendarEventExam __typename }
  studentCodeReview { studentGoalId __typename }
  activity {
    ...CalendarEventActivity
    studentFeedback { id rating comment isEmpty __typename }
    status activityType isMandatory isWaitListActive isVisible
    comments { type createTs comment __typename }
    organizers { id login __typename }
    __typename
  }
  goals { goalId goalName __typename }
  penalty { ...Penalty __typename }
  __typename
}
fragment CalendarReviewBooking on CalendarBooking {
  id answerId eventSlotId task { id goalId goalName studentTaskAdditionalAttributes { cookiesCount __typename } assignmentType __typename }
  eventSlot { id start end event { eventUserRole eventCode __typename } school { shortName __typename } __typename }
  verifierUser { ...CalendarReviewUser __typename }
  verifiableInfo { verifiableStudents { ...VerifiableStudentItem __typename } team { name __typename } __typename }
  bookingStatus isOnline vcLinkUrl additionalChecklist { filledChecklistId filledChecklistStatusRecordingEnum __typename }
  __typename
}
fragment CalendarReviewUser on User { id login __typename }
fragment VerifiableStudentItem on VerifiableStudent { userId login avatarUrl levelCode isTeamLead cookiesCount codeReviewPoints school { shortName __typename } __typename }
fragment CalendarEventExam on Exam { examId eventId beginDate endDate name location currentStudentsCount maxStudentCount updateDate goalId goalName isWaitListActive isInWaitList stopRegisterDate __typename }
fragment CalendarEventActivity on ActivityEvent { activityEventId eventId name beginDate endDate isRegistered description currentStudentsCount maxStudentCount location updateDate isWaitListActive isInWaitList stopRegisterDate __typename }
fragment Penalty on Penalty { comment id duration status startTime createTime penaltySlot { currentStudentsCount description duration startTime id endTime __typename } reasonId __typename }
""".strip()

CALENDAR_GET_EVENTS_QUERY = f"""query calendarGetEvents($from: DateTime!, $to: DateTime!) {{
  calendarEventS21 {{
    getMyCalendarEvents(from: $from, to: $to) {{
      ...CalendarEvent
      __typename
    }}
    __typename
  }}
}}

{GRAPHQL_FRAGMENTS}"""


class S21AuthError(Exception):
    """Raised when Keycloak rejects credentials or returns no token."""


class S21NetworkError(Exception):
    """
    Transient transport failure (reset by peer, timeout, DNS, etc.).

    Callers should log and retry later — do NOT treat as bad credentials.
    """


class S21ApiError(Exception):
    """Raised on GraphQL / HTTP failures against the platform."""


# Network blips we must never crash the bot on.
_NETWORK_ERRORS = (
    aiohttp.ClientOSError,  # e.g. [Errno 54] Connection reset by peer
    aiohttp.ClientError,
    asyncio.TimeoutError,
    OSError,
)


class S21ApiClient:
    """
    Stateless-ish client: one shared ``aiohttp.ClientSession``.

    Passwords are never stored on the instance — callers pass them only
    into ``get_access_token`` and discard them afterwards.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        auth_url: str,
        graphql_url: str,
        school_id: str,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._session = session
        self._auth_url = auth_url
        # Prefer configured base, but calendar calls always use the strict
        # operation URL required by the balancer.
        self._graphql_url = graphql_url.rstrip("?")
        self._school_id = school_id
        self._user_agent = user_agent

    async def get_access_token(self, username: str, password: str) -> str:
        """
        Exchange School 21 login/password for a Keycloak access_token.

        Payload is application/x-www-form-urlencoded (OpenID password grant).
        """
        form = {
            "client_id": "s21-open-api",
            "grant_type": "password",
            "username": username,
            "password": password,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": self._user_agent,
        }

        try:
            async with self._session.post(
                self._auth_url,
                data=form,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                body: dict[str, Any] = await resp.json(content_type=None)
                if resp.status != 200:
                    error = (
                        body.get("error_description")
                        or body.get("error")
                        or resp.reason
                    )
                    raise S21AuthError(
                        f"Keycloak auth failed ({resp.status}): {error}"
                    )

                token = body.get("access_token")
                if not token:
                    raise S21AuthError("Keycloak response has no access_token")
                return str(token)
        except S21AuthError:
            raise
        except _NETWORK_ERRORS as exc:
            # Connection reset / timeout — not a wrong password.
            raise S21NetworkError(f"Keycloak network error: {exc}") from exc

    async def fetch_calendar_events(
        self,
        access_token: str,
        *,
        days_ahead: int = 7,
    ) -> Optional[list[CalendarSnapshotItem]]:
        """
        Fetch calendar via strict ``calendarGetEvents`` operation.

        Returns normalized snapshot items, or ``None`` on transient network
        failures so the poller can skip the cycle without crashing.
        """
        body = await self._post_calendar(access_token, days_ahead=days_ahead)
        if body is None:
            return None

        if body.get("errors"):
            messages = "; ".join(
                str(e.get("message", e)) for e in body["errors"]
            )
            raise S21ApiError(f"GraphQL errors: {messages}")

        raw_events = self._extract_events_list(body.get("data") or {})
        logger.info("Calendar returned %s raw event(s)", len(raw_events))

        items: list[CalendarSnapshotItem] = []
        for raw in raw_events:
            items.extend(self._parse_calendar_event(raw))
        logger.info("Normalized to %s snapshot item(s)", len(items))
        return items

    async def _post_calendar(
        self,
        access_token: str,
        *,
        days_ahead: int,
    ) -> Optional[dict[str, Any]]:
        """
        POST calendarGetEvents. Network blips → WARNING + ``None``.
        HTTP / JSON protocol errors → ``S21ApiError``.
        """
        now = utc_now()
        variables = {
            "from": utc_iso(now),
            "to": utc_iso(days_from_now(days_ahead)),
        }
        payload = {
            "operationName": "calendarGetEvents",
            "variables": variables,
            "query": CALENDAR_GET_EVENTS_QUERY,
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "schoolId": self._school_id,
            "User-Agent": self._user_agent,
        }

        logger.info(
            "GraphQL calendarGetEvents from=%s to=%s",
            variables["from"],
            variables["to"],
        )

        try:
            async with self._session.post(
                CALENDAR_OPERATION_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as resp:
                text = await resp.text()
                try:
                    body: Any = json.loads(text) if text else None
                except json.JSONDecodeError:
                    body = None

                if resp.status != 200:
                    preview = (text or "")[:300]
                    raise S21ApiError(
                        f"GraphQL HTTP {resp.status}: {preview or body!r}"
                    )
        except _NETWORK_ERRORS as exc:
            # Balancer blips, timeouts, ClientOSError reset-by-peer, etc.
            logger.warning("Calendar request network error: %s", exc)
            return None

        if not isinstance(body, dict):
            raise S21ApiError("GraphQL response is not a JSON object")
        return body

    # -------------------------------------------------------------- mutations

    def _graphql_operation_url(self, operation: str) -> str:
        """Build balancer-safe GraphQL URL with ``?operation=…``."""
        base = self._graphql_url.split("?", 1)[0].rstrip("/")
        return f"{base}?operation={operation}"

    async def _post_mutation(
        self,
        token: str,
        *,
        operation_name: str,
        variables: dict[str, Any],
        query: str,
    ) -> Any:
        """
        POST a GraphQL mutation.

        Raises ``S21ApiError`` on HTTP / GraphQL errors.
        Raises ``S21NetworkError`` on transient transport failures.
        """
        payload = {
            "operationName": operation_name,
            "variables": variables,
            "query": query,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "schoolId": self._school_id,
            "User-Agent": self._user_agent,
        }
        url = self._graphql_operation_url(operation_name)
        # Raw payload dump — critical for diagnosing delete/update rejections.
        logger.info(
            "GraphQL mutation POST url=%s payload=%s",
            url,
            json.dumps(payload, ensure_ascii=False, default=str),
        )

        try:
            async with self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as resp:
                text = await resp.text()
                try:
                    body: Any = json.loads(text) if text else None
                except json.JSONDecodeError:
                    body = None

                if resp.status != 200:
                    preview = (text or "")[:300]
                    raise S21ApiError(
                        f"GraphQL HTTP {resp.status}: {preview or body!r}"
                    )
        except _NETWORK_ERRORS as exc:
            raise S21NetworkError(
                f"GraphQL mutation network error ({operation_name}): {exc}"
            ) from exc

        if not isinstance(body, dict):
            raise S21ApiError("GraphQL mutation response is not a JSON object")
        if body.get("errors"):
            messages = "; ".join(
                str(e.get("message", e)) for e in body["errors"]
            )
            raise S21ApiError(f"GraphQL errors: {messages}")
        return body.get("data")

    async def create_slot(
        self,
        token: str,
        start_time_utc: str,
        end_time_utc: str,
    ) -> Any:
        """Create an open peer-review duty slot on the platform."""
        # Avoid f-strings here: GraphQL braces `{}` collide with f-string syntax.
        query_str = (
            """mutation calendarAddEvent($start: DateTime!, $end: DateTime!) {
  student {
    addEventToTimetable(start: $start, end: $end) {
      ...CalendarEvent
      __typename
    }
    __typename
  }
}
"""
            + GRAPHQL_FRAGMENTS
        )
        return await self._post_mutation(
            token,
            operation_name="calendarAddEvent",
            variables={"start": start_time_utc, "end": end_time_utc},
            query=query_str,
        )

    @staticmethod
    def _as_slot_id_int(slot_id: str | int) -> int:
        """
        Platform expects numeric eventSlotId (Integer), not a string.

        GraphQL ``ID`` often arrives as str from JSON — cast before POST.
        """
        try:
            return int(str(slot_id).strip())
        except (TypeError, ValueError) as exc:
            raise S21ApiError(f"Invalid eventSlotId: {slot_id!r}") from exc

    async def update_slot(
        self,
        token: str,
        slot_id: str,
        new_start_utc: str,
        new_end_utc: str,
    ) -> Any:
        """Reschedule an existing open slot."""
        # Concatenate fragments with `+` — never f-strings with GraphQL `{}`.
        query_str = (
            """mutation calendarChangeEventSlot($id: ID!, $start: DateTime!, $end: DateTime!) {
  student {
    changeEventSlot(eventSlotId: $id, start: $start, end: $end) {
      ...CalendarEvent
      __typename
    }
    __typename
  }
}
"""
            + GRAPHQL_FRAGMENTS
        )
        payload_vars = {
            "id": self._as_slot_id_int(slot_id),
            "start": new_start_utc,
            "end": new_end_utc,
        }
        return await self._post_mutation(
            token,
            operation_name="calendarChangeEventSlot",
            variables=payload_vars,
            query=query_str,
        )

    async def delete_slot(self, token: str, slot_id: str) -> Any:
        """Delete an open slot by platform eventSlotId."""
        # Plain string only — f-strings corrupt GraphQL braces / variables.
        query_str = """mutation calendarDeleteEventSlot($eventSlotId: ID!) {
  student {
    deleteEventSlot(eventSlotId: $eventSlotId)
    __typename
  }
}"""
        return await self._post_mutation(
            token,
            operation_name="calendarDeleteEventSlot",
            variables={"eventSlotId": self._as_slot_id_int(slot_id)},
            query=query_str,
        )

    async def toggle_online(
        self,
        token: str,
        booking_id: str,
        is_online: bool,
    ) -> Any:
        """Switch peer-review booking between online and offline."""
        logger.info(
            "toggle_online stub booking_id=%s is_online=%s",
            booking_id,
            is_online,
        )
        raise NotImplementedError("Нужно вставить оригинальный GraphQL payload")

    # ------------------------------------------------------------------ parse

    @staticmethod
    def _extract_events_list(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract ``getMyCalendarEvents`` list from the nested response."""
        calendar = data.get("calendarEventS21") or data.get("calendar") or {}
        if isinstance(calendar, dict):
            value = calendar.get("getMyCalendarEvents")
            if isinstance(value, list):
                return value
            for key in ("getEvents", "getMyAgendaEvents"):
                alt = calendar.get(key)
                if isinstance(alt, list):
                    return alt

        if isinstance(data.get("calendarGetEvents"), list):
            return data["calendarGetEvents"]
        return []

    def _parse_calendar_event(
        self,
        raw: dict[str, Any],
    ) -> list[CalendarSnapshotItem]:
        """
        Map one GraphQL CalendarEvent into peer-review snapshot items.

        Only open slots and booked peer-reviews are tracked.
        """
        event_id = str(raw.get("id") or "").strip()
        if not event_id:
            return []

        results: list[CalendarSnapshotItem] = []

        bookings = raw.get("bookings") or []
        active_bookings = [
            b
            for b in bookings
            if isinstance(b, dict) and self._is_active_booking(b)
        ]

        if active_bookings:
            for booking in active_bookings:
                peer = self._parse_booking(raw, booking, event_id=event_id)
                if peer is not None:
                    results.append(peer)
            return results

        if self._looks_like_open_slot(raw):
            start, end = self._slot_times(raw)
            if start:
                event_slot_id = self._extract_event_slot_id(raw) or event_id
                role = self._extract_open_slot_role(raw)
                results.append(
                    CalendarSnapshotItem(
                        s21_event_id=event_id,
                        type=EVENT_TYPE_SLOT,
                        status=STATUS_OPEN,
                        start_time=start,
                        end_time=end,
                        role=role,
                        data={
                            "description": raw.get("description"),
                            "event_code": raw.get("eventCode"),
                            "event_type": raw.get("eventType"),
                            # Mutations need eventSlotId, not CalendarEvent.id.
                            "event_slot_id": event_slot_id,
                            "role": role,
                            "peer_login": None,
                        },
                    )
                )

        return results

    def _parse_booking(
        self,
        raw: dict[str, Any],
        booking: dict[str, Any],
        *,
        event_id: str,
    ) -> Optional[CalendarSnapshotItem]:
        booking_id = str(booking.get("id") or "").strip()
        slot = booking.get("eventSlot") or {}
        start = (
            (slot.get("start") if isinstance(slot, dict) else None)
            or raw.get("start")
            or ""
        )
        if not start:
            return None
        end = (
            (slot.get("end") if isinstance(slot, dict) else None)
            or raw.get("end")
        )

        role, peer_login = self._extract_role_and_peer(booking)
        is_online = bool(booking.get("isOnline", False))
        goal_name = None
        task = booking.get("task")
        if isinstance(task, dict):
            goal_name = task.get("goalName")

        event_slot_id = None
        if isinstance(slot, dict) and slot.get("id") is not None:
            event_slot_id = str(slot["id"])
        elif booking.get("eventSlotId") is not None:
            event_slot_id = str(booking["eventSlotId"])

        return CalendarSnapshotItem(
            s21_event_id=event_id,
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time=str(start),
            end_time=str(end) if end else None,
            role=role,
            data={
                "peer_login": peer_login,
                "role": role,
                "is_online": is_online,
                "booking_id": booking_id or None,
                "booking_status": booking.get("bookingStatus"),
                "vc_link": booking.get("vcLinkUrl"),
                "goal_name": goal_name,
                "name": goal_name or "Пир-Ревью",
                "event_slot_id": event_slot_id,
            },
        )

    @staticmethod
    def _is_active_booking(booking: dict[str, Any]) -> bool:
        status = str(
            booking.get("bookingStatus") or booking.get("status") or ""
        ).upper()
        if not status:
            return True
        return status not in TERMINAL_BOOKING_STATUSES

    @staticmethod
    def _looks_like_open_slot(raw: dict[str, Any]) -> bool:
        """True when this calendar row is an empty peer-review / duty slot."""
        slots = raw.get("eventSlots")
        if isinstance(slots, list) and len(slots) > 0:
            return True

        if "bookings" in raw:
            return True

        code = str(raw.get("eventCode") or "").upper()
        description = str(raw.get("description") or "").upper()
        event_type = str(raw.get("eventType") or "").upper()
        markers = (
            "CHECK",
            "REVIEW",
            "PEER",
            "VERIFIER",
            "BOOKING",
            "SLOT",
            "ПИР",
            "ПРОВЕРК",
            "ДЕЖУР",
        )
        haystack = f"{code} {description} {event_type}"
        return any(marker in haystack for marker in markers)

    @staticmethod
    def _slot_times(raw: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        start = str(raw["start"]) if raw.get("start") else None
        end = str(raw["end"]) if raw.get("end") else None
        if start:
            return start, end

        slots = raw.get("eventSlots") or []
        if isinstance(slots, list):
            for slot in slots:
                if isinstance(slot, dict) and slot.get("start"):
                    return (
                        str(slot["start"]),
                        str(slot["end"]) if slot.get("end") else None,
                    )
        return None, None

    @staticmethod
    def _extract_event_slot_id(raw: dict[str, Any]) -> Optional[str]:
        """Return ``eventSlots[0].id`` when present (needed for delete/update)."""
        slots = raw.get("eventSlots") or []
        if not isinstance(slots, list):
            return None
        for slot in slots:
            if isinstance(slot, dict) and slot.get("id") is not None:
                return str(slot["id"])
        return None

    @staticmethod
    def _event_user_role(container: Any) -> str:
        """Uppercased ``eventUserRole`` from a nested ``event`` object."""
        if not isinstance(container, dict):
            return ""
        event = container.get("event")
        if not isinstance(event, dict):
            return ""
        return str(event.get("eventUserRole") or "").upper()

    @classmethod
    def _extract_open_slot_role(cls, raw: dict[str, Any]) -> str:
        """Role for an empty duty slot (defaults to evaluator)."""
        slots = raw.get("eventSlots") or []
        if isinstance(slots, list):
            for slot in slots:
                role_raw = cls._event_user_role(slot)
                if role_raw == "EVALUATED":
                    return ROLE_EVALUATED
                if role_raw == "EVALUATOR":
                    return ROLE_EVALUATOR
        return DEFAULT_ROLE

    @classmethod
    def _extract_role_and_peer(
        cls,
        booking: dict[str, Any],
    ) -> tuple[str, Optional[str]]:
        """
        Resolve my role and the counterpart peer login.

        - EVALUATOR → I check; peer = verifiableStudents[0].login
        - EVALUATED → I am checked; peer = verifierUser.login
        Peer may be ``None`` when not assigned yet.
        """
        role_raw = cls._event_user_role(booking.get("eventSlot"))
        if not role_raw:
            # Some payloads nest role on the booking itself.
            event = booking.get("event")
            if isinstance(event, dict):
                role_raw = str(event.get("eventUserRole") or "").upper()

        if role_raw == "EVALUATOR":
            return ROLE_EVALUATOR, cls._peer_from_verifiable(booking)
        if role_raw == "EVALUATED":
            return ROLE_EVALUATED, cls._peer_from_verifier(booking)

        # Legacy / unknown role: best-effort peer detection.
        peer = cls._peer_from_verifiable(booking) or cls._peer_from_verifier(
            booking
        )
        return DEFAULT_ROLE, peer

    @staticmethod
    def _peer_from_verifiable(booking: dict[str, Any]) -> Optional[str]:
        info = booking.get("verifiableInfo") or {}
        if not isinstance(info, dict):
            return None
        students = info.get("verifiableStudents") or []
        if not isinstance(students, list) or not students:
            return None
        first = students[0]
        if isinstance(first, dict) and first.get("login"):
            return str(first["login"])
        return None

    @staticmethod
    def _peer_from_verifier(booking: dict[str, Any]) -> Optional[str]:
        verifier = booking.get("verifierUser")
        if isinstance(verifier, dict) and verifier.get("login"):
            return str(verifier["login"])
        return None

    @classmethod
    def _extract_peer_login(cls, booking: dict[str, Any]) -> Optional[str]:
        """Backward-compatible peer resolver (role-aware when possible)."""
        _role, peer = cls._extract_role_and_peer(booking)
        if peer:
            return peer
        for group_key in ("verifiers", "students"):
            group = booking.get(group_key) or []
            if not isinstance(group, list):
                continue
            for item in group:
                if not isinstance(item, dict):
                    continue
                user = item.get("user")
                if isinstance(user, dict) and user.get("login"):
                    return str(user["login"])
                if item.get("login"):
                    return str(item["login"])
        return None
