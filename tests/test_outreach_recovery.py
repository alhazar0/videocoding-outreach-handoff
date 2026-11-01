import io
import json
import os
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from scripts import outreach_scheduler as s, initial_template_router as router
from scripts.creator_activity import ensure_activity_schema
from scripts.discovery_learning import FOCUS_SQL
from scripts.continuous_lead_discovery import seed_queries
from scripts.outreach_health_check import progress_check


class TransportRecoveryTests(unittest.TestCase):
    def test_dead_tunnel_falls_back_before_login_with_verified_gmail_tls(self):
        with patch.dict(os.environ, {'VIDEOCODING_GMAIL_TUNNEL_PORT':'1465'}), \
             patch.object(s, 'GmailTunnelSMTP', side_effect=ConnectionRefusedError), \
             patch.object(s.smtplib, 'SMTP_SSL') as direct:
            smtp=s.gmail_smtp_connection()
            self.assertIs(smtp,direct.return_value)
            args,kwargs=direct.call_args
            self.assertEqual(args,('smtp.gmail.com',465))
            self.assertTrue(kwargs['context'].check_hostname)
            direct.return_value.login.assert_not_called()
            direct.return_value.send_message.assert_not_called()

    def test_working_tunnel_never_opens_another_connection(self):
        with patch.dict(os.environ, {'VIDEOCODING_GMAIL_TUNNEL_PORT':'1465'}), \
             patch.object(s,'GmailTunnelSMTP') as tunnel,patch.object(s.smtplib,'SMTP_SSL') as direct:
            self.assertIs(s.gmail_smtp_connection(),tunnel.return_value)
            direct.assert_not_called()

    def test_submission_failure_never_reopens_or_retries(self):
        smtp=MagicMock();smtp.send_message.side_effect=TimeoutError('delivery unknown')
        cm=MagicMock();cm.__enter__.return_value=smtp
        with patch.object(s,'gmail_smtp_connection',return_value=cm) as connection, \
             patch.object(s,'build_gmail_message',return_value='message'):
            ok,_=s.send_gmail_smtp('password',{},'v','subject','body',experiment_id='e',variant='v')
            self.assertFalse(ok)
            connection.assert_called_once()
            smtp.send_message.assert_called_once()


class CanonicalIdentityTests(unittest.TestCase):
    def verify(self,channel='UCabcdefghijklmnopqrstuv',video='abcdefghijk',public=True,legacy=False):
        review={'video_url':'https://www.youtube.com/watch?v=abcdefghijk','channel':'Old Name',
                'evidence_channel_url':'https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv',
                'evidence_channel_id':'UCabcdefghijklmnopqrstuv'}
        if legacy:
            review.pop('evidence_channel_id')
        oembed={'title':'Video','author_name':'New Name','author_url':'https://www.youtube.com/@newhandle'}
        player={'videoDetails':{'channelId':channel,'videoId':video,'author':'New Name','title':'Video','isPrivate':not public},
                'playabilityStatus':{'status':'OK' if public else 'LOGIN_REQUIRED'}}
        page='var ytInitialPlayerResponse = '+json.dumps(player)+';'
        with patch.object(router.urllib.request,'urlopen',side_effect=[io.BytesIO(json.dumps(oembed).encode()),io.BytesIO(page.encode())]):
            return router.verify_review(review)

    def test_renamed_channel_and_handle_verified_by_exact_ids(self):
        self.assertTrue(self.verify())

    def test_same_name_cannot_mask_other_channel_video_or_private_content(self):
        self.assertFalse(self.verify(channel='UCdifferent'))
        self.assertFalse(self.verify(video='different11'))
        self.assertFalse(self.verify(public=False))

    def test_legacy_channel_url_keeps_case_sensitive_identity(self):
        self.assertTrue(self.verify(legacy=True))
        self.assertFalse(self.verify(channel='UCAbcdefghijklmnopqrstuv',legacy=True))


class QueueActivityTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('''
          CREATE TABLE outreach_settings(key TEXT PRIMARY KEY,value TEXT);
          INSERT INTO outreach_settings VALUES('creator_recent_activity_days','30');
          CREATE TABLE contacts(id INTEGER,channel_url TEXT,planned_send_date TEXT,planned_sender_email TEXT,
            next_eligible_send_at TEXT,validation_status TEXT,subscribers INTEGER,outreach_status TEXT,
            first_sent_at TEXT,do_not_contact INTEGER,initial_template_key TEXT);
          CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT,campaign_day_et TEXT,sender_email TEXT,status TEXT);
        ''')
        ensure_activity_schema(self.db)
        self.db.execute("INSERT INTO contacts VALUES(1,'channel','2026-10-18',?,datetime('now','+4 hours'),'send_ready',2000,'not_contacted',NULL,0,'approved')",(s.MAILBOX,))
        self.db.commit()

    def test_four_hour_activity_retry_is_not_a_valid_send_window(self):
        self.db.execute("INSERT INTO creator_activity_cache VALUES('channel','cid','inactive',datetime('now','-40 days'),'video',datetime('now'),'feed')")
        self.assertEqual(s.empty_queue_status(self.db,s.MAILBOX),'waiting_for_recent_activity')
        self.db.execute("UPDATE creator_activity_cache SET status='recent',published_at=datetime('now','-1 day')")
        self.assertEqual(s.empty_queue_status(self.db,s.MAILBOX),'waiting_for_send_window')
        self.db.execute("UPDATE creator_activity_cache SET checked_at=datetime('now','-3 days')")
        self.assertEqual(s.empty_queue_status(self.db,s.MAILBOX),'waiting_for_recent_activity')

    def test_health_does_not_count_inactive_or_unknown_inventory_as_sendable(self):
        result=progress_check(self.db,'2026-10-18',datetime(2026,10,18,12,tzinfo=s.CAMPAIGN_ZONE))
        channel=result['channels'][0]
        self.assertEqual((channel['scheduled'],channel['pending'],channel['activity_held']),(1,0,1))
        self.db.execute("INSERT INTO creator_activity_cache VALUES('channel','cid','recent',datetime('now','-1 day'),'video',datetime('now'),'feed')")
        result=progress_check(self.db,'2026-10-18',datetime(2026,10,18,12,tzinfo=s.CAMPAIGN_ZONE))
        self.assertEqual(result['channels'][0]['pending'],1)

    def test_all_seeded_legacy_families_have_one_reserved_worker_lane(self):
        keys={q.tier_key for q in seed_queries(expanded=False) if q.tier==3 and q.tier_key.startswith('talking_head_')}
        self.assertGreater(len(keys),30)
        for key in keys:
            matches=[focus for focus,sql in FOCUS_SQL.items() if self.db.execute('SELECT '+sql+' FROM (SELECT 3 tier,? tier_key) j',(key,)).fetchone()[0]]
            self.assertEqual(len(matches),1,key)
            self.assertNotIn('ai',matches)


if __name__=='__main__':unittest.main()
