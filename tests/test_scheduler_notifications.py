"""Tests for scheduler notification text generation (4 triggers, both roles)."""

import unittest
from bot.database.models import (
    EVENT_TYPE_PEER_REVIEW,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    TrackedEvent,
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


if __name__ == "__main__":
    unittest.main()
