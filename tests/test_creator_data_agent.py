import json
import sqlite3
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from scripts import creator_data_agent as agent


class ProductHandler(BaseHTTPRequestHandler):
    grants = 0

    def log_message(self, *_args):
        return

    def do_GET(self):
        payload = {
            "success": True,
            "found": True,
            "creator": {
                "id": "product-user-1",
                "email": "creator@example.com",
                "emailVerified": True,
                "lastLoginAt": "2026-08-28T10:00:00Z",
                "subscription": {"status": "none", "currentPeriodEnd": None},
                "coupon": {"status": "redeemed", "code": "FREETEST100"},
                "activity": {
                    "totalTasks": 2,
                    "activeTasks": 0,
                    "completedTasks": 1,
                    "failedTasks": 1,
                    "canceledTasks": 0,
                    "lastTaskAt": "2026-08-28T10:05:00Z",
                },
            },
        }
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        ProductHandler.grants += 1
        payload = {
            "success": True,
            "subscription": {
                "status": "active",
                "manualGrant": True,
                "currentPeriodEnd": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            },
        }
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())


class CreatorDataAgentTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE contacts(
              id INTEGER PRIMARY KEY,email TEXT,channel_name TEXT,rate_info TEXT,next_action TEXT,updated_at TEXT
            );
            CREATE TABLE creator_profiles(
              contact_id INTEGER PRIMARY KEY,workflow_stage TEXT DEFAULT 'testing',registered_email TEXT,
              account_status TEXT DEFAULT 'unknown',membership_status TEXT DEFAULT 'none',membership_expires_at TEXT,
              coupon_sent_at TEXT,test_status TEXT DEFAULT 'wants_test',test_started_at TEXT,test_completed_at TEXT,
              marketing_status TEXT DEFAULT 'not_ready',
              product_user_id TEXT,usage_verification_status TEXT DEFAULT 'not_checked',usage_total_tasks INTEGER DEFAULT 0,
              usage_active_tasks INTEGER DEFAULT 0,usage_completed_tasks INTEGER DEFAULT 0,usage_last_login_at TEXT,
              usage_last_task_at TEXT,usage_verified_at TEXT,usage_source TEXT,updated_at TEXT
            );
            CREATE TABLE outreach_settings(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT);
            CREATE TABLE automation_rules(
              rule_key TEXT PRIMARY KEY,mode TEXT,risk_level TEXT,enabled INTEGER
            );
            INSERT INTO automation_rules VALUES('membership_grant','manual','high',1);
            CREATE TABLE outreach_audit_log(
              id INTEGER PRIMARY KEY,action TEXT,entity_type TEXT,entity_id TEXT,actor TEXT,detail_json TEXT,created_at TEXT
            );
            CREATE TABLE outreach_templates(template_key TEXT PRIMARY KEY,subject TEXT,body TEXT,enabled INTEGER,
              approval_status TEXT DEFAULT 'approved');
            CREATE TABLE inbox_events(
              id TEXT PRIMARY KEY,contact_id INTEGER,received_at TEXT,sender_email TEXT,body_preview TEXT,body_text TEXT
            );
            CREATE TABLE outreach_work_items(
              id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,kind TEXT,workflow_stage TEXT,status TEXT,
              risk_level TEXT,confidence REAL,template_key TEXT,subject TEXT,draft_body TEXT,decision_reason TEXT,
              resolved_at TEXT,resolved_by TEXT,
              extracted_json TEXT DEFAULT '{}',created_at TEXT,updated_at TEXT
            );
            """
        )
        agent.ensure_agent_schema(self.db)
        self.db.execute(
            "INSERT INTO contacts(id,email,channel_name,updated_at) VALUES(1,'creator@example.com','Creator',datetime('now'))"
        )
        self.db.execute(
            """INSERT INTO creator_profiles(contact_id,registered_email,coupon_sent_at,updated_at)
               VALUES(1,'creator@example.com',datetime('now'),datetime('now'))"""
        )
        templates = {
            "ask_registered_email": "Please send the email you used to register.",
            "account_not_found": "No account found for {{registered_email}}.",
            "membership_activated": "Done for {{registered_email}}. No card and no automatic charge.",
        }
        for key, body in templates.items():
            self.db.execute(
                "INSERT INTO outreach_templates(template_key,subject,body,enabled) VALUES(?,'Re: {{original_subject}}',?,1)",
                (key, body),
            )
        self.db.execute(
            "INSERT INTO outreach_templates(template_key,subject,body,enabled) VALUES('ask_rate','Re: {{original_subject}}','Please share your rate.',1)"
        )
        self.db.execute(
            "INSERT INTO outreach_templates(template_key,subject,body,enabled) VALUES('marketing_listed','Re: {{original_subject}}','Added to marketing review.',1)"
        )
        self.db.commit()
        ProductHandler.grants = 0
        self.server = HTTPServer(("127.0.0.1", 0), ProductHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.token = "x" * 40

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.db.close()

    def test_current_product_state_replaces_stale_active_membership(self):
        self.db.execute("UPDATE creator_profiles SET membership_status='active',membership_expires_at='2026-10-20'")
        agent.apply_usage(self.db,1,'creator@example.com',{'found':True,'creator':{
            'subscription':{'status':'none','currentPeriodEnd':None},
            'coupon':{'status':'not_redeemed','checkedAt':None},'activity':{}}})
        row=self.db.execute('SELECT membership_status,membership_expires_at,coupon_redemption_status FROM creator_profiles').fetchone()
        self.assertEqual(tuple(row),('none',None,'not_checked'))

    def test_missing_product_account_cannot_remain_confirmed_active(self):
        self.db.execute("UPDATE creator_profiles SET membership_status='active'")
        agent.apply_usage(self.db,1,'creator@example.com',{'found':False})
        self.assertEqual(self.db.execute('SELECT membership_status FROM creator_profiles').fetchone()[0],'unverified')

    def test_sync_separates_coupon_login_tasks_and_completed_video(self):
        checked, updated, redeemed = agent.sync_usage(self.db, self.origin, self.token, 10, 15)
        row = self.db.execute("SELECT * FROM creator_profiles WHERE contact_id=1").fetchone()
        self.assertEqual((checked, updated, redeemed), (1, 1, 1))
        self.assertEqual(row["usage_verification_status"], "completed")
        self.assertEqual(row["usage_total_tasks"], 2)
        self.assertEqual(row["usage_completed_tasks"], 1)
        self.assertEqual(row["coupon_redemption_status"], "redeemed")

    def test_cardless_grant_and_reply_are_fixed_and_idempotent(self):
        self.db.execute("UPDATE automation_rules SET mode='auto' WHERE rule_key='membership_grant'")
        self.db.execute(
            """INSERT INTO outreach_work_items(
              id,inbox_event_id,contact_id,kind,workflow_stage,status,risk_level,confidence,template_key,draft_body,
              extracted_json,created_at,updated_at) VALUES(
              9,'graph-message-9',1,'membership_request','membership_pending','pending','high',0.98,
              'ask_registered_email','','{}',datetime('now'),datetime('now'))"""
        )
        self.db.execute(
            "INSERT INTO inbox_events VALUES('graph-message-9',1,datetime('now'),'creator@example.com','','')"
        )
        self.db.commit()
        sent = []
        with mock.patch.object(agent, "refresh_access_token", return_value="graph-token"), mock.patch.object(
            agent, "send_reply", side_effect=lambda _token, message_id, body: (sent.append((message_id, body)) or True, "accepted")
        ):
            granted, replies = agent.process_memberships(self.db, self.origin, self.token, 10, True)
        self.assertEqual((granted, replies), (1, 1))
        self.assertEqual(ProductHandler.grants, 1)
        self.assertIn("creator@example.com", sent[0][1])
        self.assertEqual(self.db.execute("SELECT status FROM outreach_work_items WHERE id=9").fetchone()[0], "sent")

    def test_manual_membership_policy_blocks_grant_and_reply(self):
        self.db.execute(
            "INSERT INTO inbox_events VALUES('manual-membership',1,datetime('now'),'creator@example.com','','no card')"
        )
        self.db.execute(
            """INSERT INTO outreach_work_items(
              id,inbox_event_id,contact_id,kind,workflow_stage,status,risk_level,confidence,template_key,draft_body,
              extracted_json,created_at,updated_at) VALUES(
              8,'manual-membership',1,'membership_request','membership_pending','pending','high',0.99,
              'ask_registered_email','','{}',datetime('now'),datetime('now'))"""
        )
        self.db.commit()
        with mock.patch.object(agent, "refresh_access_token") as refresh:
            self.assertEqual(agent.process_memberships(self.db, self.origin, self.token, 10, True), (0, 0))
        refresh.assert_not_called()

    def test_unapproved_membership_template_blocks_before_any_external_call(self):
        self.db.execute("UPDATE automation_rules SET mode='auto' WHERE rule_key='membership_grant'")
        self.db.execute("UPDATE outreach_templates SET approval_status='pending_review' WHERE template_key='membership_activated'")
        with mock.patch.object(agent, "refresh_access_token") as refresh, mock.patch.object(agent, "creator_lookup") as lookup:
            self.assertEqual(agent.process_memberships(self.db, self.origin, self.token, 10, True), (0, 0))
        refresh.assert_not_called()
        lookup.assert_not_called()

    def test_activation_requires_verified_manual_month(self):
        valid = {"status": "active", "manualGrant": True,
                 "currentPeriodEnd": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()}
        self.assertTrue(agent.confirmed_month_grant(valid))
        for overrides in ({"status": "none"}, {"manualGrant": False}, {"manualGrant": None},
                          {"currentPeriodEnd": None}, {"currentPeriodEnd": "invalid"},
                          {"currentPeriodEnd": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()},
                          {"currentPeriodEnd": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()}):
            with self.subTest(overrides=overrides):
                self.assertFalse(agent.confirmed_month_grant({**valid, **overrides}))

    def test_old_unanswered_request_remains_candidate_but_tested_requires_review(self):
        self.db.execute("UPDATE creator_profiles SET usage_verification_status='completed' WHERE contact_id=1")
        self.db.execute(
            "INSERT INTO inbox_events VALUES('tested-request',1,datetime('now'),'creator@example.com','','no card')"
        )
        self.db.execute(
            """INSERT INTO outreach_work_items(
              id,inbox_event_id,contact_id,kind,workflow_stage,status,risk_level,confidence,template_key,draft_body,
              extracted_json,created_at,updated_at) VALUES(
              10,'tested-request',1,'membership_request','membership_pending','pending','high',0.99,
              'ask_registered_email','','{}',datetime('now'),datetime('now'))"""
        )
        self.db.execute(
            "INSERT INTO contacts(id,email,channel_name,updated_at) VALUES(2,'stale@example.com','Stale',datetime('now'))"
        )
        self.db.execute(
            """INSERT INTO creator_profiles(contact_id,updated_at) VALUES(2,datetime('now'))"""
        )
        self.db.execute(
            "INSERT INTO inbox_events VALUES('stale-request',2,datetime('now','-4 days'),'stale@example.com','','no card')"
        )
        self.db.execute(
            """INSERT INTO outreach_work_items(
              id,inbox_event_id,contact_id,kind,workflow_stage,status,risk_level,confidence,template_key,draft_body,
              extracted_json,created_at,updated_at) VALUES(
              11,'stale-request',2,'membership_request','membership_pending','pending','high',0.99,
              'ask_registered_email','','{}',datetime('now','-4 days'),datetime('now'))"""
        )
        self.db.commit()
        self.assertEqual([row['id'] for row in agent.membership_candidates(self.db, 20)], [11])
        self.assertEqual(agent.hold_progressed_membership_requests(self.db), 1)
        old = self.db.execute('SELECT status,kind FROM outreach_work_items WHERE id=11').fetchone()
        self.assertEqual(tuple(old), ('pending', 'membership_request'))
        tested = self.db.execute('SELECT status,kind,template_key,resolved_at FROM outreach_work_items WHERE id=10').fetchone()
        self.assertEqual(tuple(tested), ('pending', 'human_review', None, None))
        self.assertEqual(agent.hold_progressed_membership_requests(self.db), 0)

    def test_new_registered_email_message_replaces_older_membership_candidate(self):
        for event, email in [('a-old', 'wrong@example.com'), ('b-corrected', 'right@example.com')]:
            self.db.execute("INSERT INTO inbox_events VALUES(?,1,datetime('now','-5 days'),'creator@example.com','',?)",
                            (event, 'The registered email is ' + email))
            self.db.execute("""INSERT INTO outreach_work_items(
              inbox_event_id,contact_id,kind,status,risk_level,confidence,template_key,extracted_json,created_at)
              VALUES(?,1,'membership_request','pending','high',0.99,'ask_registered_email',?,datetime('now','-5 days'))""",
                            (event, json.dumps({'registered_email': email})))
        candidates = agent.membership_candidates(self.db, 20)
        self.assertEqual([r['inbox_event_id'] for r in candidates], ['b-corrected'])
        self.assertEqual(candidates[0]['registered_email'], 'right@example.com')

    def test_mixed_campaign_or_product_question_requires_human(self):
        self.db.execute(
            "INSERT INTO contacts(id,email,channel_name,updated_at) VALUES(3,'mixed@example.com','Mixed',datetime('now'))"
        )
        self.db.execute("INSERT INTO creator_profiles(contact_id,updated_at) VALUES(3,datetime('now'))")
        self.db.execute(
            "INSERT INTO inbox_events VALUES('mixed-request',3,datetime('now'),'mixed@example.com','','no card and language question')"
        )
        extracted = json.dumps({"questions": ["Can I avoid a card?", "Does it support another language?"]})
        self.db.execute(
            """INSERT INTO outreach_work_items(
              id,inbox_event_id,contact_id,kind,workflow_stage,status,risk_level,confidence,template_key,draft_body,
              extracted_json,created_at,updated_at) VALUES(
              12,'mixed-request',3,'membership_request','membership_pending','pending','high',0.99,
              'ask_registered_email','',?,datetime('now'),datetime('now'))""",
            (extracted,),
        )
        self.db.commit()
        self.assertEqual(agent.membership_candidates(self.db, 20), [])

    def test_verified_test_with_price_moves_to_marketing_review_without_sending(self):
        self.db.execute("UPDATE contacts SET rate_info='$250 dedicated' WHERE id=1")
        self.db.execute(
            "INSERT INTO inbox_events VALUES('tested-price',1,datetime('now'),'creator@example.com','Ready','I tested it. My rate is $250.')"
        )
        self.db.execute(
            """INSERT INTO outreach_work_items(
              id,inbox_event_id,contact_id,kind,workflow_stage,status,risk_level,confidence,template_key,subject,
              draft_body,extracted_json,created_at,updated_at) VALUES(
              20,'tested-price',1,'price_and_test_claim','price_received_unverified','pending','high',0.99,
              'test_claim_checking','Re: Test','','{}',datetime('now'),datetime('now'))"""
        )
        self.db.execute(
            "UPDATE creator_profiles SET usage_verification_status='completed',test_status='completed' WHERE contact_id=1"
        )
        self.db.commit()
        self.assertEqual(agent.reconcile_verified_tests(self.db), 1)
        row = self.db.execute("SELECT * FROM outreach_work_items WHERE id=20").fetchone()
        self.assertEqual(row["template_key"], "marketing_listed")
        self.assertEqual(row["workflow_stage"], "marketing_review")
        self.assertEqual(row["status"], "pending")

    def test_verified_usage_does_not_override_human_context_review(self):
        self.db.execute(
            "INSERT INTO inbox_events VALUES('tested-inline-price',1,datetime('now'),'creator@example.com','Ready','I tested it. My dedicated rate is $400.')"
        )
        self.db.execute(
            """INSERT INTO outreach_work_items(
              id,inbox_event_id,contact_id,kind,workflow_stage,status,risk_level,confidence,template_key,subject,
              draft_body,extracted_json,created_at,updated_at) VALUES(
              22,'tested-inline-price',1,'human_review','test_claimed','pending','high',0.91,
              NULL,'Re: Test','','{}',datetime('now'),datetime('now'))"""
        )
        self.db.execute(
            "UPDATE creator_profiles SET usage_verification_status='completed',test_status='completed' WHERE contact_id=1"
        )
        self.db.commit()
        self.assertEqual(agent.reconcile_verified_tests(self.db), 0)
        row = self.db.execute("SELECT kind,template_key FROM outreach_work_items WHERE id=22").fetchone()
        self.assertEqual(row["kind"], "human_review")
        self.assertIsNone(row["template_key"])

    def test_verified_test_rejection_never_becomes_ask_rate(self):
        self.db.execute(
            "INSERT INTO inbox_events VALUES('tested-rejected',1,datetime('now'),'creator@example.com','No fit','I tested it, but it is too basic and I cannot showcase it.')"
        )
        self.db.execute(
            """INSERT INTO outreach_work_items(
              id,inbox_event_id,contact_id,kind,workflow_stage,status,risk_level,confidence,template_key,subject,
              draft_body,extracted_json,created_at,updated_at) VALUES(
              23,'tested-rejected',1,'test_claim','test_claimed','pending','high',0.99,
              'test_claim_checking','Re: Test','','{}',datetime('now'),datetime('now'))"""
        )
        self.db.execute(
            "UPDATE creator_profiles SET usage_verification_status='completed',test_status='completed' WHERE contact_id=1"
        )
        self.db.commit()
        self.assertEqual(agent.reconcile_verified_tests(self.db), 1)
        row = self.db.execute("SELECT kind,template_key,decision_reason FROM outreach_work_items WHERE id=23").fetchone()
        self.assertEqual(row["kind"], "human_review")
        self.assertIsNone(row["template_key"])
        self.assertIn("拒绝", row["decision_reason"])

    def test_verified_test_with_campaign_questions_stays_manual(self):
        self.db.execute(
            "INSERT INTO inbox_events VALUES('tested-questions',1,datetime('now'),'creator@example.com','Ready','What is your budget and timeline?')"
        )
        self.db.execute(
            """INSERT INTO outreach_work_items(
              id,inbox_event_id,contact_id,kind,workflow_stage,status,risk_level,confidence,template_key,subject,
              draft_body,extracted_json,created_at,updated_at) VALUES(
              21,'tested-questions',1,'test_claim','test_claimed','pending','high',0.99,
              'test_claim_checking','Re: Test','','{}',datetime('now'),datetime('now'))"""
        )
        self.db.execute(
            "UPDATE creator_profiles SET usage_verification_status='completed',test_status='completed' WHERE contact_id=1"
        )
        self.db.commit()
        self.assertEqual(agent.reconcile_verified_tests(self.db), 1)
        row = self.db.execute("SELECT * FROM outreach_work_items WHERE id=21").fetchone()
        self.assertEqual(row["kind"], "human_review")
        self.assertIsNone(row["template_key"])

        self.db.execute(
            "UPDATE outreach_work_items SET workflow_stage='human_review_blocked' WHERE id=21"
        )
        self.db.commit()
        self.assertEqual(agent.reconcile_verified_tests(self.db), 0)
        audit_count = self.db.execute(
            "SELECT COUNT(*) FROM outreach_audit_log WHERE action='verified_test_routed' AND entity_id='21'"
        ).fetchone()[0]
        self.assertEqual(audit_count, 1)


if __name__ == "__main__":
    unittest.main()
