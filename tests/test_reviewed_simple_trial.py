import unittest
import hashlib
from unittest.mock import patch
from scripts import send_reviewed_simple_trial as sender


class SimpleTrialTests(unittest.TestCase):
    row = {'sender_email': 'creator@example.com', 'received_at': '2026-09-04T21:00:00Z'}

    def test_exact_approved_scope(self):
        self.assertEqual(sender.IDS, (19903, 19856, 19783, 19749, 19748, 19746, 19743, 19722, 19706, 19698))

    def test_individual_copy_requires_exact_review_and_supported_stage(self):
        body = 'Hi,\n\nCould you confirm the currency of your quote?\n\nBest,\nAlhazar'
        review = dict(template_key='rate_received', reviewed_body=body,
                      reviewed_body_digest=hashlib.sha256(body.encode()).hexdigest(),
                      context_digest='context', template_digest='template',
                      questions_answered=True, review_note='Currency omitted in latest quote.')
        self.assertEqual(sender.reviewed_body(review, 'default'), body)
        for field, value in [('reviewed_body', body + '!'), ('context_digest', ''),
                             ('template_digest', ''), ('questions_answered', False),
                             ('review_note', ''), ('template_key', 'coupon_test')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                sender.reviewed_body({**review, field: value}, 'default')

    def test_individual_copy_rejects_unrendered_or_unreviewed_access(self):
        for body in ['Hi {{channel_name}}', r'Hi\nThanks', 'Here is FREETEST100', '']:
            review = dict(template_key='rate_received', reviewed_body=body,
                          reviewed_body_digest=hashlib.sha256(body.encode()).hexdigest(),
                          context_digest='context', template_digest='template',
                          questions_answered=True, review_note='review')
            with self.subTest(body=body), self.assertRaises(ValueError):
                sender.reviewed_body(review, 'default')
        self.assertEqual(sender.reviewed_body({}, 'unchanged'), 'unchanged')

    def test_other_thread_coupon_blocks(self):
        with patch.object(sender.inbox_sync, 'graph_json', side_effect=[{'value': []}, {'value': [
            {'sentDateTime': '2026-09-03T21:00:00Z', 'body': {'content': 'FREETEST100'}}]}]):
            with self.assertRaisesRegex(ValueError, 'another thread'):
                sender.check_addresses('token', self.row)

    def test_custom_campaign_reply_keeps_activation_and_renewal_disclosure(self):
        disclosure = ('To activate it:\n\n1. Sign in\n2. Enter FREETEST100\n'
                      '3. Check the total is $0.00\n\n'
                      'Checkout starts a subscription and renews automatically unless cancelled. '
                      'For cardless testing, send your registration email.\n\n')
        default = 'Intro\n\nFREETEST100\n\n' + disclosure + 'Please try it with your own video.\n\nBest'
        body = 'Your requested campaign terms remain unconfirmed.\n\nFREETEST100\n\n' + disclosure + 'Best'
        review = dict(template_key='coupon_test_campaign_details', reviewed_body=body,
                      reviewed_body_digest=hashlib.sha256(body.encode()).hexdigest(),
                      context_digest='context', template_digest='template',
                      questions_answered=['Format and payment terms remain unset; no booking.'],
                      review_note='Full thread reviewed; explicit access request.')
        self.assertEqual(sender.reviewed_body(review, default), body)
        for fragment in ['2. Enter FREETEST100\n', 'and renews automatically unless cancelled. ',
                         'For cardless testing, send your registration email.']:
            changed = body.replace(fragment, '')
            with self.subTest(fragment=fragment), self.assertRaisesRegex(ValueError, 'renewal disclosure'):
                sender.reviewed_body({**review, 'reviewed_body': changed,
                                     'reviewed_body_digest': hashlib.sha256(changed.encode()).hexdigest()}, default)

    def test_other_thread_new_reply_blocks(self):
        with patch.object(sender.inbox_sync, 'graph_json', return_value={'value': [
            {'receivedDateTime': '2026-09-04T22:00:00Z'}]}):
            with self.assertRaisesRegex(ValueError, 'newer'):
                sender.check_addresses('token', self.row)

    def test_activation_context_requires_exact_approved_cardless_disclosure(self):
        core = ('Done — I’ve activated one month for creator@example.com.\n\n'
                'You don’t need to add a card. The access ends automatically after 30 days, '
                'so there is no automatic charge.\n\n')
        default = 'Hi,\n\n' + core + 'Once you’ve tried it, let me know.\n\nBest'
        body = 'Hi,\n\n' + core + 'No campaign or commercial terms have been approved.\n\nBest'
        review = dict(template_key='membership_activated', reviewed_body=body,
                      reviewed_body_digest=hashlib.sha256(body.encode()).hexdigest(),
                      context_digest='context', template_digest='template',
                      questions_answered=['Activation and all requested campaign terms.'],
                      review_note='Full review; runtime still requires exact live verified cardless grant.')
        self.assertEqual(sender.reviewed_body(review, default), body)
        for fragment in ('creator@example.com', 'no automatic charge', '30 days'):
            changed=body.replace(fragment,'')
            with self.subTest(fragment=fragment), self.assertRaisesRegex(ValueError,'cardless expiry disclosure'):
                sender.reviewed_body({**review,'reviewed_body':changed,
                    'reviewed_body_digest':hashlib.sha256(changed.encode()).hexdigest()},default)

    def test_verified_registration_renders_even_if_latest_message_omits_email(self):
        for extracted in ('{}', '{"registered_email":"obsolete@example.com"}'):
            row=dict(original_subject='Re: Test',extracted_json=extracted,
                     channel_name='Creator',template_subject='Re: {{original_subject}}',
                     template_body='Activated for {{registered_email}}.')
            _,body=sender.render_reviewed_template(row,'verified@example.com')
            self.assertEqual(body,'Activated for verified@example.com.')
            self.assertEqual(row['extracted_json'],extracted)

    def test_incomplete_history_blocks(self):
        with patch.object(sender.inbox_sync, 'graph_json', return_value={'@odata.nextLink': 'more'}):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                sender.check_addresses('token', self.row)

    def test_unchanged_passes(self):
        with patch.object(sender.inbox_sync, 'graph_json', return_value={'value': []}):
            sender.check_addresses('token', self.row)

    def test_reply_target_checks_exact_provider_identity(self):
        row = {**self.row, 'inbox_event_id': 'graph/id=',
               'conversation_id': 'conversation', 'internet_message_id': '<mail@example.com>'}
        message = {'id': row['inbox_event_id'], 'conversationId': row['conversation_id'],
                   'internetMessageId': row['internet_message_id'],
                   'receivedDateTime': row['received_at'],
                   'from': {'emailAddress': {'address': 'CREATOR@example.com'}}}
        with patch.object(sender.inbox_sync, 'graph_json', return_value=message) as read:
            sender.check_reply_target('token', row)
            self.assertIn('graph%2Fid%3D?', read.call_args.args[1])
        for field, value in [('id', 'other'), ('conversationId', 'other'),
                             ('internetMessageId', '<other>'), ('receivedDateTime', None),
                             ('receivedDateTime', '2026-09-04T22:00:00Z'),
                             ('from', {'emailAddress': {'address': 'other@example.com'}})]:
            with self.subTest(field=field), patch.object(sender.inbox_sync, 'graph_json',
                    return_value={**message, field: value}), self.assertRaisesRegex(ValueError, 'differs'):
                sender.check_reply_target('token', row)

    def test_missing_target_is_held_without_discovering_or_substituting_another_email(self):
        row = {**self.row, 'inbox_event_id': 'missing', 'conversation_id': 'conversation',
               'internet_message_id': '<mail@example.com>'}
        with patch.object(sender.inbox_sync, 'graph_json',
                          side_effect=RuntimeError('Microsoft Graph HTTP 404: ErrorItemNotFound')) as read:
            with self.assertRaisesRegex(RuntimeError, '404'):
                sender.check_reply_target('token', row)
            self.assertEqual(read.call_count, 1)
        with patch.object(sender.inbox_sync, 'graph_json') as read:
            with self.assertRaisesRegex(ValueError, 'Microsoft message target'):
                sender.check_reply_target('token', {**row, 'inbox_event_id': 'gmail:message'})
            read.assert_not_called()

    def test_completed_test_followup_allows_prior_coupon_but_not_newer_reply(self):
        with patch.object(sender.inbox_sync, 'graph_json', side_effect=[{'value': []}, {'value': [
            {'sentDateTime': '2026-09-03T21:00:00Z', 'body': {'content': 'FREETEST100'}}]}]):
            sender.check_addresses('token', self.row, require_unused_access=False)
        with patch.object(sender.inbox_sync, 'graph_json', return_value={'value': [
            {'receivedDateTime': '2026-09-04T22:00:00Z'}]}):
            with self.assertRaisesRegex(ValueError, 'newer'):
                sender.check_addresses('token', self.row, require_unused_access=False)
