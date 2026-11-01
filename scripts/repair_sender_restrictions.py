"""Audit and repair NDRs misclassified as recipient failures; never requeue mail."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

if __package__:
    from .mail_delivery import is_sender_restriction, record_restriction, RESTRICTION_SUMMARY
    from .outreach_scheduler import DEFAULT_DB, connect
else:
    from mail_delivery import is_sender_restriction, record_restriction, RESTRICTION_SUMMARY
    from outreach_scheduler import DEFAULT_DB, connect


def candidates(db):
    return [dict(row) for row in db.execute("SELECT * FROM inbox_events WHERE event_type='bounce'")
            if is_sender_restriction(str(row['sender_email']), str(row['body_text'] or ''))]


def repair(db, events):
    if not events:
        return {"events": 0, "contacts_reclassified": 0, "contacts_preserved": 0}
    record_restriction(db, max(row['received_at'] for row in events))
    for row in events:
        db.execute("UPDATE inbox_events SET event_type='sender_restricted',body_preview=? WHERE id=? AND event_type='bounce'",
                   (RESTRICTION_SUMMARY, row['id']))
    changed = preserved = 0
    for cid in sorted({row['contact_id'] for row in events if row['contact_id']}):
        contact = db.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
        if not contact:
            continue
        # A separate hard bounce/opt-out or manual suppression must survive.
        other_failure = db.execute("SELECT 1 FROM inbox_events WHERE contact_id=? AND event_type IN ('bounce','opted_out') LIMIT 1", (cid,)).fetchone()
        if (other_failure or contact['outreach_status'] in ('opted_out', 'closed')
                or contact['reply_status'] == 'not_interested'
                or contact['validation_status'] != 'invalid'
                or 'Microsoft 365 sync: bounce' not in str(contact['notes'] or '')):
            preserved += 1
            continue
        # Do not claim the mailbox is verified or restore its sending eligibility.
        # Keep first_sent_at, do_not_contact, existing replies and all send logs.
        db.execute("""UPDATE contacts SET validation_status='needs_validation',
          outreach_status=CASE WHEN outreach_status='bounced' THEN 'sender_restricted' ELSE outreach_status END,
          next_action=CASE WHEN reply_status='no_reply' THEN ? ELSE next_action END,
          notes=coalesce(notes,'') || char(10) || 'Sender restriction audit: recipient validity unknown; prior suppression retained; no automatic resend.',
          updated_at=datetime('now') WHERE id=?""", (RESTRICTION_SUMMARY, cid))
        changed += 1
    return {"events": len(events), "distinct_contacts": len({row['contact_id'] for row in events if row['contact_id']}),
            "contacts_reclassified": changed, "contacts_preserved": preserved,
            "first_seen": min(row['received_at'] for row in events), "last_seen": max(row['received_at'] for row in events),
            "automatic_resends": 0, "suppression_flags_cleared": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    db = connect(args.db)
    try:
        if not args.apply:
            rows = candidates(db)
            print(json.dumps({'events':len(rows), 'contacts':len({r['contact_id'] for r in rows if r['contact_id']})}))
            return
        if not args.backup:
            parser.error('--apply requires an unused --backup path')
        db.execute('BEGIN IMMEDIATE')
        rows = candidates(db)
        contacts = [dict(db.execute('SELECT * FROM contacts WHERE id=?',(cid,)).fetchone())
                    for cid in sorted({r['contact_id'] for r in rows if r['contact_id']})]
        fd = os.open(args.backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump({'events': rows, 'contacts': contacts}, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        result = repair(db, rows)
        db.execute("""INSERT INTO outreach_audit_log(action,entity_type,entity_id,actor,detail_json,created_at)
          VALUES('sender_restriction_reclassified','system','microsoft_550_5_1_8','operator_recovery',?,datetime('now'))""",
                   (json.dumps(result),))
        db.commit()
        print(json.dumps(result))
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    main()
