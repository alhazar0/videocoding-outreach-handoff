import hashlib
import sqlite3
import smtplib
import unittest
from unittest.mock import MagicMock, patch
from scripts import gmail_reviewed_reply as g


class GmailReviewedReplyTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(id=123, inbox_event_id='gmail:source', contact_id=1,
                        conversation_id='gmail-thread:42', internet_message_id='<creator@example.org>',
                        sender_email='creator@example.org', email='creator@example.org',
                        received_at='2026-10-18T20:00:00Z', body_text='Please send access.',
                        original_subject='Re: A paid collaboration with VideoCoding')
        self.message = dict(message_id='<creator@example.org>', thread='gmail-thread:42',
                            **{'from': ['creator@example.org'], 'to': [g.scheduler.GOOGLE_MAILBOX]},
                            date='2026-10-18T20:00:00Z', body='Please send access.', references='<first@example.com>')

    def test_missing_changed_or_ambiguous_original_is_held(self):
        for messages in ([], [self.message, self.message],
                         [{**self.message, 'thread': 'gmail-thread:43'}],
                         [{**self.message, 'from': ['other@example.org']}],
                         [{**self.message, 'to': ['team@videocoding.ai']}],
                         [{**self.message, 'body': 'Actually, unsubscribe.'}],
                         [{**self.message, 'date': '2026-10-18T20:01:00Z'}]):
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                g.check_messages(self.row, messages, True)

    def test_newer_message_and_previous_code_are_held(self):
        for new in ({**self.message, 'message_id': '<new>', 'date': '2026-10-18T20:01:00Z'},
                    {**self.message, 'message_id': '<old-code>', 'date': '2026-10-18T19:00:00Z',
                     'from': [g.scheduler.GOOGLE_MAILBOX], 'body': 'Use FREETEST100.'}):
            with self.subTest(new=new), self.assertRaises(ValueError):
                g.check_messages(self.row, [new, self.message], True)

    def test_reply_keeps_original_channel_and_thread_identity(self):
        target = g.check_messages(self.row, [self.message], True)
        mail = g.build_message(self.row, 'Reviewed reply', target)
        self.assertIn(g.scheduler.GOOGLE_MAILBOX, mail['From'])
        self.assertEqual(mail['To'], 'creator@example.org')
        self.assertEqual(mail['In-Reply-To'], '<creator@example.org>')
        self.assertEqual(mail['References'], '<first@example.com> <creator@example.org>')
        self.assertEqual(mail['Subject'], self.row['original_subject'])
        self.assertEqual(mail['Message-ID'], g.build_message(self.row, 'Reviewed reply', target)['Message-ID'])
        self.assertNotEqual(mail['Message-ID'], g.build_message(self.row, 'Changed', target)['Message-ID'])

    def test_smtp_uncertainty_never_opens_another_submission(self):
        smtp = MagicMock()
        smtp.send_message.side_effect = smtplib.SMTPServerDisconnected('connection lost after DATA')
        with patch.object(g.scheduler, 'gmail_smtp_connection') as connect, \
                patch.object(g.scheduler, 'google_app_password', return_value='test-password'):
            connect.return_value.__enter__.return_value = smtp
            ok, detail = g.submit(g.build_message(self.row, 'Reviewed reply', self.message))
        self.assertFalse(ok)
        self.assertIn('uncertain', detail)
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(smtp.send_message.call_count, 1)

    def test_smtp_recipient_refusal_is_not_success(self):
        smtp = MagicMock()
        smtp.send_message.return_value = {'creator@example.org': (550, b'refused')}
        with patch.object(g.scheduler, 'gmail_smtp_connection') as connect, \
                patch.object(g.scheduler, 'google_app_password', return_value='test-password'):
            connect.return_value.__enter__.return_value = smtp
            ok, _ = g.submit(g.build_message(self.row, 'Reviewed reply', self.message))
        self.assertFalse(ok)

    def test_imap_search_rejects_header_injection(self):
        with self.assertRaises(ValueError):
            g.quoted('creator@example.org\r\nALL')

    def test_wrong_channel_system_report_and_inactive_sender_cannot_route(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE inbox_events(id TEXT,event_type TEXT);
            INSERT INTO inbox_events VALUES('gmail:source','interested');
            CREATE TABLE outreach_send_log(contact_id INTEGER,sender_email TEXT,provider TEXT,status TEXT,sent_at TEXT);
            CREATE TABLE outreach_sender_accounts(sender_email TEXT,status TEXT);
            CREATE TABLE approved_reply_batch_log(work_item_id INTEGER,status TEXT,sent_at TEXT);
            CREATE TABLE outreach_audit_log(entity_id TEXT,action TEXT);
        """)
        db.execute('INSERT INTO outreach_send_log VALUES(1,?,?,?,?)',
                   (g.scheduler.GOOGLE_MAILBOX, 'google_workspace_smtp', 'sent', '2026-01-01T00:00:00Z'))
        db.execute('INSERT INTO outreach_sender_accounts VALUES(?,?)', (g.scheduler.GOOGLE_MAILBOX, 'active'))
        g.validate_route(db, self.row)
        with self.assertRaises(ValueError):
            g.validate_route(db, {**self.row, 'inbox_event_id': 'microsoft-message'})
        db.execute("UPDATE inbox_events SET event_type='delivery_failed'")
        with self.assertRaises(ValueError):
            g.validate_route(db, self.row)
        db.execute("UPDATE inbox_events SET event_type='interested'")
        db.execute("UPDATE outreach_sender_accounts SET status='restricted'")
        with self.assertRaises(ValueError):
            g.validate_route(db, self.row)
        db.execute("UPDATE outreach_sender_accounts SET status='active'")
        db.execute('UPDATE outreach_send_log SET sent_at=?', (g.datetime.now(g.timezone.utc).isoformat(),))
        with self.assertRaisesRegex(ValueError, '120-second'):
            g.validate_route(db, self.row)
        db.close()

    def test_individual_coupon_copy_cannot_remove_subscription_disclosure(self):
        from scripts import send_reviewed_simple_trial as sender
        approved = 'FREETEST100\nTo activate it:\nApproved steps and automatic renewal terms.\nOnce you’ve tested it, reply.'
        for body, allowed in ((approved, True), ('FREETEST100\nTo activate it:\nNo terms.', False)):
            review = dict(template_key='coupon_test_rate_received', context_digest='reviewed',
                          template_digest='approved', review_note='All questions read',
                          questions_answered=['Access and quote'], reviewed_body=body,
                          reviewed_body_digest=hashlib.sha256(body.encode()).hexdigest())
            if allowed:
                self.assertEqual(sender.reviewed_body(review, approved), body)
            else:
                with self.assertRaisesRegex(ValueError, 'renewal disclosure'):
                    sender.reviewed_body(review, approved)


if __name__ == '__main__':
    unittest.main()
