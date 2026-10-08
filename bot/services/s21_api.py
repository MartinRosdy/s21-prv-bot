"""Async School 21 API client: Keycloak auth + GraphQL calendar."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional

import aiohttp

from bot.core.utils import TASHKENT_TZ, utc_iso, utc_now
from bot.database.models import (
    DEFAULT_ROLE,
    EVENT_TYPE_PEER_REVIEW,
    EVENT_TYPE_SLOT,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_OPEN,
    TERMINAL_BOOKING_STATUSES,
    CalendarFetchResult,
    CalendarSnapshotItem,
)

logger = logging.getLogger(__name__)

# Browser-like UA required by the platform WAF / CDN.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
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

CALENDAR_GET_MY_BOOKINGS_QUERY = """query calendarGetMyBookings($from: DateTime!, $to: DateTime!) {
  student {
    getMyCalendarBookings(from: $from, to: $to) {
      ...CalendarReviewBooking
      __typename
    }
    __typename
  }
}

fragment CalendarReviewBooking on CalendarBooking {
  id
  answerId
  eventSlotId
  task {
    id
    goalId
    goalName
    studentTaskAdditionalAttributes { cookiesCount __typename }
    assignmentType
    __typename
  }
  eventSlot {
    id
    start
    end
    event { eventUserRole eventCode __typename }
    school { shortName __typename }
    __typename
  }
  verifierUser { ...CalendarReviewUser __typename }
  verifiableInfo {
    verifiableStudents { ...VerifiableStudentItem __typename }
    team { name __typename }
    __typename
  }
  bookingStatus
  isOnline
  vcLinkUrl
  additionalChecklist {
    filledChecklistId
    filledChecklistStatusRecordingEnum
    __typename
  }
  __typename
}

fragment CalendarReviewUser on User { id login __typename }
fragment VerifiableStudentItem on VerifiableStudent {
  userId login avatarUrl levelCode isTeamLead cookiesCount codeReviewPoints
  school { shortName __typename }
  __typename
}"""

CALENDAR_GET_MY_REVIEWS_QUERY = """query calendarGetMyReviews($to: DateTime, $limit: Int) {
  student {
    getMyUpcomingBookings(to: $to, limit: $limit) {
      ...Review
      __typename
    }
    __typename
  }
}

fragment Review on CalendarBooking {
  id
  answerId
  eventSlot { id start end __typename }
  task {
    id
    title
    assignmentType
    goalId
    goalName
    studentTaskAdditionalAttributes { cookiesCount __typename }
    __typename
  }
  verifierUser { ...UserInBooking __typename }
  verifiableStudent {
    id
    user { ...UserInBooking __typename }
    __typename
  }
  team { ...ProjectTeamMembers __typename }
  bookingStatus
  isOnline
  vcLinkUrl
  __typename
}

fragment UserInBooking on User {
  id
  login
  avatarUrl
  userExperience { level { id range { levelCode __typename } __typename } __typename }
  __typename
}
fragment ProjectTeamMembers on ProjectTeamMembers {
  id
  teamLead { ...ProjectTeamMember __typename }
  members { ...ProjectTeamMember __typename }
  invitedUsers { ...ProjectTeamMember __typename }
  teamName
  teamStatus
  minTeamMemberCount
  maxTeamMemberCount
  __typename
}
fragment ProjectTeamMember on User {
  id avatarUrl login
  userExperience {
    level { id range { levelCode __typename } __typename }
    cookiesCount
    codeReviewPoints
    __typename
  }
  activeSchoolShortName
  __typename
}"""

CALENDAR_GET_MY_ACTUAL_P2P_REQUESTS_QUERY = """query calendarGetMyActualP2pRequests($from: DateTime!, $to: DateTime!) {
  student {
    getMyActualP2pRequests(from: $from, to: $to) {
      ...CalendarP2pRequest
      __typename
    }
    __typename
  }
}

