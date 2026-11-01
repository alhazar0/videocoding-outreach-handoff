import sqlite3
import unittest
from pathlib import Path

from scripts.merge_lead_discovery import job_rows_by_ids, job_rows_since, rows_since, merge_activity, merge_once
from scripts.creator_activity import ensure_activity_schema


class LeadDiscoveryMergeCursorTests(unittest.TestCase):
    def test_job_cursor_advances_when_many_rows_share_one_timestamp(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE lead_discovery_jobs(id INTEGER PRIMARY KEY, updated_at TEXT)")
        db.executemany(
            "INSERT INTO lead_discovery_jobs(id,updated_at) VALUES(?, '2026-08-31T18:03:20Z')",
            [(1,), (2,), (3,)],
        )
        first = job_rows_since(db, "", 0, 2)
        self.assertEqual([row["id"] for row in first], [1, 2])
        second = job_rows_since(db, first[-1]["updated_at"], first[-1]["id"], 2)
        self.assertEqual([row["id"] for row in second], [3])
        db.close()

    def test_job_rows_by_ids_reconciles_rows_behind_the_watermark(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE lead_discovery_jobs(id INTEGER PRIMARY KEY,updated_at TEXT,status TEXT)")
        db.executemany(
            "INSERT INTO lead_discovery_jobs VALUES(?,?,?)",
            [(1, "2026-01-01T00:00:00Z", "waiting"), (2, "2026-02-01T00:00:00Z", "waiting")],
        )
        rows = job_rows_by_ids(db, [1])
        self.assertEqual([(row["id"], row["status"]) for row in rows], [(1, "waiting")])
        db.close()

    def test_large_equal_timestamp_batches_do_not_stall_for_text_or_integer_keys(self):
        for key_type,keys in [('INTEGER',list(range(1,1002))),('TEXT',[f'channel-{i:05d}' for i in range(1001)])]:
            with self.subTest(key_type=key_type):
                db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.row_factory=sqlite3.Row
                db.execute(f'CREATE TABLE sample(id {key_type} PRIMARY KEY,updated_at TEXT)')
                db.executemany("INSERT INTO sample VALUES(?,'2026-10-08T00:00:00Z')",[(k,) for k in keys])
                stamp='';key=0 if key_type=='INTEGER' else '';seen=[]
                for _ in range(5):
                    page=rows_since(db,'sample','updated_at',stamp,'id',500,after_key=key)
                    if not page:break
                    seen.extend(r['id'] for r in page);stamp=page[-1]['updated_at'];key=page[-1]['id']
                self.assertEqual(seen,keys)

    def test_mixed_iso_and_sqlite_timestamps_follow_chronological_order(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY,updated_at TEXT)')
        db.executemany('INSERT INTO sample VALUES(?,?)',[(1,'2026-10-08T01:00:00Z'),(2,'2026-10-08 02:00:00'),(3,'2026-10-08T03:00:00+00:00')])
        page=rows_since(db,'sample','updated_at','2026-10-08T01:00:00Z','id',100,after_key=1)
        self.assertEqual([r['id'] for r in page],[2,3])

    def test_shared_activity_never_replaces_newer_verification(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        ensure_activity_schema(db)
        old=dict(channel_url='url',channel_id='cid',status='recent',published_at='2026-10-01',video_url='video',checked_at='2026-10-07T00:00:00Z',detail='feed')
        self.assertEqual(merge_activity(db,[old]),1)
        new={**old,'status':'inactive','checked_at':'2026-10-08T00:00:00Z'}
        self.assertEqual(merge_activity(db,[new]),1)
        self.assertEqual(merge_activity(db,[old]),0)
        self.assertEqual(db.execute('SELECT status FROM creator_activity_cache').fetchone()[0],'inactive')


class MergeIntegrationTests(unittest.TestCase):
    def setUp(self):
        from scripts.continuous_lead_discovery import ensure_discovery_schema
        self.source=sqlite3.connect(':memory:');self.target=sqlite3.connect(':memory:')
        for db in (self.source,self.target):
            self.addCleanup(db.close);db.row_factory=sqlite3.Row
            db.executescript((Path(__file__).resolve().parents[1]/'db/0000_initial.sql').read_text())
            ensure_discovery_schema(db,worker_key='test',seed_jobs=False)

    def test_paginated_activity_and_slow_run_reconciliation_are_idempotent(self):
        for i in range(5):
            self.source.execute("INSERT INTO creator_activity_cache VALUES(?,NULL,'unknown',NULL,NULL,'2026-10-08T00:00:00Z','feed')",(f'url{i}',))
        self.source.execute("INSERT INTO lead_discovery_runs(id,job_id,status,started_at) VALUES(1,1,'running','2026-10-08T00:00:00Z')")
        self.source.execute("INSERT INTO lead_discovery_runs(id,job_id,status,started_at) VALUES(100,1,'completed','2026-10-08T00:01:00Z')")
        self.source.commit()
        for _ in range(4):merge_once(self.source,self.target,2)
        self.assertEqual(self.target.execute('SELECT count(*) FROM creator_activity_cache').fetchone()[0],5)
        self.source.execute("UPDATE lead_discovery_runs SET status='completed' WHERE id=1");self.source.commit()
        result=merge_once(self.source,self.target,2)
        self.assertEqual(result['activity_evidence'],0)
        self.assertEqual(self.target.execute('SELECT status FROM lead_discovery_runs WHERE id=1').fetchone()[0],'completed')

    def test_merge_replay_preserves_contact_suppression_and_sent_state(self):
        from scripts.continuous_lead_discovery import import_public_email, ChannelCandidate, EmailEvidence
        contact=ChannelCandidate('cid','cid','Creator','https://www.youtube.com/channel/cid','AI video','https://www.youtube.com/watch?v=abcdefghijk')
        import_public_email(self.source,{'id':1,'tier':2,'query':'AI','language':'English'},contact,'Creator',2000,
                            EmailEvidence('business@example.com',contact.url,'youtube_public_description'),True)
        self.source.commit();merge_once(self.source,self.target,2)
        self.target.execute("UPDATE contacts SET do_not_contact=1,outreach_status='contacted',first_sent_at='2026-10-08',channel_name='Keep' ");self.target.commit()
        self.source.execute("UPDATE contacts SET channel_name='Changed',updated_at='2999-01-01T00:00:00Z'");self.source.commit()
        merge_once(self.source,self.target,2)
        row=self.target.execute('SELECT * FROM contacts').fetchone()
        self.assertEqual(row['channel_name'],'Keep');self.assertEqual(row['do_not_contact'],1)
        self.assertEqual(row['first_sent_at'],'2026-10-08')
        self.assertEqual(self.target.execute('SELECT count(*) FROM contacts').fetchone()[0],1)


if __name__ == "__main__":
    unittest.main()
