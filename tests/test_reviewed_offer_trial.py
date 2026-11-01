import unittest
from unittest.mock import patch

from scripts import send_reviewed_offer_trial as sender


class ReviewedOfferTrialTests(unittest.TestCase):
    def setUp(self):
        self.row = {'conversation_id': 'thread', 'received_at': '2026-09-04T21:00:00Z'}

    def test_scope_and_copy(self):
        self.assertEqual(len(sender.IDS), 23)
        self.assertEqual(len(set(sender.IDS)), 23)
        self.assertIn(19897, sender.IDS)
        self.assertNotIn('If you’d rather', sender.BODY)
        self.assertIn('Let me know if you run into any issues — happy to help!', sender.BODY)

    def test_live_newer_inbound_blocks(self):
        with patch.object(sender.inbox_sync, 'graph_json', return_value={'value': [
            {'receivedDateTime': '2026-09-04T21:01:00Z'}
        ]}):
            with self.assertRaisesRegex(ValueError, 'newer inbox'):
                sender.mailbox_check('test-token', self.row)

    def test_prior_coupon_blocks_even_if_local_sync_missed_it(self):
        with patch.object(sender.inbox_sync, 'graph_json', side_effect=[{'value': []}, {'value': [
            {'sentDateTime': '2026-09-03T21:00:00Z', 'body': {'content': 'FREETEST100'}}
        ]}]):
            with self.assertRaisesRegex(ValueError, 'coupon reply'):
                sender.mailbox_check('test-token', self.row)

    def test_incomplete_thread_blocks(self):
        with patch.object(sender.inbox_sync, 'graph_json', return_value={'value': [], '@odata.nextLink': 'more'}):
            with self.assertRaisesRegex(ValueError, 'pagination'):
                sender.mailbox_check('test-token', self.row)

    def test_unchanged_thread_passes(self):
        with patch.object(sender.inbox_sync, 'graph_json', return_value={'value': []}):
            sender.mailbox_check('test-token', self.row)