fragment CalendarP2pRequest on P2pRequest {
  p2pRequestId
  startTime
  endTime
  isOnline
  goalId
  goalName
  studentAnswerId
  __typename
}"""


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
        # One client is shared by every user. Calendar sync fans out to four
        # GraphQL sources, so limit actual HTTP requests here rather than only
        # limiting the number of user-level scheduler jobs.
        self._request_semaphore = asyncio.Semaphore(3)

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
            async with self._request_semaphore:
                async with self._session.post(
                    self._auth_url,
                    data=form,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    text = await resp.text()
                    try:
                        parsed = json.loads(text) if text else {}
                    except json.JSONDecodeError as exc:
                        raise S21NetworkError(
                            f"Keycloak returned invalid JSON ({resp.status})"
                        ) from exc
                    body = parsed if isinstance(parsed, dict) else {}
                    if resp.status in {408, 425, 429} or resp.status >= 500:
                        raise S21NetworkError(
                            f"Keycloak temporary HTTP error: {resp.status}"
                        )
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
        except S21NetworkError:
            raise
        except _NETWORK_ERRORS as exc:
            # Connection reset / timeout — not a wrong password.
            raise S21NetworkError(f"Keycloak network error: {exc}") from exc

    async def fetch_calendar_events(
        self,
        access_token: str,
        *,
        days_ahead: int = 7,
        user_login: Optional[str] = None,
    ) -> CalendarFetchResult:
        """
        Fetch and merge every calendar source used by the School 21 web UI.

        ``calendarGetEvents`` is not authoritative for assigned reviews: some
        evaluator/evaluatee bookings only appear in the dedicated student
        queries. Successful sources are still ingested after a partial outage,
        while ``complete=False`` tells the scheduler not to infer cancellations
        from anything absent in that partial result.
        """
        window = self._calendar_window(days_ahead)
        requests = (
            self._post_query(
                access_token,
                operation_name="calendarGetEvents",
                variables=window,
                query=CALENDAR_GET_EVENTS_QUERY,
            ),
            self._post_query(
                access_token,
                operation_name="calendarGetMyBookings",
                variables=window,
                query=CALENDAR_GET_MY_BOOKINGS_QUERY,
            ),
            self._post_query(
                access_token,
                operation_name="calendarGetMyReviews",
                variables={"limit": 30},
                query=CALENDAR_GET_MY_REVIEWS_QUERY,
            ),
            self._post_query(
                access_token,
                operation_name="calendarGetMyActualP2pRequests",
                variables=window,
                query=CALENDAR_GET_MY_ACTUAL_P2P_REQUESTS_QUERY,
            ),
        )
        operation_names = (
            "calendarGetEvents",
            "calendarGetMyBookings",
            "calendarGetMyReviews",
            "calendarGetMyActualP2pRequests",
        )
        results = await asyncio.gather(*requests, return_exceptions=True)
        bodies: list[Optional[dict[str, Any]]] = []
        failed_sources: list[str] = []
        for operation_name, result in zip(operation_names, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "Calendar source %s failed: %s",
                    operation_name,
                    result,
                )
                bodies.append(None)
                failed_sources.append(operation_name)
            elif result is None:
                bodies.append(None)
                failed_sources.append(operation_name)
            else:
                bodies.append(result)

        items: list[CalendarSnapshotItem] = []
        events_body, bookings_body, reviews_body, p2p_body = bodies

        raw_events = self._extract_events_list(
            events_body.get("data") or {} if events_body else {}
        )
        for raw in raw_events:
            items.extend(self._parse_calendar_event(raw))

        raw_bookings = self._extract_student_list(
            bookings_body.get("data") or {} if bookings_body else {},
            "getMyCalendarBookings",
        )
        for booking in raw_bookings:
            item = self._parse_standalone_booking(
                booking,
                user_login=user_login,
                source="calendarGetMyBookings",
            )
            if item is not None:
                items.append(item)

        raw_reviews = self._extract_student_list(
            reviews_body.get("data") or {} if reviews_body else {},
            "getMyUpcomingBookings",
        )
        for booking in raw_reviews:
            item = self._parse_standalone_booking(
                booking,
                user_login=user_login,
                source="calendarGetMyReviews",
            )
            if item is not None:
                items.append(item)

        raw_requests = self._extract_student_list(
            p2p_body.get("data") or {} if p2p_body else {},
            "getMyActualP2pRequests",
        )
        for request in raw_requests:
            item = self._parse_p2p_request(request)
            if item is not None:
                items.append(item)

        merged = [
            item
            for item in self._merge_snapshot_items(items)
            if item.type != EVENT_TYPE_PEER_REVIEW
            or bool((item.data or {}).get("has_concrete_booking"))
        ]
        logger.info(
            "Calendar sources events=%s bookings=%s reviews=%s p2p=%s; "
            "normalized=%s merged=%s",
            len(raw_events),
            len(raw_bookings),
            len(raw_reviews),
            len(raw_requests),
            len(items),
            len(merged),
        )
        return CalendarFetchResult(
            items=merged,
            complete=not failed_sources,
            failed_sources=tuple(failed_sources),
        )

    @staticmethod
    def _iso_millis(value: datetime) -> str:
        """Serialize an aware datetime without losing the platform's .999 bound."""
        utc_value = value.astimezone(timezone.utc)
        millis = utc_value.microsecond // 1000
        return f"{utc_value:%Y-%m-%dT%H:%M:%S}.{millis:03d}Z"

    @classmethod
    def _calendar_window(cls, days_ahead: int) -> dict[str, str]:
        """Web-compatible range: local midnight through the next N days."""
        if days_ahead < 1:
            raise ValueError("days_ahead must be positive")
        local_today = utc_now().astimezone(TASHKENT_TZ).date()
        local_start = datetime.combine(local_today, time.min, tzinfo=TASHKENT_TZ)
        local_end = local_start + timedelta(days=days_ahead) - timedelta(milliseconds=1)
        return {
            "from": cls._iso_millis(local_start),
            "to": cls._iso_millis(local_end),
        }

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
        variables = self._calendar_window(days_ahead)
        return await self._post_query(
            access_token,
            operation_name="calendarGetEvents",
            variables=variables,
            query=CALENDAR_GET_EVENTS_QUERY,
        )

    async def _post_query(
        self,
        access_token: str,
        *,
        operation_name: str,
        variables: dict[str, Any],
        query: str,
    ) -> Optional[dict[str, Any]]:
        """Execute one read query; return ``None`` on transport failure."""
        payload = {
            "operationName": operation_name,
            "variables": variables,
            "query": query,
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "schoolId": self._school_id,
            "User-Agent": self._user_agent,
        }

        logger.info(
            "GraphQL query operation=%s variables=%s",
            operation_name,
            json.dumps(variables, ensure_ascii=False),
        )

        try:
            async with self._request_semaphore:
                async with self._session.post(
                    self._graphql_operation_url(operation_name),
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
            logger.warning("GraphQL query %s network error: %s", operation_name, exc)
            return None

        if not isinstance(body, dict):
            raise S21ApiError(
                f"GraphQL {operation_name} response is not a JSON object"
            )
        if body.get("errors"):
            messages = "; ".join(
                str(
                    error.get("message", error)
                    if isinstance(error, dict)
                    else error
                )
                for error in body["errors"]
            )
            raise S21ApiError(f"GraphQL {operation_name} errors: {messages}")
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
        # Log operation and variables only; the full query is noisy in production.
        logger.info(
            "GraphQL mutation POST url=%s variables=%s",
            url,
            json.dumps(variables, ensure_ascii=False, default=str),
        )

        try:
            async with self._request_semaphore:
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
        data = await self._post_mutation(
            token,
            operation_name="calendarAddEvent",
            variables={"start": start_time_utc, "end": end_time_utc},
            query=query_str,
        )
        if not isinstance(data, dict) or not isinstance(data.get("student"), dict):
            raise S21ApiError("calendarAddEvent returned no student result")
        if not data["student"].get("addEventToTimetable"):
            raise S21ApiError("calendarAddEvent returned no created event")
        return data

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
        data = await self._post_mutation(
            token,
            operation_name="calendarChangeEventSlot",
            variables=payload_vars,
            query=query_str,
        )
        if not isinstance(data, dict) or not isinstance(data.get("student"), dict):
            raise S21ApiError("calendarChangeEventSlot returned no student result")
        if not data["student"].get("changeEventSlot"):
            raise S21ApiError("calendarChangeEventSlot returned no changed event")
        return data

    async def delete_slot(self, token: str, slot_id: str) -> Any:
        """Delete an open slot by platform eventSlotId."""
        # Plain string only — f-strings corrupt GraphQL braces / variables.
        query_str = """mutation calendarDeleteEventSlot($eventSlotId: ID!) {
  student {
    deleteEventSlot(eventSlotId: $eventSlotId)
    __typename
  }
}"""
        data = await self._post_mutation(
            token,
            operation_name="calendarDeleteEventSlot",
            variables={"eventSlotId": self._as_slot_id_int(slot_id)},
            query=query_str,
        )
        if not isinstance(data, dict) or not isinstance(data.get("student"), dict):
            raise S21ApiError("calendarDeleteEventSlot returned no student result")
        # The mutation returns a boolean. Require an explicit True so an empty
        # or rejected response is never reported to the user as a deletion.
        if data["student"].get("deleteEventSlot") is not True:
            raise S21ApiError("calendarDeleteEventSlot did not delete the slot")
        return data

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

    @staticmethod
    def _extract_student_list(
        data: dict[str, Any],
        field: str,
    ) -> list[dict[str, Any]]:
        """Extract a list from the ``student`` GraphQL namespace."""
        student = data.get("student") or {}
        if not isinstance(student, dict):
            return []
        value = student.get(field)
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @classmethod
    def _is_non_review_event(cls, raw: dict[str, Any]) -> bool:
        """
        True when this event is a campus activity, workshop, exam, penalty,
        or other non-peer-review event (e.g. "Participant...").
        """
        if raw.get("activity") is not None:
            return True
        if raw.get("exam") is not None:
            return True
        if raw.get("penalty") is not None:
            return True

        desc = str(raw.get("description") or "").strip().lower()
        if "participant" in desc or "участник" in desc or "мероприятие" in desc:
            return True

        event_type = str(raw.get("eventType") or "").upper()
        event_code = str(raw.get("eventCode") or "").upper()

        non_review_markers = (
            "ACTIVITY",
            "EXAM",
            "PENALTY",
            "WORKSHOP",
            "LECTURE",
            "MEETING",
            "EVENT",
            "CONFERENCE",
        )
        for marker in non_review_markers:
            if marker in event_type or marker in event_code:
                # Unless it's explicitly a peer review slot
                if not any(
                    rev in event_type or rev in event_code
                    for rev in ("PEER", "REVIEW", "SLOT", "CHECK")
                ):
                    return True

        return False

    def _parse_calendar_event(
        self,
        raw: dict[str, Any],
    ) -> list[CalendarSnapshotItem]:
        """
        Map one GraphQL CalendarEvent into peer-review snapshot items.

        Only open slots and booked peer-reviews are tracked.
        All campus activities, exams, penalties, and non-review events are ignored.
        """
        if self._is_non_review_event(raw):
            return []

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
                            "source": "calendarGetEvents",
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
        user_login: Optional[str] = None,
        source: str = "calendarGetEvents",
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

        role, peer_login = self._extract_role_and_peer(
            booking,
            user_login=user_login,
        )
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
                "answer_id": (
                    str(booking["answerId"])
                    if booking.get("answerId") is not None
                    else None
                ),
                "source": source,
                "has_concrete_booking": True,
            },
        )

    def _parse_standalone_booking(
        self,
        booking: dict[str, Any],
        *,
        user_login: Optional[str],
        source: str,
    ) -> Optional[CalendarSnapshotItem]:
        """Normalize a booking returned outside ``CalendarEvent``."""
        if not self._is_active_booking(booking):
            return None
        booking_id = str(booking.get("id") or "").strip()
        slot = booking.get("eventSlot") or {}
        slot_id = ""
        if isinstance(slot, dict) and slot.get("id") is not None:
            slot_id = str(slot["id"]).strip()
        if not booking_id and not slot_id:
            return None
        stable_id = f"booking:{booking_id}" if booking_id else f"slot:{slot_id}"
        return self._parse_booking(
            {},
            booking,
            event_id=stable_id,
            user_login=user_login,
            source=source,
        )

    @staticmethod
    def _parse_p2p_request(
        request: dict[str, Any],
    ) -> Optional[CalendarSnapshotItem]:
        """Normalize the evaluated student's active P2P request fallback."""
        request_id = str(request.get("p2pRequestId") or "").strip()
        start = request.get("startTime")
        if not request_id or not start:
            return None
        answer_id = request.get("studentAnswerId")
        goal_name = request.get("goalName")
        return CalendarSnapshotItem(
            s21_event_id=f"p2p:{request_id}",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time=str(start),
            end_time=(
                str(request["endTime"])
                if request.get("endTime") is not None
                else None
            ),
            role=ROLE_EVALUATED,
            data={
                "peer_login": None,
                "role": ROLE_EVALUATED,
                "is_online": bool(request.get("isOnline", False)),
                "booking_id": None,
                "booking_status": "ACTUAL_P2P_REQUEST",
                "vc_link": None,
                "goal_name": goal_name,
                "name": goal_name or "Пир-Ревью",
                "event_slot_id": None,
                "answer_id": str(answer_id) if answer_id is not None else None,
                "p2p_request_id": request_id,
                "source": "calendarGetMyActualP2pRequests",
                "has_concrete_booking": False,
            },
        )

    @staticmethod
    def _snapshot_keys(item: CalendarSnapshotItem) -> tuple[str, ...]:
        """Cross-query identities ordered from strongest to weakest."""
        data = item.data or {}
        keys: list[str] = []
        for prefix, value in (
            ("answer", data.get("answer_id")),
            ("slot", data.get("event_slot_id")),
            ("booking", data.get("booking_id")),
        ):
            if value is not None and str(value).strip():
                keys.append(f"{prefix}:{str(value).strip()}")
        keys.append(f"event:{item.s21_event_id}")
        return tuple(keys)

    @staticmethod
    def _merge_two_items(
        current: CalendarSnapshotItem,
        incoming: CalendarSnapshotItem,
    ) -> CalendarSnapshotItem:
        """Merge duplicate query rows while preserving the first stable id."""
        current_data = dict(current.data or {})
        incoming_data = dict(incoming.data or {})
        current_score = sum(value not in (None, "", [], {}) for value in current_data.values())
        incoming_score = sum(value not in (None, "", [], {}) for value in incoming_data.values())
        preferred = incoming if incoming_score > current_score else current
        secondary = current if preferred is incoming else incoming
        merged_data = dict(secondary.data or {})
        merged_data.update(
            {
                key: value
                for key, value in (preferred.data or {}).items()
                if value not in (None, "", [], {})
            }
        )
        role = preferred.role or secondary.role
        return CalendarSnapshotItem(
            s21_event_id=current.s21_event_id,
            type=(
                EVENT_TYPE_PEER_REVIEW
                if EVENT_TYPE_PEER_REVIEW in {current.type, incoming.type}
                else preferred.type
            ),
            status=(
                STATUS_BOOKED
                if STATUS_BOOKED in {current.status, incoming.status}
                else preferred.status
            ),
            start_time=preferred.start_time or secondary.start_time,
            end_time=preferred.end_time or secondary.end_time,
            role=role,
            data=merged_data,
        )

    @classmethod
    def _merge_snapshot_items(
        cls,
        items: list[CalendarSnapshotItem],
    ) -> list[CalendarSnapshotItem]:
        """Deduplicate CalendarEvent, booking, review, and P2P representations."""
        merged: list[CalendarSnapshotItem] = []
        key_to_index: dict[str, int] = {}
        for item in items:
            matching = {
                key_to_index[key]
                for key in cls._snapshot_keys(item)
                if key in key_to_index
            }
            if not matching:
                index = len(merged)
                merged.append(item)
            else:
                index = min(matching)
                merged[index] = cls._merge_two_items(merged[index], item)
            for key in cls._snapshot_keys(merged[index]):
                key_to_index[key] = index
            for key in cls._snapshot_keys(item):
                key_to_index[key] = index
        return merged

    @staticmethod
    def _is_active_booking(booking: dict[str, Any]) -> bool:
        status = str(
            booking.get("bookingStatus") or booking.get("status") or ""
        ).strip().upper()
        if not status:
            return True
        return status not in TERMINAL_BOOKING_STATUSES

    @classmethod
    def _looks_like_open_slot(cls, raw: dict[str, Any]) -> bool:
        """True when this calendar row is an empty peer-review / duty slot."""
        if cls._is_non_review_event(raw):
            return False

        # If there are active bookings, it's handled as booked review, not open slot
        bookings = raw.get("bookings") or []
        if isinstance(bookings, list) and any(
            isinstance(b, dict) and cls._is_active_booking(b) for b in bookings
        ):
            return False

        code = str(raw.get("eventCode") or "").upper()
        description = str(raw.get("description") or "").upper()
        event_type = str(raw.get("eventType") or "").upper()
        markers = (
            "CHECK",
            "REVIEW",
            "PEER",
            "VERIFIER",
            "SLOT",
            "ПИР",
            "ПРОВЕРК",
            "ДЕЖУР",
        )
        haystack = f"{code} {description} {event_type}"

        slots = raw.get("eventSlots")
        if isinstance(slots, list) and len(slots) > 0:
            for s in slots:
                if isinstance(s, dict):
                    st = str(s.get("type") or "").upper()
                    if any(m in st for m in markers):
                        return True
            if any(marker in haystack for marker in markers):
                return True
            # Duty slot in School 21 without activity/exam/bookings markers
            if not any(
                bad in haystack
                for bad in ("ACTIVITY", "EXAM", "PENALTY", "WORKSHOP", "PARTICIPANT")
            ):
                return True

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

    @staticmethod
    def _normalize_event_role(role_raw: str) -> Optional[str]:
        """Map platform role enum variants to the bot's two stable roles."""
        value = str(role_raw or "").strip().upper()
        if value in {"EVALUATOR", "VERIFIER", "CHECKER"}:
            return ROLE_EVALUATOR
        if value in {"EVALUATED", "VERIFIABLE", "CHECKED", "STUDENT"}:
            return ROLE_EVALUATED
        return None

    @classmethod
    def _extract_open_slot_role(cls, raw: dict[str, Any]) -> str:
        """Role for an empty duty slot (defaults to evaluator)."""
        slots = raw.get("eventSlots") or []
        if isinstance(slots, list):
            for slot in slots:
                role_raw = cls._event_user_role(slot)
                role = cls._normalize_event_role(role_raw)
                if role is not None:
                    return role
        return DEFAULT_ROLE

    @classmethod
    def _extract_role_and_peer(
        cls,
        booking: dict[str, Any],
        *,
        user_login: Optional[str] = None,
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

        normalized_role = cls._normalize_event_role(role_raw)
        if normalized_role == ROLE_EVALUATOR:
            return ROLE_EVALUATOR, cls._peer_from_verifiable(booking)
        if normalized_role == ROLE_EVALUATED:
            return ROLE_EVALUATED, cls._peer_from_verifier(booking)

        # ``calendarGetMyReviews`` has no eventUserRole. Resolve it by
        # comparing the authenticated login with both sides of the booking.
        normalized_login = str(user_login or "").strip().lower()
        verifier_login = cls._peer_from_verifier(booking)
        evaluated_logins = cls._verifiable_logins(booking)
        if normalized_login:
            if verifier_login == normalized_login:
                peer = next(
                    (login for login in evaluated_logins if login != normalized_login),
                    None,
                )
                return ROLE_EVALUATOR, peer
            if normalized_login in evaluated_logins:
                return ROLE_EVALUATED, verifier_login

        # Legacy / unknown role: best-effort peer detection.
        peer = cls._peer_from_verifiable(booking) or verifier_login
        return DEFAULT_ROLE, peer

    @classmethod
    def _verifiable_logins(cls, booking: dict[str, Any]) -> list[str]:
        """Collect evaluatee logins from both booking response shapes."""
        result: list[str] = []
        info = booking.get("verifiableInfo") or {}
        if isinstance(info, dict):
            students = info.get("verifiableStudents") or []
            if isinstance(students, list):
                for student in students:
                    if isinstance(student, dict) and student.get("login"):
                        result.append(str(student["login"]).strip().lower())

        singular = booking.get("verifiableStudent") or {}
        if isinstance(singular, dict):
            user = singular.get("user") or {}
            if isinstance(user, dict) and user.get("login"):
                result.append(str(user["login"]).strip().lower())

        team = booking.get("team") or {}
        if isinstance(team, dict):
            for key in ("teamLead", "members", "invitedUsers"):
                value = team.get(key)
                candidates = value if isinstance(value, list) else [value]
                for candidate in candidates:
                    if isinstance(candidate, dict) and candidate.get("login"):
                        result.append(str(candidate["login"]).strip().lower())
        return list(dict.fromkeys(result))

    @classmethod
    def _peer_from_verifiable(cls, booking: dict[str, Any]) -> Optional[str]:
        logins = cls._verifiable_logins(booking)
        return logins[0] if logins else None

    @staticmethod
    def _peer_from_verifier(booking: dict[str, Any]) -> Optional[str]:
        verifier = booking.get("verifierUser")
        if isinstance(verifier, dict) and verifier.get("login"):
            return str(verifier["login"]).strip().lower()
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
                    return str(user["login"]).strip().lower()
                if item.get("login"):
                    return str(item["login"]).strip().lower()
        return None
