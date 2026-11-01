import sqlite3
import unittest
import http.client
from unittest.mock import patch
from scripts import continuous_lead_discovery as discovery

from scripts.continuous_lead_discovery import claim_next_job, public_contact_emails, seed_queries, valid_match_for_tier, discovery_cooldown
from scripts.discovery_learning import ensure_learning_schema


class PublicPageFailureTests(unittest.TestCase):
    def test_malformed_contact_link_cannot_crash_worker(self):
        url='https://example.com/About A Channel'
        with patch.object(discovery,'is_public_http_url',return_value=True), \
             patch.object(discovery.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=http.client.InvalidURL('URL contains spaces')
            self.assertEqual(discovery.fetch_public_html(url),('',url))

    def test_malformed_authority_is_skipped_before_request(self):
        url='https://[invalid/contact'
        self.assertEqual(discovery.fetch_public_html(url),('',url))


class MultiWorkerClaimTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE lead_discovery_jobs(
              id INTEGER PRIMARY KEY,tier INTEGER,tier_key TEXT,query TEXT,search_mode TEXT,
              language TEXT,status TEXT,priority INTEGER,run_count INTEGER DEFAULT 0,
              last_started_at TEXT,last_completed_at TEXT,next_run_at TEXT,last_error TEXT,updated_at TEXT,
              claimed_by TEXT,claim_token TEXT,lease_until TEXT,heartbeat_at TEXT,
              last_new_channels INTEGER DEFAULT 0,last_candidates INTEGER DEFAULT 0,low_yield_streak INTEGER DEFAULT 0
            );
            CREATE TABLE lead_discovery_runs(
              id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER,status TEXT,started_at TEXT,
              worker_key TEXT,claim_token TEXT,completed_at TEXT,error TEXT,
              new_channels INTEGER DEFAULT 0,channels_found INTEGER DEFAULT 0
            );
            CREATE TABLE lead_discovery_channels(channel_key TEXT,scan_status TEXT,
              scan_claimed_by TEXT,scan_lease_until TEXT);
            """
        )
        ensure_learning_schema(self.db)
        for job_id in (1, 2, 3, 4):
            self.db.execute(
                """INSERT INTO lead_discovery_jobs(id,tier,tier_key,query,search_mode,language,status,priority,run_count,last_started_at,last_completed_at,next_run_at,last_error,updated_at,claimed_by,claim_token,lease_until,heartbeat_at) VALUES(
                  ?,3,'talking_head','query','recent','English','queued',100,0,NULL,NULL,
                  '2000-01-01T00:00:00Z',NULL,'2000-01-01T00:00:00Z',NULL,NULL,NULL,NULL
                )""",
                (job_id,),
            )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_workers_atomically_claim_different_jobs(self):
        claims = [
            claim_next_job(self.db, f"collector-{number:02d}", 1800)
            for number in range(1, 5)
        ]
        self.assertTrue(all(claim is not None for claim in claims))
        claimed_jobs = [claim[0] for claim in claims if claim is not None]
        self.assertEqual(len({row["id"] for row in claimed_jobs}), 4)
        self.assertEqual(
            [row["claimed_by"] for row in claimed_jobs],
            ["collector-01", "collector-02", "collector-03", "collector-04"],
        )

    def test_due_tier_three_is_reserved_for_two_workers_when_both_are_due(self):
        self.db.execute("DELETE FROM lead_discovery_jobs")
        for job_id, tier in ((10, 2), (11, 3)):
            self.db.execute(
                """INSERT INTO lead_discovery_jobs(id,tier,tier_key,query,search_mode,language,status,priority,run_count,last_started_at,last_completed_at,next_run_at,last_error,updated_at,claimed_by,claim_token,lease_until,heartbeat_at) VALUES(
                  ?,?,'topic','query','recent_public_videos','English','queued',100,1,
                  NULL,NULL,'2000-01-01T00:00:00Z',NULL,'2000-01-01T00:00:00Z',
                  NULL,NULL,NULL,NULL
                )""",
                (job_id, tier),
            )
        self.db.commit()
        ai_claim = claim_next_job(self.db, "collector-01", 1800)
        self.assertEqual(ai_claim[0]["tier"], 2)
        talking_head_claim = claim_next_job(self.db, "collector-07", 1800)
        self.assertEqual(talking_head_claim[0]["tier"], 3)

    def test_talking_head_worker_falls_back_to_ai_when_not_due(self):
        self.db.execute("DELETE FROM lead_discovery_jobs")
        self.db.execute(
            """INSERT INTO lead_discovery_jobs(id,tier,tier_key,query,search_mode,language,status,priority,run_count,last_started_at,last_completed_at,next_run_at,last_error,updated_at,claimed_by,claim_token,lease_until,heartbeat_at) VALUES(
              12,2,'ai','query','recent_public_videos','English','queued',100,1,
              NULL,NULL,'2000-01-01T00:00:00Z',NULL,'2000-01-01T00:00:00Z',
              NULL,NULL,NULL,NULL
            )"""
        )
        self.db.commit()
        claim = claim_next_job(self.db, "collector-08", 1800)
        self.assertEqual(claim[0]["tier"], 2)

    def test_focused_workers_keep_ai_and_creator_work_separate(self):
        self.db.execute("UPDATE lead_discovery_jobs SET tier=2,tier_key='ai_tools' WHERE id=1")
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='creator_video_editing' WHERE id=2")
        self.db.commit()
        ai = claim_next_job(self.db, 'collector-01', 1800, focus='ai')
        creator = claim_next_job(self.db, 'collector-07', 1800, focus='creator')
        self.assertEqual(ai[0]['id'], 1)
        self.assertEqual(creator[0]['id'], 2)
        # Unrelated talking-head tasks do not consume either reserved lane.
        self.assertIsNone(claim_next_job(self.db, 'collector-02', 1800, focus='ai'))
        self.assertIsNone(claim_next_job(self.db, 'collector-08', 1800, focus='creator'))

    def test_invalid_focus_fails_before_claiming(self):
        with self.assertRaises(ValueError):
            claim_next_job(self.db, 'collector-01', 1800, focus='unknown')
        self.assertEqual(self.db.execute("SELECT count(*) FROM lead_discovery_runs").fetchone()[0], 0)

    def test_legacy_talking_head_jobs_are_reachable_without_crossing_lanes(self):
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='talking_head_film' WHERE id=1")
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='talking_head_science' WHERE id=2")
        self.db.commit()
        self.assertEqual(claim_next_job(self.db,'collector-05',1800,'creator')[0]['id'],1)
        self.assertEqual(claim_next_job(self.db,'collector-07',1800,'knowledge')[0]['id'],2)
        self.assertIsNone(claim_next_job(self.db,'collector-01',1800,'ai'))
        self.assertIsNone(claim_next_job(self.db,'collector-06',1800,'creator'))
        self.assertIsNone(claim_next_job(self.db,'collector-08',1800,'knowledge'))

    def test_knowledge_lane_and_exhausted_query_deprioritization(self):
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='knowledge_science',run_count=3,low_yield_streak=3 WHERE id=1")
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='knowledge_physics',run_count=1,low_yield_streak=0 WHERE id=2")
        self.db.commit()
        claim = claim_next_job(self.db, 'collector-08', 1800, focus='knowledge')
        self.assertEqual(claim[0]['id'], 2)

    def test_unused_query_family_gets_exploration_before_repeated_family(self):
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='creator_capcut',priority=999 WHERE id IN (1,2)")
        self.db.execute("UPDATE lead_discovery_jobs SET status='waiting',next_run_at='2999-01-01',last_started_at='2026-10-01' WHERE id=1")
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='creator_blender',priority=100 WHERE id=3")
        self.db.commit()
        self.assertEqual(claim_next_job(self.db,'collector-05',1800,focus='creator')[0]['id'],3)

    def test_parallel_exploration_spreads_topics_before_language_variants(self):
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='creator_capcut',language='English',priority=999 WHERE id=1")
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='creator_capcut',language='French',priority=999 WHERE id=2")
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='creator_blender',priority=100 WHERE id=3")
        self.db.commit()
        first=claim_next_job(self.db,'collector-05',1800,focus='creator')
        second=claim_next_job(self.db,'collector-06',1800,focus='creator')
        self.assertEqual(first[0]['tier_key'],'creator_capcut')
        self.assertEqual(second[0]['tier_key'],'creator_blender')


class PublicEmailQualityTests(unittest.TestCase):
    def test_rejects_sentry_dsn_and_keeps_public_business_email(self):
        text = (
            "Business inquiries: hello@creatorstudio.com "
            "https://hash@sentry.wixpress.com/123"
        )
        self.assertEqual(public_contact_emails(text), ["hello@creatorstudio.com"])


class QueryInventoryTests(unittest.TestCase):
    def test_ai_long_tail_inventory_is_unique_and_ai_first(self):
        seeds = seed_queries()
        keys = {(seed.search_mode, seed.query.casefold()) for seed in seeds}

        self.assertEqual(len(seeds), len(keys))
        self.assertGreaterEqual(sum(seed.tier <= 2 for seed in seeds), 6_900)
        self.assertTrue(
            any(
                seed.tier == 2
                and seed.query == "Perplexity AI tutorial India"
                for seed in seeds
            )
        )
        self.assertTrue(
            any(
                seed.tier == 2
                and seed.query == "AI tools tutorial for YouTubers"
                for seed in seeds
            )
        )

    def test_expanded_inventory_adds_new_languages_and_regions(self):
        seeds = seed_queries(expanded=True)
        queries = {seed.query for seed in seeds}
        self.assertIn("Perplexity AI hướng dẫn", queries)
        self.assertIn("Perplexity AI tutorial Ghana", queries)
        self.assertTrue(any("Vietnam" in seed.query and seed.tier == 3 for seed in seeds))

    def test_new_inventory_covers_ai_fields_and_targeted_creator_topics(self):
        seeds = seed_queries(expanded=True)
        queries = {seed.query for seed in seeds}
        self.assertTrue({'AI robotics', 'AI healthcare applications', 'AI safety research',
                         '人工智能 教程', '人工知能 使い方', 'DaVinci Resolve tutorial',
                         'content creation workflow', '视频剪辑 教程'} <= queries)
        creator = [s for s in seeds if s.tier_key.startswith('creator_')]
        self.assertTrue(creator)
        self.assertTrue(all(s.tier == 3 for s in creator))
        self.assertEqual(len(seeds), len({(s.search_mode, s.query.casefold()) for s in seeds}))


class TopicEvidenceTests(unittest.TestCase):
    def test_broader_tools_and_knowledge_topics(self):
        for title in ('AE tutorial for beginners', 'AE 特效教程', 'Blender animation basics', '剪映剪辑入门'):
            self.assertTrue(valid_match_for_tier(3, 'creator_ae', title))
        for title in ('Why the universe expands', 'Physics explained', '科普：太阳如何发光', '历史讲解'):
            self.assertTrue(valid_match_for_tier(3, 'knowledge_science', title))
        self.assertFalse(valid_match_for_tier(3, 'knowledge_science', 'My shopping haul'))

    def test_repeated_low_novelty_queries_back_off_and_recover(self):
        job={'low_yield_streak':0,'search_mode':'recent_public_videos'}
        result={'channels_found':100,'new_channels':2,'contacts_imported':0}
        for expected in (7,14,30,60,60):
            streak, days=discovery_cooldown(job,result)
            self.assertEqual(days,expected)
            job['low_yield_streak']=streak
        result.update(new_channels=60,contacts_imported=5)
        self.assertEqual(discovery_cooldown(job,result),(0,2))
    def test_native_ai_titles_and_research_topics_are_recognized(self):
        for title in ('人工智能最新进展', '人工智慧教學', '人工知能の使い方', '인공지능 입문',
                      'Cómo usar inteligencia artificial', 'Ferramentas de inteligência artificial',
                      'Künstliche Intelligenz erklärt', 'شرح الذكاء الاصطناعي',
                      'Будущее искусственного интеллекта', 'Yapay zekâ araçları',
                      'Computer vision tutorial', 'Retrieval-augmented generation explained'):
            with self.subTest(title=title):
                self.assertTrue(valid_match_for_tier(2, 'ai_native', title))

    def test_query_alone_does_not_qualify_unrelated_titles(self):
        for title in ('Best camera for travel', 'A day in my life', 'KI city walking tour'):
            self.assertFalse(valid_match_for_tier(2, 'ai_native', title))
            self.assertFalse(valid_match_for_tier(3, 'creator_video_editing', title))
        self.assertTrue(valid_match_for_tier(3, 'creator_davinci', 'DaVinci Resolve tutorial'))
        self.assertTrue(valid_match_for_tier(3, 'creator_zh', '视频剪辑入门教程'))
        self.assertFalse(valid_match_for_tier(2, 'ai_product_capcut', 'CapCut editing tutorial'))


if __name__ == "__main__":
    unittest.main()
