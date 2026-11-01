"""Read-only provider reconciliation for an explicitly reviewed reply batch."""
import argparse
import json
import sys
import urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import creator_data_agent as product, inbox_sync
from scripts.send_reviewed_simple_trial import IDS


def normalize(text):
    return ' '.join((text or '').split())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-manifest', type=Path)
    args = parser.parse_args()
    ids = IDS
    if args.review_manifest:
        ids = [int(entry['id']) for entry in json.loads(args.review_manifest.read_text())['reviews']]
        if len(ids) != len(set(ids)):
            parser.error('Duplicate work item IDs in review manifest')
    db = product.connect(product.DEFAULT_DB)
    token = product.refresh_access_token(db)
    confirmed = 0
    for index, wid in enumerate(ids):
        if index and index % 20 == 0:
            token = product.refresh_access_token(db)
        row = db.execute('''SELECT w.draft_body,e.sender_email,l.queued_at,l.status
            FROM outreach_work_items w JOIN inbox_events e ON e.id=w.inbox_event_id
            LEFT JOIN approved_reply_batch_log l ON l.work_item_id=w.id WHERE w.id=?''', (wid,)).fetchone()
        if row['status'] != 'sent':
            print(json.dumps({'id': wid, 'verified': False, 'local_status': row['status']}), flush=True)
            continue
        query = urllib.parse.urlencode({'$search': f'"to:{row["sender_email"]}"', '$top': '100',
                                        '$select': 'id,sentDateTime,body,toRecipients'})
        data = inbox_sync.graph_json(token, 'https://graph.microsoft.com/v1.0/me/mailFolders/sentitems/messages?' + query)
        matches = [m for m in data.get('value', [])
                   if inbox_sync.parse_timestamp(m['sentDateTime']) >= inbox_sync.parse_timestamp(row['queued_at'])
                   and normalize(inbox_sync.message_text(m)) == normalize(row['draft_body'])
                   and any((r.get('emailAddress') or {}).get('address', '').lower() == row['sender_email'].lower()
                           for r in m.get('toRecipients', []))]
        verified = len(matches) == 1 and not data.get('@odata.nextLink')
        confirmed += int(verified)
        print(json.dumps({'id': wid, 'verified': verified, 'exact_matches': len(matches)}), flush=True)
    print(json.dumps({'verified': confirmed, 'total': len(ids)}), flush=True)
