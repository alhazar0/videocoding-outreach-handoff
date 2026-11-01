"""Keep explicitly reviewed unresolved service cases open after a mail acknowledgement.

Delivery attempts stay in the existing logs. Only resolving the service case
explicitly may release the work item; mailbox Sent imports cannot do so.
"""


def set_pending_case(db, work_item_id, contact_id, reason):
    if not str(reason or '').strip():
        raise ValueError('A pending service case needs its unresolved question')
    row = db.execute('SELECT contact_id FROM outreach_work_items WHERE id=?',
                     (work_item_id,)).fetchone()
    if not row or row[0] != contact_id:
        raise ValueError('Service case contact must match its reviewed work item')
    db.execute('''CREATE TABLE IF NOT EXISTS reviewed_service_cases(
        work_item_id INTEGER PRIMARY KEY, contact_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', reason TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    db.execute('''INSERT INTO reviewed_service_cases
        (work_item_id,contact_id,status,reason,created_at,updated_at)
        VALUES(?,?,'pending',?,datetime('now'),datetime('now'))
        ON CONFLICT(work_item_id) DO UPDATE SET status='pending',
          reason=excluded.reason,updated_at=datetime('now')''',
        (work_item_id,contact_id,reason))
    db.execute('''CREATE TRIGGER IF NOT EXISTS preserve_reviewed_service_case
        AFTER UPDATE OF status,template_key,kind,risk_level,resolved_at,resolved_by,decision_reason
        ON outreach_work_items
        WHEN EXISTS(SELECT 1 FROM reviewed_service_cases s
          WHERE s.work_item_id=NEW.id AND s.status='pending'
            AND (NEW.status<>'pending' OR NEW.template_key IS NOT NULL
              OR NEW.kind<>'human_review' OR NEW.risk_level<>'high'
              OR NEW.resolved_at IS NOT NULL OR NEW.resolved_by IS NOT NULL
              OR coalesce(NEW.decision_reason,'')<>s.reason))
        BEGIN
          UPDATE outreach_work_items SET status='pending',template_key=NULL,
            kind='human_review',risk_level='high',resolved_at=NULL,resolved_by=NULL,
            decision_reason=(SELECT reason FROM reviewed_service_cases WHERE work_item_id=NEW.id)
          WHERE id=NEW.id;
        END''')
    db.execute('''UPDATE outreach_work_items SET status='pending',template_key=NULL,
        kind='human_review',risk_level='high',resolved_at=NULL,resolved_by=NULL,
        decision_reason=?,updated_at=datetime('now') WHERE id=?''', (reason,work_item_id))
