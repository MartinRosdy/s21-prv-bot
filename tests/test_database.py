"""SQLite migration, lifecycle, and notification idempotency tests."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bot.database.db import Database
from bot.database.models import (
    EVENT_TYPE_PEER_REVIEW,
    EVENT_TYPE_SLOT,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_OPEN,
    TrackedEvent,
)


class TestDatabase(unittest.IsolatedAsyncioTestCase):
    async def test_context_manager_and_per_booking_notification_claim(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bot.db")
            async with db:
                await db.upsert_user(100, "PeEr", "encrypted")
                normalized_user = await db.get_user(100)
                self.assertIsNotNone(normalized_user)
                assert normalized_user is not None
                self.assertEqual(normalized_user.s21_login, "peer")
                saved = await db.upsert_event(
                    TrackedEvent(
                        id=None,
                        user_id=100,
                        s21_event_id="event-1",
                        type=EVENT_TYPE_PEER_REVIEW,
                        status=STATUS_BOOKED,
                        start_time="2999-10-02T19:30:00.000Z",
                    )
                )
                self.assertIsNotNone(saved.id)
                event_id = int(saved.id or 0)
                self.assertTrue(
                    await db.claim_event_notification(event_id, "booking-1")
                )
                self.assertFalse(
                    await db.claim_event_notification(event_id, "booking-1")
                )
                self.assertTrue(
                    await db.claim_event_notification(event_id, "booking-2")
                )

                await db.upsert_event(
                    TrackedEvent(
                        id=None,
                        user_id=100,
                        s21_event_id="event-2",
                        type=EVENT_TYPE_SLOT,
                        status=STATUS_OPEN,
                        start_time="2999-10-02T20:00:00.000Z",
                        role=ROLE_EVALUATOR,
                    )
                )
                await db.upsert_event(
                    TrackedEvent(
                        id=None,
                        user_id=100,
                        s21_event_id="event-3",
                        type=EVENT_TYPE_PEER_REVIEW,
                        status=STATUS_BOOKED,
                        start_time="2999-10-02T21:00:00.000Z",
                        role=ROLE_EVALUATED,
                    )
                )
                evaluator_count, evaluated_count = (
                    await db.get_active_slot_counts(100)
                )
                self.assertEqual(evaluator_count, 2)
                self.assertEqual(evaluated_count, 1)

                # A status-only patch must not erase fresh event metadata.
                await db.update_event_status(event_id, STATUS_OPEN)
                patched = await db.get_event_by_id(event_id)
                self.assertIsNotNone(patched)
                assert patched is not None
                self.assertEqual(patched.status, STATUS_OPEN)
                self.assertEqual(patched.type, EVENT_TYPE_PEER_REVIEW)
                self.assertEqual(
                    patched.start_time,
                    "2999-10-02T19:30:00.000Z",
                )

                with self.assertRaises(RuntimeError):
                    async with db.transaction() as connection:
                        await db._execute_write(
                            connection,
                            "DELETE FROM events WHERE user_id = ?",
                            (100,),
                        )
                        raise RuntimeError("force rollback")

                # The failed transaction must not leak a partial DELETE.
                events_after_rollback = await db.get_events(100)
                self.assertEqual(len(events_after_rollback), 3)

                self.assertTrue(await db.clear_user_credentials(100))
                with self.assertRaises(RuntimeError):
                    await db.upsert_event(
                        TrackedEvent(
                            id=None,
                            user_id=100,
                            s21_event_id="late-poll",
                            type=EVENT_TYPE_SLOT,
                            status=STATUS_OPEN,
                            start_time="2999-10-02T22:00:00.000Z",
                        )
                    )
                self.assertEqual(await db.get_events(100), [])

            self.assertIsNone(db._conn)


if __name__ == "__main__":
    unittest.main()
