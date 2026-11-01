import json
import unittest
from datetime import datetime,timedelta,timezone
from urllib.error import HTTPError
from unittest.mock import patch
from scripts import creator_activity as activity


class PublicVideoActivityTests(unittest.TestCase):
    cid='UC'+'a'*22
    vid='abcdefghijk'
    now=datetime(2026,10,19,2,tzinfo=timezone.utc)

    def channel(self, identity=None, selected=True, content_type='LOCKUP_CONTENT_TYPE_VIDEO'):
        return 'var ytInitialData = '+json.dumps({'metadata':{'channelMetadataRenderer':{'externalId':identity or self.cid}},'contents':{'twoColumnBrowseResultsRenderer':{'tabs':[{'tabRenderer':{'title':'Videos','selected':selected,'content':{'richGridRenderer':{'contents':[{'richItemRenderer':{'content':{'lockupViewModel':{'contentType':content_type,'contentId':self.vid}}}}]}}}}]}}})+';'

    def player(self, **changes):
        stamp=(self.now-timedelta(days=2)).isoformat()
        data={'playabilityStatus':{'status':'OK'},'videoDetails':{'videoId':self.vid,'channelId':self.cid,'isUpcoming':False},'microformat':{'playerMicroformatRenderer':{'externalChannelId':self.cid,'publishDate':stamp,'uploadDate':stamp}}}
        for where,key,value in changes.get('fields',[]):data[where][key]=value
        data['microformat']['playerMicroformatRenderer'].update(changes.get('meta',{}))
        return 'var ytInitialPlayerResponse = '+json.dumps(data)+';'

    def test_only_selected_owned_video_tab_is_used(self):
        self.assertEqual(activity.public_channel_video_ids(self.channel(),self.cid),[self.vid])
        for page in (self.channel(identity='UC'+'b'*22),self.channel(selected=False),self.channel(content_type='LOCKUP_CONTENT_TYPE_PLAYLIST')):
            self.assertEqual(activity.public_channel_video_ids(page,self.cid),[])

    def test_exact_current_timestamp_proves_activity(self):
        r=activity.activity_from_video_page(self.player(),self.cid,self.vid,now=self.now)
        self.assertTrue(activity.recent(r,now=self.now))
        self.assertEqual(r['video_url'],'https://www.youtube.com/watch?v='+self.vid)

    def test_identity_privacy_and_player_status_must_match(self):
        for page in (self.player(fields=[('videoDetails','channelId','UC'+'b'*22)]),self.player(meta={'externalChannelId':'UC'+'b'*22}),self.player(fields=[('videoDetails','videoId','other_vid12')]),self.player(fields=[('videoDetails','isUpcoming',True)]),self.player(fields=[('videoDetails','isPrivate',True)]),self.player(meta={'isUnlisted':True}),self.player(fields=[('playabilityStatus','status','LOGIN_REQUIRED')])):
            self.assertEqual(activity.activity_from_video_page(page,self.cid,self.vid,now=self.now)['status'],'unknown')

    def test_old_future_relative_and_date_only_remain_unknown(self):
        for stamp in ((self.now-timedelta(days=31)).isoformat(),(self.now+timedelta(hours=1)).isoformat(),'2 days ago','2026-10-18','2026-10-18T10:00:00',None):
            with self.subTest(stamp=stamp):
                r=activity.activity_from_video_page(self.player(meta={'publishDate':stamp}),self.cid,self.vid,now=self.now)
                self.assertEqual(r['status'],'unknown')

    def test_404_feed_uses_bounded_public_pages(self):
        error=HTTPError('feed',404,'Not Found',{},None)
        with patch.object(activity,'now_utc',return_value=self.now),patch.object(activity,'get_text',side_effect=[error,self.channel(),self.player()]) as get:
            r=activity.fetch_activity('https://www.youtube.com/channel/'+self.cid,self.cid)
        self.assertEqual(r['status'],'recent');self.assertEqual(get.call_count,3)
        self.assertIn('feed HTTP 404',r['detail'])

    def test_403_429_and_retry_after_do_not_trigger_alternative_requests(self):
        for code,headers in ((403,{}),(429,{}),(503,{'Retry-After':'60'})):
            with patch.object(activity,'get_text',side_effect=HTTPError('feed',code,'held',headers,None)) as get:
                r=activity.fetch_activity('https://www.youtube.com/channel/'+self.cid,self.cid)
            self.assertEqual(r['status'],'unknown');self.assertEqual(get.call_count,1)

    def test_fallback_identity_mismatch_stops_before_watch_request(self):
        with patch.object(activity,'get_text',side_effect=[HTTPError('feed',500,'error',{},None),self.channel(identity='UC'+'b'*22)]) as get:
            r=activity.fetch_activity('https://www.youtube.com/channel/'+self.cid,self.cid)
        self.assertEqual(r['status'],'unknown');self.assertEqual(get.call_count,2)

    def test_auth_failure_on_public_page_does_not_retry(self):
        with patch.object(activity,'get_text',side_effect=[HTTPError('feed',404,'error',{},None),HTTPError('page',429,'limited',{},None)]) as get:
            r=activity.fetch_activity('https://www.youtube.com/channel/'+self.cid,self.cid)
        self.assertEqual(r['status'],'unknown');self.assertEqual(get.call_count,2)

    def test_existing_healthy_feed_does_not_add_page_requests(self):
        feed=f'<feed><entry><yt:channelId>{self.cid}</yt:channelId><yt:videoId>{self.vid}</yt:videoId><published>{self.now.isoformat()}</published></entry></feed>'
        with patch.object(activity,'now_utc',return_value=self.now),patch.object(activity,'get_text',return_value=feed) as get:
            r=activity.fetch_activity('https://www.youtube.com/channel/'+self.cid,self.cid)
        self.assertEqual(r['status'],'recent');self.assertEqual(get.call_count,1)

if __name__=='__main__':unittest.main()
