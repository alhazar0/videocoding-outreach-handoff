#!/usr/bin/env python3
"""Recover missing legacy inbound bodies without sending or changing creator state."""
import argparse
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import creator_data_agent as product
from scripts import inbox_sync


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args()
    db = product.connect(product.DEFAULT_DB)
    rows = db.execute("""SELECT id,conversation_id,sender_email FROM inbox_events
        WHERE trim(coalesce(body_text,''))='' AND event_type IN ('reply','interested')
        ORDER BY received_at ASC LIMIT ?""", (max(1, min(200, args.limit)),)).fetchall()
    token = product.refresh_access_token(db)
    completed = failed = 0
    for row in rows:
        try:
            url = ('https://graph.microsoft.com/v1.0/me/messages/' +
                   urllib.parse.quote(row['id'], safe='') +
                   '?$select=id,conversationId,from,body')
            message = inbox_sync.graph_json(token, url)
            sender = ((message.get('from') or {}).get('emailAddress') or {}).get('address', '')
            if sender.lower() != str(row['sender_email']).lower():
                raise ValueError('sender mismatch')
            if row['conversation_id'] and message.get('conversationId') != row['conversation_id']:
                raise ValueError('conversation mismatch')
            if not (message.get('body') or {}).get('content'):
                raise ValueError('empty provider body')
            body = inbox_sync.message_text(message)
            with db:
                result = db.execute("UPDATE inbox_events SET body_text=? WHERE id=? AND trim(coalesce(body_text,''))=''",
                                    (body, row['id']))
                if result.rowcount:
                    db.execute("""UPDATE outreach_work_items SET confidence=NULL,updated_at=datetime('now')
                        WHERE inbox_event_id=? AND status='pending'""", (row['id'],))
            completed += result.rowcount
        except Exception as exc:
            failed += 1
            # Do not log bearer credentials or private message text.
            print(json.dumps({'recovered': completed, 'failed': failed,
                              'error_type': type(exc).__name__}), flush=True)
    print(json.dumps({'requested': len(rows), 'recovered': completed, 'failed': failed}), flush=True)


if __name__ == '__main__':
    main()
