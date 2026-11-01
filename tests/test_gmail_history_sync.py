import sqlite3
import unittest
from email.message import EmailMessage
from unittest.mock import MagicMock,patch

from scripts.gmail_history_sync import import_message,sync,setting
from scripts.inbox_sync import ensure_inbox_schema
from scripts import send_coupon_replies


class GmailHistoryTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE contacts(id INTEGER PRIMARY KEY,email TEXT,channel_name TEXT,
          channel_url TEXT,first_sent_at TEXT,outreach_status TEXT,validation_status TEXT,do_not_contact INTEGER,
          next_action TEXT,updated_at TEXT);
          CREATE TABLE outreach_settings(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT);
          INSERT INTO contacts VALUES(1,'creator@example.com','Creator','channel','2026-10-18','sent','send_ready',0,'',NULL);
        ''');ensure_inbox_schema(self.db)

    def raw(self,body='Actual sent body'):
        m=EmailMessage();m['From']='creator@outreach.videocoding.ai';m['To']='creator@example.com'
        m['Subject']='VideoCoding';m['Message-ID']='<original@example.com>';m['Date']='Sun, 18 Oct 2026 12:00:00 +0000'
        m.set_content(body);return m.as_bytes()

    def dsn(self,code,action='failed',diagnostic='Mailbox unavailable'):
        return f'''From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>
To: creator@outreach.videocoding.ai
Date: Sun, 18 Oct 2026 12:01:00 +0000
Message-ID: <ndr@example.com>
Subject: Delivery Status Notification
MIME-Version: 1.0
Content-Type: multipart/report; boundary="boundary"; report-type=delivery-status

--boundary
Content-Type: text/plain

Delivery failed from creator@outreach.videocoding.ai.
--boundary
Content-Type: message/delivery-status

Reporting-MTA: dns; googlemail.com

Final-Recipient: rfc822; creator@example.com
Action: {action}
Status: {code}
Diagnostic-Code: smtp; {diagnostic}

--boundary--
'''.encode()

    def test_provider_sent_body_is_saved_exactly_and_idempotently(self):
        before=tuple(self.db.execute('SELECT * FROM contacts').fetchone())
        for _ in range(2):self.assertEqual(import_message(self.db,self.raw(),'thread',sent=True),'sent_matched')
        rows=self.db.execute('SELECT * FROM outreach_sent_messages').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['body_text'],'Actual sent body\n')
        self.assertEqual(rows[0]['sent_at'],'2026-10-18T12:00:00Z')
        self.assertEqual(tuple(self.db.execute('SELECT * FROM contacts').fetchone()),before)

    def test_invalid_header_date_uses_provider_date_without_guessing_timezone(self):
        raw=self.raw().replace(b'Sun, 18 Oct 2026 12:00:00 +0000',b'Sun, 18 Oct 2026 12:00:00 -0000')
        import_message(self.db,raw,'thread',sent=True,internal_date='18-Oct-2026 13:00:00 +0000')
        self.assertEqual(self.db.execute('SELECT sent_at FROM outreach_sent_messages').fetchone()[0],'2026-10-18T13:00:00Z')

    def test_dsn_uses_structured_recipient_and_suppresses_only_invalid_address(self):
        self.assertEqual(import_message(self.db,self.dsn('5.1.1'),'thread',sent=False),'delivery_reports')
        self.assertEqual(self.db.execute('SELECT do_not_contact FROM contacts').fetchone()[0],1)
        row=self.db.execute('SELECT contact_id,event_type FROM inbox_events').fetchone()
        self.assertEqual(tuple(row),(1,'bounce'))
        self.assertEqual(import_message(self.db,self.dsn('5.1.1'),'thread',sent=False),'duplicate')

    def test_delay_and_policy_failure_do_not_invalidate_address(self):
        for code,action,event in [('4.2.2','delayed','delivery_delayed'),('5.7.0','failed','delivery_failed')]:
            self.db.execute('DELETE FROM inbox_events')
            import_message(self.db,self.dsn(code,action),'thread',sent=False)
            self.assertEqual(tuple(self.db.execute('SELECT do_not_contact,validation_status FROM contacts').fetchone()),(0,'send_ready'))
            self.assertEqual(self.db.execute('SELECT event_type FROM inbox_events').fetchone()[0],event)

    def test_explicit_recipient_not_found_is_hard_failure_even_with_generic_code(self):
        raw = self.dsn('5.1.0').replace(b'Diagnostic-Code: smtp;', b'Diagnostic-Code: smtp; Recipient not found.')
        import_message(self.db, raw, 'thread', sent=False)
        self.assertEqual(self.db.execute('SELECT do_not_contact FROM contacts').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT event_type FROM inbox_events').fetchone()[0], 'bounce')

    def test_automatic_header_is_preserved_and_never_clears_suppression(self):
        for column in ('reply_status','reply_received_at','reply_from','reply_summary'):
            self.db.execute(f'ALTER TABLE contacts ADD COLUMN {column} TEXT')
        self.db.execute("ALTER TABLE contacts ADD COLUMN notes TEXT DEFAULT ''")
        self.db.execute('UPDATE contacts SET do_not_contact=1')
        m=EmailMessage();m['From']='creator@example.com';m['To']='creator@outreach.videocoding.ai'
        m['Date']='Sun, 18 Oct 2026 12:00:00 +0000';m['Message-ID']='<auto@example.com>'
        m['Auto-Submitted']='auto-replied';m.set_content('We have received your proposal.')
        self.assertEqual(import_message(self.db,m.as_bytes(),'thread',sent=False),'inbound_automated')
        self.assertEqual(self.db.execute('SELECT do_not_contact FROM contacts').fetchone()[0],1)

    def test_gmail_reply_id_cannot_enter_graph_transport(self):
        with patch.object(send_coupon_replies,'graph_request') as network:
            ok,detail=send_coupon_replies.send_reply('token','gmail:example','Reply')
        self.assertFalse(ok);self.assertIn('original Gmail',detail);network.assert_not_called()

    def client(self):
        m=MagicMock();m.list.return_value=('OK',[b'(\\Sent) "/" "Localized Sent"'])
        m.select.return_value=('OK',[b'1']);m.response.return_value=('UIDVALIDITY',[b'1'])
        return m

    def test_checkpoint_not_advanced_on_incomplete_fetch(self):
        m=self.client();m.uid.side_effect=[('OK',[b'1 2']),('OK',[(b'1 (UID 1 X-GM-THRID 9)',self.raw()),b')'])]
        with self.assertRaisesRegex(RuntimeError,'incomplete body batch'):
            sync(self.db,connection=m)
        self.assertIsNone(setting(self.db,'gmail_imap_sent_cursor'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM outreach_sent_messages').fetchone()[0],0)
        m.select.assert_called_with('"Localized Sent"',readonly=True)
        self.assertIn('BODY.PEEK[]',m.uid.call_args.args[-1])

    def test_uid_star_cannot_reprocess_last_uid_below_cursor(self):
        setting(self.db,'gmail_imap_sent_cursor','{"validity":"1","uid":5}')
        setting(self.db,'gmail_imap_inbox_cursor','{"validity":"1","uid":5}');self.db.commit()
        m=self.client();m.uid.return_value=('OK',[b'5'])
        result=sync(self.db,connection=m)
        self.assertEqual(result['sent_scanned'],0);self.assertEqual(result['inbox_scanned'],0)
        self.assertTrue(all(call.args[0]=='search' for call in m.uid.call_args_list))


if __name__=='__main__':unittest.main()
