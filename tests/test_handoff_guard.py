import os
import sqlite3
import unittest
from unittest.mock import patch
from scripts import outreach_scheduler, creator_data_agent
from scripts.handoff_guard import require_live_opt_in


class HandoffGuardTests(unittest.TestCase):
    def test_no_keychain_or_network_without_explicit_opt_in(self):
        with patch.dict(os.environ, {}, clear=True), patch('subprocess.run') as proc:
            for fn in [outreach_scheduler.google_app_password, creator_data_agent.keychain_token]:
                with self.assertRaisesRegex(RuntimeError, 'disabled'):
                    fn()
            with sqlite3.connect(':memory:') as db:
                with self.assertRaisesRegex(RuntimeError, 'disabled'):
                    outreach_scheduler.refresh_access_token(db)
            proc.assert_not_called()

    def test_only_exact_opt_in_value_is_accepted(self):
        for value in ['', '0', 'true', 'yes']:
            with patch.dict(os.environ, {'VIDEOCODING_ALLOW_LIVE_OPERATIONS':value}):
                with self.assertRaises(RuntimeError): require_live_opt_in()
        with patch.dict(os.environ, {'VIDEOCODING_ALLOW_LIVE_OPERATIONS':'1'}):
            require_live_opt_in()
