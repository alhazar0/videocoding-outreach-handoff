import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from tests import test_continuous_lead_discovery as base_tests
from scripts import continuous_lead_discovery as discovery
from scripts.discovery_learning import learned_family, family_score, recover_expired_leases
from scripts.continuous_lead_discovery import claim_next_job, renew_job_heartbeat


class LearningTests(unittest.TestCase):
    def setUp(self):
        fixture=base_tests.MultiWorkerClaimTests();fixture.setUp()
        self.db=fixture.db;self.addCleanup(fixture.tearDown)
        self.now=datetime.now(timezone.utc).replace(microsecond=0)
        self.stamp=self.now.isoformat().replace('+00:00','Z')
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='creator_capcut',run_count=2 WHERE id IN (1,2)")
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='creator_blender',run_count=2 WHERE id IN (3,4)")
        for job,eligible in ((1,6),(1,6),(3,1),(3,1)):
            self.db.execute('''INSERT INTO lead_discovery_runs(job_id,status,started_at,completed_at,worker_key,
              eligible_contacts,new_channels,channels_found,active_channels,metrics_version,selection_mode)
              VALUES(?,'completed',?,?,'seed',?,50,100,40,1,'explore')''',
              (job,(self.now-timedelta(minutes=10)).isoformat(),self.stamp,eligible))
        self.db.execute("INSERT INTO lead_discovery_runs(job_id,status,started_at,worker_key,selection_mode) VALUES(1,'interrupted',?,'collector-05','explore')",(self.stamp,))
        self.db.commit()

    def test_measured_yield_wins_over_priority_and_other_families(self):
        self.db.execute('UPDATE lead_discovery_jobs SET priority=999 WHERE id=3');self.db.commit()
        job,run,_=claim_next_job(self.db,'collector-05',1800,'creator')
        self.assertEqual(job['tier_key'],'creator_capcut')
        self.assertEqual(self.db.execute('SELECT selection_mode FROM lead_discovery_runs WHERE id=?',(run,)).fetchone()[0],'yield')
        family,_,_=learned_family(self.db,'creator','collector-05',self.stamp)
        self.assertEqual(family,('creator_blender','English'))  # Active family is not duplicated.

    def test_every_fourth_claim_explores(self):
        for _ in range(3):
            self.db.execute("INSERT INTO lead_discovery_runs(job_id,status,started_at,worker_key,selection_mode) VALUES(1,'completed',?,'collector-05','yield')",(self.stamp,))
        family,mode,_=learned_family(self.db,'creator','collector-05',self.stamp)
        self.assertIsNone(family);self.assertEqual(mode,'explore')

    def test_unmeasured_old_and_single_lucky_sample_do_not_train(self):
        for update in ("metrics_version=0", "metrics_version=1,started_at='2000-01-01'"):
            self.db.execute('UPDATE lead_discovery_runs SET '+update)
            self.assertIsNone(learned_family(self.db,'creator','collector-05',self.stamp)[0])
        self.db.execute("UPDATE lead_discovery_runs SET started_at=? WHERE id=1",(self.stamp,))
        self.assertIsNone(learned_family(self.db,'creator','collector-05',self.stamp)[0])

    def test_zero_qualified_candidates_cannot_win_on_raw_volume(self):
        self.db.execute('UPDATE lead_discovery_runs SET eligible_contacts=0,new_channels=10000 WHERE job_id=1')
        self.assertEqual(learned_family(self.db,'creator','collector-05',self.stamp)[0],('creator_blender','English'))

    def test_learning_and_claiming_include_the_same_legacy_families(self):
        self.db.execute("UPDATE lead_discovery_jobs SET tier_key='talking_head_film' WHERE id IN (1,2)")
        self.db.commit()
        self.assertEqual(learned_family(self.db,'creator','collector-05',self.stamp)[0],('talking_head_film','English'))
        self.assertEqual(claim_next_job(self.db,'collector-05',1800,'creator')[0]['tier_key'],'talking_head_film')

    def test_score_rewards_time_novelty_and_reliable_results(self):
        baseline=dict(eligible=5,seconds=600,new_channels=80,channels=100,active=50,inactive=40,unknown=10)
        score=family_score(baseline)
        for change in ({'seconds':1200},{'new_channels':1},{'unknown':100}):
            self.assertLess(family_score({**baseline,**change}),score)

    def test_recovers_expired_job_and_run_but_preserves_live_lease(self):
        expired=(self.now-timedelta(hours=1)).isoformat().replace('+00:00','Z')
        live=(self.now+timedelta(hours=1)).isoformat().replace('+00:00','Z')
        for job,until in ((1,expired),(2,live)):
            self.db.execute("UPDATE lead_discovery_jobs SET status='running',lease_until=?,claim_token=?,claimed_by=? WHERE id=?",(until,str(job),str(job),job))
            self.db.execute("INSERT INTO lead_discovery_runs(job_id,status,started_at,claim_token) VALUES(?,'running',?,?)",(job,expired,str(job)))
            self.db.execute("INSERT INTO lead_discovery_channels VALUES(?,'scanning',?,?)",(str(job),str(job),until))
        self.assertEqual(recover_expired_leases(self.db,self.stamp),1)
        self.assertEqual(self.db.execute('SELECT status FROM lead_discovery_jobs WHERE id=1').fetchone()[0],'queued')
        self.assertEqual(self.db.execute('SELECT status FROM lead_discovery_jobs WHERE id=2').fetchone()[0],'running')
        self.assertEqual(self.db.execute("SELECT status FROM lead_discovery_runs WHERE claim_token='1'").fetchone()[0],'interrupted')
        self.assertEqual(self.db.execute("SELECT scan_status FROM lead_discovery_channels WHERE channel_key='2'").fetchone()[0],'scanning')
        stale=dict(id=1,claim_token='1')
        with self.assertRaises(RuntimeError):
            renew_job_heartbeat(self.db,stale,'1',1800)

    def test_only_new_active_large_enough_contacts_reward_learning(self):
        candidates=[discovery.ChannelCandidate(str(i),'cid',str(i),'url'+str(i),'AI video','video') for i in range(4)]
        def scan(candidate):
            i=int(candidate.key)
            return candidate.name, (100 if i==1 else 2000), [discovery.EmailEvidence(str(i)+'@example.com','public','description')], {
                'status':'inactive' if i==3 else 'recent','published_at':self.stamp,'checked_at':self.stamp}
        with patch.object(discovery,'discover_channels',return_value=(4,candidates)), \
             patch.object(discovery,'renew_job_heartbeat'), patch.object(discovery,'upsert_channel',return_value=True), \
             patch.object(discovery,'scan_channel',side_effect=scan), patch.object(discovery,'save_activity'), \
             patch.object(discovery,'has_mx',return_value=True), \
             patch.object(discovery,'import_public_email',side_effect=lambda db,job,candidate,*args: candidate.key!='2'):
            result=discovery.run_job(MagicMock(),{},4,4,2)
        self.assertEqual(result['contacts_imported'],2)
        self.assertEqual(result['eligible_contacts'],1)
        self.assertEqual(result['active_channels'],3)
        self.assertEqual(result['inactive_channels'],1)


if __name__=='__main__':
    unittest.main()
