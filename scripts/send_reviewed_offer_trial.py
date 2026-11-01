"""Execute only the 23 recipients approved in the 2026-09-05 conversation.

No broad matching or automated template approval. Each send rechecks local and
live mailbox state plus product access; ambiguous delivery is never retried.
"""
import argparse
import fcntl
import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import creator_data_agent as product
from scripts import send_approved_reply_batch as batch
from scripts import inbox_sync

IDS = [18717,19070,19104,19189,19261,19361,19366,19387,19405,19411,
       19412,19427,19504,19510,19612,19661,19715,19756,19782,19791,
       19858,19860,19897]
KEY = 'offer_trial_first_20260905'
BODY = """Hi {{channel_name}},

Thanks for sharing your offer! Before we start, we’d like you to try VideoCoding to see if it’s a good fit for your content and audience.

Try it with one of your own videos and let me know how it goes. Then we can discuss the next steps.

Here’s your code for one free month of VideoCoding Pro:

FREETEST100

To activate it:

1. Sign in or create an account at https://videocoding.ai
2. Click Upgrade to Pro
3. Click Add promotion code and enter FREETEST100
4. Make sure the total shows $0.00 before confirming

Let me know if you run into any issues — happy to help!

Best,
Alhazar"""


def check_local(db, wid, require_unused_access=True):
    r = db.execute('''SELECT w.*,e.received_at,e.conversation_id,e.sender_email,e.internet_message_id,
      e.subject original_subject,e.body_text,c.channel_name,c.email,c.do_not_contact,c.rate_info,
      p.registered_email,p.membership_status,p.coupon_sent_at,p.test_status,p.usage_verification_status
      FROM outreach_work_items w JOIN inbox_events e ON e.id=w.inbox_event_id
      JOIN contacts c ON c.id=w.contact_id LEFT JOIN creator_profiles p ON p.contact_id=c.id
      WHERE w.id=?''', (wid,)).fetchone()
    if not r or r['status']!='pending' or r['do_not_contact']:
        raise ValueError('not pending or suppressed')
    if require_unused_access and (r['coupon_sent_at'] or r['membership_status'] in ('active','trialing') or r['usage_verification_status'] in ('started','completed') or r['test_status'] in ('claimed','completed')):
        raise ValueError('already has access or started testing')
    if db.execute("SELECT 1 FROM approved_reply_batch_log WHERE work_item_id=?",(wid,)).fetchone():
        raise ValueError('prior batch attempt; no automatic retry')
    if db.execute('''SELECT 1 FROM inbox_events WHERE contact_id=? AND
        (datetime(received_at)>datetime(?) OR (datetime(received_at)=datetime(?) AND id>?))''',
        (r['contact_id'],r['received_at'],r['received_at'],r['inbox_event_id'])).fetchone():
        raise ValueError('newer inbound exists')
    if db.execute('''SELECT 1 FROM outreach_sent_messages WHERE contact_id=? AND
        (datetime(sent_at)>datetime(?) OR (? AND body_text LIKE '%FREETEST100%'))''',
        (r['contact_id'],r['received_at'],require_unused_access)).fetchone():
        raise ValueError('newer outbound or previous code')
    if db.execute("SELECT 1 FROM coupon_reply_log WHERE contact_id=? AND (status IN ('sending','delivery_unknown') OR (? AND status='sent'))",(r['contact_id'],require_unused_access)).fetchone():
        raise ValueError('prior coupon delivery or uncertainty')
    if db.execute("SELECT 1 FROM safe_workflow_reply_log WHERE contact_id=? AND status IN ('sending','delivery_unknown')",(r['contact_id'],)).fetchone():
        raise ValueError('unresolved other sender')
    return r


