"""Regression tests for the compact main/reviews navigation."""

import unittest

from bot.database.models import (
    EVENT_TYPE_PEER_REVIEW,
    EVENT_TYPE_SLOT,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_OPEN,
    TrackedEvent,
)
from bot.handlers.start import help_text
from bot.keyboards.menu import build_main_menu_kb, main_menu_text
from bot.keyboards.reviews import build_slots_list_kb


class TestMenuUi(unittest.TestCase):
    def test_main_menu_has_copyable_login_stats_and_one_button(self):
        text = main_menu_text(
            "ru",
            "peer<&",
            evaluator_count=2,
            evaluated_count=1,
        )
        self.assertIn("<code>peer&lt;&amp;</code>", text)
        self.assertIn("🔍 Я проверяющий: 2 | 📖 Меня проверяют: 1", text)
        self.assertNotIn("А для занятого", text)

        keyboard = build_main_menu_kb("ru")
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].callback_data, "menu_reviews")

    def test_reviews_filters_are_compact_and_have_no_open_category(self):
        slots = [
            TrackedEvent(
                id=1,
                user_id=100,
                s21_event_id="open",
                type=EVENT_TYPE_SLOT,
                status=STATUS_OPEN,
                start_time="2999-10-02T19:00:00.000Z",
                role=ROLE_EVALUATOR,
            ),
            TrackedEvent(
                id=2,
                user_id=100,
                s21_event_id="booked",
                type=EVENT_TYPE_PEER_REVIEW,
                status=STATUS_BOOKED,
                start_time="2999-10-02T20:00:00.000Z",
                role=ROLE_EVALUATED,
            ),
        ]
        keyboard = build_slots_list_kb(slots, "ru")
        self.assertEqual(
            [button.text for button in keyboard.inline_keyboard[0]],
            ["🔍 (1)", "📖 (1)"],
        )
        callbacks = {
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        }
        self.assertNotIn("slot_cat:open", callbacks)

    def test_help_has_exact_t15_line(self):
        text = help_text("ru")
        exact = "2. ⏳ За 15 минут: напоминание с никнеймом пира, ролью и форматом"
        self.assertIn(exact, text)
        self.assertNotIn("кнопка смены формата", text.lower())


if __name__ == "__main__":
    unittest.main()
