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
from bot.handlers.auth import _ASK_LOGIN, _ASK_PASSWORD, _LOGIN_NOTICE
from bot.handlers.start import FIRST_START_PROMPT, help_text
from bot.keyboards.menu import build_help_kb, build_main_menu_kb, main_menu_text
from bot.keyboards.reviews import build_slot_card_kb, build_slots_list_kb
from bot.routers.reviews import _TEXTS, _slot_card_text


class TestMenuUi(unittest.TestCase):
    def test_main_menu_has_copyable_login_stats_and_one_button(self):
        text = main_menu_text(
            "ru",
            "Peer<&",
            evaluator_count=2,
            evaluated_count=1,
        )
        self.assertIn("<code>peer&lt;&amp;</code>", text)
        self.assertIn("🔍 Я проверяющий: 2 | 📖 Меня проверяют: 1", text)
        self.assertIn("А для занятого — переключить на онлайн формат", text)

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
                data={"peer_login": "peer_student"},
            ),
        ]
        keyboard = build_slots_list_kb(slots, "ru")
        self.assertEqual(
            [button.text for button in keyboard.inline_keyboard[0]],
            ["🔍 1", "📖 1"],
        )
        callbacks = {
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        }
        self.assertNotIn("slot_cat:open", callbacks)
        button_text = " ".join(
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        )
        self.assertNotIn("peer_student", button_text)

    def test_peer_login_is_copyable_in_slot_card(self):
        slot = TrackedEvent(
            id=3,
            user_id=100,
            s21_event_id="booked-card",
            type=EVENT_TYPE_PEER_REVIEW,
            status=STATUS_BOOKED,
            start_time="2999-10-02T20:00:00.000Z",
            role=ROLE_EVALUATED,
            data={"peer_login": "peer_student", "is_online": False},
        )
        self.assertIn("`peer_student`", _slot_card_text(slot, "ru"))
        button_texts = [
            button.text
            for row in build_slot_card_kb(slot, "ru").inline_keyboard
            for button in row
        ]
        self.assertIn("🌐 Переключить на Онлайн", button_texts)

    def test_first_start_and_auth_prompts_are_short(self):
        self.assertEqual(
            FIRST_START_PROMPT,
            "Выберите язык / Choose language / Tilni tanlang",
        )
        self.assertEqual(_ASK_LOGIN["ru"], "Введите ваш логин от Школы 21:")
        self.assertEqual(_ASK_PASSWORD["ru"], "Введите ваш пароль от Школы 21:")
        self.assertIn("<blockquote>", _LOGIN_NOTICE["ru"])
        self.assertIn("AES-128", _LOGIN_NOTICE["ru"])

    def test_empty_slots_text_is_exact(self):
        self.assertEqual(
            _TEXTS["ru"]["no_slots"],
            "📬 У вас пока нет запланированных проверок и свободных слотов",
        )

    def test_help_has_exact_t15_line(self):
        text = help_text("ru")
        exact = "2. ⏳ За 15 минут: напоминание с никнеймом пира, ролью и форматом"
        self.assertIn(exact, text)
        self.assertNotIn("кнопка смены формата", text.lower())
        self.assertIn("Для занятого слота можно сменить формат на онлайн", text)
        self.assertIn(
            "• 🔍 Я проверяющий (Evaluator): твои открытые ревью слоты, где ты оцениваешь чужой проект",
            text,
        )

    def test_help_localizations_have_identical_structure_and_home_button(self):
        texts = [help_text(lang) for lang in ("ru", "en", "uz")]
        self.assertEqual({len(text.splitlines()) for text in texts}, {31})
        for text in texts:
            for marker in ("📖", "🤖", "🧭", "⏳", "🌐", "🔔", "⌨️", "👨‍💻"):
                self.assertIn(marker, text)

        expected = {
            "ru": "🏠 Главное меню",
            "en": "🏠 Main menu",
            "uz": "🏠 Asosiy menyu",
        }
        for lang, label in expected.items():
            button = build_help_kb(lang).inline_keyboard[0][0]
            self.assertEqual(button.text, label)
            self.assertEqual(button.callback_data, "menu_home")


if __name__ == "__main__":
    unittest.main()
