import sqlite3
import unittest
from scripts.reviewed_service_case import set_pending_case


class ReviewedServiceCaseTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('''
          CREATE TABLE outreach_work_items(id PRIMARY KEY,contact_id,status,template_key,
            kind,risk_level,resolved_at,resolved_by,decision_reason,updated_at,draft_body);
          CREATE TABLE approved_reply_batch_log(work_item_id PRIMARY KEY,status,sent_at);
          INSERT INTO outreach_work_items VALUES(1,10,'sent','product_question_hold',
            'human_review','high','2026-10-19','operator','acknowledged',NULL,'Exact sent body');
          INSERT INTO approved_reply_batch_log VALUES(1,'sent','2026-10-19T08:58:34Z');
        ''')

    def test_acknowledgement_does_not_close_pending_billing(self):
        set_pending_case(self.db,1,10,'Invoice and refund still unverified')
        self.db.execute("UPDATE outreach_work_items SET status='sent',resolved_at=datetime('now'),resolved_by='inbox_sync',decision_reason='Newer Sent resolves reply'")
        self.assertEqual(self.db.execute('SELECT status,template_key,resolved_at,resolved_by,decision_reason FROM outreach_work_items').fetchone(),('pending',None,None,None,'Invoice and refund still unverified'))
        self.assertEqual(self.db.execute('SELECT status,sent_at FROM approved_reply_batch_log').fetchone(),('sent','2026-10-19T08:58:34Z'))
        self.assertEqual(self.db.execute('SELECT draft_body FROM outreach_work_items').fetchone()[0],'Exact sent body')

    def test_automatic_template_or_superseding_cannot_resolve_service(self):
        set_pending_case(self.db,1,10,'Deletion not verified')
        self.db.execute('PRAGMA recursive_triggers=ON')
        self.db.execute("UPDATE outreach_work_items SET status='superseded',template_key='coupon_test',kind='coupon_ready',risk_level='low'")
        self.assertEqual(self.db.execute('SELECT status,template_key,kind,risk_level FROM outreach_work_items').fetchone(),('pending',None,'human_review','high'))

    def test_explicitly_resolved_case_can_close(self):
        set_pending_case(self.db,1,10,'Pending service')
        self.db.execute("UPDATE reviewed_service_cases SET status='resolved'")
        self.db.execute("UPDATE outreach_work_items SET status='sent',resolved_by='service_operator'")
        self.assertEqual(self.db.execute('SELECT status,resolved_by FROM outreach_work_items').fetchone(),('sent','service_operator'))

    def test_wrong_contact_and_empty_reason_are_rejected(self):
        for cid,reason in [(20,'Invoice pending'),(10,'')]:
            with self.assertRaises(ValueError):set_pending_case(self.db,1,cid,reason)
        self.assertEqual(self.db.execute('SELECT status FROM outreach_work_items').fetchone()[0],'sent')


if __name__=='__main__':unittest.main()
