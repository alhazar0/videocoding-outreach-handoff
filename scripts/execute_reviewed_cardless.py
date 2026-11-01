"""Explicit user-reviewed account grants and approved replies; no general auto mode."""
import argparse,fcntl,hashlib,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import creator_data_agent as a,send_approved_reply_batch as batch
from scripts import send_reviewed_offer_trial as guard
from scripts.send_reviewed_simple_trial import context_digest,check_addresses

MANUAL_NINE={19235,19203,19171,19051,18431,18088,17888,17797,17704,
 20054,18240,17994,17861,15440,19256}

def confirmed_missing(status,p):
 return status==200 and p.get('success') is True and p.get('found') is False

def eligible_account(status,p,email):
 c=p.get('creator') or {};s=c.get('subscription') or {};act=c.get('activity') or {}
 return (status==200 and p.get('success') is True and p.get('found') is True
  and str(c.get('email') or '').lower()==email.lower() and c.get('emailVerified') is True
  and s.get('status')=='none' and not s.get('stripeBacked') and not s.get('manualGrant')
  and act.get('totalTasks')==0)

def confirmed_access(status,p,email):
 c=p.get('creator') or {};s=c.get('subscription') or {}
 return (status==200 and p.get('success') is True and p.get('found') is True
   and str(c.get('email') or '').lower()==email.lower() and c.get('emailVerified') is True
   and a.confirmed_month_grant(s) and s.get('stripeBacked') is False)

