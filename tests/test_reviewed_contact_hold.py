import hashlib
import json
import sqlite3
import unittest
from scripts import inbox_sync, creator_followup_agent, reply_context
from scripts import creator_data_agent
from scripts.reviewed_contact_hold import active_hold, set_hold
from scripts.send_reviewed_simple_trial import reviewed_body
from tests.test_reply_context import context_db


class ReviewedContactHoldTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('''CREATE TABLE contacts(id INTEGER PRIMARY KEY,email,channel_name,channel_url,
          first_sent_at,outreach_status,followup1_due,followup2_due,notes,updated_at,
          reply_status,reply_received_at,reply_from,reply_summary,next_action,do_not_contact DEFAULT 0)''')
        self.db.execute("INSERT INTO contacts(id,email,channel_name,outreach_status,notes) VALUES(1,'creator@example.com','Creator','not_contacted','')")
        inbox_sync.ensure_inbox_schema(self.db)

    def sent(self,mid='sent1'):
        return {'id':mid,'conversationId':'thread1','sentDateTime':'2026-10-18T12:00:00Z',
          'subject':'A paid collaboration with VideoCoding',
          'toRecipients':[{'emailAddress':{'address':'creator@example.com'}}],
          'body':{'contentType':'text','content':'Thanks for your reply.'}}

    def incoming(self,body='Interested in a paid collaboration.'):
        return {'id':'in1','conversationId':'thread1','receivedDateTime':'2026-10-19T02:00:00Z',
          'subject':'Re: A paid collaboration with VideoCoding',
          'from':{'emailAddress':{'address':'creator@example.com'}},
          'body':{'contentType':'text','content':body}}

    def test_repeat_sent_sync_never_resurrects_cleared_followup_dates(self):
        inbox_sync.sync_sent_message(self.db,self.sent())
        self.assertTrue(self.db.execute('SELECT followup1_due FROM contacts').fetchone()[0])
        self.db.execute('UPDATE contacts SET followup1_due=NULL,followup2_due=NULL')
        inbox_sync.sync_sent_message(self.db,self.sent('later-sent'))
        self.assertEqual(tuple(self.db.execute('SELECT followup1_due,followup2_due FROM contacts').fetchone()),(None,None))

    def test_inbound_before_historical_sent_import_has_no_new_followup_dates(self):
        inbox_sync.sync_inbox_message(self.db,self.incoming())
        inbox_sync.sync_sent_message(self.db,self.sent())
        self.assertEqual(tuple(self.db.execute('SELECT followup1_due,followup2_due FROM contacts').fetchone()),(None,None))

    def test_new_inbound_preserves_manual_hold_and_still_records_message(self):
        set_hold(self.db,1,123,'Paid proposal only; no trial reminders.')
        inbox_sync.sync_inbox_message(self.db,self.incoming())
        self.assertEqual(self.db.execute('SELECT next_action FROM contacts').fetchone()[0],'Paid proposal only; no trial reminders.')
        self.assertEqual(self.db.execute('SELECT count(*) FROM inbox_events').fetchone()[0],1)
        self.assertTrue(active_hold(self.db,1))

    def test_unsubscribe_takes_precedence_over_hold(self):
        set_hold(self.db,1,123,'Paid proposal only.')
        inbox_sync.sync_inbox_message(self.db,self.incoming('Please unsubscribe me.'))
        self.assertEqual(self.db.execute('SELECT do_not_contact FROM contacts').fetchone()[0],1)
        self.assertIn('停止联系',self.db.execute('SELECT next_action FROM contacts').fetchone()[0])

    def test_hold_prevents_followup_even_after_newer_outbound(self):
        row={'contact_id':1,'source_sent_at':'2026-10-19T03:00:00Z'}
        self.assertTrue(creator_followup_agent.still_eligible(self.db,row))
        set_hold(self.db,1,123,'No automatic reminders.')
        self.assertFalse(creator_followup_agent.still_eligible(self.db,row))

    def test_valid_ai_context_cannot_override_persistent_operator_hold(self):
        db=context_db();self.addCleanup(db.close)
        for column in ('next_action','followup1_due','followup2_due'):db.execute(f'ALTER TABLE contacts ADD COLUMN {column}')
        decision={'template_key':'coupon_test','context_complete':True,'context_audit':{
          'version':reply_context.VERSION,'fingerprint':reply_context.fingerprint(db,1),
          'delivery_complete':True,'draft_fingerprint':hashlib.sha256((reply_context.VERSION+'\n').encode()).hexdigest()}}
        db.execute('UPDATE outreach_work_items SET extracted_json=?',(json.dumps(decision),))
        self.assertIsNone(reply_context.context_hold_reason(db,1))
        set_hold(db,1,1,'Requires a concrete paid offer.')
        self.assertIn('operator-reviewed contact hold',reply_context.context_hold_reason(db,1))

    def test_legacy_writer_cannot_replace_hold_or_restart_dates(self):
        set_hold(self.db,1,123,'Paid proposal only.')
        self.db.execute("UPDATE contacts SET next_action='Send trial',followup1_due='2026-11-01',followup2_due='2026-11-04' WHERE id=1")
        self.assertEqual(tuple(self.db.execute('SELECT next_action,followup1_due,followup2_due FROM contacts').fetchone()),('Paid proposal only.',None,None))
        self.db.execute('PRAGMA recursive_triggers=ON')
        self.db.execute("UPDATE contacts SET next_action='Another generic classification' WHERE id=1")
        self.assertEqual(self.db.execute('SELECT next_action FROM contacts').fetchone()[0],'Paid proposal only.')

    def test_noncode_scope_reply_requires_exact_hashed_review_and_cannot_add_coupon(self):
        text='No approved campaign or meeting is scheduled.'
        review={'template_key':'reviewed_scope_before_trial_20260913','context_digest':'context',
          'template_digest':'template','review_note':'Read all history','questions_answered':['scope'],
          'reviewed_body':text,'reviewed_body_digest':hashlib.sha256(text.encode()).hexdigest()}
        self.assertEqual(reviewed_body(review,'default'),text)
        review['reviewed_body']='FREETEST100';review['reviewed_body_digest']=hashlib.sha256(b'FREETEST100').hexdigest()
        with self.assertRaisesRegex(ValueError,'cannot introduce trial access'):reviewed_body(review,'default')

    def test_completed_usage_cannot_reopen_held_promotion(self):
        for column in ('subscribers','category','country','language','rate_info'):self.db.execute(f'ALTER TABLE contacts ADD COLUMN {column}')
        self.db.executescript('''CREATE TABLE creator_profiles(contact_id,usage_verification_status,usage_completed_tasks,marketing_status,updated_at);
          CREATE TABLE outreach_work_items(contact_id,kind);
          CREATE TABLE promotion_creator_list(contact_id,list_status,interest_status,interest_evidence,updated_at);
          INSERT INTO creator_profiles VALUES(1,'completed',1,'listed','2026-10-19');
          INSERT INTO promotion_creator_list VALUES(1,'waiting','ready_to_promote','Old interest',NULL);
          UPDATE contacts SET reply_status='interested';''')
        set_hold(self.db,1,123,'Billing complaint remains unresolved.')
        self.assertEqual(creator_data_agent.refresh_promotion_creator_list(self.db),0)
        self.assertEqual(tuple(self.db.execute('SELECT list_status,interest_status FROM promotion_creator_list').fetchone()),('held','needs_review'))

if __name__=='__main__':unittest.main()
