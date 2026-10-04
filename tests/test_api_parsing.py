"""Tests for API calendar parsing and event filtering."""

import unittest
from unittest.mock import AsyncMock
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
        self.assertNotIn("isOnline", kwargs["query"])

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
        self.assertNotIn("isOnline", kwargs["query"])

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
                        "verifiableStudents": [{"login": "student_peer"}]
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


if __name__ == "__main__":
    unittest.main()