def mailbox_check(token, row, require_unused_access=True):
    # Direct provider reads protect against sync lag. Do not send if pagination
    # or a provider error means the complete thread cannot be verified.
    for folder,date_field in [('inbox','receivedDateTime'),('sentitems','sentDateTime')]:
        query=urllib.parse.urlencode({'$filter':"conversationId eq '"+row['conversation_id'].replace("'","''")+"'",
          '$select':f'id,{date_field},body', '$top':'100'})
        data=inbox_sync.graph_json(token,f'https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages?{query}')
        if data.get('@odata.nextLink'):
            raise ValueError('provider thread requires pagination')
        cutoff=inbox_sync.parse_timestamp(row['received_at'])
        for message in data.get('value',[]):
            when=inbox_sync.parse_timestamp(message.get(date_field))
            if when and when>cutoff:
                raise ValueError('provider has a newer '+folder+' message')
            if require_unused_access and folder=='sentitems' and 'FREETEST100' in (message.get('body') or {}).get('content',''):
                raise ValueError('provider already has a coupon reply')


def run(send):
    db=product.connect(product.DEFAULT_DB)
    batch.ensure_batch_schema(db)
    token=product.refresh_access_token(db)
    product_token=product.keychain_token()
    if send:
        db.execute('''INSERT INTO outreach_templates(template_key,name,stage,subject,body,risk_level,enabled,
          version,updated_at,approval_status,creation_reason) VALUES(?,?,?,'Re: {{original_subject}}',?,'high',1,1,
          datetime('now'),'approved',?) ON CONFLICT(template_key) DO NOTHING''',
          (KEY,'已报价 · 先测试适配受众（用户定稿）','coupon_ready',BODY,'User approved exact copy and explicit 23-recipient batch on 2026-09-05; no general auto-send rule.'))
        db.commit()
        saved=db.execute('SELECT body,approval_status,enabled FROM outreach_templates WHERE template_key=?',(KEY,)).fetchone()
        assert saved['body']==BODY and saved['approval_status']=='approved' and saved['enabled']
    results=[]
    last_send=0
    for wid in IDS:
        try:
            if send and last_send:
                time.sleep(max(0,30-(time.monotonic()-last_send)))
            row=check_local(db,wid)
            mailbox_check(token,row)
            status,payload=product.creator_lookup('https://videocoding.ai',product_token,row['registered_email'] or row['email'])
            if status!=200 or not payload.get('success'):
                raise ValueError('product status unavailable')
            creator=payload.get('creator') or {}
            if payload.get('found') and ((creator.get('subscription') or {}).get('status') in ('active','trialing') or
                int((creator.get('activity') or {}).get('totalTasks') or 0)>0):
                raise ValueError('product backend shows existing access or testing')
            row=check_local(db,wid)
            if not send:
                print(json.dumps({'id':wid,'ready':True}),flush=True)
                continue
            prepared=dict(row)
            prepared.update(template_key=KEY,template_version=1,template_subject='Re: {{original_subject}}',template_body=BODY)
            _,body=batch.render(prepared)
            batch.record_audit(db,'user_approved_offer_trial_review',wid,{'previous_template':row['template_key'],
                'review_source':'analysis/offer-trial-template-review-2026-09-05.md','product_lookup_confirmed':True})
            db.execute('UPDATE outreach_work_items SET template_key=?,draft_body=?,updated_at=datetime(\'now\') WHERE id=? AND status=\'pending\'',(KEY,body,wid))
            batch.record_sending(db,prepared)
            ok,detail=batch.send_reply(token,row['inbox_event_id'],body)
            last_send=time.monotonic()
            batch.record_result(db,prepared,ok,detail)
            results.append({'id':wid,'status':'sent' if ok else 'delivery_unknown'})
            print(json.dumps(results[-1]),flush=True)
            if not ok:
                break
        except Exception as error:
            # If an exception happened after recording an attempt, its sending
            # marker blocks retry and will be reconciled, never silently resent.
            results.append({'id':wid,'status':'held','reason':str(error)[:200]})
            print(json.dumps(results[-1]),flush=True)
    print(json.dumps({'send':send,'results':results}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--send',action='store_true')
    args=parser.parse_args()
    handles=[]
    try:
        for path in [batch.DEFAULT_LOCK,batch.SAFE_WORKFLOW_LOCK,batch.COUPON_LOCK]:
            handle=path.open('a+');handles.append(handle)
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        run(args.send)
    finally:
        for handle in handles:
            handle.close()
