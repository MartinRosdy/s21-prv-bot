"""Runtime configuration validation tests."""

import unittest

from pydantic import ValidationError

from bot.core.config import Settings


class TestSettings(unittest.TestCase):
    def _settings(self, interval: int) -> Settings:
        return Settings(
            BOT_TOKEN="123456:test-token",
            ENCRYPTION_KEY="test-key",
            POLL_INTERVAL_SECONDS=interval,
            _env_file=None,
        )

    def test_poll_interval_accepts_operational_range(self):
        self.assertEqual(self._settings(5).poll_interval_seconds, 5)
        self.assertEqual(self._settings(3600).poll_interval_seconds, 3600)

    def test_poll_interval_rejects_pathological_values(self):
        for interval in (0, 4, 3601):
            with self.subTest(interval=interval), self.assertRaises(ValidationError):
                self._settings(interval)


if __name__ == "__main__":
    unittest.main()
