import hashlib
import json
import sqlite3
import unittest
from scripts.reply_context import VERSION, SNAPSHOT_SQL, fingerprint, context_hold_reason


def context_db():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE contacts(id INTEGER PRIMARY KEY,email,channel_name,initial_template_key,rate_info,do_not_contact DEFAULT 0);
      CREATE TABLE inbox_events(id TEXT PRIMARY KEY,contact_id,conversation_id,received_at,subject,body_text,body_preview,sender_email,event_type DEFAULT 'reply');
      CREATE TABLE outreach_work_items(id INTEGER PRIMARY KEY,contact_id,inbox_event_id,status,template_key,extracted_json,subject,draft_body,resolved_at,updated_at,resolved_by);
      CREATE TABLE outreach_sent_messages(id TEXT PRIMARY KEY,contact_id,conversation_id,sent_at,subject,body_text);
      CREATE TABLE creator_followups(id INTEGER PRIMARY KEY,contact_id,conversation_id,sent_at,status,subject,body);
      CREATE TABLE outreach_send_log(id INTEGER PRIMARY KEY,contact_id,sent_at,status);
      CREATE TABLE outreach_templates(template_key TEXT PRIMARY KEY,subject,body,version,enabled,approval_status);
      CREATE TABLE creator_profiles(contact_id INTEGER PRIMARY KEY,registered_email,account_status,test_status,membership_status,membership_expires_at,
        coupon_code,coupon_sent_at,coupon_redemption_status,usage_verification_status,usage_total_tasks,usage_active_tasks,usage_completed_tasks,
        price_dedicated,price_integration,price_shorts,currency,turnaround,marketing_status);
      INSERT INTO contacts(id,email) VALUES(1,'creator@example.com');
      INSERT INTO inbox_events VALUES('current',1,'thread-a','2026-10-08T02:00:00Z','Re: demo','Please send a code.','','creator@example.com','reply');
      INSERT INTO outreach_work_items(id,contact_id,inbox_event_id,status,template_key) VALUES(1,1,'current','pending','coupon_test');
      INSERT INTO creator_profiles(contact_id,test_status) VALUES(1,'not_started');
      INSERT INTO outreach_templates VALUES('coupon_test','Access','Code and instructions',1,1,'approved');
    ''')
    return db


class ReplyContextTests(unittest.TestCase):
    def setUp(self):
        self.db = context_db()
        self.addCleanup(self.db.close)

    def audit(self):
        decision = {'template_key': 'coupon_test', 'context_complete': True, 'context_audit': {
            'version': VERSION, 'fingerprint': fingerprint(self.db, 1), 'delivery_complete': True, 'draft_fingerprint': hashlib.sha256((VERSION + '\n').encode()).hexdigest()}}
        self.db.execute('UPDATE outreach_work_items SET extracted_json=? WHERE id=1', (json.dumps(decision),))

    def test_unchanged_evidence_remains_valid_after_routing_metadata_changes(self):
        self.audit()
        self.db.execute("UPDATE outreach_work_items SET updated_at='now' WHERE id=1")
        self.assertIsNone(context_hold_reason(self.db, 1))

    def test_changed_draft_cannot_be_sent_under_old_review(self):
        self.audit()
        self.db.execute("UPDATE outreach_work_items SET draft_body='unreviewed promise'")
        self.assertIn('draft changed', context_hold_reason(self.db, 1))

    def test_reconciled_draft_is_not_history_or_expected_send(self):
        self.db.execute("INSERT INTO outreach_work_items(id,contact_id,inbox_event_id,status,draft_body,resolved_at,resolved_by) VALUES(2,1,'current','sent','Never sent draft','2026-10-01','operations_queue_reconciler')")
        snapshot = json.loads(self.db.execute(SNAPSHOT_SQL, (1,)).fetchone()[0])
        self.assertEqual(snapshot['expected_sent_count'], 0)
        self.assertNotIn('Never sent draft', [m['body'] for m in snapshot['messages']])

    def test_new_message_invalidates_old_review(self):
        self.audit()
        self.db.execute("INSERT INTO inbox_events VALUES('new',1,'thread-a','2026-10-08T03:00:00Z','Re','Actually I cannot add a card.','','creator@example.com','reply')")
        self.assertIn('changed', context_hold_reason(self.db, 1))

    def test_profile_and_template_changes_invalidate_old_review(self):
        for query in ["UPDATE creator_profiles SET membership_status='active'", "UPDATE outreach_templates SET body='changed copy'"]:
            self.audit()
            self.db.execute(query)
            self.assertIn('changed', context_hold_reason(self.db, 1))

    def test_legacy_or_incomplete_audit_is_never_sendable(self):
        self.assertIn('missing', context_hold_reason(self.db, 1))
        self.audit()
        decision = json.loads(self.db.execute('SELECT extracted_json FROM outreach_work_items').fetchone()[0])
        decision['context_audit']['delivery_complete'] = False
        self.db.execute('UPDATE outreach_work_items SET extracted_json=?', (json.dumps(decision),))
        self.assertIn('incomplete', context_hold_reason(self.db, 1))

    def test_history_preserves_old_requests_and_separate_thread_identity(self):
        for i in range(20):
            self.db.execute("INSERT INTO inbox_events VALUES(?,1,?,?,?,?,?,?,'reply')",
                            (f'old-{i}', 'thread-b' if i == 0 else 'thread-a', f'2026-09-{i+1:02}T00:00:00Z', 'Question', 'Does it support Hindi?', '', 'creator@example.com'))
        snapshot = json.loads(self.db.execute(SNAPSHOT_SQL, (1,)).fetchone()[0])
        self.assertEqual(len(snapshot['messages']), 21)
        self.assertEqual(snapshot['messages'][0]['conversation_id'], 'thread-b')

    def test_unknown_contact_still_has_conversation_history(self):
        self.db.execute('UPDATE outreach_work_items SET contact_id=NULL')
        self.db.execute('UPDATE inbox_events SET contact_id=NULL')
        self.db.execute("INSERT INTO outreach_sent_messages VALUES('s',NULL,'thread-a','2026-10-01','Invite','Try the product')")
        snapshot = json.loads(self.db.execute(SNAPSHOT_SQL, (1,)).fetchone()[0])
        self.assertEqual(len(snapshot['messages']), 2)

    def test_timestamp_proximity_does_not_discard_distinct_sent_reply(self):
        self.db.execute("INSERT INTO outreach_work_items(id,contact_id,inbox_event_id,status,subject,draft_body,resolved_at) VALUES(2,1,'current','sent','Reply','Important answer','2026-10-01T00:00:00Z')")
        self.db.execute("INSERT INTO outreach_sent_messages VALUES('s',1,'thread-a','2026-10-01T00:01:00Z','Other','Different message')")
        snapshot = json.loads(self.db.execute(SNAPSHOT_SQL, (1,)).fetchone()[0])
        self.assertIn('Important answer', [m['body'] for m in snapshot['messages']])

    def test_cached_candidate_cannot_send_after_a_different_draft_is_reviewed(self):
        self.audit()
        self.assertIn('queued draft changed', context_hold_reason(self.db, 1, expected_draft='old draft'))
        self.assertIn('queued template changed', context_hold_reason(self.db, 1, expected_template='ask_rate'))

    def test_sent_status_blocks_repeat_send(self):
        self.audit()
        self.db.execute("UPDATE outreach_work_items SET status='sent'")
        self.assertIn('no longer pending', context_hold_reason(self.db, 1))

    def test_delivery_report_is_not_labeled_as_creator_intent(self):
        self.db.execute("INSERT INTO inbox_events VALUES('ndr',1,'thread-a','2026-10-01','Undeliverable','Sender restricted','','postmaster@example.com','sender_restricted')")
        snapshot = json.loads(self.db.execute(SNAPSHOT_SQL,(1,)).fetchone()[0])
        self.assertEqual(snapshot['messages'][0]['direction'],'system')
        self.assertEqual(snapshot['messages'][1]['direction'],'creator')


if __name__ == '__main__':
    unittest.main()
