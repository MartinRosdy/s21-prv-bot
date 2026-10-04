"""Tests for scheduler notification text generation (4 triggers, both roles)."""

import unittest
from unittest.mock import AsyncMock
from bot.database.models import (
    EVENT_TYPE_PEER_REVIEW,
    EVENT_TYPE_SLOT,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_OPEN,
    TrackedEvent,
    User,
)
from bot.services.scheduler import (
    PeerReviewScheduler,
    _build_booked_instant_text,
)


class TestSchedulerNotifications(unittest.TestCase):
    def test_trigger_1_instant_booked_evaluator(self):
        text = _build_booked_instant_text(
            role=ROLE_EVALUATOR,
            peer_raw="peer_student",
            when="2 октября, 19:30 - 20:00",
            is_online=True,
            language="ru",
        )
        self.assertIn("К тебе записались на проверку!", text)
        self.assertIn("🔍 <b>Проверяющий</b>", text)
        self.assertIn("<code>peer_student</code>", text)
        self.assertIn("🌐 Онлайн", text)

    def test_trigger_1_instant_booked_evaluated(self):
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

    def test_trigger_2_t15_evaluator_and_evaluated(self):
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

    def test_trigger_3_t2_and_trigger_4_t0(self):
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
        db.claim_event_notification.assert_any_await(10, "future")

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
