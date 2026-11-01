import sqlite3
import unittest

from scripts import safe_workflow_reply_agent as agent


class SafeWorkflowReplyAgentTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE contacts(
              id INTEGER PRIMARY KEY,email TEXT,channel_name TEXT,initial_template_key TEXT,rate_info TEXT,
              next_action TEXT,do_not_contact INTEGER DEFAULT 0,updated_at TEXT
            );
            CREATE TABLE inbox_events(
              id TEXT PRIMARY KEY,contact_id INTEGER,received_at TEXT,body_text TEXT,body_preview TEXT
            );
            CREATE TABLE outreach_work_items(
              id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,kind TEXT,workflow_stage TEXT,status TEXT,
              risk_level TEXT,confidence REAL,template_key TEXT,subject TEXT,draft_body TEXT,decision_reason TEXT,
              proposed_action_json TEXT,updated_at TEXT,resolved_at TEXT,resolved_by TEXT
            );
            CREATE TABLE outreach_templates(
              template_key TEXT PRIMARY KEY,name TEXT,stage TEXT,subject TEXT,body TEXT,
              risk_level TEXT,enabled INTEGER,version INTEGER,updated_at TEXT
            );
            CREATE TABLE coupon_reply_log(
              id INTEGER PRIMARY KEY,contact_id INTEGER,status TEXT
            );
            CREATE TABLE creator_profiles(
              contact_id INTEGER PRIMARY KEY,usage_verification_status TEXT,workflow_stage TEXT,updated_at TEXT
            );
            CREATE TABLE automation_rules(
              rule_key TEXT PRIMARY KEY,mode TEXT,risk_level TEXT,enabled INTEGER
            );
            INSERT INTO automation_rules VALUES('price_received','manual','high',1);
            INSERT INTO automation_rules VALUES('price_request','draft','medium',1);
            INSERT INTO automation_rules VALUES('marketing_list','draft','medium',1);
            INSERT INTO automation_rules VALUES('semantic_template_router','draft','medium',1);
            """
        )
        agent.ensure_agent_schema(self.db)
        for key in ("coupon_test", "coupon_test_t2v_knowledge_v1"):
            self.db.execute(
                """INSERT INTO outreach_templates VALUES(
                  ?,'Coupon','coupon_ready','Coupon','Code {{coupon_code}} for {{channel_name}}','medium',1,1,datetime('now')
                )""",
                (key,),
            )
        self.db.execute("INSERT INTO contacts VALUES(1,'one@example.com','Creator One',NULL,NULL,NULL,0,datetime('now'))")
        self.db.execute("INSERT INTO creator_profiles VALUES(1,'not_checked','replied',datetime('now'))")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def add_item(self, body, kind="price_received", template="rate_received", item_id=10):
        self.db.execute("INSERT INTO inbox_events VALUES('message-1',1,datetime('now'),?,'')", (body,))
        self.db.execute(
            """INSERT INTO outreach_work_items VALUES(
              ?,'message-1',1,?,'price_received','pending','high',0.99,?,'Re','fixed body','old','[]',datetime('now'),NULL,NULL
            )""",
            (item_id, kind, template),
        )
        self.db.commit()

    def test_price_and_explicit_test_request_becomes_coupon_without_accepting_rate(self):
        self.add_item("My dedicated rate is $250. Please send me the code so I can test it.")
        result = agent.reconcile_context(self.db)
        row = self.db.execute("SELECT * FROM outreach_work_items WHERE id=10").fetchone()
        self.assertEqual(result["converted_coupon"], 1)
        self.assertEqual(row["kind"], "coupon_reply")
        self.assertEqual(row["template_key"], "coupon_test_rate_received")
        self.assertIn("FREETEST100", row["draft_body"])
        self.assertIn("不代表接受报价", row["decision_reason"])

    def test_campaign_question_is_forced_to_human(self):
        self.add_item("I will test it. Could you share the campaign budget, brief, and timeline?", kind="test_planned", template="test_planned_ack")
        result = agent.reconcile_context(self.db)
        row = self.db.execute("SELECT * FROM outreach_work_items WHERE id=10").fetchone()
        self.assertEqual(result["forced_human"], 1)
        self.assertEqual(row["kind"], "human_review")
        self.assertEqual(row["workflow_stage"], "human_review_blocked")
        self.assertIsNone(row["template_key"])
        self.assertEqual(agent.reconcile_context(self.db)["forced_human"], 0)

    def test_quoted_old_question_does_not_override_current_simple_rate(self):
        self.add_item("My rate is $400.\n\nFrom: Alhazar\nWhat is your budget and timeline?")
        agent.reconcile_context(self.db)
        self.assertEqual(self.db.execute("SELECT kind FROM outreach_work_items WHERE id=10").fetchone()[0], "price_received")
        self.db.execute("UPDATE automation_rules SET mode='auto' WHERE rule_key='price_received'")
        # A rule-mode change alone cannot authorize an old, unaudited draft.
        self.assertEqual(agent.eligible(self.db, 10), [])

    def test_non_auto_rule_never_enters_send_queue(self):
        self.add_item("My rate is $400.")
        agent.reconcile_context(self.db)
        self.assertEqual(agent.eligible(self.db, 10), [])

    def test_current_price_blocks_ask_rate(self):
        self.add_item(
            "I completed the test. My rate is $250 for a dedicated video.",
            kind="test_verified",
            template="ask_rate",
        )
        result = agent.reconcile_context(self.db)
        row = self.db.execute("SELECT kind,template_key,decision_reason FROM outreach_work_items WHERE id=10").fetchone()
        self.assertEqual(result["forced_human"], 1)
        self.assertEqual(row["kind"], "human_review")
        self.assertIsNone(row["template_key"])
        self.assertIn("已经包含报价", row["decision_reason"])

    def test_payment_terms_and_product_issues_never_auto_send(self):
        self.add_item(
            "I tested it, but rendering is too slow. Please send 50% upfront to my PayPal.",
            kind="price_received",
            template="marketing_listed",
        )
        result = agent.reconcile_context(self.db)
        row = self.db.execute("SELECT kind,template_key FROM outreach_work_items WHERE id=10").fetchone()
        self.assertEqual(result["forced_human"], 1)
        self.assertEqual(row["kind"], "human_review")
        self.assertIsNone(row["template_key"])

    def test_stale_sending_is_held_for_delivery_reconciliation(self):
        self.add_item("I tested it. Please share the rate process.", kind="test_verified", template="ask_rate")
        self.db.execute(
            """INSERT INTO safe_workflow_reply_log(
              work_item_id,contact_id,template_key,status,queued_at,updated_at
            ) VALUES(10,1,'ask_rate','sending',datetime('now','-30 minutes'),datetime('now','-30 minutes'))"""
        )
        self.db.commit()
        self.assertEqual(agent.recover_stale_sending(self.db), 1)
        log = self.db.execute("SELECT status,error FROM safe_workflow_reply_log WHERE work_item_id=10").fetchone()
        item = self.db.execute("SELECT kind,workflow_stage FROM outreach_work_items WHERE id=10").fetchone()
        self.assertEqual(log["status"], "delivery_unknown")
        self.assertIn("verify Sent Items", log["error"])
        self.assertEqual((item["kind"], item["workflow_stage"]), ("human_review", "delivery_reconciliation"))
        self.assertEqual(agent.recover_stale_sending(self.db), 0)


if __name__ == "__main__":
    unittest.main()
