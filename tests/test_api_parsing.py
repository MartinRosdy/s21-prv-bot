"""Tests for API calendar parsing and event filtering."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from bot.services.s21_api import S21ApiClient
from bot.database.models import EVENT_TYPE_SLOT, EVENT_TYPE_PEER_REVIEW, STATUS_OPEN, STATUS_BOOKED


class TestApiParsing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # S21ApiClient doesn't need actual session for static parsing methods
        self.api = S21ApiClient(
            None,  # type: ignore
            auth_url="https://auth.example.com",
            graphql_url="https://api.example.com",
            school_id="dummy-school-id",
        )

    def test_filter_out_activity_events(self):
        raw_activity = {
            "id": "act-1",
            "start": "2026-10-02T10:00:00.000Z",
            "end": "2026-10-02T12:00:00.000Z",
            "eventType": "ACTIVITY",
            "eventCode": "EVENT",
            "description": "Workshop on Algorithms",
            "activity": {
                "activityEventId": "123",
                "name": "Workshop",
            },
            "bookings": [],
        }
        self.assertTrue(S21ApiClient._is_non_review_event(raw_activity))
        res = self.api._parse_calendar_event(raw_activity)
        self.assertEqual(res, [])

    def test_filter_out_participant_events(self):
        raw_participant = {
            "id": "part-1",
            "start": "2026-10-02T14:00:00.000Z",
            "end": "2026-10-02T16:00:00.000Z",
            "description": "Participant in Campus Open Day",
            "eventType": "EVENT",
            "bookings": [],
        }
        self.assertTrue(S21ApiClient._is_non_review_event(raw_participant))
        res = self.api._parse_calendar_event(raw_participant)
        self.assertEqual(res, [])

    def test_filter_out_exam_and_penalty(self):
        raw_exam = {
            "id": "exam-1",
            "start": "2026-10-02T09:00:00.000Z",
            "end": "2026-10-02T13:00:00.000Z",
            "exam": {"examId": "e1"},
            "bookings": [],
        }
        raw_penalty = {
            "id": "pen-1",
            "start": "2026-10-02T09:00:00.000Z",
            "penalty": {"id": "p1"},
            "bookings": [],
        }
        self.assertTrue(S21ApiClient._is_non_review_event(raw_exam))
        self.assertTrue(S21ApiClient._is_non_review_event(raw_penalty))
        self.assertEqual(self.api._parse_calendar_event(raw_exam), [])
        self.assertEqual(self.api._parse_calendar_event(raw_penalty), [])

    def test_looks_like_open_slot_not_fooled_by_empty_bookings(self):
        # A non-review event that has empty bookings list should NOT be an open slot
        non_slot = {
            "id": "event-99",
            "eventType": "EVENT",
            "description": "Participant gathering",
            "bookings": [],
        }
        self.assertFalse(S21ApiClient._looks_like_open_slot(non_slot))

    def test_parse_valid_open_duty_slot(self):
        valid_slot = {
            "id": "slot-101",
            "start": "2026-10-02T15:00:00.000Z",
            "end": "2026-10-02T18:00:00.000Z",
            "eventType": "SLOT",
            "eventCode": "REVIEW_SLOT",
            "eventSlots": [{"id": 1010, "type": "SLOT"}],
            "bookings": [],
        }
        self.assertTrue(S21ApiClient._looks_like_open_slot(valid_slot))
        items = self.api._parse_calendar_event(valid_slot)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].s21_event_id, "slot-101")
        self.assertEqual(items[0].type, EVENT_TYPE_SLOT)
        self.assertEqual(items[0].status, STATUS_OPEN)
        self.assertEqual(items[0].data["event_slot_id"], "1010")
        self.assertNotIn("is_online", items[0].data)

    async def test_calendar_window_includes_already_started_events(self):
        response = MagicMock(status=200)
        response.text = AsyncMock(return_value='{"data": {}}')
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=None)
        session = MagicMock()
        session.post.return_value = context
        api = S21ApiClient(
            session,
            auth_url="https://auth.example.com",
            graphql_url="https://api.example.com",
            school_id="dummy-school-id",
        )
        now = datetime(2026, 10, 5, 13, 24, tzinfo=timezone.utc)

        with patch("bot.services.s21_api.utc_now", return_value=now):
            await api._post_calendar("token", days_ahead=7)

        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["variables"]["from"], "2026-10-04T19:00:00.000Z")
        self.assertEqual(payload["variables"]["to"], "2026-10-11T18:59:59.999Z")

    async def test_fetch_uses_all_web_calendar_sources(self):
        self.api._post_query = AsyncMock(
            side_effect=[
                {"data": {"calendarEventS21": {"getMyCalendarEvents": []}}},
                {"data": {"student": {"getMyCalendarBookings": []}}},
                {"data": {"student": {"getMyUpcomingBookings": []}}},
                {"data": {"student": {"getMyActualP2pRequests": []}}},
            ]
        )

        result = await self.api.fetch_calendar_events("token", user_login="me")

        self.assertEqual(result.items, [])
        self.assertTrue(result.complete)
        self.assertEqual(result.failed_sources, ())
        operations = [
            call.kwargs["operation_name"]
            for call in self.api._post_query.await_args_list
        ]
        self.assertEqual(
            operations,
            [
                "calendarGetEvents",
                "calendarGetMyBookings",
                "calendarGetMyReviews",
                "calendarGetMyActualP2pRequests",
            ],
        )

    async def test_partial_source_failure_preserves_previous_snapshot(self):
        self.api._post_query = AsyncMock(
            side_effect=[
                {"data": {"calendarEventS21": {"getMyCalendarEvents": []}}},
                None,
                {"data": {"student": {"getMyUpcomingBookings": []}}},
                {"data": {"student": {"getMyActualP2pRequests": []}}},
            ]
        )

        result = await self.api.fetch_calendar_events("token", user_login="me")

        self.assertEqual(result.items, [])
        self.assertFalse(result.complete)
        self.assertEqual(result.failed_sources, ("calendarGetMyBookings",))

    async def test_create_slot_uses_supported_payload(self):
        post = AsyncMock(return_value={"student": {"addEventToTimetable": {"id": "1"}}})
        self.api._post_mutation = post

        await self.api.create_slot(
            "token",
            "2026-10-02T15:00:00.000Z",
            "2026-10-02T16:00:00.000Z",
        )

        kwargs = post.await_args.kwargs
        self.assertEqual(set(kwargs["variables"]), {"start", "end"})
        mutation = kwargs["query"].split("fragment CalendarEvent", 1)[0]
        self.assertNotIn("isOnline", mutation)

    async def test_update_slot_uses_supported_payload(self):
        post = AsyncMock(return_value={"student": {"changeEventSlot": {"id": "1"}}})
        self.api._post_mutation = post

        await self.api.update_slot(
            "token",
            "1010",
            "2026-10-02T15:00:00.000Z",
            "2026-10-02T16:00:00.000Z",
        )

        kwargs = post.await_args.kwargs
        self.assertEqual(kwargs["variables"]["id"], 1010)
        self.assertEqual(set(kwargs["variables"]), {"id", "start", "end"})
        mutation = kwargs["query"].split("fragment CalendarEvent", 1)[0]
        self.assertNotIn("isOnline", mutation)

    async def test_create_slot_rejects_empty_success_response(self):
        from bot.services.s21_api import S21ApiError

        self.api._post_mutation = AsyncMock(return_value={"student": {"addEventToTimetable": None}})
        with self.assertRaises(S21ApiError):
            await self.api.create_slot("token", "start", "end")

    def test_parse_valid_booked_peer_review(self):
        booked_event = {
            "id": "rev-202",
            "bookings": [
                {
                    "id": "book-1",
                    "bookingStatus": "CONFIRMED",
                    "isOnline": True,
                    "eventSlot": {
                        "id": 2020,
                        "start": "2026-10-02T16:00:00.000Z",
                        "end": "2026-10-02T16:30:00.000Z",
                        "event": {"eventUserRole": "EVALUATOR"},
                    },
                    "verifiableInfo": {
                        "verifiableStudents": [{"login": "Student_Peer"}]
                    },
                    "task": {"goalName": "C_SimpleBashUtils"},
                }
            ],
        }
        items = self.api._parse_calendar_event(booked_event)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].s21_event_id, "rev-202")
        self.assertEqual(items[0].type, EVENT_TYPE_PEER_REVIEW)
        self.assertEqual(items[0].status, STATUS_BOOKED)
        self.assertEqual(items[0].data["peer_login"], "student_peer")
        self.assertTrue(items[0].data["is_online"])
        self.assertEqual(items[0].data["goal_name"], "C_SimpleBashUtils")

    def test_parse_evaluator_booking_from_dedicated_source(self):
        booking = {
            "id": "book-evaluator",
            "answerId": "answer-1",
            "bookingStatus": "CONFIRMED",
            "eventSlot": {
                "id": "slot-1",
                "start": "2026-10-06T10:00:00.000Z",
                "end": "2026-10-06T10:30:00.000Z",
                "event": {"eventUserRole": "EVALUATOR"},
            },
            "verifiableInfo": {
                "verifiableStudents": [{"login": "Evaluatee_User"}]
            },
            "verifierUser": {"login": "my_login"},
            "task": {"goalName": "CPP1"},
            "isOnline": True,
        }

        item = self.api._parse_standalone_booking(
            booking,
            user_login="my_login",
            source="calendarGetMyBookings",
        )

        self.assertIsNotNone(item)
        self.assertEqual(item.role, "evaluator")
        self.assertEqual(item.data["peer_login"], "evaluatee_user")
        self.assertEqual(item.data["answer_id"], "answer-1")

    def test_parse_evaluated_review_without_event_role(self):
        booking = {
            "id": "book-evaluated",
            "answerId": "answer-2",
            "bookingStatus": "ACTIVE",
            "eventSlot": {
                "id": "slot-2",
                "start": "2026-10-06T11:00:00.000Z",
                "end": "2026-10-06T11:30:00.000Z",
            },
            "verifierUser": {"login": "Checker_User"},
            "verifiableStudent": {"user": {"login": "MY_LOGIN"}},
            "task": {"goalName": "CPP2"},
        }

        item = self.api._parse_standalone_booking(
            booking,
            user_login="my_login",
            source="calendarGetMyReviews",
        )

        self.assertIsNotNone(item)
        self.assertEqual(item.role, "evaluated")
        self.assertEqual(item.data["peer_login"], "checker_user")

    def test_deduplicate_open_event_and_booking_by_event_slot(self):
        open_item = self.api._parse_calendar_event(
            {
                "id": "calendar-event-1",
                "start": "2026-10-06T12:00:00.000Z",
                "end": "2026-10-06T13:00:00.000Z",
                "eventType": "SLOT",
                "eventCode": "REVIEW_SLOT",
                "eventSlots": [{"id": "slot-3", "type": "SLOT"}],
                "bookings": [],
            }
        )[0]
        booked_item = self.api._parse_standalone_booking(
            {
                "id": "book-3",
                "bookingStatus": "CONFIRMED",
                "eventSlot": {
                    "id": "slot-3",
                    "start": "2026-10-06T12:00:00.000Z",
                    "end": "2026-10-06T12:30:00.000Z",
                    "event": {"eventUserRole": "CHECKER"},
                },
                "verifiableInfo": {
                    "verifiableStudents": [{"login": "peer3"}]
                },
            },
            user_login="me",
            source="calendarGetMyBookings",
        )

        merged = self.api._merge_snapshot_items([open_item, booked_item])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].s21_event_id, "calendar-event-1")
        self.assertEqual(merged[0].status, STATUS_BOOKED)
        self.assertEqual(merged[0].type, EVENT_TYPE_PEER_REVIEW)
        self.assertEqual(merged[0].role, "evaluator")
        self.assertEqual(merged[0].data["peer_login"], "peer3")

    def test_p2p_request_merges_with_booking_by_answer_id(self):
        p2p = self.api._parse_p2p_request(
            {
                "p2pRequestId": "request-4",
                "studentAnswerId": "answer-4",
                "startTime": "2026-10-06T14:00:00.000Z",
                "endTime": "2026-10-06T14:30:00.000Z",
                "goalName": "SQL1",
                "isOnline": False,
            }
        )
        booking = self.api._parse_standalone_booking(
            {
                "id": "book-4",
                "answerId": "answer-4",
                "bookingStatus": "CONFIRMED",
                "eventSlot": {
                    "id": "slot-4",
                    "start": "2026-10-06T14:00:00.000Z",
                    "end": "2026-10-06T14:30:00.000Z",
                    "event": {"eventUserRole": "EVALUATED"},
                },
                "verifierUser": {"login": "checker4"},
            },
            user_login="me",
            source="calendarGetMyBookings",
        )

        merged = self.api._merge_snapshot_items([p2p, booking])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].role, "evaluated")
        self.assertEqual(merged[0].data["peer_login"], "checker4")


if __name__ == "__main__":
    unittest.main()
