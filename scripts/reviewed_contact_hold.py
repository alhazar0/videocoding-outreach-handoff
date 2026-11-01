"""Explicit operator holds survive mailbox imports and block automatic nudges.

They are not opt-outs. A new inbound stays available for full human review;
only an explicit operator decision may clear a hold.
"""


def active_hold(db, contact_id):
    if not contact_id or not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reviewed_contact_holds'"
    ).fetchone():
        return None
    row = db.execute(
        "SELECT reason FROM reviewed_contact_holds WHERE contact_id=? AND active=1",
        (int(contact_id),),
    ).fetchone()
    return str(row[0]) if row else None


def set_hold(db, contact_id, work_item_id, reason):
    if not reason or not str(reason).strip():
        raise ValueError('A reviewed hold needs a concrete reason')
    db.execute("""CREATE TABLE IF NOT EXISTS reviewed_contact_holds(
        contact_id INTEGER PRIMARY KEY, source_work_item_id INTEGER NOT NULL,
        reason TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("""INSERT INTO reviewed_contact_holds
        (contact_id,source_work_item_id,reason,active,created_at,updated_at)
        VALUES(?,?,?,1,datetime('now'),datetime('now'))
        ON CONFLICT(contact_id) DO UPDATE SET
        source_work_item_id=excluded.source_work_item_id,reason=excluded.reason,
        active=1,updated_at=datetime('now')""", (contact_id,work_item_id,reason))
    # Contacts are also updated by the web importer. Keep this invariant in
    # the shared database so an older writer cannot undo an operator hold.
    db.execute("""CREATE TRIGGER IF NOT EXISTS preserve_reviewed_contact_hold
        AFTER UPDATE OF next_action,followup1_due,followup2_due ON contacts
        WHEN coalesce(NEW.do_not_contact,0)=0
          AND EXISTS(SELECT 1 FROM reviewed_contact_holds h WHERE h.contact_id=NEW.id AND h.active=1
            AND (coalesce(NEW.next_action,'')<>h.reason OR NEW.followup1_due IS NOT NULL OR NEW.followup2_due IS NOT NULL))
        BEGIN
          UPDATE contacts SET next_action=(SELECT reason FROM reviewed_contact_holds WHERE contact_id=NEW.id AND active=1),
            followup1_due=NULL,followup2_due=NULL WHERE id=NEW.id;
        END""")


def hold_for_work_item(db, work_item_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reviewed_contact_holds'").fetchone():
        return None
    row = db.execute('SELECT contact_id FROM outreach_work_items WHERE id=?',
                     (work_item_id,)).fetchone()
    return active_hold(db, row[0]) if row else None
