"""Tests for slot splitting algorithm upon self-booking collision."""

import unittest
from datetime import datetime, timezone

from bot.database.models import (
    EVENT_TYPE_PEER_REVIEW,
    EVENT_TYPE_SLOT,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_OPEN,
    CalendarSnapshotItem,
)
from bot.services.slot_splitter import calculate_slot_splits


class TestSlotSplitter(unittest.TestCase):
    def test_user_requested_scenario_19_00_to_22_00(self):
        """
        User request requirement 3:
        - Open slot: 19:00 - 22:00 (evaluator)
        - Self-booking: 19:30 - 20:00 (evaluated, 30 min)
        Outcome:
          a) Evaluator slot: 19:00 - 19:30
          б) Evaluated slot: 19:30 - 20:00
          в) Evaluator slot: 20:00 - 22:00
        """
        open_slot = CalendarSnapshotItem(
            s21_event_id="slot-orig",
            type=EVENT_TYPE_SLOT,
            status=STATUS_OPEN,
            start_time="2026-10-02T19:00:00.000Z",
            end_time="2026-10-02T22:00:00.000Z",
            role=ROLE_EVALUATOR,
            data={"event_slot_id": "111", "is_online": False},
        )
        booking = CalendarSnapshotItem(
            s21_event_id="booking-1",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2026-10-02T19:30:00.000Z",
            end_time="2026-10-02T20:00:00.000Z",
            role=ROLE_EVALUATED,
            data={"peer_login": "checker_peer", "is_online": True},
        )

        plan = calculate_slot_splits([open_slot, booking])

        # Plan operations:
        # Original slot updated to 19:00 - 19:30
        self.assertEqual(len(plan.slots_to_update), 1)
        self.assertEqual(plan.slots_to_update[0]["event_slot_id"], "111")
        self.assertEqual(plan.slots_to_update[0]["new_start_utc"], "2026-10-02T19:00:00.000Z")
        self.assertEqual(plan.slots_to_update[0]["new_end_utc"], "2026-10-02T19:30:00.000Z")

        # Second piece created for 20:00 - 22:00
        self.assertEqual(len(plan.slots_to_create), 1)
        self.assertEqual(plan.slots_to_create[0]["new_start_utc"], "2026-10-02T20:00:00.000Z")
        self.assertEqual(plan.slots_to_create[0]["new_end_utc"], "2026-10-02T22:00:00.000Z")

        # Normalized schedule has exactly 3 items in chronological order:
        items = plan.normalized_items
        self.assertEqual(len(items), 3)

        # 1. 19:00 - 19:30 Evaluator slot
        self.assertEqual(items[0].start_time, "2026-10-02T19:00:00.000Z")
        self.assertEqual(items[0].end_time, "2026-10-02T19:30:00.000Z")
        self.assertEqual(items[0].role, ROLE_EVALUATOR)
        self.assertEqual(items[0].status, STATUS_OPEN)

        # 2. 19:30 - 20:00 Evaluated review
        self.assertEqual(items[1].start_time, "2026-10-02T19:30:00.000Z")
        self.assertEqual(items[1].end_time, "2026-10-02T20:00:00.000Z")
        self.assertEqual(items[1].role, ROLE_EVALUATED)
        self.assertEqual(items[1].status, STATUS_BOOKED)
        self.assertEqual(items[1].data["peer_login"], "checker_peer")

        # 3. 20:00 - 22:00 Evaluator slot
        self.assertEqual(items[2].start_time, "2026-10-02T20:00:00.000Z")
        self.assertEqual(items[2].end_time, "2026-10-02T22:00:00.000Z")
        self.assertEqual(items[2].role, ROLE_EVALUATOR)
        self.assertEqual(items[2].status, STATUS_OPEN)

    def test_booking_at_start_of_slot(self):
        """Booking at 19:00 - 19:30 inside 19:00 - 21:00."""
        open_slot = CalendarSnapshotItem(
            s21_event_id="slot-orig",
            type=EVENT_TYPE_SLOT,
            status=STATUS_OPEN,
            start_time="2026-10-02T19:00:00.000Z",
            end_time="2026-10-02T21:00:00.000Z",
            role=ROLE_EVALUATOR,
            data={"event_slot_id": "222"},
        )
        booking = CalendarSnapshotItem(
            s21_event_id="booking-start",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2026-10-02T19:00:00.000Z",
            end_time="2026-10-02T19:30:00.000Z",
            role=ROLE_EVALUATED,
            data={},
        )
        plan = calculate_slot_splits([open_slot, booking])
        self.assertEqual(len(plan.slots_to_update), 1)
        self.assertEqual(plan.slots_to_update[0]["new_start_utc"], "2026-10-02T19:30:00.000Z")
        self.assertEqual(plan.slots_to_update[0]["new_end_utc"], "2026-10-02T21:00:00.000Z")
        self.assertEqual(len(plan.slots_to_create), 0)
        self.assertEqual(len(plan.normalized_items), 2)

    def test_booking_at_end_of_slot(self):
        """Booking at 20:30 - 21:00 inside 19:00 - 21:00."""
        open_slot = CalendarSnapshotItem(
            s21_event_id="slot-orig",
            type=EVENT_TYPE_SLOT,
            status=STATUS_OPEN,
            start_time="2026-10-02T19:00:00.000Z",
            end_time="2026-10-02T21:00:00.000Z",
            role=ROLE_EVALUATOR,
            data={"event_slot_id": "333"},
        )
        booking = CalendarSnapshotItem(
            s21_event_id="booking-end",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2026-10-02T20:30:00.000Z",
            end_time="2026-10-02T21:00:00.000Z",
            role=ROLE_EVALUATED,
            data={},
        )
        plan = calculate_slot_splits([open_slot, booking])
        self.assertEqual(len(plan.slots_to_update), 1)
        self.assertEqual(plan.slots_to_update[0]["new_start_utc"], "2026-10-02T19:00:00.000Z")
        self.assertEqual(plan.slots_to_update[0]["new_end_utc"], "2026-10-02T20:30:00.000Z")
        self.assertEqual(len(plan.slots_to_create), 0)
        self.assertEqual(len(plan.normalized_items), 2)

    def test_booking_completely_covers_slot(self):
        """Booking 19:00 - 20:00 covering 19:00 - 20:00."""
        open_slot = CalendarSnapshotItem(
            s21_event_id="slot-orig",
            type=EVENT_TYPE_SLOT,
            status=STATUS_OPEN,
            start_time="2026-10-02T19:00:00.000Z",
            end_time="2026-10-02T20:00:00.000Z",
            role=ROLE_EVALUATOR,
            data={"event_slot_id": "444"},
        )
        booking = CalendarSnapshotItem(
            s21_event_id="booking-full",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2026-10-02T19:00:00.000Z",
            end_time="2026-10-02T20:00:00.000Z",
            role=ROLE_EVALUATED,
            data={},
        )
        plan = calculate_slot_splits([open_slot, booking])
        self.assertEqual(len(plan.slots_to_delete), 1)
        self.assertEqual(plan.slots_to_delete[0]["event_slot_id"], "444")
        self.assertEqual(len(plan.normalized_items), 1)
        self.assertEqual(plan.normalized_items[0].s21_event_id, "booking-full")

    def test_split_drops_15_minute_remainder(self):
        """Automatic splitting must never create a forbidden short slot."""
        items = [
            CalendarSnapshotItem(
                s21_event_id="slot-short-edge",
                type=EVENT_TYPE_SLOT,
                status=STATUS_OPEN,
                start_time="2026-10-02T19:00:00.000Z",
                end_time="2026-10-02T20:00:00.000Z",
                role=ROLE_EVALUATOR,
                data={"event_slot_id": "901"},
            ),
            CalendarSnapshotItem(
                s21_event_id="booking-short-edge",
                type=EVENT_TYPE_PEER_REVIEW,
                status=STATUS_BOOKED,
                start_time="2026-10-02T19:15:00.000Z",
                end_time="2026-10-02T19:30:00.000Z",
                role=ROLE_EVALUATED,
            ),
        ]

        plan = calculate_slot_splits(items)

        self.assertEqual(len(plan.slots_to_update), 1)
        self.assertEqual(
            plan.slots_to_update[0]["new_start_utc"],
            "2026-10-02T19:30:00.000Z",
        )
        self.assertEqual(
            plan.slots_to_update[0]["new_end_utc"],
            "2026-10-02T20:00:00.000Z",
        )
        self.assertEqual(plan.slots_to_create, [])


if __name__ == "__main__":
    unittest.main()
