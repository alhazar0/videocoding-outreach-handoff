"""Maintain the user-requested 10,000 approved invitation buffer; never sends mail."""
import argparse
import fcntl
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import outreach_scheduler as scheduler
from scripts.initial_template_router import route_unassigned
from scripts.creator_activity import refresh_pending_activity, activity_days, ensure_activity_schema

DEFAULT_TARGET = 10000
MAX_TARGET = 10000

def bounded_target(value):
    return max(1,min(value,MAX_TARGET))

def prepared_count(db):
    days = activity_days(db)
    activity_sql = ''
    if days:
        ensure_activity_schema(db)
        activity_sql = '''AND EXISTS(SELECT 1 FROM creator_activity_cache a WHERE a.channel_url=c.channel_url
          AND a.status='recent' AND julianday(a.checked_at) BETWEEN julianday('now')-2 AND julianday('now')
          AND julianday(a.published_at) BETWEEN julianday('now')-? AND julianday('now'))'''
    return db.execute("""SELECT count(*) FROM contacts c
      WHERE validation_status='send_ready' AND subscribers>=1000 AND do_not_contact=0
      AND outreach_status='not_contacted' AND first_sent_at IS NULL
      AND planned_send_date IS NOT NULL
      AND EXISTS(SELECT 1 FROM outreach_templates t WHERE t.template_key=c.initial_template_key
        AND t.enabled=1 AND t.approval_status='approved')
      AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=c.id AND l.campaign_kind='creator_initial') """
      + activity_sql, (days,) if days else ()).fetchone()[0]

def run(batches=6,target=DEFAULT_TARGET):
    db=scheduler.connect(scheduler.DEFAULT_DB)
    try:
        for _ in range(batches):
            activity = refresh_pending_activity(db, limit=100)
            before=prepared_count(db)
            if before>=target:
                summary={'status':'buffer_ready','prepared':before,'target':target,'activity':activity}
                scheduler.set_setting(db,'initial_queue_replenisher_last_result',json.dumps(summary))
                print(json.dumps(summary),flush=True)
                break
            result=route_unassigned(db,apply=True,limit=min(100,target-before))
            planned=scheduler.append_unplanned_contacts(db)
            summary={k:v for k,v in result.items() if k!='reviews'}
            summary.update(planned=planned,prepared=prepared_count(db),target=target,activity=activity)
            scheduler.set_setting(db,'initial_queue_replenisher_last_result',json.dumps(summary))
            print(json.dumps(summary),flush=True)
            # Failed checks enter a cooldown. Continue to later candidates within
            # this bounded run; stop only when no live checks can be attempted.
            if not result['assigned'] and not result.get('verification',{}).get('attempted'):
                break
    finally:
        db.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batches',type=int,default=6)
    parser.add_argument('--target',type=int,default=DEFAULT_TARGET)
    args=parser.parse_args()
    with (scheduler.PROJECT_ROOT/'.wrangler/initial-queue-replenisher.lock').open('a+') as handle:
        try:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)
        run(max(1,min(args.batches,30)),bounded_target(args.target))
