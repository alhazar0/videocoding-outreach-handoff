import sqlite3
import unittest
from pathlib import Path
from scripts.creator_feedback import collect

class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE inbox_events(id TEXT,contact_id INTEGER,sender_email TEXT,received_at TEXT,body_text TEXT,body_preview TEXT,event_type TEXT)')
    def add(self, body, kind='reply'):
        self.db.execute("INSERT INTO inbox_events VALUES('one',1,'creator@example.com','2026-09-07',?,'',?)",(body,kind))
    def tearDown(self):
        self.db.close()
    def test_generated_migration_compatible(self):
        sql=(Path(__file__).resolve().parents[1]/'drizzle/0008_dry_blackheart.sql').read_text()
        self.db.executescript(sql)
        self.add('The API request timed out after 1800s.')
        self.assertEqual(collect(self.db),1)
        self.assertEqual(collect(self.db),0)
    def test_dedup_and_preserve_review(self):
        self.add('I keep getting API request timed out after 1800s.')
        self.assertEqual(collect(self.db),1)
        self.db.execute("UPDATE creator_feedback SET review_state='reviewed',notes='investigated'")
        self.assertEqual(collect(self.db),0)
        self.assertEqual(self.db.execute('SELECT notes FROM creator_feedback').fetchone()[0],'investigated')
    def test_no_feedback_for_simple_interest(self):
        self.add('I would love to test it. Please send the code.')
        self.assertEqual(collect(self.db),0)
    def test_bounce_ignored(self):
        self.add('payment details required', 'bounce')
        self.assertEqual(collect(self.db),0)
    def test_provider_reports_do_not_become_product_feedback(self):
        for kind in ('sender_restricted','delivery_delayed','delivery_failed','automated'):
            with self.subTest(kind=kind):
                self.db.execute('DELETE FROM inbox_events')
                self.add('The recipient server timed out; payment details required.', kind)
                self.assertEqual(collect(self.db),0)
    def test_new_candidates_not_approved(self):
        self.add('It is asking for card details.')
        collect(self.db)
        self.assertEqual(self.db.execute('SELECT review_state FROM creator_feedback').fetchone()[0],'candidate')
    def test_positive(self):
        self.add('I tested it and I am really impressed by the output.')
        self.assertEqual(collect(self.db),1)

if __name__=='__main__': unittest.main()
