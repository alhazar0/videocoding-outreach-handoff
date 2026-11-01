import sqlite3
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from scripts import creator_activity as activity


class RecentActivityTests(unittest.TestCase):
    now=datetime(2026,10,8,12,tzinfo=timezone.utc)
    cid='UC'+'a'*22

    def feed(self, entries):
        return '<feed>'+''.join(f'<entry><yt:channelId>{cid}</yt:channelId><yt:videoId>abcdefghijk</yt:videoId><published>{date}</published></entry>' for cid,date in entries)+'</feed>'

    def test_boundary_and_future_uploads(self):
        for age, status in ((1,'recent'),(30,'recent'),(31,'inactive'),(-1,'unknown')):
            result=activity.activity_from_feed(self.feed([(self.cid,(self.now-timedelta(days=age)).isoformat())]),self.cid,now=self.now)
            self.assertEqual(result['status'],status)

    def test_old_relevant_video_does_not_exclude_a_new_channel_upload(self):
        feed=self.feed([(self.cid,(self.now-timedelta(days=100)).isoformat()),
                        (self.cid,(self.now-timedelta(days=3)).isoformat())])
        result=activity.activity_from_feed(feed,self.cid,now=self.now)
        self.assertTrue(activity.recent(result,now=self.now))

    def test_wrong_channel_and_missing_date_fail_closed(self):
        self.assertEqual(activity.activity_from_feed(self.feed([('UC'+'b'*22,self.now.isoformat())]),self.cid,now=self.now)['status'],'unknown')
        self.assertEqual(activity.activity_from_feed('<feed/>',self.cid,now=self.now)['status'],'unknown')

    def test_cache_is_reused_but_not_past_activity_cutoff(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        self.addCleanup(db.close)
        activity.ensure_activity_schema(db)
        result=activity.activity_from_feed(self.feed([(self.cid,(self.now-timedelta(days=29)).isoformat())]),self.cid,now=self.now)
        activity.save_activity(db,'https://www.youtube.com/@example',result)
        with patch.object(activity,'now_utc',return_value=self.now), patch.object(activity,'fetch_activity') as fetch:
            self.assertTrue(activity.recent(activity.check_activity(db,'https://www.youtube.com/@example'),now=self.now))
            fetch.assert_not_called()
        with patch.object(activity,'now_utc',return_value=self.now+timedelta(days=1,hours=1)):
            self.assertIsNone(activity.cached_activity(db,'https://www.youtube.com/@example'))

    def test_network_error_is_unknown_not_inactive(self):
        with patch.object(activity,'get_text',side_effect=TimeoutError('timeout')):
            result=activity.fetch_activity('https://www.youtube.com/channel/'+self.cid,self.cid)
        self.assertEqual(result['status'],'unknown')


if __name__=='__main__':
    unittest.main()
