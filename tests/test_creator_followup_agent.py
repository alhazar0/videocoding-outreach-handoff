import argparse
import sqlite3
import unittest
from unittest import mock

from scripts import creator_followup_agent as agent


class CreatorFollowupAgentTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE contacts(
              id INTEGER PRIMARY KEY,email TEXT,channel_name TEXT,do_not_contact INTEGER DEFAULT 0,
              outreach_status TEXT DEFAULT 'sent',followup1_due TEXT,next_action TEXT,updated_at TEXT
            );
            CREATE TABLE outreach_templates(
              template_key TEXT PRIMARY KEY,name TEXT,stage TEXT,subject TEXT,body TEXT,
              risk_level TEXT,enabled INTEGER,version INTEGER,updated_at TEXT
            );
            CREATE TABLE outreach_settings(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT);
            CREATE TABLE automation_rules(
              rule_key TEXT PRIMARY KEY,mode TEXT,risk_level TEXT,enabled INTEGER
            );
            INSERT INTO automation_rules VALUES('first_contact','draft','medium',1);
            INSERT INTO automation_rules VALUES('price_request','draft','medium',1);
            INSERT INTO automation_rules VALUES('membership_grant','manual','high',1);
            INSERT INTO automation_rules VALUES('semantic_template_router','draft','medium',1);
            CREATE TABLE outreach_send_log(
              id INTEGER PRIMARY KEY,contact_id INTEGER,sent_at TEXT,sender_email TEXT,status TEXT
            );
            CREATE TABLE inbox_events(
              id TEXT PRIMARY KEY,contact_id INTEGER,received_at TEXT,conversation_id TEXT
            );
            CREATE TABLE coupon_reply_log(
              id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,sent_at TEXT,
              conversation_id TEXT,status TEXT
            );
            CREATE TABLE creator_profiles(
              contact_id INTEGER PRIMARY KEY,registered_email TEXT,usage_verification_status TEXT DEFAULT 'not_checked',
              price_dedicated REAL,price_integration REAL,price_shorts REAL
            );
            CREATE TABLE outreach_work_items(
              id INTEGER PRIMARY KEY,inbox_event_id TEXT,contact_id INTEGER,status TEXT,template_key TEXT,
              resolved_at TEXT
            );
            """
        )
        agent.ensure_followup_schema(self.db)
        self.db.execute("INSERT INTO contacts(id,email,channel_name) VALUES(1,'one@example.com','Creator One')")
        self.db.execute("INSERT INTO contacts(id,email,channel_name) VALUES(2,'two@example.com','Creator Two')")
        self.db.execute("INSERT INTO creator_profiles(contact_id) VALUES(1)")
        self.db.execute("INSERT INTO creator_profiles(contact_id) VALUES(2)")
        self.db.execute(
            """INSERT INTO outreach_templates(template_key,name,stage,subject,body,risk_level,enabled,version,updated_at)
               VALUES('test_followup','Test followup','testing','Re: Test','Did you get a chance to test?','medium',1,1,datetime('now'))"""
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_initial_followup_queues_only_after_24h_without_reply(self):
        self.db.execute("INSERT INTO outreach_send_log VALUES(10,1,datetime('now','-25 hours'),'team@videocoding.ai','sent')")
        self.db.execute("INSERT INTO outreach_send_log VALUES(11,2,datetime('now','-25 hours'),'team@videocoding.ai','sent')")
        self.db.execute("INSERT INTO inbox_events VALUES('reply-2',2,datetime('now','-2 hours'),NULL)")
        self.db.commit()
        self.assertEqual(agent.queue_initial_followups(self.db), 1)
        row = self.db.execute("SELECT * FROM creator_followups").fetchone()
        self.assertEqual(row["contact_id"], 1)
        self.assertIn("Creator One", row["body"])

    def test_coupon_followup_excludes_real_completed_test(self):
        self.db.execute("INSERT INTO coupon_reply_log VALUES(20,'coupon-1',1,datetime('now','-25 hours'),'conv-1','sent')")
        self.db.execute("INSERT INTO coupon_reply_log VALUES(21,'coupon-2',2,datetime('now','-25 hours'),'conv-2','sent')")
        self.db.execute("UPDATE creator_profiles SET usage_verification_status='completed' WHERE contact_id=2")
        self.db.commit()
        self.assertEqual(agent.queue_coupon_followups(self.db), 1)
        row = self.db.execute("SELECT * FROM creator_followups WHERE source_kind='coupon'").fetchone()
        self.assertEqual(row["contact_id"], 1)
        self.assertEqual(row["conversation_id"], "coupon-1")

    def test_workflow_followup_covers_cardless_activation_and_missing_registration_email(self):
        self.db.execute("INSERT INTO inbox_events VALUES('membership-1',1,datetime('now','-3 days'),'conv-1')")
        self.db.execute("INSERT INTO inbox_events VALUES('registration-2',2,datetime('now','-3 days'),'conv-2')")
        self.db.execute("INSERT INTO outreach_work_items VALUES(30,'membership-1',1,'sent','membership_activated',datetime('now','-25 hours'))")
        self.db.execute("INSERT INTO outreach_work_items VALUES(31,'registration-2',2,'sent','ask_registered_email',datetime('now','-25 hours'))")
        self.db.commit()
        self.assertEqual(agent.queue_workflow_followups(self.db), 2)
        keys = [row[0] for row in self.db.execute("SELECT template_key FROM creator_followups ORDER BY id")]
        self.assertEqual(keys, ["test_followup", "registered_email_followup_24h"])

    def test_new_reply_cancels_pending_followup_before_send(self):
        self.db.execute("INSERT INTO outreach_send_log VALUES(40,1,datetime('now','-25 hours'),'team@videocoding.ai','sent')")
        self.db.commit()
        agent.queue_initial_followups(self.db)
        self.db.execute("INSERT INTO inbox_events VALUES('late-reply',1,datetime('now'),NULL)")
        self.db.commit()
        self.assertEqual(agent.cancel_replied_or_ineligible(self.db), 1)
        self.assertEqual(self.db.execute("SELECT status FROM creator_followups").fetchone()[0], "cancelled")

    def test_draft_policy_never_auto_sends_followup(self):
        self.db.execute("INSERT INTO outreach_send_log VALUES(50,1,datetime('now','-25 hours'),'team@videocoding.ai','sent')")
        self.db.commit()
        self.assertEqual(agent.queue_initial_followups(self.db), 1)
        args = argparse.Namespace(
            limit=20,
            daily_limit=250,
            gmail_daily_limit=25,
            send=True,
            min_interval_seconds=0,
            max_interval_seconds=0,
        )
        self.assertEqual(agent.send_due(self.db, args), (0, 0))
        row = self.db.execute("SELECT status,error FROM creator_followups").fetchone()
        self.assertEqual(row["status"], "manual_required")
        self.assertIn("draft", row["error"])

    def test_restricted_microsoft_keeps_pending_and_preserves_gmail_path(self):
        self.db.execute("INSERT INTO outreach_settings VALUES('microsoft_outbound_block_reason','restricted',datetime('now'))")
        for cid, sender in ((1,agent.MAILBOX),(2,agent.GOOGLE_MAILBOX)):
            self.db.execute("""INSERT INTO creator_followups(contact_id,source_kind,source_id,template_key,sender_email,
              source_sent_at,due_at,subject,body,status,queued_at,updated_at)
              VALUES(?,'initial',?,'initial_followup_24h',?,datetime('now','-25 hours'),datetime('now','-1 hour'),
              'Follow up','Hello','pending',datetime('now'),datetime('now'))""",(cid,str(cid),sender))
        self.db.commit()
        args=argparse.Namespace(limit=20,daily_limit=250,gmail_daily_limit=25,send=True,min_interval_seconds=0,max_interval_seconds=0)
        with mock.patch.object(agent,'gmail_smtp_transport_available',return_value=(True,'ok')), \
             mock.patch.object(agent,'template_automation_mode',return_value='auto'), \
             mock.patch.object(agent,'still_eligible',return_value=True), \
             mock.patch.object(agent,'google_app_password',return_value='test'), \
             mock.patch.object(agent,'send_gmail_smtp',return_value=(True,'accepted')) as gmail, \
             mock.patch.object(agent,'refresh_access_token') as token, \
             mock.patch.object(agent,'graph_send_new') as graph:
            self.assertEqual(agent.send_due(self.db,args),(1,0))
            gmail.assert_called_once()
            graph.assert_not_called()
            token.assert_not_called()
        self.assertEqual(self.db.execute('SELECT status FROM creator_followups WHERE contact_id=1').fetchone()[0],'pending')

    def test_interrupted_followup_requires_delivery_verification(self):
        self.db.execute("INSERT INTO outreach_send_log VALUES(60,1,datetime('now','-25 hours'),'team@videocoding.ai','sent')")
        self.db.commit()
        self.assertEqual(agent.queue_initial_followups(self.db), 1)
        self.db.execute(
            "UPDATE creator_followups SET status='sending',updated_at=datetime('now','-30 minutes')"
        )
        self.db.commit()
        self.assertEqual(agent.recover_stale_sending(self.db), 1)
        row = self.db.execute("SELECT status,error FROM creator_followups").fetchone()
        self.assertEqual(row["status"], "manual_required")
        self.assertIn("verify Sent Items", row["error"])
        self.assertEqual(agent.recover_stale_sending(self.db), 0)

    def test_unavailable_gmail_transport_keeps_followup_pending(self):
        self.db.execute(
            """INSERT INTO creator_followups(
                 contact_id,source_kind,source_id,template_key,sender_email,source_sent_at,due_at,
                 subject,body,status,queued_at,updated_at
               ) VALUES(1,'initial','gmail-1','initial_followup_24h',?,datetime('now','-25 hours'),
                 datetime('now','-1 hour'),'Follow up','Hello','pending',datetime('now'),datetime('now'))""",
            (agent.GOOGLE_MAILBOX,),
        )
        self.db.commit()
        args = argparse.Namespace(
            limit=20,
            daily_limit=250,
            gmail_daily_limit=25,
            send=True,
            min_interval_seconds=0,
            max_interval_seconds=0,
        )
        with mock.patch.object(
            agent,
            "gmail_smtp_transport_available",
            return_value=(False, "Gmail SMTP transport unavailable: TimeoutError: timed out"),
        ), mock.patch.object(agent, "google_app_password") as password:
            self.assertEqual(agent.send_due(self.db, args), (0, 0))
        password.assert_not_called()
        row = self.db.execute("SELECT status FROM creator_followups WHERE source_id='gmail-1'").fetchone()
        self.assertEqual(row["status"], "pending")
        setting = self.db.execute(
            "SELECT value FROM outreach_settings WHERE key='gmail_smtp_transport_status'"
        ).fetchone()
        self.assertIn("transport unavailable", setting["value"])


if __name__ == "__main__":
    unittest.main()
