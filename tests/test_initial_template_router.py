import hashlib
import io
import json
import sqlite3
import unittest
from unittest.mock import patch
from scripts import initial_template_router as r
from scripts.outreach_scheduler import empty_queue_status
REAL_VERIFY = r.verify_review


class InitialRoutingTests(unittest.TestCase):
    def setUp(self):
        self.contact = dict(id=1, channel_name='Example', channel_url='https://www.youtube.com/@example', category='Creative & Media')
        self.evidence = dict(channel_key='key', channel_id='UCexample', channel_name='Example', channel_url='https://www.youtube.com/@example', matched_video_title='Video Editing Tutorial for Beginners', matched_video_url='https://www.youtube.com/watch?v=example')
        self.verifier=patch.object(r,'verify_review',return_value=True).start()
        self.addCleanup(patch.stopall)

    def test_video_editor_routes_existing_body(self):
        self.assertEqual(r.classify(self.contact,[self.evidence])[0],'first_contact')

    def test_search_category_without_video_is_not_evidence(self):
        self.assertEqual(r.classify(self.contact,[])[1],'missing_video_evidence')

    def test_search_topic_is_not_used_as_content(self):
        self.contact['topic']='video editing tutorial'
        self.evidence['matched_video_title']='My Holiday'
        self.assertEqual(r.classify(self.contact,[self.evidence])[0],'first_contact_legacy_light')

    def test_conflicting_identity_holds_all_evidence(self):
        bad=dict(self.evidence,channel_name='Someone Else')
        self.assertEqual(r.classify(self.contact,[self.evidence,bad])[1],'channel_identity_conflict')

    def test_channel_id_handle_alias_requires_exact_id(self):
        self.contact['channel_url']='https://youtube.com/channel/UCexample'
        self.assertEqual(r.classify(self.contact,[self.evidence])[0],'first_contact')
        self.evidence['channel_id']='UCother'
        self.assertIsNone(r.classify(self.contact,[self.evidence])[0])

    def test_non_youtube_and_missing_title_hold(self):
        self.evidence['matched_video_url']='https://example.com/watch'
        self.assertIsNone(r.classify(self.contact,[self.evidence])[0])

    def test_manual_category_preserved(self):
        self.contact['category']='Needs Review'
        self.assertEqual(r.classify(self.contact,[self.evidence])[1],'category_needs_review')

    def test_knowledge_requires_subject_not_just_tutorial(self):
        self.contact['category']='Education & Science'
        self.evidence['matched_video_title']='Easy Makeup Tutorial'
        self.assertNotEqual(r.classify(self.contact,[self.evidence])[0],'first_contact_t2v_knowledge_v1')
        self.evidence['matched_video_title']='Physics Explained for Beginners'
        self.assertEqual(r.classify(self.contact,[self.evidence])[0],'first_contact_t2v_knowledge_v1')

    def test_child_content_held(self):
        self.evidence['matched_video_title']='AI Video Tutorial for Kids'
        self.assertIsNone(r.classify(self.contact,[self.evidence])[0])

    def test_email_syntax_never_guesses_repairs(self):
        for email in ['contact..name-handoff-fixture@gmail.com','.name-handoff-fixture@gmail.com','name.@gmail.com','name@-bad.com','name\n@gmail.com','name@domain',' name-handoff-fixture@gmail.com']:
            self.assertFalse(r.valid_recipient(email),email)
        self.assertTrue(r.valid_recipient('creator+business@example.com'))

    def test_encoded_leading_whitespace_requires_source_review(self):
        for prefix in ['%20', '%09', '%0a', '%0B', '%0c', '%0D', '%20%20']:
            with self.subTest(prefix=prefix):
                self.assertFalse(r.valid_recipient(prefix + 'fixturecreator@gmail.com'))

    def test_literal_percent_and_plus_are_not_rewritten_or_blanket_blocked(self):
        for email in ['100%creator@example.com', 'creator%25@example.com',
                      'creator+business@example.com']:
            self.assertTrue(r.valid_recipient(email), email)

    def test_encoded_whitespace_candidate_is_not_assigned_a_template(self):
        d = self.database()
        d.execute("UPDATE contacts SET email='%20fixturecreator@gmail.com'")
        d.commit()
        with patch.dict(r.BODY_HASHES, {'first_contact': hashlib.sha256(b'approved body').hexdigest()}, clear=True):
            result = r.route_unassigned(d, apply=True)
        self.assertEqual(result['assigned'], 0)
        self.assertIsNone(d.execute('SELECT initial_template_key FROM contacts').fetchone()[0])

    def database(self):
        d=sqlite3.connect(':memory:'); d.row_factory=sqlite3.Row
        d.executescript('''
          CREATE TABLE contacts(id INTEGER PRIMARY KEY,email TEXT,channel_name TEXT,channel_url TEXT,category TEXT,validation_status TEXT,subscribers INTEGER,outreach_status TEXT,first_sent_at TEXT,do_not_contact INTEGER,initial_template_key TEXT,platform TEXT,fit_score INTEGER,updated_at TEXT);
          CREATE TABLE outreach_send_log(contact_id INTEGER,campaign_kind TEXT,status TEXT);
          CREATE TABLE inbox_events(contact_id INTEGER,sender_email TEXT);
          CREATE TABLE outreach_sent_messages(contact_id INTEGER,recipient_email TEXT);
          CREATE TABLE outreach_templates(template_key TEXT,body TEXT,enabled INTEGER,approval_status TEXT);
          CREATE TABLE automation_rules(rule_key TEXT,mode TEXT,enabled INTEGER);
          CREATE TABLE lead_discovery_emails(contact_id INTEGER,email TEXT,channel_key TEXT);
          CREATE TABLE lead_discovery_channels(channel_key TEXT,channel_id TEXT,channel_name TEXT,channel_url TEXT,matched_video_title TEXT,matched_video_url TEXT);
          CREATE TABLE outreach_audit_log(action TEXT,entity_type TEXT,entity_id TEXT,actor TEXT,detail_json TEXT,created_at TEXT);
          CREATE TABLE outreach_settings(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT);
          INSERT INTO contacts VALUES(1,'one@example.com','Example','https://www.youtube.com/@example','Creative & Media','send_ready',1000,'not_contacted',NULL,0,NULL,'YouTube',10,NULL);
          INSERT INTO automation_rules VALUES('first_contact','auto',1);
          INSERT INTO outreach_templates VALUES('first_contact','approved body',1,'approved');
          INSERT INTO lead_discovery_emails VALUES(1,'one@example.com','key');
        ''')
        d.execute('INSERT INTO lead_discovery_channels VALUES(?,?,?,?,?,?)',tuple(self.evidence[k] for k in ('channel_key','channel_id','channel_name','channel_url','matched_video_title','matched_video_url')))
        d.commit();self.addCleanup(d.close);return d

    def test_preview_does_not_write_and_apply_is_idempotent(self):
        d=self.database()
        with patch.dict(r.BODY_HASHES,{'first_contact':hashlib.sha256(b'approved body').hexdigest()},clear=True):
            before=d.total_changes
            self.assertEqual(r.route_unassigned(d)['eligible'],1)
            self.assertEqual(d.total_changes,before)
            self.assertEqual(r.route_unassigned(d,apply=True)['assigned'],1)
            self.assertEqual(r.route_unassigned(d,apply=True)['assigned'],0)
            self.assertEqual(d.execute('SELECT count(*) FROM outreach_audit_log').fetchone()[0],1)

    def test_unapproved_disabled_changed_copy_and_rule_hold(self):
        for update in ["UPDATE outreach_templates SET approval_status='pending_review'", "UPDATE outreach_templates SET enabled=0", "UPDATE automation_rules SET mode='draft'", "UPDATE outreach_templates SET body='new content'"]:
            with self.subTest(update=update):
                d=self.database();d.execute(update);d.commit()
                with patch.dict(r.BODY_HASHES,{'first_contact':hashlib.sha256(b'approved body').hexdigest()},clear=True):
                    self.assertEqual(r.route_unassigned(d,apply=True)['assigned'],0)

    def test_previous_attempt_inbound_outbound_suppression_and_duplicate_hold(self):
        updates=["INSERT INTO outreach_send_log VALUES(1,'creator_initial','delivery_unknown')", "INSERT INTO inbox_events VALUES(NULL,'ONE@example.com')", "INSERT INTO outreach_sent_messages VALUES(NULL,'one@example.com')", "UPDATE contacts SET do_not_contact=1", "UPDATE contacts SET subscribers=999", "INSERT INTO contacts SELECT 2,email,channel_name,channel_url,category,validation_status,subscribers,outreach_status,first_sent_at,do_not_contact,initial_template_key,platform,fit_score,updated_at FROM contacts"]
        for update in updates:
            with self.subTest(update=update):
                d=self.database();d.execute(update);d.commit()
                self.assertEqual(len(r.candidates(d)),0)

    def test_missing_schema_fails_closed(self):
        d=sqlite3.connect(':memory:');self.addCleanup(d.close)
        self.assertEqual(r.route_unassigned(d)['status'],'routing_schema_missing')

    def test_live_video_verification_failure_does_not_assign(self):
        d=self.database();self.verifier.return_value=False
        with patch.dict(r.BODY_HASHES,{'first_contact':hashlib.sha256(b'approved body').hexdigest()},clear=True):
            result=r.route_unassigned(d,apply=True)
            self.assertEqual(result['assigned'],0)
            self.assertEqual(result['verification']['attempted'],1)
            self.assertEqual(result['verification']['failed'],1)
            self.assertIsNone(d.execute('SELECT initial_template_key FROM contacts').fetchone()[0])
            self.verifier.reset_mock()
            result=r.route_unassigned(d,apply=True)
            self.assertEqual(result['assigned'],0)
            self.assertEqual(result['verification']['attempted'],0)
            self.assertEqual(result['verification']['cooling_down'],1)
            self.verifier.assert_not_called()

    def test_live_author_and_url_must_match_and_title_must_be_present(self):
        review={'video_url':self.evidence['matched_video_url'],'video_title':self.evidence['matched_video_title'],'channel':'Example','evidence_channel_url':self.evidence['channel_url']}
        data={'title':review['video_title'],'author_name':'Example','author_url':review['evidence_channel_url']}
        for field in (None,'title','author_name','author_url'):
            actual=dict(data)
            if field: actual[field]='' if field=='title' else 'wrong'
            with patch.object(r.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(actual).encode())):
                self.assertEqual(REAL_VERIFY(review),field is None)

    def test_localized_title_preserves_verified_author_identity(self):
        review={'video_url':self.evidence['matched_video_url'],'video_title':self.evidence['matched_video_title'],'channel':'Example','evidence_channel_url':self.evidence['channel_url']}
        data={'title':'视频剪辑入门教程','author_name':'Example','author_url':review['evidence_channel_url']}
        with patch.object(r.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(data).encode())):
            self.assertTrue(REAL_VERIFY(review))

    def test_empty_queue_status_distinguishes_missing_template_and_future_window(self):
        d=self.database()
        d.execute('ALTER TABLE contacts ADD COLUMN planned_sender_email TEXT')
        d.execute('ALTER TABLE contacts ADD COLUMN next_eligible_send_at TEXT')
        self.assertEqual(empty_queue_status(d,'sender@example.com'),'waiting_for_template_assignment')
        d.execute("UPDATE contacts SET initial_template_key='first_contact',planned_sender_email='sender@example.com',next_eligible_send_at=datetime('now','+1 day')")
        self.assertEqual(empty_queue_status(d,'sender@example.com'),'waiting_for_send_window')
        d.execute("UPDATE contacts SET outreach_status='sent'")
        self.assertEqual(empty_queue_status(d,'sender@example.com'),'queue_empty')
