import sqlite3
import unittest

from scripts.outreach_scheduler import (
    CAMPAIGN_ZONE,
    GOOGLE_MAILBOX,
    GOOGLE_PROVIDER,
    SEND_WINDOW_HOURS,
    build_gmail_message,
    approved_initial_template,
    candidate_contacts,
    contact_template_metadata,
    delivery_committed_today,
    parse_iso,
    partition_policy_allowed_contacts,
    release_unassigned_segment_queue,
    recover_stale_initial_sends,
    scheduled_for_campaign_day,
    sender_config,
    sent_today,
    stable_cohort,
    timezone_metadata,
)


class TimezoneSchedulerTests(unittest.TestCase):
    def test_public_country_uses_real_timezone(self):
        contact = {"email": "creator@example.com", "country": "India"}
        timezone_name, source, confidence, window = timezone_metadata(contact)
        self.assertEqual(timezone_name, "Asia/Kolkata")
        self.assertEqual(source, "public_country")
        self.assertEqual(confidence, "high")
        self.assertEqual(window, "local_10am")

    def test_unknown_country_is_labeled_as_test_cohort(self):
        contact = {"email": "creator@example.com", "country": "Unknown"}
        timezone_name, source, confidence, window = timezone_metadata(contact)
        self.assertIsNone(timezone_name)
        self.assertEqual(source, "deterministic_global_cohort")
        self.assertEqual(confidence, "unknown")
        self.assertTrue(window.endswith("_test"))
        self.assertEqual(stable_cohort(contact["email"]), stable_cohort(contact["email"]))

    def test_all_contacts_start_at_8am_eastern(self):
        campaign_day = "2026-08-14"
        contacts = [
            {"email": "us@example.com", "country": "United States"},
            {"email": "au@example.com", "country": "Australia"},
            {"email": "unknown-a@example.com", "country": "Unknown"},
            {"email": "unknown-b@example.com", "country": "Unknown"},
            {"email": "unknown-c@example.com", "country": "Unknown"},
        ]
        for contact in contacts:
            scheduled_at, timezone_name, source, confidence, window = scheduled_for_campaign_day(contact, campaign_day)
            self.assertEqual(parse_iso(scheduled_at).astimezone(CAMPAIGN_ZONE).date().isoformat(), campaign_day)
            self.assertEqual(parse_iso(scheduled_at).astimezone(CAMPAIGN_ZONE).hour, 8)
            self.assertEqual(timezone_name, "America/New_York")
            self.assertEqual(source, "campaign_policy")
            self.assertEqual(confidence, "high")
            self.assertEqual(window, "america_new_york_8am")

    def test_send_window_is_long_enough_for_current_daily_pacing(self):
        self.assertEqual(SEND_WINDOW_HOURS, 13)


