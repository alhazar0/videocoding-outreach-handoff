import io
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import outreach_scheduler as scheduler, send_coupon_replies as coupon
from scripts.inbox_sync import ensure_inbox_schema, sync_inbox_message
from scripts.mail_delivery import is_sender_restriction, is_graph_delivery, RESTRICTION_REASON
from scripts.repair_sender_restrictions import candidates, repair

PROVIDER = 'microsoftexchange329e71ec88ae4615bbc36ab6ce41109e@netorgft20461431.onmicrosoft.com'
NDR = "Delivery has failed to creator@example.com. Remote server returned '550 5.1.8 Access denied, bad outbound sender AS(42004)'"


class SenderRestrictionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript(Path('db/0000_initial.sql').read_text())
        self.db.execute("ALTER TABLE contacts ADD COLUMN next_action TEXT DEFAULT ''")
        scheduler.ensure_schema(self.db)
        ensure_inbox_schema(self.db)
        self.db.execute("""INSERT INTO contacts(id,email,channel_name,channel_url,platform,category,ai_confidence,
          validation_status,verification_proof,email_domain,country,language,topic,priority,fit_score,source_file,
          source_count,source_rows,updated_at,notes)
          VALUES(1,'creator@example.com','Creator','channel','YouTube','AI','high','send_ready','public','example.com',
          'US','en','AI','high',90,'public',1,1,datetime('now'),'')""")
        self.db.commit()

    def message(self, body=NDR, mid='ndr'):
        return {'id':mid,'subject':'Undeliverable: VideoCoding collaboration',
                'receivedDateTime':'2026-10-18T00:00:00Z',
                'from':{'emailAddress':{'address':PROVIDER,'name':'Microsoft Outlook'}},
                'body':{'contentType':'text','content':body}}

    def test_sender_ndr_does_not_invalidate_or_opt_out_recipient(self):
        before = dict(self.db.execute('SELECT * FROM contacts WHERE id=1').fetchone())
        self.assertEqual(sync_inbox_message(self.db,self.message()),(True,'sender_restricted'))
        self.assertEqual(dict(self.db.execute('SELECT * FROM contacts WHERE id=1').fetchone()),before)
        self.assertIn('550 5.1.8',scheduler.microsoft_outbound_block_reason(self.db))
        scheduler.ensure_schema(self.db)
        self.assertEqual(self.db.execute("SELECT status FROM outreach_sender_accounts WHERE sender_email=?",(scheduler.MAILBOX,)).fetchone()[0],'restricted')
        self.assertEqual(sync_inbox_message(self.db,self.message()),(False,'duplicate'))

    def test_real_bad_recipient_remains_suppressed(self):
        result=sync_inbox_message(self.db,self.message('Delivery has failed to creator@example.com: 550 5.1.1 Recipient not found'))
        self.assertEqual(result,(True,'bounce'))
        row=self.db.execute('SELECT validation_status,do_not_contact FROM contacts').fetchone()
        self.assertEqual(tuple(row),('invalid',1))
        self.assertIsNone(scheduler.microsoft_outbound_block_reason(self.db))

    def test_creator_quoting_error_cannot_pause_account(self):
        self.assertFalse(is_sender_restriction('creator@example.com',NDR))
        self.assertFalse(is_sender_restriction(PROVIDER,'550 5.1.1 Recipient not found'))

    def test_historical_repair_preserves_suppression_and_does_not_requeue(self):
        # Seed the historical invalid-address state before substituting the sender-restriction report.
        sync_inbox_message(self.db,self.message('Delivery has failed to creator@example.com: 550 5.1.1 Recipient not found'))
        self.db.execute("UPDATE inbox_events SET body_text=? WHERE id='ndr'",(NDR,))
        self.db.execute("UPDATE contacts SET first_sent_at='2026-10-08T14:00:00Z'")
        result=repair(self.db,candidates(self.db))
        self.assertEqual(result['contacts_reclassified'],1)
        row=self.db.execute('SELECT validation_status,outreach_status,do_not_contact,first_sent_at FROM contacts').fetchone()
        self.assertEqual(tuple(row),('needs_validation','sender_restricted',1,'2026-10-08T14:00:00Z'))
        self.assertEqual(candidates(self.db),[])

    def test_historical_real_bounce_or_optout_is_preserved(self):
        for kind in ('bounce','opted_out'):
            with self.subTest(kind=kind):
                self.db.execute('DELETE FROM inbox_events')
                # Model the legacy misclassification, not the current delivery-report classifier.
                sync_inbox_message(self.db,self.message('Delivery has failed to creator@example.com: 550 5.1.1 Recipient not found'))
                self.db.execute("UPDATE inbox_events SET body_text=? WHERE id='ndr'",(NDR,))
                self.db.execute("INSERT INTO inbox_events(id,received_at,event_type,contact_id,processed_at) VALUES('other','2026-10-10',?,1,'2026-10-10')",(kind,))
                result=repair(self.db,candidates(self.db))
                self.assertEqual(result['contacts_reclassified'],0)
                self.assertEqual(tuple(self.db.execute('SELECT validation_status,do_not_contact FROM contacts').fetchone()),('invalid',1))

    def test_transport_blocks_all_graph_send_paths_without_network(self):
        for endpoint in ('sendMail','messages/id/reply','messages/id/replyAll','messages/id/send','messages/id/forward'):
            url='https://graph.microsoft.com/v1.0/me/'+endpoint
            with patch.object(scheduler,'microsoft_outbound_block_reason',return_value=RESTRICTION_REASON), \
                 patch.object(scheduler.urllib.request,'urlopen') as network:
                status,_=scheduler.request_json(url,data=b'{}',headers={})
                self.assertEqual(status,423)
                network.assert_not_called()
            with patch.object(coupon,'microsoft_outbound_block_reason',return_value=RESTRICTION_REASON), \
                 patch.object(coupon.urllib.request,'urlopen') as network:
                ok,detail,_=coupon.graph_request('token',url,payload={})
                self.assertFalse(ok)
                self.assertIn('sender_restricted',detail)
                network.assert_not_called()

    def test_read_sync_and_token_refresh_are_not_send_operations(self):
        self.assertFalse(is_graph_delivery('https://graph.microsoft.com/v1.0/me/messages','GET'))
        self.assertFalse(is_graph_delivery('https://login.microsoftonline.com/tenant/oauth2/v2.0/token'))


if __name__=='__main__':
    unittest.main()
