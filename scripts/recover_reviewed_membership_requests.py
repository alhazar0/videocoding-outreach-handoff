"""Recover the nine audited requests into a non-sending review queue only."""
import argparse
import fcntl
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import creator_data_agent as agent

REVIEWED_IDS = (19235, 19203, 19171, 19051, 18431, 18088, 17888, 17797, 17704)


def recover(db, apply=False):
    db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
    recovered, skipped = [], []
    try:
        for wid in REVIEWED_IDS:
            row = db.execute('SELECT * FROM outreach_work_items WHERE id=?', (wid,)).fetchone()
            if not row or row['status'] != 'superseded' or row['resolved_by'] != 'creator_data_agent':
                skipped.append(wid)
                continue
            blocked = db.execute('''SELECT 1 FROM inbox_events e
                LEFT JOIN contacts c ON c.id=e.contact_id WHERE e.id=? AND (
                  coalesce(c.do_not_contact,0)=1 OR EXISTS(
                    SELECT 1 FROM inbox_events n WHERE n.contact_id=e.contact_id AND
                      (datetime(n.received_at)>datetime(e.received_at) OR
                       (datetime(n.received_at)=datetime(e.received_at) AND n.id>e.id))) OR EXISTS(
                    SELECT 1 FROM outreach_sent_messages s WHERE
                      (s.contact_id=e.contact_id OR lower(s.recipient_email)=lower(e.sender_email))
                      AND datetime(s.sent_at)>datetime(e.received_at)))''', (row['inbox_event_id'],)).fetchone()
            logged = any(db.execute(f'SELECT 1 FROM {table} WHERE {key}=? LIMIT 1', (value,)).fetchone()
                         for table, key, value in (
                             ('approved_reply_batch_log', 'work_item_id', wid),
                             ('safe_workflow_reply_log', 'work_item_id', wid),
                             ('coupon_reply_log', 'inbox_event_id', row['inbox_event_id'])))
            if blocked or logged:
                skipped.append(wid)
                continue
            if apply:
                agent.audit(db, 'reviewed_membership_request_recovered', wid, {'before': dict(row)})
                db.execute('''UPDATE outreach_work_items SET status='pending',kind='human_review',
                    workflow_stage='human_review_blocked',risk_level='high',confidence=1.0,
                    template_key=NULL,draft_body='',proposed_action_json='[]',resolved_at=NULL,resolved_by=NULL,
                    decision_reason='已核对：旧会员请求因72小时规则被误关闭；恢复完整上下文审核，尚未授权发送或开通。',
                    updated_at=datetime('now') WHERE id=? AND status='superseded' ''', (wid,))
            recovered.append(wid)
        if apply:
            db.commit()
        else:
            db.rollback()
        return {'applied': apply, 'recovered': recovered, 'skipped': skipped}
    except Exception:
        db.rollback()
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    with agent.DEFAULT_LOCK.open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = agent.connect(agent.DEFAULT_DB)
        try:
            print(json.dumps(recover(db, args.apply)))
        finally:
            db.close()
