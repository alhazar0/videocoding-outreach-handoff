"""Initial submissions stop at the ET boundary even inside an active batch."""
import contextlib
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from scripts import outreach_scheduler as scheduler


class CampaignWindowGuardTests(unittest.TestCase):
    def test_boundaries_and_campaign_day_across_dst(self):
        for day in ('2026-10-18', '2026-11-02'):
            for hour, minute, expected in ((7,59,False),(8,0,True),(20,59,True),(21,0,False)):
                local = datetime.fromisoformat(f'{day}T{hour:02d}:{minute:02d}:00').replace(tzinfo=scheduler.CAMPAIGN_ZONE)
                self.assertEqual(scheduler.campaign_send_window_open(day, local.astimezone(timezone.utc)), expected)
                self.assertFalse(scheduler.campaign_send_window_open('2026-10-17', local))

    def run_with_clock(self, *, outside=False, activity_crosses=False, refresh_crosses=False):
        clock = [datetime(2026,10,19,1 if outside else 0,0 if outside else 59,tzinfo=timezone.utc)]
        rows = [dict(id=i,email=f'creator{i}@example.com',channel_name=f'Creator {i}',channel_url=f'https://youtube.com/@creator{i}',send_timezone='America/New_York',timezone_source='campaign_policy',send_window='america_new_york_8am',next_eligible_send_at='2026-10-18T12:00:00Z') for i in (1,2)]
        db = MagicMock()
        def execute(sql, values=()):
            cur = MagicMock()
            if 'SELECT * FROM contacts WHERE id=' in sql:
                cur.fetchone.return_value = next(r for r in rows if r['id']==values[0])
            elif 'SELECT count(*) FROM contacts c' in sql:
                cur.fetchone.return_value = (1000,)
            elif 'SELECT COUNT(*) count FROM contacts' in sql:
                cur.fetchone.return_value = {'count':2}
            else:
                cur.fetchone.return_value = {'status':'active'}
            return cur
        db.execute.side_effect=execute
        def activity(*args, **kwargs):
            if activity_crosses:clock[0]=datetime(2026,10,19,1,0,tzinfo=timezone.utc)
            return {'status':'active'}
        def sleep(*args):clock[0]=datetime(2026,10,19,1,0,tzinfo=timezone.utc)
        token_calls = []
        def token(*args):
            token_calls.append(True)
            if refresh_crosses and len(token_calls)>1:
                clock[0]=datetime(2026,10,19,1,0,tzinfo=timezone.utc)
            return 'test-only-token'
        settings={}
        stubs=dict(connect=db,ensure_schema=None,recover_stale_initial_sends=0,microsoft_outbound_block_reason='',automation_rule_mode='auto',maybe_upgrade_queue_policy=0,release_unassigned_segment_queue=0,append_unplanned_contacts=0,reschedule_missed_windows=0,replace_activity_held_slots=0,sent_today=0,delivery_committed_today=0,candidate_contacts=rows,partition_policy_allowed_contacts=(rows,[]),refresh_access_token='test-only-token',next_due_run='next',template_automation_mode='auto',initial_template_is_approved=True,approved_initial_template=('Subject','Body','v1'),contact_template_metadata=('first_contact','experiment','variant'),render_initial_body='Body')
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('sys.argv',['scheduler']))
            args=scheduler.parse_args()
            for name,value in stubs.items():stack.enter_context(patch.object(scheduler,name,return_value=value))
            stack.enter_context(patch.object(scheduler,'refresh_access_token',side_effect=token))
            stack.enter_context(patch.object(scheduler,'utc_now',side_effect=lambda:clock[0]))
            stack.enter_context(patch.object(scheduler,'set_setting',side_effect=lambda db,k,v:settings.__setitem__(k,v)))
            stack.enter_context(patch.object(scheduler.time,'sleep',side_effect=sleep))
            stack.enter_context(patch('scripts.creator_activity.activity_days',return_value=30))
            stack.enter_context(patch('scripts.creator_activity.check_activity',side_effect=activity))
            stack.enter_context(patch('scripts.creator_activity.recent',return_value=True))
            send=stack.enter_context(patch.object(scheduler,'send_mail',return_value=(False,'HTTP 401') if refresh_crosses else (True,'accepted')))
            smtp=stack.enter_context(patch.object(scheduler,'send_gmail_smtp'))
            with contextlib.redirect_stdout(__import__('io').StringIO()):self.assertEqual(scheduler.run(args),1 if refresh_crosses else 0)
            smtp.assert_not_called()
        reservations=[call for call in db.execute.call_args_list if 'INSERT INTO outreach_send_log(' in call.args[0]]
        return send.call_count,len(reservations),settings,db

    def test_already_closed_window_does_not_send_or_reserve(self):
        sent,reserved,settings,_=self.run_with_clock(outside=True)
        self.assertEqual((sent,reserved),(0,0))
        self.assertEqual(settings['scheduler_status'],'waiting_for_send_window')
        self.assertEqual(settings['scheduler_next_run_at'],'2026-10-19T12:00:00Z')

    def test_inflight_batch_stops_after_sleep_crosses_close(self):
        sent,reserved,settings,_=self.run_with_clock()
        self.assertEqual((sent,reserved),(1,1))
        self.assertEqual(json.loads(settings['scheduler_last_result'])['status'],'waiting_for_send_window')
        self.assertEqual(settings['scheduler_next_run_at'],'2026-10-19T12:00:00Z')

    def test_activity_request_crossing_close_does_not_reserve_or_send(self):
        sent,reserved,settings,db=self.run_with_clock(activity_crosses=True)
        self.assertEqual((sent,reserved),(0,0))
        self.assertEqual(settings['scheduler_status'],'waiting_for_send_window')
        db.rollback.assert_called()

    def test_authentication_refresh_crossing_close_does_not_retry(self):
        sent,reserved,settings,_=self.run_with_clock(refresh_crosses=True)
        self.assertEqual((sent,reserved),(1,1))
        self.assertEqual(settings['scheduler_status'],'partial')
        self.assertEqual(json.loads(settings['scheduler_last_result'])['failed'],1)

if __name__=='__main__':unittest.main()
