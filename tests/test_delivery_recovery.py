import io
import json
import os
import sqlite3
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from scripts import initial_template_router as router
from scripts import outreach_scheduler as scheduler


class QueueRecoveryTests(unittest.TestCase):
    def database(self, count, scheduled='2026-10-07T12:00:00Z'):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.executescript('''
          CREATE TABLE contacts(id INTEGER PRIMARY KEY, validation_status TEXT,
            subscribers INTEGER,outreach_status TEXT,first_sent_at TEXT,
            do_not_contact INTEGER,planned_sender_email TEXT,planned_send_date TEXT,
            next_eligible_send_at TEXT,send_timezone TEXT,timezone_source TEXT,
            timezone_confidence TEXT,send_window TEXT,updated_at TEXT);
          CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT,
            campaign_day_et TEXT,sender_email TEXT,status TEXT);
        ''')
        db.executemany('INSERT INTO contacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            (i, 'send_ready', 1000, 'not_contacted', None, 0,
             scheduler.GOOGLE_MAILBOX, scheduled[:10], scheduled, None, None, None, None, None)
            for i in range(1, count + 1)
        ])
        db.commit()
        return db

    def allocations(self, db):
        return {(r[0], r[1]): r[2] for r in db.execute('''SELECT planned_send_date,
          planned_sender_email,count(*) FROM contacts GROUP BY 1,2''')}

    def test_accumulated_gmail_backlog_refills_both_channels_within_caps(self):
        db = self.database(750)
        with patch.object(scheduler, 'utc_now', return_value=datetime(2026, 10, 7, 17, tzinfo=timezone.utc)):
            self.assertEqual(scheduler.reschedule_missed_windows(db), 750)
            self.assertEqual(scheduler.reschedule_missed_windows(db), 0)
        self.assertEqual(self.allocations(db), {
            ('2026-10-07', scheduler.MAILBOX): 700,
            ('2026-10-07', scheduler.GOOGLE_MAILBOX): 50,
        })

    def test_existing_accepted_and_ambiguous_sends_reserve_capacity(self):
        db = self.database(710)
        for i in range(40):
            db.execute('INSERT INTO outreach_send_log VALUES(?,?,?,?,?)',
                       (-i, 'creator_initial', '2026-10-07', scheduler.MAILBOX,
                        'delivery_unknown' if i == 0 else 'sent'))
        db.commit()
        with patch.object(scheduler, 'utc_now', return_value=datetime(2026, 10, 7, 17, tzinfo=timezone.utc)):
            scheduler.reschedule_missed_windows(db)
        self.assertEqual(self.allocations(db)[('2026-10-07', scheduler.MAILBOX)], 660)
        self.assertEqual(self.allocations(db)[('2026-10-07', scheduler.GOOGLE_MAILBOX)], 50)

    def test_expired_reservations_do_not_reenter_closed_window(self):
        db = self.database(800, '2026-10-06T12:00:00Z')
        now = datetime(2026, 10, 8, 2, tzinfo=timezone.utc)  # 22:00 ET on Oct 7.
        with patch.object(scheduler, 'utc_now', return_value=now):
            scheduler.reschedule_missed_windows(db)
        allocations = self.allocations(db)
        self.assertEqual(sum(n for (d, _), n in allocations.items() if d == '2026-10-08'), 750)
        self.assertEqual(sum(n for (d, _), n in allocations.items() if d == '2026-10-09'), 50)
        self.assertTrue(all(d >= '2026-10-08' for d, _ in allocations))

    def test_attempted_and_suppressed_contacts_are_never_reallocated(self):
        db = self.database(60)
        db.execute('UPDATE contacts SET do_not_contact=1 WHERE id=1')
        db.execute('INSERT INTO outreach_send_log VALUES(?,?,?,?,?)',
                   (2, 'creator_initial', '2026-10-07', scheduler.GOOGLE_MAILBOX, 'sending'))
        db.commit()
        before = [dict(r) for r in db.execute('SELECT * FROM contacts WHERE id IN (1,2)')]
        with patch.object(scheduler, 'utc_now', return_value=datetime(2026, 10, 7, 17, tzinfo=timezone.utc)):
            self.assertEqual(scheduler.reschedule_missed_windows(db), 58)
        self.assertEqual([dict(r) for r in db.execute('SELECT * FROM contacts WHERE id IN (1,2)')], before)


