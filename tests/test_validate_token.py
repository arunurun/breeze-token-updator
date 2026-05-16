from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import validate_token  # noqa: E402


class ValidateTokenMarketHoursTests(unittest.TestCase):
    def test_parse_ist_holidays_env_accepts_multiple_delimiters(self) -> None:
        parsed = validate_token.parse_ist_holidays_env("2026-01-26,2026-08-15\n2026-10-02;2026-11-14")
        self.assertEqual(
            parsed,
            {
                date(2026, 1, 26),
                date(2026, 8, 15),
                date(2026, 10, 2),
                date(2026, 11, 14),
            },
        )

    @patch("validate_token.load_nse_holidays_ist", return_value=set())
    def test_market_closed_reason_weekend(self, _mock_holidays: object) -> None:
        saturday_ist = datetime(2026, 5, 16, 6, 0)
        reason = validate_token.market_closed_reason_ist(saturday_ist)
        self.assertEqual(reason, "Indian market is closed (weekend).")

    @patch("validate_token.load_nse_holidays_ist", return_value={date(2026, 1, 26)})
    @patch.dict(os.environ, {}, clear=False)
    def test_market_closed_reason_nse_holiday(self, _mock_holidays: object) -> None:
        monday_ist = datetime(2026, 1, 26, 6, 0)
        reason = validate_token.market_closed_reason_ist(monday_ist)
        self.assertEqual(reason, "Indian market is closed (NSE trading holiday).")

    @patch("validate_token.load_nse_holidays_ist", return_value=set())
    @patch.dict(os.environ, {"MARKET_HOLIDAYS_IST": "2026-03-30"}, clear=False)
    def test_market_closed_reason_env_holiday_override(self, _mock_holidays: object) -> None:
        holiday_ist = datetime(2026, 3, 30, 6, 0)
        reason = validate_token.market_closed_reason_ist(holiday_ist)
        self.assertEqual(reason, "Indian market is closed (NSE trading holiday).")

    @patch("validate_token.load_nse_holidays_ist", return_value=set())
    @patch.dict(os.environ, {}, clear=False)
    def test_market_closed_reason_open_day(self, _mock_holidays: object) -> None:
        open_day_ist = datetime(2026, 3, 31, 6, 0)
        reason = validate_token.market_closed_reason_ist(open_day_ist)
        self.assertIsNone(reason)

    @patch("validate_token.datetime")
    @patch("validate_token.market_closed_reason_ist", return_value="Indian market is closed (weekend).")
    @patch("validate_token.load_dotenv")
    def test_main_exits_early_when_market_closed(
        self,
        _mock_dotenv: object,
        _mock_closed_reason: object,
        mock_datetime: object,
    ) -> None:
        mock_datetime.now.return_value = datetime(2026, 5, 16, 6, 0)
        exit_code = validate_token.main()
        self.assertEqual(exit_code, 0)

    @patch("validate_token.send_email_alert")
    @patch("validate_token.datetime")
    @patch("validate_token.market_closed_reason_ist", return_value="Indian market is closed (weekend).")
    @patch("validate_token.load_dotenv")
    @patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch"}, clear=False)
    def test_main_sends_email_on_manual_market_closed(
        self,
        _mock_dotenv: object,
        _mock_closed_reason: object,
        mock_datetime: object,
        mock_send_email: object,
    ) -> None:
        mock_datetime.now.return_value = datetime(2026, 5, 16, 6, 0)
        exit_code = validate_token.main()
        self.assertEqual(exit_code, 0)
        mock_send_email.assert_called_once()

    @patch("validate_token.send_email_alert")
    @patch("validate_token.datetime")
    @patch("validate_token.market_closed_reason_ist", return_value="Indian market is closed (weekend).")
    @patch("validate_token.load_dotenv")
    @patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule"}, clear=False)
    def test_main_does_not_send_email_on_scheduled_market_closed(
        self,
        _mock_dotenv: object,
        _mock_closed_reason: object,
        mock_datetime: object,
        mock_send_email: object,
    ) -> None:
        mock_datetime.now.return_value = datetime(2026, 5, 16, 6, 0)
        exit_code = validate_token.main()
        self.assertEqual(exit_code, 0)
        mock_send_email.assert_not_called()


class WorkflowScheduleTests(unittest.TestCase):
    def test_validate_workflow_uses_6am_ist_weekdays(self) -> None:
        workflow_path = ROOT_DIR / ".github" / "workflows" / "validate_token.yml"
        text = workflow_path.read_text(encoding="utf-8")
        self.assertIn('cron: "30 0 * * 1-5"', text)


if __name__ == "__main__":
    unittest.main()
