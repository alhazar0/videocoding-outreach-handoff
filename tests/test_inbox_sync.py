import sqlite3
import unittest

from scripts.inbox_sync import ensure_inbox_schema, reconcile_reply_queue, sync_sent_message, sync_inbox_message, fetch_messages, failed_address, message_text, delivery_report_kind, classification, is_bounce
from unittest.mock import patch


class InboxSentContextTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE contacts(
          id INTEGER PRIMARY KEY,email TEXT,channel_name TEXT,channel_url TEXT,first_sent_at TEXT,
          outreach_status TEXT,followup1_due TEXT,followup2_due TEXT,notes TEXT,updated_at TEXT
        )""")
        self.db.execute(
            "INSERT INTO contacts VALUES(1,'creator@example.com','Creator','https://example.com',NULL,'not_contacted',NULL,NULL,'',datetime('now'))"
        )
        ensure_inbox_schema(self.db)

    def tearDown(self):
        self.db.close()

    def test_never_contact_again_suppresses_and_resolves_pending_work(self):
        for name in ('reply_status','reply_received_at','reply_from','reply_summary','next_action'):
            self.db.execute(f'ALTER TABLE contacts ADD COLUMN {name} TEXT')
        self.db.execute('ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER DEFAULT 0')
        self.db.execute("UPDATE contacts SET followup1_due='2026-10-20',followup2_due='2026-10-22'")
        self.db.execute('''CREATE TABLE outreach_work_items(id INTEGER PRIMARY KEY, contact_id INTEGER,
            inbox_event_id TEXT,status TEXT,resolved_at TEXT,resolved_by TEXT,decision_reason TEXT,updated_at TEXT)''')
        self.db.execute("INSERT INTO outreach_work_items(id,contact_id,status) VALUES(7,1,'pending')")
        message={'id':'never-contact','receivedDateTime':'2026-10-19T06:55:55Z',
                 'from':{'emailAddress':{'address':'creator@example.com'}},
                 'body':{'content':'Never contact us again'}}
        self.assertEqual(sync_inbox_message(self.db,message),(True,'opted_out'))
        self.assertEqual(self.db.execute('SELECT do_not_contact FROM contacts').fetchone()[0],1)
        reconcile_reply_queue(self.db)
        self.assertEqual(self.db.execute('SELECT status FROM outreach_work_items').fetchone()[0],'ignored')
        # Importing later Sent history must not clear the suppression.
        sync_sent_message(self.db,{'id':'old-sent','subject':'A paid collaboration with VideoCoding',
            'sentDateTime':'2026-10-18T12:00:00Z','conversationId':'old-thread',
            'toRecipients':[{'emailAddress':{'address':'creator@example.com'}}],
            'body':{'content':'Our previous invitation.'}})
        self.assertEqual(self.db.execute('SELECT do_not_contact FROM contacts').fetchone()[0],1)

    def test_explicit_never_email_variants_do_not_match_discussion_about_rules(self):
        for text in ('Please never email me again.', 'Thanks. Never contact us again!', 'Never e-mail us again'):
            with self.subTest(text=text):
                self.assertEqual(classification(text)[4],1)
        self.assertEqual(classification('I never contact sponsors without an agreed budget.')[4],0)
        self.assertEqual(classification('We should discuss whether to never contact us again is appropriate wording.')[4],0)

    def test_plain_text_preserves_forwarded_email_evidence(self):
        text = 'From: Agent <agent@example.com>\r\nTo: <creator@example.com>\r\nBudget < $500.'
        self.assertEqual(message_text({'body': {'contentType': 'text', 'content': text}}), text.replace('\r\n', '\n'))
        self.assertEqual(message_text({'body': {'contentType': 'html', 'content': '<p>Hello<br>Creator</p>'}}), 'Hello\nCreator')

    def test_delivery_kind_does_not_equate_mailbox_full_or_policy_failure_with_invalid_address(self):
        self.assertEqual(delivery_report_kind('552-5.2.2 The recipient inbox is out of storage space'), 'delivery_failed')
        self.assertEqual(delivery_report_kind('550 5.0.350 Policy violation or system error'), 'delivery_failed')
        self.assertEqual(delivery_report_kind('452 4.2.2 Delivery delayed'), 'delivery_delayed')
        self.assertEqual(delivery_report_kind('550 5.1.1 User unknown'), 'bounce')
        self.assertEqual(delivery_report_kind("550 5.4.1 contact wasn't found at example.com"), 'bounce')

    def test_full_mailbox_holds_followups_without_invalidating_address(self):
        self.db.execute('ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER DEFAULT 0')
        self.db.execute('ALTER TABLE contacts ADD COLUMN next_action TEXT')
        self.db.execute("ALTER TABLE contacts ADD COLUMN validation_status TEXT DEFAULT 'send_ready'")
        message = dict(id='full-mailbox', subject='Undeliverable: A paid collaboration with VideoCoding',
                       receivedDateTime='2026-10-18T22:22:11Z',
                       **{'from': {'emailAddress': {'address': 'postmaster@example.com'}}},
                       body={'contentType': 'text', 'content': "Your message to creator@example.com could not be delivered.\n552 5.2.2 The recipient mailbox is full."})
        self.assertEqual(sync_inbox_message(self.db, message), (True, 'delivery_failed'))
        self.assertEqual(tuple(self.db.execute('SELECT do_not_contact,validation_status FROM contacts').fetchone()), (1, 'send_ready'))

    def test_expired_dns_delivery_overrides_earlier_retry_diagnostic(self):
        text = ("Delivery has failed to these recipients or groups:\n"
                "creator@example.com\n"
                "550 5.4.312 Message expired, DNS query failed(InfoNoRecords)\n"
                "450 4.4.312 DNS query failed [LastAttemptedServerName=example.com]")
        self.assertEqual(delivery_report_kind(text), 'delivery_failed')

    def test_expiration_after_transient_history_is_still_final_failure(self):
        text = ("450 4.4.312 DNS query failed\n"
                "550 5.4.312 Message expired, DNS query failed")
        self.assertEqual(delivery_report_kind(text), 'delivery_failed')

    def test_dns_retry_without_expiration_stays_delayed(self):
        self.assertEqual(
            delivery_report_kind('450 4.4.312 DNS query failed. Delivery is delayed; retrying.'),
            'delivery_delayed')

    def test_expired_dns_report_holds_recipient_without_invalidating_address(self):
        self.db.execute('ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER DEFAULT 0')
        self.db.execute('ALTER TABLE contacts ADD COLUMN next_action TEXT')
        self.db.execute("ALTER TABLE contacts ADD COLUMN validation_status TEXT DEFAULT 'send_ready'")
        message = {
            'id': 'expired-dns', 'subject': '未送达: A paid collaboration with VideoCoding',
            'receivedDateTime': '2026-10-20T12:17:36Z',
            'from': {'emailAddress': {'name': 'Microsoft Outlook',
                                     'address': 'microsoftexchange@example.onmicrosoft.com'}},
            'body': {'contentType': 'text', 'content':
                     'Delivery has failed to these recipients or groups:\ncreator@example.com\n'
                     '550 5.4.312 Message expired, DNS query failed\n'
                     '450 4.4.312 DNS query failed'}}
        self.assertEqual(sync_inbox_message(self.db, message), (True, 'delivery_failed'))
        self.assertEqual(tuple(self.db.execute(
            'SELECT event_type,contact_id FROM inbox_events').fetchone()),
            ('delivery_failed', 1))
        self.assertEqual(tuple(self.db.execute(
            'SELECT do_not_contact,validation_status FROM contacts').fetchone()),
            (1, 'send_ready'))

    def test_hard_bounce_suppresses_exact_reported_recipient(self):
        for name in ('validation_status', 'reply_status', 'next_action'):
            self.db.execute(f'ALTER TABLE contacts ADD COLUMN {name} TEXT')
        self.db.execute('ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER DEFAULT 0')
        self.db.execute('UPDATE contacts SET email=? WHERE id=1', ("'support@example.com",))
        self.db.execute("INSERT INTO contacts(id,email,notes) VALUES(2,'support@example.com','')")
        message = {'id': 'punctuation-bounce', 'subject': 'Undeliverable: A paid collaboration with VideoCoding',
                   'receivedDateTime': '2026-10-18T21:04:37Z',
                   'from': {'emailAddress': {'address': 'postmaster@example.com'}},
                   'body': {'content': "Delivery failed for support@example.com.\nStatus code: 550 5.1.1\nRecipient Address: 'support@example.com"}}
        self.assertEqual(sync_inbox_message(self.db, message), (True, 'bounce'))
        event = self.db.execute('SELECT contact_id,matched_email FROM inbox_events').fetchone()
        self.assertEqual(tuple(event), (1, "'support@example.com"))
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT id,do_not_contact FROM contacts ORDER BY id')], [(1, 1), (2, 0)])

    def test_localized_forwarding_failure_is_not_creator_reply_or_sender_restriction(self):
        self.db.execute('ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER DEFAULT 0')
        self.db.execute('ALTER TABLE contacts ADD COLUMN next_action TEXT')
        self.db.execute("ALTER TABLE contacts ADD COLUMN validation_status TEXT DEFAULT 'send_ready'")
        message = {'id': 'localized-forwarding', 'subject': '未送达: A paid collaboration with VideoCoding',
                   'receivedDateTime': '2026-10-19T14:36:24Z',
                   'from': {'emailAddress': {'address': 'postmaster@recipient.onmicrosoft.com'}},
                   'body': {'contentType': 'text', 'content': "Your email couldn't be forwarded from creator@example.com to another email address.\n550 5.7.520 Access denied, Your organization does not allow external forwarding. AS(7555)"}}
        self.assertEqual(sync_inbox_message(self.db, message), (True, 'delivery_failed'))
        self.assertEqual(tuple(self.db.execute('SELECT contact_id,event_type FROM inbox_events').fetchone()),
                         (1, 'delivery_failed'))
        self.assertEqual(tuple(self.db.execute('SELECT do_not_contact,validation_status FROM contacts').fetchone()),
                         (1, 'send_ready'))

    def test_localized_full_mailbox_is_linked_without_invalidating_address(self):
        self.db.execute('ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER DEFAULT 0')
        self.db.execute('ALTER TABLE contacts ADD COLUMN next_action TEXT')
        self.db.execute("ALTER TABLE contacts ADD COLUMN validation_status TEXT DEFAULT 'send_ready'")
        message = {'id': 'localized-full', 'subject': '未送达: A paid collaboration with VideoCoding',
                   'receivedDateTime': '2026-10-19T21:26:25Z',
                   'from': {'emailAddress': {'address': 'mailer-daemon@mx.example.net'}},
                   'body': {'contentType': 'text', 'content': "Your message could not\nbe delivered to one or more recipients.\n<creator@example.com>: 552 5.2.2 Quota exceeded (mailbox for user is full)"}}
        self.assertEqual(sync_inbox_message(self.db, message), (True, 'delivery_failed'))
        self.assertEqual(tuple(self.db.execute('SELECT contact_id,matched_email FROM inbox_events').fetchone()),
                         (1, 'creator@example.com'))
        self.assertEqual(tuple(self.db.execute('SELECT do_not_contact,validation_status FROM contacts').fetchone()),
                         (1, 'send_ready'))

    def test_localized_diagnostic_quoted_by_creator_is_not_system_report(self):
        message = {'subject': '未送达: A paid collaboration with VideoCoding',
                   'from': {'emailAddress': {'address': 'creator@example.com'}}}
        self.assertFalse(is_bounce(message, 'I saw 552 5.2.2 Quota exceeded. Can you help?'))

    def test_localized_system_subject_without_diagnostic_is_not_sufficient(self):
        message = {'subject': '未送达: A paid collaboration with VideoCoding',
                   'from': {'emailAddress': {'address': 'postmaster@example.com'}}}
        self.assertFalse(is_bounce(message, 'Please review the mail setup.'))

    def test_explicit_dsn_recipient_precedes_prose_and_legacy_fallback_remains(self):
        self.assertEqual(failed_address("Help: other@example.net\nFinal-Recipient: rfc822; o'neil+creator@example.com\r\n"), "o'neil+creator@example.com")
        self.assertEqual(failed_address('Delivery failed to creator@example.com'), 'creator@example.com')

    def test_unmatched_videocoding_collaboration_is_retained(self):
        message = {'id': 'unknown-creator', 'conversationId': 'unknown-thread',
                   'subject': 'Re: VideoCoding Collaboration', 'receivedDateTime': '2026-09-25T23:26:08Z',
                   'from': {'emailAddress': {'address': 'redacted-1c99ae1965@example.com', 'name': 'Steven'}},
                   'body': {'content': 'Please send the creator code so we can test VideoCoding.'}}
        self.assertEqual(sync_inbox_message(self.db, message), (True, 'reply'))
        row = self.db.execute("SELECT contact_id,body_text FROM inbox_events WHERE id='unknown-creator'").fetchone()
        self.assertIsNone(row['contact_id'])
        self.assertIn('creator code', row['body_text'])

    def test_partial_pagination_does_not_silently_return_success(self):
        with patch('scripts.inbox_sync.graph_json', return_value={'value': [], '@odata.nextLink': 'https://graph.microsoft.com/next'}):
            with self.assertRaisesRegex(RuntimeError, 'checkpoint retained'):
                fetch_messages('token', 'inbox', 'receivedDateTime', '2026-09-25T00:00:00Z', ['id'], max_pages=1)

    def test_historical_reply_cannot_clear_suppression_or_regress_latest_state(self):
        for name in ('reply_status','reply_received_at','reply_from','reply_summary','next_action'):
            self.db.execute(f'ALTER TABLE contacts ADD COLUMN {name} TEXT')
        self.db.execute('ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER DEFAULT 0')
        message={'id':'old','receivedDateTime':'2026-09-01T00:00:00Z',
                 'from':{'emailAddress':{'address':'creator@example.com'}},
                 'body':{'content':'Interested, please send access.'}}
        for suppressed in (0,1):
            self.db.execute('DELETE FROM inbox_events')
            self.db.execute("UPDATE contacts SET do_not_contact=?,reply_received_at='2026-10-18',reply_summary='Latest reply'",(suppressed,))
            sync_inbox_message(self.db,message)
            self.assertEqual(tuple(self.db.execute('SELECT do_not_contact,reply_summary FROM contacts').fetchone()),(suppressed,'Latest reply'))
        self.db.execute('DELETE FROM inbox_events')
        self.db.execute('UPDATE contacts SET do_not_contact=0')
        message['body']['content']='Please unsubscribe me.'
        sync_inbox_message(self.db,message)
        self.assertEqual(self.db.execute('SELECT do_not_contact FROM contacts').fetchone()[0],1)

    def test_sent_message_body_is_saved_as_conversation_context(self):
        message = {
            "id": "graph-sent-1",
            "conversationId": "thread-1",
            "sentDateTime": "2026-08-31T01:00:00Z",
            "subject": "A paid collaboration with VideoCoding",
            "toRecipients": [{"emailAddress": {"address": "creator@example.com"}}],
            "body": {"content": "<p>Here is the creator code.</p><p>On Mon Creator wrote:</p><p>old text</p>"},
        }
        self.assertTrue(sync_sent_message(self.db, message))
        stored = self.db.execute(
            "SELECT conversation_id,contact_id,body_text FROM outreach_sent_messages WHERE id='graph-sent-1'"
        ).fetchone()
        self.assertEqual(stored["conversation_id"], "thread-1")
        self.assertEqual(stored["contact_id"], 1)
        self.assertIn("Here is the creator code", stored["body_text"])
        self.assertNotIn("old text", stored["body_text"])

    def test_representative_sent_context_requires_verified_thread_and_recipient(self):
        self.db.execute("""INSERT INTO outreach_conversations VALUES(
            'verified-thread',1,'agent@example.org','A paid collaboration with VideoCoding',
            '2026-10-18T12:00:00Z',datetime('now'))""")
        message = dict(id='representative-sent', conversationId='verified-thread',
                       sentDateTime='2026-10-18T13:00:00Z',
                       subject='RE: A paid collaboration with VideoCoding',
                       toRecipients=[{'emailAddress': {'address': 'agent@example.org'}}],
                       body={'content': 'Here are the requested activation steps.'})
        self.assertTrue(sync_sent_message(self.db, message))
        stored = self.db.execute('SELECT contact_id,recipient_email,body_text FROM outreach_sent_messages').fetchone()
        self.assertEqual(tuple(stored), (1, 'agent@example.org', 'Here are the requested activation steps.'))
        for changes in (
            {'conversationId': 'unrelated-thread'},
            {'toRecipients': [{'emailAddress': {'address': 'another-agent@example.org'}}]},
            {'toRecipients': [{'emailAddress': {'address': 'agent@example.org'}},
                              {'emailAddress': {'address': 'another-agent@example.org'}}]},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(sync_sent_message(self.db, {**message, 'id': 'must-not-import', **changes}))
        self.assertEqual(self.db.execute('SELECT count(*) FROM outreach_sent_messages').fetchone()[0], 1)

    def reviewed_proposal(self):
        self.db.executescript("""
            CREATE TABLE outreach_work_items(id INTEGER PRIMARY KEY,inbox_event_id,contact_id,draft_body);
            CREATE TABLE approved_reply_batch_log(work_item_id,status,sent_at);
            INSERT INTO inbox_events(id,contact_id,conversation_id,sender_email,received_at,event_type,processed_at)
                VALUES('proposal-in',1,'proposal-thread','agent@example.org','2026-10-19T04:30:00Z','reply',datetime('now'));
            INSERT INTO outreach_work_items VALUES(77,'proposal-in',1,'Here are the terms to discuss.');
            INSERT INTO approved_reply_batch_log VALUES(77,'sent','2026-10-19T04:59:00Z');
        """)
        return dict(id='proposal-sent',conversationId='proposal-thread',
                    sentDateTime='2026-10-19T04:59:03Z',subject='RE: Proposal For Business Partnership',
                    toRecipients=[{'emailAddress': {'address': 'agent@example.org'}}],
                    body={'contentType':'text','content':'Here are the terms to discuss.'})

    def test_reviewed_reply_with_creator_subject_imports_precise_representative_context(self):
        message=self.reviewed_proposal()
        self.assertTrue(sync_sent_message(self.db,message))
        self.assertEqual(tuple(self.db.execute('SELECT contact_id,recipient_email,body_text FROM outreach_sent_messages').fetchone()),
                         (1,'agent@example.org','Here are the terms to discuss.'))
        self.assertTrue(sync_sent_message(self.db,message))
        self.assertEqual(self.db.execute('SELECT count(*) FROM outreach_sent_messages').fetchone()[0],1)

    def test_campaign_reply_recovers_representative_after_primary_invitation_import(self):
        message = self.reviewed_proposal()
        message['subject'] = 'RE: A paid collaboration with VideoCoding'
        invitation = {**message, 'id': 'initial-invitation',
                      'sentDateTime': '2026-10-18T12:00:00Z',
                      'toRecipients': [{'emailAddress': {'address': 'creator@example.com'}}],
                      'body': {'contentType': 'text', 'content': 'Initial invitation.'}}
        self.assertTrue(sync_sent_message(self.db, invitation))
        self.assertTrue(sync_sent_message(self.db, message))
        self.assertEqual(tuple(self.db.execute(
            "SELECT contact_id,recipient_email,body_text FROM outreach_sent_messages WHERE id='proposal-sent'").fetchone()),
            (1, 'agent@example.org', 'Here are the terms to discuss.'))
        # Reimporting old primary-address history must not prevent later recovery.
        self.assertTrue(sync_sent_message(self.db, invitation))
        self.assertTrue(sync_sent_message(self.db, message))
        self.assertEqual(self.db.execute('SELECT count(*) FROM outreach_sent_messages').fetchone()[0], 2)

    def test_campaign_representative_fallback_requires_exact_accepted_reply(self):
        message = self.reviewed_proposal()
        message['subject'] = 'RE: A paid collaboration with VideoCoding'
        for changes in ({'body': {'content': 'Different terms.'}},
                        {'conversationId': 'other-creator-thread'},
                        {'sentDateTime': '2026-10-19T05:10:00Z'}):
            with self.subTest(changes=changes):
                self.assertFalse(sync_sent_message(self.db, {**message, **changes}))
        for status in ('sending', 'delivery_unknown', 'failed'):
            self.db.execute('UPDATE approved_reply_batch_log SET status=?', (status,))
            self.assertFalse(sync_sent_message(self.db, message))
        self.assertEqual(self.db.execute('SELECT count(*) FROM outreach_sent_messages').fetchone()[0], 0)

    def test_creator_subject_requires_exact_body_thread_recipient_and_submission_time(self):
        message=self.reviewed_proposal()
        for changes in (
            {'body':{'contentType':'text','content':'Different commercial terms.'}},
            {'conversationId':'another-creator-thread'},
            {'sentDateTime':'2026-10-19T05:10:00Z'},
            {'toRecipients':[{'emailAddress':{'address':'creator@example.com'}}]},
            {'toRecipients':[{'emailAddress':{'address':'agent@example.org'}},
                             {'emailAddress':{'address':'another@example.org'}}]},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(sync_sent_message(self.db,{**message,**changes}))
        self.assertEqual(self.db.execute('SELECT count(*) FROM outreach_sent_messages').fetchone()[0],0)

    def test_creator_subject_does_not_import_unaccepted_or_ambiguous_review(self):
        message=self.reviewed_proposal()
        for status in ('sending','delivery_unknown','failed'):
            self.db.execute('UPDATE approved_reply_batch_log SET status=?',(status,))
            self.assertFalse(sync_sent_message(self.db,message))
        self.db.execute("UPDATE approved_reply_batch_log SET status='sent'")
        self.db.execute("INSERT INTO approved_reply_batch_log VALUES(77,'sent','2026-10-19T04:59:01Z')")
        self.assertFalse(sync_sent_message(self.db,message))

    def test_unreviewed_personal_subject_is_not_imported(self):
        message=dict(id='personal',conversationId='personal-thread',sentDateTime='2026-10-19T04:59:03Z',
                     subject='RE: Personal plans',toRecipients=[{'emailAddress':{'address':'creator@example.com'}}],
                     body={'contentType':'text','content':'An unrelated personal message.'})
        self.assertFalse(sync_sent_message(self.db,message))

    def test_reply_queue_keeps_only_latest_unanswered_conversation_item(self):
        self.db.execute("ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER DEFAULT 0")
        self.db.executescript(
            """
            CREATE TABLE outreach_work_items(
              id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,status TEXT,
              resolved_at TEXT,resolved_by TEXT,decision_reason TEXT,updated_at TEXT
            );
            """
        )
        self.db.execute(
            """INSERT INTO inbox_events(
              id,conversation_id,received_at,sender_email,event_type,contact_id,processed_at
            ) VALUES('old','thread-1','2026-08-31T01:00:00Z','creator@example.com','reply',1,datetime('now'))"""
        )
        self.db.execute(
            """INSERT INTO inbox_events(
              id,conversation_id,received_at,sender_email,event_type,contact_id,processed_at
            ) VALUES('latest','thread-1','2026-08-31T02:00:00Z','creator@example.com','reply',1,datetime('now'))"""
        )
        self.db.execute("INSERT INTO outreach_work_items VALUES(1,'old',1,'pending',NULL,NULL,'',datetime('now'))")
        self.db.execute("INSERT INTO outreach_work_items VALUES(2,'latest',1,'pending',NULL,NULL,'',datetime('now'))")
        self.db.commit()

        first = reconcile_reply_queue(self.db)
        self.assertEqual(first, {"sent_resolved": 0, "older_superseded": 1, "dnc_ignored": 0})
        self.assertEqual(
            dict(self.db.execute("SELECT id,status FROM outreach_work_items ORDER BY id").fetchall()),
            {1: "superseded", 2: "pending"},
        )

        self.db.execute(
            """INSERT INTO outreach_sent_messages(
              id,conversation_id,contact_id,recipient_email,subject,body_text,sent_at,synced_at
            ) VALUES('sent-later','thread-1',1,'creator@example.com','','','2026-08-31T03:00:00Z',datetime('now'))"""
        )
        self.db.commit()
        second = reconcile_reply_queue(self.db)
        self.assertEqual(second, {"sent_resolved": 1, "older_superseded": 0, "dnc_ignored": 0})
        self.assertEqual(self.db.execute("SELECT status FROM outreach_work_items WHERE id=2").fetchone()[0], "sent")

    def test_reply_queue_ignores_do_not_contact(self):
        self.db.execute("ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER DEFAULT 0")
        self.db.execute("UPDATE contacts SET do_not_contact=1 WHERE id=1")
        self.db.execute(
            """CREATE TABLE outreach_work_items(
              id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,status TEXT,
              resolved_at TEXT,resolved_by TEXT,decision_reason TEXT,updated_at TEXT
            )"""
        )
        self.db.execute(
            """INSERT INTO inbox_events(
              id,conversation_id,received_at,sender_email,event_type,contact_id,processed_at
            ) VALUES('dnc','thread-dnc','2026-08-31T01:00:00Z','creator@example.com','reply',1,datetime('now'))"""
        )
        self.db.execute("INSERT INTO outreach_work_items VALUES(3,'dnc',1,'pending',NULL,NULL,'',datetime('now'))")
        self.db.commit()
        result = reconcile_reply_queue(self.db)
        self.assertEqual(result["dnc_ignored"], 1)
        self.assertEqual(self.db.execute("SELECT status FROM outreach_work_items WHERE id=3").fetchone()[0], "ignored")


if __name__ == "__main__":
    unittest.main()
