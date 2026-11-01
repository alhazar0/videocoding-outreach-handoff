"""Regression checks for reviewed replies; all database/provider effects mocked."""
import contextlib
import io
import json
import hashlib
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, patch
from scripts import send_reviewed_simple_trial as sender


class RemainingReplyGuardTests(unittest.TestCase):
    def run_case(self, key, *, fields=None, payload=None, prior_body='', approval='approved', quote=None, send=False, send_result=(True, 'ok'), twice=False, reviewed=None, rendered='approved body', target_error=None):
        row = dict(id=1, contact_id=1, inbox_event_id='event', sender_email='creator@example.com',
                   email='creator@example.com', registered_email=None, rate_info=None,
                   coupon_sent_at='2026-09-01', membership_status='none', test_status='wants_test', template_key=key)
        row.update(fields or {})
        db = MagicMock()
        def execute(sql, args=()):
            cursor = MagicMock()
            if 'SELECT 1 FROM outreach_sent_messages' in sql:
                cursor.fetchone.return_value = (1,)
            elif 'SELECT * FROM outreach_templates' in sql:
                cursor.fetchone.return_value = dict(enabled=1, approval_status=approval, body='approved body', subject='reply', version=1)
            elif 'SELECT body_text FROM outreach_sent_messages' in sql:
                cursor.__iter__.return_value = iter([{'body_text': prior_body}] if prior_body else [])
            elif 'SELECT rate_info FROM contacts' in sql:
                cursor.fetchone.return_value = {'rate_info': row['rate_info']}
            return cursor
        db.execute.side_effect = execute
        review = dict(id=1, template_key=key)
        review.update(reviewed or {})
        if quote:
            review['latest_rate_summary'] = quote
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            for obj, name, value in [
                (sender.product, 'connect', db),
                (sender.product, 'refresh_access_token', 'mock-token'),
                (sender.product, 'keychain_token', 'mock-product-token'),
                (sender.product, 'creator_lookup', (200, payload or {'success': True, 'found': False})),
                (sender.guard, 'check_local', row), (sender.guard, 'mailbox_check', None),
                (sender, 'check_addresses', None), (sender.batch, 'ensure_batch_schema', None),
                (sender.batch, 'render', ('reply', rendered)),
                (sender, 'context_digest', 'reviewed-history'),
                (sender.batch, 'record_audit', None), (sender.batch, 'record_sending', None),
                (sender.batch, 'record_result', None),
            ]:
                stack.enter_context(patch.object(obj, name, return_value=value))
            stack.enter_context(patch.object(sender, 'check_reply_target',
                                            return_value=None, side_effect=target_error))
            mail = stack.enter_context(patch.object(sender.batch, 'send_reply', return_value=send_result))
            with contextlib.redirect_stdout(output):
                sender.run(send, [review, {**review, 'id': 2}] if twice else [review], 'unit-test')
            calls = mail.call_count
        return json.loads(output.getvalue().splitlines()[-1])['results'], calls

    def held(self, key, reason, **kwargs):
        results, calls = self.run_case(key, send=True, **kwargs)
        self.assertEqual(results[0]['status'], 'held')
        self.assertIn(reason, results[0]['reason'])
        self.assertEqual(calls, 0)

    def test_future_ack_does_not_override_claim(self):
        self.held('test_planned_ack', 'testing state advanced', fields={'test_status': 'claimed'})

    def test_future_ack_holds_actual_usage(self):
        self.held('test_planned_ack', 'testing state advanced', payload={'success': True, 'found': True, 'creator': {'activity': {'totalTasks': 1}}})

    def test_account_check_does_not_reask_known_email(self):
        self.held('test_claim_checking', 'account already known', fields={'registered_email': 'confirmed@example.com'})

    def test_account_check_does_not_reask_verified_test(self):
        self.held('test_claim_checking', 'account already known', payload={'success': True, 'found': True, 'creator': {'activity': {'completedTasks': 1}}})

    def test_cardless_question_holds_existing_access(self):
        self.held('ask_registered_email', 'registration known', payload={'success': True, 'found': True, 'creator': {'subscription': {'status': 'active'}}})

    def test_missing_account_reply_requires_current_exact_negative_lookup(self):
        fields = {'registered_email': 'registered@example.com'}
        reviewed = {'expected_registered_email': 'registered@example.com'}
        negative = {'success': True, 'found': False, 'email': 'registered@example.com'}
        results, calls = self.run_case('account_not_found', fields=fields, reviewed=reviewed, payload=negative)
        self.assertEqual(results, [{'id': 1, 'status': 'ready'}])
        self.assertEqual(calls, 0)
        for changed_fields, changed_review, payload in [
            ({'registered_email': None}, reviewed, negative),
            (fields, {}, negative),
            (fields, reviewed, {**negative, 'found': True}),
            (fields, reviewed, {'success': True, 'email': 'registered@example.com'}),
            (fields, reviewed, {**negative, 'email': 'different@example.com'}),
        ]:
            with self.subTest(payload=payload, fields=changed_fields, reviewed=changed_review):
                self.held('account_not_found', 'exact reviewed email', fields=changed_fields,
                          reviewed=changed_review, payload=payload)

    def test_activation_requires_exact_verified_cardless_month_evidence(self):
        expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        review = {**self.review_evidence(), 'review_note': 'Exact explicit cardless request and grant reviewed.',
                  'expected_registered_email': 'registered@example.com',
                  'expected_product_user_id': 'product-user', 'expected_membership_expires_at': expiry}
        creator = {'id': 'product-user', 'email': 'registered@example.com', 'emailVerified': True,
                   'subscription': {'status': 'active', 'manualGrant': True, 'stripeBacked': False,
                                    'currentPeriodEnd': expiry}}
        fields = {'registered_email': 'registered@example.com'}
        payload = {'success': True, 'found': True, 'creator': creator}
        results, calls = self.run_case('membership_activated', fields=fields, reviewed=review, payload=payload)
        self.assertEqual(results, [{'id': 1, 'status': 'ready'}]); self.assertEqual(calls, 0)
        for patch_creator in [{'emailVerified': False}, {'email': 'other@example.com'}, {'id': 'other'},
                              *[{'subscription': {**creator['subscription'], **change}} for change in
                                [{'manualGrant': False}, {'stripeBacked': True}, {'status': 'none'},
                                 {'currentPeriodEnd': (datetime.now(timezone.utc)+timedelta(days=2)).isoformat()},
                                 {'currentPeriodEnd': (datetime.now(timezone.utc)+timedelta(days=30,hours=1)).isoformat()}]]]:
            with self.subTest(change=patch_creator):
                self.held('membership_activated', 'confirmed cardless month', fields=fields, reviewed=review,
                          payload={**payload, 'creator': {**creator, **patch_creator}})
        self.held('membership_activated', 'confirmed cardless month', fields=fields, reviewed={}, payload=payload)

    def test_campaign_does_not_ignore_known_rate(self):
        self.held('campaign_details_after_coupon', 'no known quote', fields={'rate_info': 'USD 200'})

    def test_identical_ack_does_not_loop(self):
        self.held('test_planned_ack', 'identical acknowledgement', prior_body='approved   body')

    def test_rate_ack_requires_reviewed_quote(self):
        self.held('rate_received', 'reviewed quote required')

    def test_coupon_rate_ack_requires_reviewed_quote(self):
        self.held('coupon_test_rate_received', 'reviewed quote required')

    def test_quote_snapshot_guard_matches_dry_run_and_send(self):
        quote = '20k per video; currency unspecified'
        for send in (False, True):
            for reviewed in ({}, {'stored_rate_digest': 'old-snapshot'}):
                with self.subTest(send=send, reviewed=reviewed):
                    results, calls = self.run_case('rate_received', send=send,
                        fields={'rate_info': quote}, quote='INR 20,000 integration', reviewed=reviewed)
                    self.assertEqual(results[0]['status'], 'held')
                    self.assertIn('quote changed', results[0]['reason'])
                    self.assertEqual(calls, 0)
        results, calls = self.run_case('rate_received', fields={'rate_info': quote},
            quote='INR 20,000 integration',
            reviewed={'stored_rate_digest': hashlib.sha256(quote.encode()).hexdigest()})
        self.assertEqual(results, [{'id': 1, 'status': 'ready'}])
        self.assertEqual(calls, 0)

    def test_unapproved_template_is_held(self):
        self.held('test_planned_ack', 'template not approved', approval='draft')

    def test_missing_provider_target_holds_before_any_send_attempt(self):
        self.held('test_planned_ack', '404',
                  target_error=RuntimeError('Microsoft Graph HTTP 404: ErrorItemNotFound'))

    def test_unknown_delivery_stops_batch_without_retry(self):
        results, calls = self.run_case('test_planned_ack', send=True, twice=True, send_result=(False, 'unknown'))
        self.assertEqual(results, [{'id': 1, 'status': 'delivery_unknown'}])
        self.assertEqual(calls, 1)

    def test_account_verification_ready_without_false_claim(self):
        results, calls = self.run_case('test_claim_checking')
        self.assertEqual(results, [{'id': 1, 'status': 'ready'}])
        self.assertEqual(calls, 0)

    def review_evidence(self):
        return {'context_digest': 'reviewed-history',
                'template_digest': hashlib.sha256(b'approved body').hexdigest(),
                'questions_answered': 'Budget and scope answered; no other unresolved question.'}

    def test_new_context_templates_require_explicit_review(self):
        for key in sender.CONTEXT_REVIEW_KEYS:
            with self.subTest(key=key):
                self.held(key, 'requires reviewed history')

    def test_context_reply_requires_question_coverage(self):
        evidence = self.review_evidence(); evidence.pop('questions_answered')
        self.held('reviewed_scope_before_trial_20260913', 'requires reviewed history', reviewed=evidence)

    def test_context_reply_rejects_changed_history(self):
        evidence = self.review_evidence(); evidence['context_digest'] = 'old-history'
        self.held('reviewed_scope_before_trial_20260913', 'reviewed context changed', reviewed=evidence)

    def test_context_reply_rejects_changed_copy(self):
        evidence = self.review_evidence(); evidence['template_digest'] = 'old-copy'
        self.held('reviewed_scope_before_trial_20260913', 'reviewed template changed', reviewed=evidence)

    def test_context_reply_does_not_ignore_quote(self):
        self.held('reviewed_scope_before_trial_20260913', 'cannot ignore a supplied quote',
                  reviewed=self.review_evidence(), fields={'rate_info': 'USD 900'})

    def test_initial_scope_blocks_existing_access(self):
        self.held('reviewed_scope_before_trial_20260913', 'already has access', reviewed=self.review_evidence(),
                  payload={'success': True, 'found': True, 'creator': {'subscription': {'status': 'active'}}})

    def test_initial_scope_does_not_require_unrequested_code(self):
        results, calls = self.run_case('reviewed_scope_before_trial_20260913', reviewed=self.review_evidence())
        self.assertEqual(results, [{'id': 1, 'status': 'ready'}]); self.assertEqual(calls, 0)

    def test_code_variant_cannot_omit_fixed_code(self):
        self.held('reviewed_arabic_code_20260913', 'fixed coupon missing', reviewed=self.review_evidence())


if __name__ == '__main__':
    unittest.main()