class GoogleWorkspaceSenderTests(unittest.TestCase):
    def test_interrupted_initial_send_is_never_blindly_retried(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute(
            """CREATE TABLE outreach_send_log(
              id INTEGER PRIMARY KEY,contact_id INTEGER,campaign_kind TEXT,campaign_day_et TEXT,
              status TEXT,queued_at TEXT,sent_at TEXT,error TEXT
            )"""
        )
        db.execute(
            """INSERT INTO outreach_send_log VALUES(
              1,9,'creator_initial','2026-08-31','sending',datetime('now','-30 minutes'),NULL,NULL
            )"""
        )
        self.assertEqual(recover_stale_initial_sends(db), 1)
        row = db.execute("SELECT status,error FROM outreach_send_log WHERE id=1").fetchone()
        self.assertEqual(row["status"], "delivery_unknown")
        self.assertIn("verify provider Sent Items", row["error"])
        self.assertEqual(recover_stale_initial_sends(db), 0)
        db.close()

    def test_initial_sender_obeys_fail_closed_automation_rule(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE automation_rules(
          rule_key TEXT PRIMARY KEY,mode TEXT,enabled INTEGER
        )""")
        db.execute("INSERT INTO automation_rules VALUES('first_contact','draft',1)")
        db.execute("CREATE TABLE contacts(id INTEGER PRIMARY KEY,initial_template_key TEXT)")
        db.execute("""CREATE TABLE outreach_templates(
          template_key TEXT PRIMARY KEY,body TEXT,enabled INTEGER,approval_status TEXT
        )""")
        db.executemany(
            "INSERT INTO outreach_templates VALUES(?,?,1,'approved')",
            [("first_contact", "approved copy"), ("first_contact_t2v_knowledge_v1", "approved t2v copy")],
        )
        db.executemany(
            "INSERT INTO contacts VALUES(?,?)",
            [(1, None), (2, "first_contact_t2v_knowledge_v1")],
        )
        contacts = db.execute("SELECT * FROM contacts ORDER BY id").fetchall()
        allowed, held = partition_policy_allowed_contacts(
            db,
            contacts,
            sender_config("microsoft_graph"),
        )
        self.assertEqual(allowed, [])
        self.assertEqual([item["mode"] for item in held], ["draft", "draft"])

        db.execute("UPDATE automation_rules SET mode='auto' WHERE rule_key='first_contact'")
        allowed, held = partition_policy_allowed_contacts(
            db,
            contacts,
            sender_config("microsoft_graph"),
        )
        self.assertEqual([row["id"] for row in allowed], [1, 2])
        self.assertEqual(held, [])

    def test_assigned_segment_template_fails_closed_when_missing(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE outreach_templates(
          template_key TEXT PRIMARY KEY,subject TEXT,body TEXT,version INTEGER,enabled INTEGER,
          approval_status TEXT
        )""")
        with self.assertRaisesRegex(RuntimeError, "missing.*disabled"):
            approved_initial_template(db, "first_contact_t2v_knowledge_v1")

    def test_initial_template_must_be_explicitly_approved(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE outreach_templates(
          template_key TEXT PRIMARY KEY,subject TEXT,body TEXT,version INTEGER,enabled INTEGER,
          approval_status TEXT
        )""")
        db.execute(
            "INSERT INTO outreach_templates VALUES('first_contact','Subject','Body',3,1,'pending_review')"
        )
        with self.assertRaisesRegex(RuntimeError, "not approved"):
            approved_initial_template(db, "first_contact")
        db.execute("UPDATE outreach_templates SET approval_status='approved'")
        subject, body, version = approved_initial_template(db, "first_contact")
        self.assertEqual((subject, body, version), ("Subject", "Body", "first_contact-v3"))

    def test_current_rollout_includes_non_ai_send_ready_contacts(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE contacts(
          id INTEGER PRIMARY KEY,curated_ai INTEGER,ai_match INTEGER,category TEXT,
          validation_status TEXT,outreach_status TEXT,
          first_sent_at TEXT,do_not_contact INTEGER,planned_sender_email TEXT,
          planned_send_date TEXT,initial_template_key TEXT,
          next_eligible_send_at TEXT,fit_score INTEGER,subscribers INTEGER
        )""")
        db.execute("""CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT)""")
        db.execute(
            "INSERT INTO contacts VALUES(1,0,0,'Education','send_ready','not_contacted',NULL,0,?,'2026-08-26',NULL,NULL,90,10000)",
            ("team@videocoding.ai",),
        )
        rows = candidate_contacts(
            db,
            explicit_ids=[1],
            limit=1,
            sender_email="team@videocoding.ai",
        )
        self.assertEqual([row["id"] for row in rows], [1])

    def test_future_rollout_allows_ai_default_but_segments_non_ai(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE contacts(
          id INTEGER PRIMARY KEY,curated_ai INTEGER,ai_match INTEGER,category TEXT,
          validation_status TEXT,outreach_status TEXT,
          first_sent_at TEXT,do_not_contact INTEGER,planned_sender_email TEXT,
          planned_send_date TEXT,initial_template_key TEXT,next_eligible_send_at TEXT,
          fit_score INTEGER,subscribers INTEGER
        )""")
        db.execute("CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT)")
        db.executemany(
            "INSERT INTO contacts VALUES(?,?,?,?, 'send_ready','not_contacted',NULL,0,?,'2026-08-27',?,NULL,90,10000)",
            [
                (1, 0, 0, "Education", "team@videocoding.ai", None),
                (2, 0, 0, "Education", "team@videocoding.ai", "first_contact_t2v_knowledge_v1"),
                (3, 1, 1, "AI & Tech", "team@videocoding.ai", None),
            ],
        )
        rows = candidate_contacts(
            db,
            explicit_ids=[1, 2, 3],
            limit=3,
            sender_email="team@videocoding.ai",
        )
        self.assertEqual([row["id"] for row in rows], [2, 3])

    def test_release_unassigned_queue_frees_only_new_non_ai_slots(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE contacts(
          id INTEGER PRIMARY KEY,curated_ai INTEGER,ai_match INTEGER,category TEXT,
          validation_status TEXT,outreach_status TEXT,first_sent_at TEXT,do_not_contact INTEGER,
          planned_sender_email TEXT,planned_send_date TEXT,initial_template_key TEXT,
          next_eligible_send_at TEXT,send_timezone TEXT,timezone_source TEXT,
          timezone_confidence TEXT,send_window TEXT,updated_at TEXT
        )""")
        db.execute("CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT)")
        rows = [
            (1, 1, 1, "AI & Tech", "2026-08-28", None),
            (2, 0, 0, "Education", "2026-08-28", "first_contact_t2v_knowledge_v1"),
            (3, 0, 0, "Education", "2026-08-28", None),
            (4, 0, 0, "Education", "2026-08-26", None),
        ]
        for contact_id, curated_ai, ai_match, category, planned_day, template in rows:
            db.execute(
                """INSERT INTO contacts VALUES(?,?,?,?,'send_ready','not_contacted',NULL,0,
                   'team@videocoding.ai',?,?,?,'America/New_York','campaign_policy','high',
                   'america_new_york_8am',datetime('now'))""",
                (contact_id, curated_ai, ai_match, category, planned_day, template, "2026-08-28T12:00:00Z"),
            )
        self.assertEqual(release_unassigned_segment_queue(db), 1)
        planned = {row["id"]: row["planned_send_date"] for row in db.execute("SELECT id,planned_send_date FROM contacts")}
        self.assertEqual(planned, {1: "2026-08-28", 2: "2026-08-28", 3: None, 4: "2026-08-26"})

    def test_sender_hard_blocks_unknown_and_under_1000_audiences(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE contacts(
          id INTEGER PRIMARY KEY,curated_ai INTEGER,ai_match INTEGER,category TEXT,
          validation_status TEXT,outreach_status TEXT,first_sent_at TEXT,do_not_contact INTEGER,
          planned_sender_email TEXT,planned_send_date TEXT,initial_template_key TEXT,
          next_eligible_send_at TEXT,fit_score INTEGER,subscribers INTEGER
        )""")
        db.execute("CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT)")
        db.executemany(
            "INSERT INTO contacts VALUES(?,1,1,'AI & Tech','send_ready','not_contacted',NULL,0,?,'2026-08-26',NULL,NULL,90,?)",
            [
                (1, "team@videocoding.ai", None),
                (2, "team@videocoding.ai", 999),
                (3, "team@videocoding.ai", 1000),
            ],
        )
        rows = candidate_contacts(
            db, explicit_ids=[1, 2, 3], limit=3, sender_email="team@videocoding.ai"
        )
        self.assertEqual([row["id"] for row in rows], [3])

    def test_t2v_assignment_has_distinct_experiment_metadata(self):
        config = sender_config("microsoft_graph")
        template_key, experiment_id, variant = contact_template_metadata(
            {"initial_template_key": "first_contact_t2v_knowledge_v1"},
            config,
        )
        self.assertEqual(template_key, "first_contact_t2v_knowledge_v1")
        self.assertEqual(experiment_id, "segmented-first-contact-2026-08")
        self.assertEqual(variant, "t2v-knowledge-v1")

    def test_microsoft_channel_uses_winning_first_contact_copy(self):
        config = sender_config("microsoft_graph")
        self.assertEqual(config["template_key"], "first_contact")
        self.assertEqual(config["variant"], "first-contact-v1")

    def test_microsoft_channel_has_700_cap_and_45_to_60_second_jitter(self):
        config = sender_config("microsoft_graph")
        self.assertEqual(config["daily_target"], 700)
        self.assertEqual(config["daily_hard_cap"], 700)
        self.assertEqual(config["min_interval_seconds"], 45.0)
        self.assertEqual(config["max_interval_seconds"], 60.0)

    def test_google_channel_has_independent_50_cap(self):
        config = sender_config(GOOGLE_PROVIDER)
        self.assertEqual(config["sender_email"], GOOGLE_MAILBOX)
        self.assertEqual(config["daily_hard_cap"], 50)
        self.assertEqual(config["min_interval_seconds"], 120.0)
        self.assertEqual(config["max_interval_seconds"], 120.0)
        self.assertEqual(config["template_key"], "first_contact")
        self.assertEqual(config["variant"], "first-contact-v1")

    def test_gmail_message_keeps_human_identity_and_reply_mailbox(self):
        message = build_gmail_message(
            {"id": 42, "email": "creator@example.com"},
            "first_contact-v1",
            "A paid collaboration with VideoCoding",
            "Hi Creator,",
            experiment_id="first-contact-winner-rollout-2026-08",
            variant="first-contact-v1",
        )
        self.assertIn(GOOGLE_MAILBOX, message["From"])
        self.assertEqual(message["Reply-To"], "team@videocoding.ai")
        self.assertEqual(message["X-VideoCoding-Variant"], "first-contact-v1")
        self.assertIn("Hi Creator,", message.get_content())

    def test_daily_count_isolated_by_sender(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE outreach_send_log(
          campaign_day_et TEXT,status TEXT,sender_email TEXT
        )""")
        db.executemany(
            "INSERT INTO outreach_send_log VALUES('2026-08-14','sent',?)",
            [("team@videocoding.ai",), ("team@videocoding.ai",), (GOOGLE_MAILBOX,)],
        )
        self.assertEqual(sent_today(db, "2026-08-14", GOOGLE_MAILBOX), 1)
        self.assertEqual(sent_today(db, "2026-08-14", "team@videocoding.ai"), 2)

    def test_unknown_delivery_counts_toward_hard_cap_but_not_confirmed_sent(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE outreach_send_log(
          campaign_day_et TEXT,status TEXT,sender_email TEXT
        )""")
        db.executemany(
            "INSERT INTO outreach_send_log VALUES('2026-08-14',?,?)",
            [
                ("sent", "team@videocoding.ai"),
                ("delivery_unknown", "team@videocoding.ai"),
                ("sending", "team@videocoding.ai"),
                ("failed", "team@videocoding.ai"),
            ],
        )
        self.assertEqual(sent_today(db, "2026-08-14", "team@videocoding.ai"), 1)
        self.assertEqual(delivery_committed_today(db, "2026-08-14", "team@videocoding.ai"), 3)
        db.close()


if __name__ == "__main__":
    unittest.main()
