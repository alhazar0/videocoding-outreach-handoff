import sqlite3
import unittest

from scripts.send_coupon_replies import body_for, plan, coupon_account_lookup_email


class SegmentedCouponReplyTests(unittest.TestCase):
    def test_unknown_sender_lookup_does_not_confirm_registration(self):
        row = {'registered_email': None, 'registered_contact_email': None, 'sender_email': 'redacted-1c99ae1965@example.com'}
        self.assertEqual(coupon_account_lookup_email(row), 'redacted-1c99ae1965@example.com')
        self.assertIsNone(row['registered_email'])
        row['registered_email'] = 'explicit-account@example.com'
        self.assertEqual(coupon_account_lookup_email(row), 'explicit-account@example.com')

    def test_t2v_body_uses_prompt_and_ten_minute_product(self):
        body = body_for("t2v_knowledge", "creator@example.com")
        self.assertIn("short prompt", body)
        self.assertIn("up to 10 minutes", body)
        self.assertNotIn("talking-head video", body)

    def test_t2v_contact_is_routed_to_t2v_coupon_variant(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
          CREATE TABLE contacts(
            id INTEGER PRIMARY KEY,channel_name TEXT,email TEXT,initial_template_key TEXT
          );
          CREATE TABLE inbox_events(
            id TEXT PRIMARY KEY,contact_id INTEGER,event_type TEXT,received_at TEXT,
            sender_email TEXT,sender_name TEXT,subject TEXT,body_preview TEXT,conversation_id TEXT
          );
          CREATE TABLE coupon_reply_log(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,recipient_email TEXT,
            conversation_id TEXT,variant TEXT,status TEXT
          );
          CREATE TABLE outreach_work_items(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,kind TEXT,status TEXT,risk_level TEXT,
            confidence REAL,template_key TEXT
          );
          CREATE TABLE automation_rules(rule_key TEXT PRIMARY KEY,mode TEXT,enabled INTEGER);
          INSERT INTO automation_rules VALUES('coupon_reply','auto',1);
          INSERT INTO contacts VALUES(
            1,'Knowledge Creator','creator@example.com','first_contact_t2v_knowledge_v1'
          );
          INSERT INTO inbox_events VALUES(
            'message-1',1,'interested',datetime('now','-1 hour'),'creator@example.com',
            'Knowledge Creator','Re: collaboration','I would love to test it','conversation-1'
          );
          INSERT INTO outreach_work_items VALUES(
            10,'message-1','coupon_reply','pending','medium',0.99,'coupon_test_t2v_knowledge_v1'
          );
        """)
        eligible, skipped = plan(db)
        self.assertEqual(skipped, [])
        self.assertEqual(len(eligible), 1)
        self.assertEqual(eligible[0]["variant"], "t2v_knowledge")

    def test_combined_coupon_and_rate_template_is_manual(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
          CREATE TABLE contacts(id INTEGER PRIMARY KEY,channel_name TEXT,email TEXT,initial_template_key TEXT);
          CREATE TABLE inbox_events(
            id TEXT PRIMARY KEY,contact_id INTEGER,event_type TEXT,received_at TEXT,
            sender_email TEXT,sender_name TEXT,subject TEXT,body_preview TEXT,conversation_id TEXT
          );
          CREATE TABLE coupon_reply_log(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,recipient_email TEXT,
            conversation_id TEXT,variant TEXT,status TEXT
          );
          CREATE TABLE outreach_work_items(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,kind TEXT,status TEXT,risk_level TEXT,
            confidence REAL,template_key TEXT
          );
          CREATE TABLE automation_rules(rule_key TEXT PRIMARY KEY,mode TEXT,enabled INTEGER);
          INSERT INTO automation_rules VALUES('price_received','manual',1);
          INSERT INTO contacts VALUES(1,'Rate Creator','creator@example.com',NULL);
          INSERT INTO inbox_events VALUES(
            'message-1',1,'reply',datetime('now','-1 hour'),'creator@example.com','Creator',
            'Re: collaboration','My rate is $400. Please send the free-month code.','conversation-1'
          );
          INSERT INTO outreach_work_items VALUES(
            15,'message-1','coupon_reply','pending','medium',0.99,'coupon_test_rate_received'
          );
        """)
        eligible, skipped = plan(db)
        self.assertEqual(eligible, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("manual mode", skipped[0]["reason"])

    def test_sender_requires_ai_approved_coupon_work_item(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
          CREATE TABLE contacts(
            id INTEGER PRIMARY KEY,channel_name TEXT,email TEXT,initial_template_key TEXT
          );
          CREATE TABLE inbox_events(
            id TEXT PRIMARY KEY,contact_id INTEGER,event_type TEXT,received_at TEXT,
            sender_email TEXT,sender_name TEXT,subject TEXT,body_preview TEXT,conversation_id TEXT
          );
          CREATE TABLE coupon_reply_log(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,recipient_email TEXT,
            conversation_id TEXT,variant TEXT,status TEXT
          );
          CREATE TABLE outreach_work_items(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,kind TEXT,status TEXT,risk_level TEXT,
            confidence REAL,template_key TEXT
          );
          INSERT INTO contacts VALUES(
            1,'Paid Only Creator','paid@example.com','first_contact_ai_creator_v1'
          );
          INSERT INTO inbox_events VALUES(
            'message-1',1,'reply',datetime('now','-1 hour'),'paid@example.com',
            'Paid Only Creator','Re: collaboration',
            'We are not looking to do a free one-month trial. Please share your budget.',
            'conversation-1'
          );
          INSERT INTO outreach_work_items VALUES(
            11,'message-1','human_review','pending','high',0.99,NULL
          );
        """)
        eligible, skipped = plan(db)
        self.assertEqual(eligible, [])
        self.assertEqual(skipped, [])

    def test_conditional_payment_or_subscription_is_held_for_human(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
          CREATE TABLE contacts(
            id INTEGER PRIMARY KEY,channel_name TEXT,email TEXT,initial_template_key TEXT
          );
          CREATE TABLE inbox_events(
            id TEXT PRIMARY KEY,contact_id INTEGER,event_type TEXT,received_at TEXT,
            sender_email TEXT,sender_name TEXT,subject TEXT,body_preview TEXT,conversation_id TEXT
          );
          CREATE TABLE coupon_reply_log(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,recipient_email TEXT,
            conversation_id TEXT,variant TEXT,status TEXT
          );
          CREATE TABLE outreach_work_items(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,kind TEXT,status TEXT,risk_level TEXT,
            confidence REAL,template_key TEXT
          );
          INSERT INTO contacts VALUES(1,'Conditional Creator','creator@example.com',NULL);
          INSERT INTO inbox_events VALUES(
            'message-1',1,'reply',datetime('now','-1 hour'),'creator@example.com','Creator',
            'Re: collaboration','Once you subscribe and pay the deposit, send me the access code.','conversation-1'
          );
          INSERT INTO outreach_work_items VALUES(
            12,'message-1','coupon_reply','pending','medium',0.99,'coupon_test'
          );
        """)
        eligible, skipped = plan(db)
        self.assertEqual(eligible, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("human review", skipped[0]["reason"])

    def test_coupon_with_unanswered_campaign_question_is_held_for_human(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
          CREATE TABLE contacts(
            id INTEGER PRIMARY KEY,channel_name TEXT,email TEXT,initial_template_key TEXT
          );
          CREATE TABLE inbox_events(
            id TEXT PRIMARY KEY,contact_id INTEGER,event_type TEXT,received_at TEXT,
            sender_email TEXT,sender_name TEXT,subject TEXT,body_preview TEXT,conversation_id TEXT
          );
          CREATE TABLE coupon_reply_log(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,recipient_email TEXT,
            conversation_id TEXT,variant TEXT,status TEXT
          );
          CREATE TABLE outreach_work_items(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,kind TEXT,status TEXT,risk_level TEXT,
            confidence REAL,template_key TEXT
          );
          INSERT INTO contacts VALUES(1,'Compound Creator','creator@example.com',NULL);
          INSERT INTO inbox_events VALUES(
            'message-1',1,'reply',datetime('now','-1 hour'),'creator@example.com','Creator',
            'Re: collaboration','Please send the code. Could you share the budget, deliverables, and timeline?',
            'conversation-1'
          );
          INSERT INTO outreach_work_items VALUES(
            13,'message-1','coupon_reply','pending','medium',0.99,'coupon_test'
          );
        """)
        eligible, skipped = plan(db)
        self.assertEqual(eligible, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("固定模板无法完整回答", skipped[0]["reason"])

    def test_standard_coupon_with_existing_rate_is_held(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
          CREATE TABLE contacts(
            id INTEGER PRIMARY KEY,channel_name TEXT,email TEXT,initial_template_key TEXT
          );
          CREATE TABLE inbox_events(
            id TEXT PRIMARY KEY,contact_id INTEGER,event_type TEXT,received_at TEXT,
            sender_email TEXT,sender_name TEXT,subject TEXT,body_preview TEXT,conversation_id TEXT
          );
          CREATE TABLE coupon_reply_log(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,recipient_email TEXT,
            conversation_id TEXT,variant TEXT,status TEXT
          );
          CREATE TABLE outreach_work_items(
            id INTEGER PRIMARY KEY,inbox_event_id TEXT,kind TEXT,status TEXT,risk_level TEXT,
            confidence REAL,template_key TEXT
          );
          INSERT INTO contacts VALUES(1,'Rate Creator','creator@example.com',NULL);
          INSERT INTO inbox_events VALUES(
            'message-1',1,'reply',datetime('now','-1 hour'),'creator@example.com','Creator',
            'Re: collaboration','My rate is $400. Please send the free-month code.','conversation-1'
          );
          INSERT INTO outreach_work_items VALUES(
            14,'message-1','coupon_reply','pending','medium',0.99,'coupon_test'
          );
        """)
        eligible, skipped = plan(db)
        self.assertEqual(eligible, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("已报价 Coupon 模板", skipped[0]["reason"])


if __name__ == "__main__":
    unittest.main()