class PublicMetadataTests(unittest.TestCase):
    channel_id = 'UC' + 'a' * 22

    def review(self):
        return {'video_url': 'https://www.youtube.com/watch?v=abcdefghijk',
                'channel': 'Example Creator', 'evidence_channel_id': self.channel_id,
                'evidence_channel_url': 'https://www.youtube.com/@example'}

    def player(self):
        return {'playabilityStatus': {'status': 'OK', 'playableInEmbed': False},
                'videoDetails': {'videoId': 'abcdefghijk', 'channelId': self.channel_id,
                                 'author': 'Example Creator', 'title': 'A public tutorial'}}

    def verify(self, player):
        page = 'var ytInitialPlayerResponse = ' + json.dumps(player) + ';'
        unavailable = urllib.error.HTTPError('https://www.youtube.com/oembed', 401, 'Unauthorized', {}, None)
        with patch.object(router.urllib.request, 'urlopen', side_effect=[unavailable, io.BytesIO(page.encode())]):
            return router.verify_review(self.review())

    def test_public_non_embeddable_video_can_still_verify_its_creator(self):
        self.assertTrue(self.verify(self.player()))

    def test_other_creator_video_and_private_video_are_rejected(self):
        for field, value in [('videoId', 'other123456'), ('channelId', 'UC' + 'b' * 22),
                             ('isPrivate', True), ('title', '')]:
            player = self.player()
            player['videoDetails'][field] = value
            self.assertFalse(self.verify(player), field)
        player = self.player()
        player['playabilityStatus']['status'] = 'LOGIN_REQUIRED'
        self.assertFalse(self.verify(player))

    def test_channel_rename_does_not_override_exact_canonical_identity(self):
        player = self.player()
        player['videoDetails']['author'] = 'Renamed Creator'
        self.assertTrue(self.verify(player))

    def test_transient_network_failure_is_not_treated_as_verified(self):
        with patch.object(router.urllib.request, 'urlopen', side_effect=TimeoutError('timeout')):
            self.assertFalse(router.verify_review(self.review()))


class GmailTransportTests(unittest.TestCase):
    def test_loopback_tunnel_preserves_gmail_certificate_hostname(self):
        smtp = scheduler.GmailTunnelSMTP.__new__(scheduler.GmailTunnelSMTP)
        smtp.context = MagicMock()
        with patch.dict(os.environ, {'VIDEOCODING_GMAIL_TUNNEL_PORT': '1465'}), \
                patch.object(scheduler.socket, 'create_connection') as connect:
            smtp._get_socket('smtp.gmail.com', 465, 8)
        connect.assert_called_once_with(('127.0.0.1', 1465), 8)
        smtp.context.wrap_socket.assert_called_once_with(connect.return_value, server_hostname='smtp.gmail.com')

    def test_failed_tls_verification_closes_socket(self):
        smtp = scheduler.GmailTunnelSMTP.__new__(scheduler.GmailTunnelSMTP)
        smtp.context = MagicMock()
        smtp.context.wrap_socket.side_effect = scheduler.ssl.SSLError('invalid certificate')
        with patch.dict(os.environ, {'VIDEOCODING_GMAIL_TUNNEL_PORT': '1465'}), \
                patch.object(scheduler.socket, 'create_connection') as connect:
            with self.assertRaises(scheduler.ssl.SSLError):
                smtp._get_socket('smtp.gmail.com', 465, 8)
        connect.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