def run(manifest,execute=False):
 d=a.connect(a.DEFAULT_DB);batch.ensure_batch_schema(d)
 d.execute('''CREATE TABLE IF NOT EXISTS reviewed_cardless_operations (
 work_item_id INTEGER PRIMARY KEY,account_email TEXT,operation TEXT NOT NULL,status TEXT NOT NULL,
 idempotency_key TEXT UNIQUE,detail_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL)''');d.commit()
 token=a.refresh_access_token(d);pt=a.keychain_token();results=[];last_send=0
 for index,r in enumerate(manifest['reviews']):
  wid=r['id'];grant_attempt=False;mail_attempt=False
  try:
   if wid in MANUAL_NINE and not (manifest.get('user_reauthorized_manual_execution') is True
      and r.get('manual_hold_reauthorized') is True):
    raise ValueError('previous explicit manual-only item')
   if r['action'] not in ('ask_email','account_not_found','grant_and_reply','grant_only'):raise ValueError('invalid action')
   if index and index%15==0:token=a.refresh_access_token(d)
   if execute and last_send:time.sleep(max(0,30-(time.monotonic()-last_send)))
   row=guard.check_local(d,wid,False)
   if context_digest(d,wid)!=r['context_digest']:raise ValueError('reviewed context changed')
   if d.execute('SELECT 1 FROM reviewed_cardless_operations WHERE work_item_id=?',(wid,)).fetchone():raise ValueError('prior operation; reconcile rather than retry')
   guard.mailbox_check(token,row,False);check_addresses(token,row,False)
   key={'ask_email':'ask_registered_email','account_not_found':'account_not_found'}.get(r['action'],'membership_activated')
   t=d.execute('SELECT * FROM outreach_templates WHERE template_key=? AND enabled=1 AND approval_status=?',(key,'approved')).fetchone()
   if not t or hashlib.sha256(t['body'].encode()).hexdigest()!=r['template_digest']:raise ValueError('approved template changed')
   email=r.get('registered_email') or row['registered_email'] or row['sender_email']
   status,p=a.creator_lookup('https://videocoding.ai',pt,email)
   if status!=200 or p.get('success') is not True:raise ValueError('account check unavailable')
   if r['action']=='ask_email':
    c=p.get('creator') or {}
    if p.get('found') and ((c.get('subscription') or {}).get('status') in ('active','trialing') or int((c.get('activity') or {}).get('totalTasks') or 0)>0):raise ValueError('possible existing access/testing; confirm identity manually')
   elif r['action']=='account_not_found':
    if not r.get('registered_email') or not confirmed_missing(status,p):raise ValueError('account absence not confirmed; do not request registration')
   elif not r.get('registered_email') or not eligible_account(status,p,email):raise ValueError('not an eligible exact verified unused account')
   row=guard.check_local(d,wid,False)
   if context_digest(d,wid)!=r['context_digest']:raise ValueError('context changed during checks')
   if not execute:
    results.append({'id':wid,'status':'ready','action':r['action']});print(json.dumps(results[-1]),flush=True);continue
   body=a.render_template(t['body'],email)
   batch.record_audit(d,'explicit_cardless_review',wid,{'manifest':manifest['authorization'],'review':r})
   if r['action'] in ('grant_and_reply','grant_only'):
    idem=hashlib.sha256(('reviewed-cardless-month:'+email.lower()).encode()).hexdigest()
    d.execute("INSERT INTO reviewed_cardless_operations VALUES(?,?,?,'granting',?,'{}',?)",(wid,email,r['action'],idem,a.iso_utc()));d.commit();grant_attempt=True
    gs,gp=a.api_json('https://videocoding.ai',pt,'/api/internal/outreach/membership-grants',{'email':email,'reason':'Creator explicitly requested cardless testing; user approved reviewed one-month grant','idempotencyKey':idem})
    if gs!=200 or gp.get('success') is not True:raise RuntimeError('grant result requires reconciliation')
    cs,cp=a.creator_lookup('https://videocoding.ai',pt,email)
    if not confirmed_access(cs,cp,email):raise RuntimeError('new free month not independently confirmed')
    sub=cp['creator']['subscription']
    d.execute("UPDATE reviewed_cardless_operations SET status='granted',detail_json=?,updated_at=? WHERE work_item_id=?",(json.dumps(sub),a.iso_utc(),wid))
    d.execute("UPDATE creator_profiles SET registered_email=?,account_status='verified',membership_status='active',membership_expires_at=?,updated_at=datetime('now') WHERE contact_id=?",(email,sub['currentPeriodEnd'],row['contact_id']))
    batch.record_audit(d,'reviewed_cardless_grant_confirmed',wid,{'email':email,'subscription':sub});d.commit();grant_attempt=False
    if r['action']=='grant_only':
     d.execute("UPDATE contacts SET next_action='已核验开通一个月；其他问题未被模板覆盖，待完整回复。',updated_at=datetime('now') WHERE id=?",(row['contact_id'],))
     d.execute("UPDATE outreach_work_items SET decision_reason=coalesce(decision_reason,'')||char(10)||?,updated_at=datetime('now') WHERE id=? AND status='pending'",('本轮已确认后台开通，其他问题仍待完整回复；不得重复开通或直接套 activated 模板。',wid));d.commit()
     results.append({'id':wid,'status':'granted_reply_held','expires_at':sub['currentPeriodEnd']});print(json.dumps(results[-1]),flush=True);continue
    # A new message after the grant must hold the reply without undoing or repeating the grant.
    guard.mailbox_check(token,row,False);check_addresses(token,row,False)
    row=guard.check_local(d,wid,False)
    if context_digest(d,wid)!=r['context_digest']:raise ValueError('post-grant context changed; reply held')
   prepared=dict(row);prepared.update(template_key=key,template_version=t['version'])
   d.execute("UPDATE outreach_work_items SET template_key=?,draft_body=?,updated_at=datetime('now') WHERE id=? AND status='pending'",(key,body,wid))
   batch.record_sending(d,prepared);mail_attempt=True
   ok,detail=batch.send_reply(token,row['inbox_event_id'],body);last_send=time.monotonic()
   batch.record_result(d,prepared,ok,detail)
   if not ok:raise RuntimeError('mail delivery unknown; do not retry')
   mail_attempt=False
   if r['action']=='ask_email':
    d.execute("UPDATE contacts SET next_action='等待博主确认 VideoCoding 注册邮箱；未开通。' WHERE id=?",(row['contact_id'],))
   elif r['action']=='account_not_found':
    d.execute("UPDATE contacts SET next_action='后台未找到指定账号；已请博主先注册，未开通。' WHERE id=?",(row['contact_id'],))
   else:d.execute("UPDATE reviewed_cardless_operations SET status='granted_reply_sent',updated_at=? WHERE work_item_id=?",(a.iso_utc(),wid))
   d.commit();results.append({'id':wid,'status':'sent','action':r['action']});print(json.dumps(results[-1]),flush=True)
  except Exception as e:
   state='delivery_unknown' if mail_attempt else 'grant_unknown' if grant_attempt else 'held'
   if grant_attempt:
    d.execute("UPDATE reviewed_cardless_operations SET status='grant_unknown',updated_at=? WHERE work_item_id=?",(a.iso_utc(),wid));d.commit()
   if execute:
    batch.record_audit(d,'reviewed_cardless_operation_held',wid,{'state':state,'reason':str(e)[:200]});d.commit()
   results.append({'id':wid,'status':state,'reason':str(e)[:200]});print(json.dumps(results[-1]),flush=True)
   if mail_attempt or grant_attempt:break
 print(json.dumps({'execute':execute,'results':results}),flush=True)

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--execute',action='store_true');parser.add_argument('--wait-lock',action='store_true',help='Wait behind other reviewed senders; never execute concurrently.');args=parser.parse_args()
 manifest=json.loads(args.manifest.read_text());ids=[r['id'] for r in manifest['reviews']]
 if len(ids)!=len(set(ids)):raise ValueError('duplicate reviewed id')
 handles=[]
 try:
  for p in [batch.DEFAULT_LOCK,batch.SAFE_WORKFLOW_LOCK,batch.COUPON_LOCK,a.DEFAULT_LOCK]:
   h=p.open('a+');handles.append(h);fcntl.flock(h.fileno(),fcntl.LOCK_EX | (0 if args.wait_lock else fcntl.LOCK_NB))
  run(manifest,args.execute)
 finally:
  for h in handles:h.close()
