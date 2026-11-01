import contextlib
import io
import sqlite3
import unittest
from unittest.mock import MagicMock, patch
from scripts import replenish_initial_queue as r

class ReplenisherTests(unittest.TestCase):
    def test_only_currently_approved_templates_count(self):
        d=sqlite3.connect(':memory:')
        self.addCleanup(d.close)
        d.executescript('''CREATE TABLE contacts(id INTEGER,validation_status TEXT,subscribers INTEGER,do_not_contact INTEGER,outreach_status TEXT,first_sent_at TEXT,planned_send_date TEXT,initial_template_key TEXT);
        CREATE TABLE outreach_settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE outreach_templates(template_key TEXT,enabled INTEGER,approval_status TEXT);
        CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT);
        INSERT INTO contacts VALUES(1,'send_ready',1000,0,'not_contacted',NULL,'2026-09-14','example');
        INSERT INTO outreach_templates VALUES('example',1,'approved');''')
        self.assertEqual(r.prepared_count(d),1)
        d.execute("UPDATE outreach_templates SET approval_status='draft'")
        self.assertEqual(r.prepared_count(d),0)
        d.execute("UPDATE outreach_templates SET approval_status='approved',enabled=0")
        self.assertEqual(r.prepared_count(d),0)
        d.execute('UPDATE outreach_templates SET enabled=1')
        d.execute("INSERT INTO outreach_send_log VALUES(1,'creator_initial')")
        self.assertEqual(r.prepared_count(d),0)

    def test_ready_inventory_requires_fresh_recent_activity(self):
        d=sqlite3.connect(':memory:');self.addCleanup(d.close)
        d.executescript('''CREATE TABLE contacts(id INTEGER,channel_url TEXT,validation_status TEXT,subscribers INTEGER,
          do_not_contact INTEGER,outreach_status TEXT,first_sent_at TEXT,planned_send_date TEXT,initial_template_key TEXT);
          CREATE TABLE outreach_settings(key TEXT PRIMARY KEY,value TEXT);
          INSERT INTO outreach_settings VALUES('creator_recent_activity_days','30');
          CREATE TABLE outreach_templates(template_key TEXT,enabled INTEGER,approval_status TEXT);
          CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT);
          INSERT INTO contacts VALUES(1,'channel','send_ready',1000,0,'not_contacted',NULL,'2026-10-08','approved');
          INSERT INTO outreach_templates VALUES('approved',1,'approved');''')
        self.assertEqual(r.prepared_count(d),0)
        d.execute("INSERT INTO creator_activity_cache VALUES('channel','cid','recent',datetime('now','-1 day'),'video',datetime('now'),NULL)")
        d.commit();self.assertEqual(r.prepared_count(d),1)
        for update in ("status='inactive'", "status='recent',published_at=datetime('now','-31 days')",
                       "published_at=datetime('now','+1 day')", "published_at=datetime('now','-1 day'),checked_at=datetime('now','-3 days')",
                       "checked_at=datetime('now','+1 day')"):
            d.execute('UPDATE creator_activity_cache SET '+update);d.commit()
            self.assertEqual(r.prepared_count(d),0)

    def test_failed_first_batch_does_not_starve_later_candidates(self):
        d=MagicMock()
        with patch.object(r.scheduler,'connect',return_value=d), patch.object(r,'prepared_count',return_value=1000), patch.object(r.scheduler,'append_unplanned_contacts',return_value=0), patch.object(r.scheduler,'set_setting'), patch.object(r,'route_unassigned',side_effect=[
            {'assigned':0,'verification':{'attempted':100}},
            {'assigned':3,'verification':{'attempted':100}},
            {'assigned':0,'verification':{'attempted':0}},
        ]) as route, contextlib.redirect_stdout(io.StringIO()):
            r.run(batches=6)
            self.assertEqual(route.call_count,3)
        d.close.assert_called_once()

    def test_full_buffer_does_not_verify_or_reschedule(self):
        with patch.object(r.scheduler,'connect'), patch.object(r,'prepared_count',return_value=10000), patch.object(r,'route_unassigned') as route, patch.object(r.scheduler,'append_unplanned_contacts') as plan, contextlib.redirect_stdout(io.StringIO()):
            r.run()
            route.assert_not_called();plan.assert_not_called()

    def test_ten_thousand_target_is_not_clamped_to_old_limit(self):
        self.assertEqual(r.DEFAULT_TARGET,10000)
        self.assertEqual(r.bounded_target(10000),10000)
        self.assertEqual(r.bounded_target(20000),10000)
        self.assertEqual(r.bounded_target(0),1)

    def test_three_thousand_is_no_longer_a_full_buffer(self):
        with patch.object(r.scheduler,'connect'), patch.object(r,'prepared_count',return_value=3000), patch.object(r.scheduler,'append_unplanned_contacts',return_value=0), patch.object(r.scheduler,'set_setting'), patch.object(r,'route_unassigned',return_value={'assigned':0,'verification':{'attempted':0}}) as route, contextlib.redirect_stdout(io.StringIO()):
            r.run(batches=1)
            route.assert_called_once()

if __name__=='__main__':unittest.main()
