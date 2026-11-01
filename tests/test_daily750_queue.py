from collections import Counter
from datetime import datetime
from types import SimpleNamespace
import sqlite3
import unittest
from unittest.mock import patch
from scripts import outreach_scheduler as s
from scripts.outreach_health_check import progress_check, selected_phase
from scripts.outreach_keep_awake import remaining_window_seconds


class Daily750QueueTests(unittest.TestCase):
    def test_idle_sleep_prevention_is_bounded_to_sending_window(self):
        now=datetime(2026,9,8,12,tzinfo=s.CAMPAIGN_ZONE)
        self.assertEqual(remaining_window_seconds(now),9*3600)
        self.assertEqual(remaining_window_seconds(now.replace(hour=7)),0)
        self.assertEqual(remaining_window_seconds(now.replace(hour=21)),0)

    def test_two_channels_receive_work_before_main_is_full(self):
        planned={}; assigned=[]; day='2026-09-08'
        for _ in range(15):
            sender=s.choose_sender(day,{},planned);assigned.append(sender)
            planned[(day,sender)]=planned.get((day,sender),0)+1
        self.assertEqual(Counter(assigned),{s.MAILBOX:14,s.GOOGLE_MAILBOX:1})

    def test_daily_750_exact_capacity_and_committed_reservations(self):
        day='2026-09-08';committed={(day,s.MAILBOX):115};planned={}
        for _ in range(635):
            sender=s.choose_sender(day,committed,planned)
            self.assertIsNotNone(sender)
            planned[(day,sender)]=planned.get((day,sender),0)+1
        self.assertEqual(planned[(day,s.MAILBOX)],585)
        self.assertEqual(planned[(day,s.GOOGLE_MAILBOX)],50)
        self.assertIsNone(s.choose_sender(day,committed,planned))

    def test_continue_successful_scheduled_batches_only_within_same_day(self):
        args=SimpleNamespace(scheduled=True,dry_run=False,replan_only=False,contact_ids='',provider='microsoft_graph')
        result={'status':'complete','sent':50,'failed':0,'delivery_unknown':0,'total_sent_today':200}
        now=datetime(2026,9,8,12,tzinfo=s.CAMPAIGN_ZONE)
        self.assertTrue(s.should_continue_scheduled(args,result,'2026-09-08',now))
        for field,value in [('failed',1),('delivery_unknown',1),('status','partial'),('total_sent_today',700),('sent',0)]:
            self.assertFalse(s.should_continue_scheduled(args,{**result,field:value},'2026-09-08',now))
        self.assertFalse(s.should_continue_scheduled(args,result,'2026-09-07',now))
        self.assertFalse(s.should_continue_scheduled(args,result,'2026-09-08',now.replace(hour=21)))

    def test_planner_does_not_overwrite_another_planners_reservation(self):
        d=sqlite3.connect(':memory:');d.row_factory=sqlite3.Row;self.addCleanup(d.close)
        d.executescript('''
          CREATE TABLE contacts(id INTEGER,validation_status TEXT,subscribers INTEGER,outreach_status TEXT,first_sent_at TEXT,do_not_contact INTEGER,planned_sender_email TEXT,planned_send_date TEXT,next_eligible_send_at TEXT,send_timezone TEXT,timezone_source TEXT,timezone_confidence TEXT,send_window TEXT,updated_at TEXT);
          CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT,campaign_day_et TEXT,sender_email TEXT,status TEXT);
          INSERT INTO contacts VALUES(1,'send_ready',1000,'not_contacted',NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL);
        ''')
        rows=d.execute('SELECT * FROM contacts').fetchall()
        self.assertEqual(s.plan_rows(d,rows,reset=False),1)
        first=dict(d.execute('SELECT * FROM contacts').fetchone())
        self.assertEqual(s.plan_rows(d,rows,reset=False),0)
        self.assertEqual(dict(d.execute('SELECT * FROM contacts').fetchone()),first)

    def test_midday_small_send_count_is_not_healthy(self):
        d=sqlite3.connect(':memory:');self.addCleanup(d.close)
        d.executescript('''
          CREATE TABLE outreach_settings(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT,campaign_day_et TEXT,sender_email TEXT,status TEXT);
          CREATE TABLE contacts(id INTEGER,planned_send_date TEXT,planned_sender_email TEXT,validation_status TEXT,subscribers INTEGER,outreach_status TEXT,first_sent_at TEXT,do_not_contact INTEGER);
        ''')
        day='2026-09-08'; now=datetime(2026,9,8,12,tzinfo=s.CAMPAIGN_ZONE)
        d.executemany("INSERT INTO outreach_send_log VALUES(?,'creator_initial',?,?,'sent')",[(i,day,s.MAILBOX) for i in range(84)])
        result=progress_check(d,day,now)
        self.assertEqual(result['status'],'alert')
        self.assertEqual(result['sent'],84)
        self.assertTrue(any('名单不足' in x for x in result['issues']))
        self.assertTrue(any('进度落后' in x for x in result['issues']))
        self.assertEqual(selected_phase(now,None),'progress')
