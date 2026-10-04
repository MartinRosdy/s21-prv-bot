"""SQLite migration, lifecycle, and notification idempotency tests."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bot.database.db import Database
from bot.database.models import (
    EVENT_TYPE_PEER_REVIEW,
    STATUS_BOOKED,
    TrackedEvent,
)


class TestDatabase(unittest.IsolatedAsyncioTestCase):
    async def test_context_manager_and_per_booking_notification_claim(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bot.db")
            async with db:
                await db.upsert_user(100, "peer", "encrypted")
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

            self.assertIsNone(db._conn)


if __name__ == "__main__":
    unittest.main()
