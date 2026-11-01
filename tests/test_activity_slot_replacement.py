import sqlite3
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from scripts import outreach_scheduler as s


class ActivitySlotTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.now=datetime.now(s.CAMPAIGN_ZONE).replace(hour=12)
        self.day=self.now.date().isoformat();self.future=(self.now.date()+timedelta(days=1)).isoformat()
        self.db.executescript('''
          CREATE TABLE contacts(id INTEGER PRIMARY KEY,channel_url TEXT,initial_template_key TEXT,
            validation_status TEXT,outreach_status TEXT,first_sent_at TEXT,do_not_contact INTEGER,
            subscribers INTEGER,planned_sender_email TEXT,planned_send_date TEXT,next_eligible_send_at TEXT,
            send_timezone TEXT,timezone_source TEXT,timezone_confidence TEXT,send_window TEXT,updated_at TEXT);
          CREATE TABLE outreach_templates(template_key TEXT,enabled INTEGER,approval_status TEXT);
          CREATE TABLE outreach_settings(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE outreach_sender_accounts(sender_email TEXT,status TEXT);
          CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT,campaign_day_et TEXT,sender_email TEXT,status TEXT);
          CREATE TABLE creator_activity_cache(channel_url TEXT PRIMARY KEY,status TEXT,checked_at TEXT,published_at TEXT);
          INSERT INTO outreach_templates VALUES('approved',1,'approved');
          INSERT INTO outreach_settings VALUES('creator_recent_activity_days','30');
        ''')
        self.db.executemany('INSERT INTO outreach_sender_accounts VALUES(?,?)',[(s.MAILBOX,'restricted'),(s.GOOGLE_MAILBOX,'active')])
        for cid,sender,day in [(1,s.GOOGLE_MAILBOX,self.day),(2,s.GOOGLE_MAILBOX,self.future),(3,s.MAILBOX,self.future)]:
            self.db.execute("INSERT INTO contacts VALUES(?,?,'approved','send_ready','not_contacted',NULL,0,2000,?,?,NULL,NULL,NULL,NULL,NULL,NULL)",
                            (cid,str(cid),sender,day))
        for cid in (2,3):
            self.db.execute("INSERT INTO creator_activity_cache VALUES(?,'recent',datetime('now'),datetime('now','-1 day'))",(str(cid),))
        self.db.commit()

    def swap(self):
        with patch.object(s,'utc_now',return_value=self.now):
            return s.replace_activity_held_slots(self.db,s.GOOGLE_MAILBOX)

    def test_same_sender_swap_preserves_restriction_and_daily_reservations(self):
        self.assertEqual(self.swap(),1)
        rows={r['id']:dict(r) for r in self.db.execute('SELECT * FROM contacts')}
        self.assertEqual(rows[1]['planned_send_date'],self.future)
        self.assertEqual(rows[2]['planned_send_date'],self.day)
        self.assertEqual(rows[3]['planned_send_date'],self.future)
        self.assertEqual(rows[3]['planned_sender_email'],s.MAILBOX)
        self.assertEqual(self.swap(),0)

    def test_ambiguous_attempts_count_toward_existing_cap(self):
        self.db.executemany("INSERT INTO outreach_send_log VALUES(?,'creator_initial',?,?,'delivery_unknown')",
                            [(i+100,self.day,s.GOOGLE_MAILBOX) for i in range(50)])
        self.db.commit();self.assertEqual(self.swap(),0)

    def test_inactive_unassigned_reservation_is_deferred_without_assigning_copy(self):
        self.db.execute('UPDATE contacts SET initial_template_key=NULL WHERE id=1')
        self.db.commit()
        self.assertEqual(self.swap(),1)
        held=self.db.execute('SELECT * FROM contacts WHERE id=1').fetchone()
        self.assertEqual(held['planned_send_date'],self.future)
        self.assertIsNone(held['initial_template_key'])
        self.assertEqual(held['planned_sender_email'],s.GOOGLE_MAILBOX)

    def test_future_unassigned_or_disabled_copy_is_never_promoted(self):
        for key in (None,'disabled'):
            self.db.execute('UPDATE contacts SET initial_template_key=? WHERE id=2',(key,))
            self.db.commit()
            self.assertEqual(self.swap(),0)

    def test_replacement_does_not_run_before_send_window(self):
        with patch.object(s,'utc_now',return_value=self.now.replace(hour=7)):
            self.assertEqual(s.replace_activity_held_slots(self.db,s.GOOGLE_MAILBOX),0)

    def test_suppressed_or_stale_or_already_attempted_replacement_is_excluded(self):
        for query in ["UPDATE contacts SET do_not_contact=1 WHERE id=2",
                      "UPDATE creator_activity_cache SET checked_at=datetime('now','-3 days') WHERE channel_url='2'",
                      "INSERT INTO outreach_send_log VALUES(2,'creator_initial','2026-01-01','x','failed')"]:
            with self.subTest(query=query):
                self.db.execute(query)
                # The helper owns its transaction; restore the three fixture fields after each case.
                self.db.commit()
                self.assertEqual(self.swap(),0)
                self.db.execute('UPDATE contacts SET do_not_contact=0 WHERE id=2')
                self.db.execute("UPDATE creator_activity_cache SET checked_at=datetime('now') WHERE channel_url='2'")
                self.db.execute('DELETE FROM outreach_send_log');self.db.commit()


if __name__=='__main__':unittest.main()
