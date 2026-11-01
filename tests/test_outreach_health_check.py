from datetime import datetime
import unittest

from scripts.outreach_health_check import CAMPAIGN_ZONE, selected_phase


class OutreachHealthScheduleTests(unittest.TestCase):
    def test_preflight_and_zero_send_windows_use_new_york_time(self):
        self.assertEqual(selected_phase(datetime(2026, 9, 1, 7, 55, tzinfo=CAMPAIGN_ZONE), None), "preflight")
        self.assertEqual(selected_phase(datetime(2026, 9, 1, 8, 5, tzinfo=CAMPAIGN_ZONE), None), "zero-send")
        self.assertIsNone(selected_phase(datetime(2026, 9, 1, 8, 1, tzinfo=CAMPAIGN_ZONE), None))


if __name__ == "__main__":
    unittest.main()
