"""Conversation freshness proof shared with the API; no mail is sent here."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from pathlib import Path
try:
    from reviewed_contact_hold import hold_for_work_item
except ImportError:
    from scripts.reviewed_contact_hold import hold_for_work_item

VERSION = '2026-10-08-context-v1'
SNAPSHOT_SQL = (Path(__file__).resolve().parents[1] / 'app/lib/reply-context-snapshot.sql').read_text()

def fingerprint(db: sqlite3.Connection, work_id: int) -> str | None:
    row = db.execute(SNAPSHOT_SQL, (work_id,)).fetchone()
    return hashlib.sha256((VERSION + '\n' + row[0]).encode()).hexdigest() if row else None

def context_hold_reason(db: sqlite3.Connection, work_id: int, *, expected_template: str | None = None, expected_draft: str | None = None) -> str | None:
    try:
        row = db.execute('SELECT extracted_json,template_key,status,draft_body FROM outreach_work_items WHERE id=?', (work_id,)).fetchone()
        if not row or row['status'] != 'pending':
            return 'context requires reanalysis: work item is no longer pending'
        hold = hold_for_work_item(db, work_id)
        if hold:
            return 'operator-reviewed contact hold requires fresh manual review: ' + hold
        if expected_template is not None and expected_template != str(row['template_key'] or ''):
            return 'context requires reanalysis: queued template changed'
        if expected_draft is not None and expected_draft != str(row['draft_body'] or ''):
            return 'context requires reanalysis: queued draft changed'
        decision = json.loads(row['extracted_json'] or '{}')
        audit = decision.get('context_audit') or {}
        if audit.get('version') != VERSION or not audit.get('fingerprint'):
            return 'context requires reanalysis: missing current conversation audit'
        if not audit.get('delivery_complete'):
            return 'context requires human review: delivered history was incomplete'
        if audit['fingerprint'] != fingerprint(db, work_id):
            return 'context requires reanalysis: conversation, account state or approved template changed'
        if not decision.get('context_complete') or decision.get('template_gap') or decision.get('missing_context') or decision.get('contradictions'):
            return 'context requires human review: context or template coverage remains unresolved'
        if decision.get('needs_human') or decision.get('questions'):
            return 'automation policy holds matched draft for human approval'
        expected_draft = hashlib.sha256((VERSION + '\n' + str(row['draft_body'] or '')).encode()).hexdigest()
        if audit.get('draft_fingerprint') != expected_draft:
            return 'context requires reanalysis: draft changed after context review'
        if decision.get('template_key') != row['template_key']:
            return 'context requires reanalysis: template changed after context review'
    except sqlite3.Error:
        return 'context requires reanalysis: conversation snapshot is unavailable'
    except (ValueError, TypeError, AttributeError):
        return 'context requires reanalysis: invalid context audit'
    return None
