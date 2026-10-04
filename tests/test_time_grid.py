"""Tests for the five-step time grid and 15-minute rule."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bot.core.utils import TASHKENT_TZ
from bot.keyboards.reviews import (
    MIN_BOOKING_LEAD_MINUTES,
    MIN_SLOT_DURATION_MINUTES,
    SlotDurationError,
    _is_slot_time_valid_today,
    build_hour_picker_kb,
    build_minute_picker_kb,
    compose_slot_datetimes,
    is_slot_start_allowed,
)


class TestTimeGrid(unittest.TestCase):
    def test_15_minute_rule_logic(self):
        """
        At 18:16 Tashkent time:
        - 18:00 is past -> False
        - 18:15 is past -> False
        - 18:30 is in 14 minutes (< 15 min) -> False
        - 18:45 is in 29 minutes (>= 15 min) -> True
        - 19:00 is in 44 minutes -> True
        """
        mock_now = datetime(2026, 10, 2, 18, 16, tzinfo=TASHKENT_TZ)
        self.assertFalse(_is_slot_time_valid_today(18, 0, mock_now))
        self.assertFalse(_is_slot_time_valid_today(18, 15, mock_now))
        self.assertFalse(_is_slot_time_valid_today(18, 30, mock_now))
        self.assertTrue(_is_slot_time_valid_today(18, 45, mock_now))
        self.assertTrue(_is_slot_time_valid_today(19, 0, mock_now))

    def test_hour_picker_replaces_past_hours_with_dot(self):
        """
        When mock time is 20:50:
        Hours 08..20 have no valid minutes left (since 20:45 is past, 21:00 is in 10 min < 15 min).
        Hours 08..20 should be displayed as '•' with callback act='disabled'.
        Hour 21 has 21:15, 21:30, 21:45 valid (>= 15 min), so it shows '21'.
        """
        mock_now = datetime(2026, 10, 2, 20, 50, tzinfo=TASHKENT_TZ)
        with patch("bot.keyboards.reviews.utc_now", return_value=mock_now.astimezone(TASHKENT_TZ)):
            kb = build_hour_picker_kb(which="sh", day_offset=0)
            buttons_by_text = {}
            for row in kb.inline_keyboard:
                for btn in row:
                    buttons_by_text[btn.text] = btn.callback_data

            self.assertIn("•", buttons_by_text)
            self.assertEqual(buttons_by_text["•"], "sw:disabled:0")

            # Hour 21 and 22 should be available as normal digits
            self.assertIn("21", buttons_by_text)
            self.assertEqual(buttons_by_text["21"], "sw:sh:21")
            self.assertIn("22", buttons_by_text)

    def test_minute_picker_replaces_invalid_minutes_with_dot(self):
        """
        At 18:16 on today:
        In hour 18:
        - 00, 15, 30 are invalid (< 15 min from 18:16) -> '•'
        - 45 is valid (>= 15 min) -> '45'
        """
        mock_now = datetime(2026, 10, 2, 18, 16, tzinfo=TASHKENT_TZ)
        with patch("bot.keyboards.reviews.utc_now", return_value=mock_now.astimezone(TASHKENT_TZ)):
            kb = build_minute_picker_kb(which="sm", day_offset=0, hour=18)
            row = kb.inline_keyboard[0]
            # row has 4 buttons: 00, 15, 30, 45
            self.assertEqual(len(row), 4)
            self.assertEqual(row[0].text, "•")
            self.assertEqual(row[0].callback_data, "sw:disabled:0")
            self.assertEqual(row[1].text, "•")
            self.assertEqual(row[1].callback_data, "sw:disabled:0")
            self.assertEqual(row[2].text, "•")
            self.assertEqual(row[2].callback_data, "sw:disabled:0")
            self.assertEqual(row[3].text, "45")
            self.assertEqual(row[3].callback_data, "sw:sm:45")

    def test_hour_picker_has_24_hours_without_leading_zero(self):
        kb = build_hour_picker_kb(which="sh", day_offset=1)
        buttons = [button for row in kb.inline_keyboard for button in row]
        hours = buttons[:24]
        self.assertEqual([button.text for button in hours], [str(h) for h in range(24)])
        self.assertEqual(hours[8].callback_data, "sw:sh:8")

    def test_future_day_end_before_start_is_disabled(self):
        kb = build_hour_picker_kb(
            which="eh",
            day_offset=1,
            start_hour=18,
            start_minute=30,
        )
        hour_buttons = [
            button
            for row in kb.inline_keyboard
            for button in row
            if button.callback_data and button.callback_data.startswith("sw:")
        ]
        self.assertEqual(hour_buttons[0].text, "•")
        self.assertEqual(hour_buttons[18].text, "•")
        self.assertEqual(hour_buttons[19].text, "19")

    def test_15_minute_end_is_disabled(self):
        kb = build_minute_picker_kb(
            which="em",
            day_offset=1,
            hour=18,
            start_hour=18,
            start_minute=30,
        )
        row = kb.inline_keyboard[0]
        self.assertEqual(row[3].text, "•")
        self.assertEqual(row[3].callback_data, "sw:disabled:0")

    def test_late_start_without_30_minute_end_is_disabled(self):
        kb = build_minute_picker_kb(which="sm", day_offset=1, hour=23)
        row = kb.inline_keyboard[0]
        self.assertEqual([button.text for button in row], ["00", "15", "•", "•"])

    def test_final_guard_rejects_stale_callback(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)
        self.assertFalse(
            is_slot_start_allowed(now + timedelta(minutes=14), now=now)
        )
        self.assertTrue(
            is_slot_start_allowed(now + timedelta(minutes=15), now=now)
        )

    def test_compose_rejects_non_positive_interval(self):
        with self.assertRaises(ValueError):
            compose_slot_datetimes(
                day_offset=1,
                start_hour=18,
                start_minute=30,
                end_hour=18,
                end_minute=15,
            )

    def test_compose_rejects_15_minute_slot(self):
        with self.assertRaises(SlotDurationError):
            compose_slot_datetimes(
                day_offset=1,
                start_hour=18,
                start_minute=30,
                end_hour=18,
                end_minute=45,
            )
        self.assertEqual(MIN_SLOT_DURATION_MINUTES, 30)

    def test_compose_accepts_30_minute_slot(self):
        start, end = compose_slot_datetimes(
            day_offset=1,
            start_hour=18,
            start_minute=30,
            end_hour=19,
            end_minute=0,
        )
        self.assertEqual(end - start, timedelta(minutes=30))


if __name__ == "__main__":
    unittest.main()
