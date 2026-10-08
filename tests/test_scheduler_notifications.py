"""Tests for scheduler notification text generation (4 triggers, both roles)."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from bot.database.models import (
    EVENT_TYPE_PEER_REVIEW,
    EVENT_TYPE_SLOT,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_OPEN,
    CalendarSnapshotItem,
    TrackedEvent,
    User,
)
from bot.services.scheduler import (
    PeerReviewScheduler,
    _build_booked_instant_text,
)
from bot.services.s21_api import S21AuthError


class TestSchedulerNotifications(unittest.IsolatedAsyncioTestCase):
    async def test_trigger_1_instant_booked_evaluator(self):
        text = _build_booked_instant_text(
            role=ROLE_EVALUATOR,
            peer_raw="Peer_Student",
            when="2 октября, 19:30 - 20:00",
            is_online=True,
            language="ru",
        )
        self.assertIn("К тебе записались на проверку!", text)
        self.assertIn("🔍 <b>Проверяющий</b>", text)
        self.assertIn("<code>peer_student</code>", text)
        self.assertIn("🌐 Онлайн", text)

    async def test_trigger_1_instant_booked_evaluated(self):
        text = _build_booked_instant_text(
            role=ROLE_EVALUATED,
            peer_raw="peer_checker",
            when="2 октября, 19:30 - 20:00",
            is_online=False,
            language="ru",
        )
        self.assertIn("Ты записался на проверку!", text)
        self.assertIn("📖 <b>Проверяемый</b>", text)
        self.assertIn("<code>peer_checker</code>", text)
        self.assertIn("🏢 Офлайн", text)

    async def test_trigger_2_t15_evaluator_and_evaluated(self):
        scheduler = PeerReviewScheduler(
            bot=None,  # type: ignore
            db=None,  # type: ignore
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )
        ev_evaluator = TrackedEvent(
            id=1,
            user_id=100,
            s21_event_id="e1",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2026-10-02T19:30:00.000Z",
            end_time="2026-10-02T20:00:00.000Z",
            role=ROLE_EVALUATOR,
            data={"peer_login": "student1", "is_online": True},
        )
        t15_text = scheduler._build_reminder_text(ev_evaluator, minutes_before=15, language="ru")
        self.assertIn("Пир-ревью начнется через 15 минут!", t15_text)
        self.assertIn("🔍 <b>Проверяющий</b>", t15_text)
        self.assertIn("Твой проверяемый: <code>student1</code>", t15_text)
        self.assertIn("🌐 Онлайн", t15_text)

        ev_evaluated = TrackedEvent(
            id=2,
            user_id=100,
            s21_event_id="e2",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2026-10-02T19:30:00.000Z",
            end_time="2026-10-02T20:00:00.000Z",
            role=ROLE_EVALUATED,
            data={"peer_login": "checker1", "is_online": False},
        )
        t15_text_eval = scheduler._build_reminder_text(ev_evaluated, minutes_before=15, language="ru")
        self.assertIn("Пир-ревью начнется через 15 минут!", t15_text_eval)
        self.assertIn("📖 <b>Проверяемый</b>", t15_text_eval)
        self.assertIn("Твой проверяющий: <code>checker1</code>", t15_text_eval)
        self.assertIn("🏢 Офлайн", t15_text_eval)

    async def test_trigger_3_t2_and_trigger_4_t0(self):
        scheduler = PeerReviewScheduler(
            bot=None,  # type: ignore
            db=None,  # type: ignore
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )
        ev = TrackedEvent(
            id=3,
            user_id=100,
            s21_event_id="e3",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2026-10-02T19:30:00.000Z",
            end_time="2026-10-02T20:00:00.000Z",
            role=ROLE_EVALUATOR,
            data={"peer_login": "student1", "is_online": True},
        )
        t2_text = scheduler._build_reminder_text(ev, minutes_before=2, language="ru")
        self.assertIn("Пир-Ревью через 2 минуты!", t2_text)
        self.assertIn("<code>student1</code>", t2_text)

        t0_text = scheduler._build_reminder_text(ev, minutes_before=0, language="ru")
        self.assertIn("Пир-Ревью начинается прямо сейчас!", t0_text)
        self.assertIn("<code>student1</code>", t0_text)


class TestBookingNotificationGuard(unittest.IsolatedAsyncioTestCase):
    async def test_bad_credentials_warning_is_not_repeated_each_poll(self):
        api = AsyncMock()
        api.get_access_token.side_effect = S21AuthError("bad credentials")
        crypto = MagicMock()
        crypto.decrypt.return_value = "secret"
        scheduler = PeerReviewScheduler(
            bot=AsyncMock(),
            db=AsyncMock(),
            crypto=crypto,
            api=api,
        )
        scheduler._safe_send = AsyncMock(return_value=True)
        user = User(100, "mylogin", "encrypted", language="en")

        await scheduler._process_user_locked(user)
        await scheduler._process_user_locked(user)

        scheduler._safe_send.assert_awaited_once()
        self.assertIn("Could not sign in", scheduler._safe_send.await_args.args[1])

    async def test_reconcile_matches_booking_source_to_existing_slot_alias(self):
        db = AsyncMock()
        existing = TrackedEvent(
            id=20,
            user_id=100,
            s21_event_id="calendar-event-old",
            type=EVENT_TYPE_SLOT,
            status=STATUS_OPEN,
            start_time="2999-10-02T19:00:00.000Z",
            end_time="2999-10-02T20:00:00.000Z",
            data={"event_slot_id": "slot-20"},
            role=ROLE_EVALUATOR,
        )
        db.get_events = AsyncMock(side_effect=[[existing], [existing], [existing]])
        bot = AsyncMock()
        scheduler = PeerReviewScheduler(
            bot=bot,
            db=db,
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )
        scheduler._apply_item = AsyncMock()
        incoming = CalendarSnapshotItem(
            s21_event_id="booking:book-20",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2999-10-02T19:00:00.000Z",
            end_time="2999-10-02T19:30:00.000Z",
            role=ROLE_EVALUATOR,
            data={
                "event_slot_id": "slot-20",
                "booking_id": "book-20",
                "peer_login": "peer20",
            },
        )
        user = User(100, "mylogin", "encrypted")

        await scheduler._reconcile(user, [incoming])

        self.assertEqual(incoming.s21_event_id, "calendar-event-old")
        scheduler._apply_item.assert_awaited_once_with(user, incoming, existing)
        db.update_event_status.assert_not_awaited()

    async def test_partial_snapshot_never_cancels_unseen_booking(self):
        db = AsyncMock()
        existing = TrackedEvent(
            id=21,
            user_id=100,
            s21_event_id="known-booking",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2999-10-02T19:00:00.000Z",
            end_time="2999-10-02T19:30:00.000Z",
            role=ROLE_EVALUATED,
        )
        db.get_events = AsyncMock(side_effect=[[existing], [existing], [existing]])
        scheduler = PeerReviewScheduler(
            bot=AsyncMock(),
            db=db,
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )

        await scheduler._reconcile(
            User(100, "mylogin", "encrypted"),
            [],
            authoritative=False,
        )

        db.update_event_status.assert_not_awaited()

    async def test_future_booking_is_claimed_only_once(self):
        db = AsyncMock()
        db.claim_event_notification = AsyncMock(side_effect=[True, False])
        bot = AsyncMock()
        scheduler = PeerReviewScheduler(
            bot=bot,
            db=db,
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )
        event = TrackedEvent(
            id=10,
            user_id=100,
            s21_event_id="future",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2999-10-02T19:30:00.000Z",
        )

        self.assertTrue(await scheduler._notify_booking_once(100, event, "new"))
        self.assertFalse(await scheduler._notify_booking_once(100, event, "new"))
        bot.send_message.assert_awaited_once()
        self.assertIsNone(bot.send_message.await_args.kwargs["reply_markup"])
        db.claim_event_notification.assert_any_await(10, "future")

    async def test_failed_delivery_releases_claim_for_next_poll(self):
        db = AsyncMock()
        db.claim_event_notification = AsyncMock(return_value=True)
        scheduler = PeerReviewScheduler(
            bot=AsyncMock(),
            db=db,
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )
        scheduler._safe_send = AsyncMock(return_value=False)
        event = TrackedEvent(
            id=15,
            user_id=100,
            s21_event_id="future-failed-send",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2999-10-02T19:30:00.000Z",
            data={"booking_id": "booking-15"},
        )

        self.assertFalse(await scheduler._notify_booking_once(100, event, "new"))
        db.release_event_notification.assert_awaited_once_with(15, "booking-15")

    async def test_operational_notifications_follow_user_language(self):
        db = AsyncMock()
        db.upsert_event = AsyncMock(
            return_value=TrackedEvent(
                id=22,
                user_id=100,
                s21_event_id="open-en",
                type=EVENT_TYPE_SLOT,
                status=STATUS_OPEN,
                start_time="2999-10-02T19:00:00.000Z",
                end_time="2999-10-02T20:00:00.000Z",
                role=ROLE_EVALUATOR,
            )
        )
        bot = AsyncMock()
        scheduler = PeerReviewScheduler(
            bot=bot,
            db=db,
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )
        item = CalendarSnapshotItem(
            s21_event_id="open-en",
            type=EVENT_TYPE_SLOT,
            status=STATUS_OPEN,
            start_time="2999-10-02T19:00:00.000Z",
            end_time="2999-10-02T20:00:00.000Z",
            role=ROLE_EVALUATOR,
        )

        await scheduler._apply_item(
            User(100, "mylogin", "encrypted", language="en"), item, None
        )

        sent_text = bot.send_message.await_args.args[1]
        self.assertIn("New slot created", sent_text)
        self.assertNotIn("Создан новый слот", sent_text)

    async def test_past_booking_is_completed_without_notification(self):
        db = AsyncMock()
        bot = AsyncMock()
        scheduler = PeerReviewScheduler(
            bot=bot,
            db=db,
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )
        event = TrackedEvent(
            id=11,
            user_id=100,
            s21_event_id="past",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2020-10-02T19:30:00.000Z",
        )

        self.assertFalse(await scheduler._notify_booking_once(100, event, "old"))
        db.update_event_status.assert_awaited_once_with(11, "COMPLETED")
        bot.send_message.assert_not_awaited()

    async def test_ongoing_booking_is_not_hidden_or_completed(self):
        db = AsyncMock()
        bot = AsyncMock()
        scheduler = PeerReviewScheduler(
            bot=bot,
            db=db,
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )
        event = TrackedEvent(
            id=14,
            user_id=100,
            s21_event_id="ongoing",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2020-10-05T07:00:00.000Z",
            end_time="2999-10-05T18:00:00.000Z",
        )
        now = datetime(2026, 10, 5, 13, 24, tzinfo=timezone.utc)

        self.assertFalse(scheduler._starts_in_future(event))
        self.assertTrue(scheduler._ends_in_future(event, now=now))

        # The start has passed, so no late booking alert is sent; the row must
        # nevertheless stay BOOKED and visible until its end.
        self.assertFalse(await scheduler._notify_booking_once(100, event, "late"))
        db.update_event_status.assert_not_awaited()
        bot.send_message.assert_not_awaited()

    async def test_stale_reminder_for_open_slot_is_skipped(self):
        db = AsyncMock()
        db.get_event_by_id.return_value = TrackedEvent(
            id=12,
            user_id=100,
            s21_event_id="open-again",
            type=EVENT_TYPE_SLOT,
            status=STATUS_OPEN,
            start_time="2999-10-02T19:30:00.000Z",
        )
        bot = AsyncMock()
        scheduler = PeerReviewScheduler(
            bot=bot,
            db=db,
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )

        await scheduler.send_reminder(12, 15)

        bot.send_message.assert_not_awaited()
        db.get_user.assert_not_awaited()

    async def test_reminder_after_logout_is_skipped(self):
        db = AsyncMock()
        db.get_event_by_id.return_value = TrackedEvent(
            id=13,
            user_id=100,
            s21_event_id="booked-before-logout",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2999-10-02T19:30:00.000Z",
        )
        db.get_user.return_value = User(
            telegram_chat_id=100,
            s21_login=None,
            encrypted_password=None,
        )
        bot = AsyncMock()
        scheduler = PeerReviewScheduler(
            bot=bot,
            db=db,
            crypto=None,  # type: ignore
            api=None,  # type: ignore
        )

        await scheduler.send_reminder(13, 15)

        bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
