"""Read Gmail Sent/Inbox via IMAP, without moving mail or changing read flags."""
from __future__ import annotations

import argparse
import email
import fcntl
import hashlib
import imaplib
import json
import re
import ssl
from datetime import datetime, timezone
from email import policy
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

if __package__:
    from .outreach_scheduler import DEFAULT_DB, GOOGLE_MAILBOX, connect, google_app_password
else:
    from outreach_scheduler import DEFAULT_DB, GOOGLE_MAILBOX, connect, google_app_password


def setting(db, key, value=None):
    if value is None:
        row = db.execute('SELECT value FROM outreach_settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else None
    db.execute("""INSERT INTO outreach_settings(key,value,updated_at) VALUES(?,?,datetime('now'))
      ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""", (key,str(value)))


def parse_message(raw, internal_date=None):
    msg=email.message_from_bytes(raw,policy=policy.default)
    body=msg.get_body(preferencelist=('plain','html'))
    text=body.get_content() if body else ''
    if body and body.get_content_type()=='text/html':
        import html
        text=html.unescape(re.sub('<[^>]+>',' ',text))
    try:
        when=parsedate_to_datetime(str(msg['Date']))
    except (ValueError,TypeError):
        when=None
    if when is None or when.tzinfo is None:
        if not internal_date: raise ValueError('message date lacks timezone and no provider timestamp is available')
        when=datetime.strptime(internal_date,'%d-%b-%Y %H:%M:%S %z')
    return msg,text,when.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')


def import_message(db, raw, thread_id, *, sent, internal_date=None):
    if __package__:
        from .inbox_sync import sync_inbox_message
    else:
        from inbox_sync import sync_inbox_message
    msg,text,when=parse_message(raw,internal_date)
    mid=str(msg.get('Message-ID') or '')
    identity=hashlib.sha256((mid or raw.hex()).encode()).hexdigest()
    source_id='gmail:'+identity
    sender=getaddresses([str(msg.get('From') or '')])[0][1].lower()
    conversation='gmail-thread:'+str(thread_id)
    if sent:
        if sender != GOOGLE_MAILBOX.lower(): return 'ignored'
        recipients=[a.lower() for _,a in getaddresses([str(msg.get('To') or '')])]
        matched=0
        for address in recipients:
            contact=db.execute('SELECT id FROM contacts WHERE lower(email)=?',(address,)).fetchone()
            if not contact: continue
            db.execute("""INSERT OR IGNORE INTO outreach_sent_messages(
              id,conversation_id,contact_id,recipient_email,subject,body_text,sent_at,synced_at)
              VALUES(?,?,?,?,?,?,?,datetime('now'))""",
              (source_id+':'+str(contact[0]),conversation,contact[0],address,str(msg.get('Subject') or ''),text,when))
            matched+=1
        return 'sent_matched' if matched else 'ignored'
    if mid and db.execute('SELECT 1 FROM inbox_events WHERE internet_message_id=?',(mid,)).fetchone(): return 'duplicate'
    if db.execute('SELECT 1 FROM inbox_events WHERE id=?',(source_id,)).fetchone(): return 'duplicate'
    # A structured delivery report identifies the failed recipient, not the first address in quoted headers.
    reports=[]
    if sender in ('mailer-daemon@googlemail.com','mailer-daemon@google.com'):
        for part in msg.walk():
            if part.get_content_type()=='message/delivery-status':
                reports.extend(block for block in part.get_payload() if block.get('Final-Recipient'))
    if reports:
        imported=0
        for index,report in enumerate(reports):
            address=str(report['Final-Recipient']).split(';')[-1].strip().lower()
            contact=db.execute('SELECT id FROM contacts WHERE lower(email)=?',(address,)).fetchone()
            if not contact: continue
            code=str(report.get('Status') or '')
            diagnostic=str(report.get('Diagnostic-Code') or '')
            delayed=str(report.get('Action') or '').lower()=='delayed' or code.startswith('4.')
            invalid=not delayed and (code in ('5.1.1','5.1.3','5.1.10') or bool(re.search(r'recipient does not exist|recipient not found|user unknown|no such user',diagnostic,re.I)))
            kind='bounce' if invalid else 'delivery_delayed' if delayed else 'delivery_failed'
            eid=source_id+':dsn:'+str(index)
            cursor=db.execute("""INSERT OR IGNORE INTO inbox_events(id,internet_message_id,conversation_id,
              received_at,sender_email,subject,body_preview,body_text,event_type,contact_id,matched_email,processed_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
              (eid,mid,conversation,when,sender,str(msg.get('Subject') or ''),f'Google 投递报告：{code} {diagnostic}'[:700],text+'\n'+diagnostic,kind,contact[0],address))
            if cursor.rowcount and invalid:
                db.execute("""UPDATE contacts SET validation_status='invalid',do_not_contact=1,
                  outreach_status=CASE WHEN outreach_status IN ('opted_out','closed') THEN outreach_status ELSE 'bounced' END,
                  next_action='Google 确认收件地址不存在；停止发送。',updated_at=datetime('now') WHERE id=?""",(contact[0],))
            imported+=cursor.rowcount
        return 'delivery_reports' if imported else 'duplicate'
    if sender==GOOGLE_MAILBOX.lower() or not db.execute('SELECT 1 FROM contacts WHERE lower(email)=?',(sender,)).fetchone():
        return 'ignored'
    # Retain genuine direct replies, but Graph transport must never receive a Gmail id.
    message={'id':source_id,'internetMessageId':mid,'conversationId':conversation,'receivedDateTime':when,
             'from':{'emailAddress':{'address':sender,'name':str(msg.get('From') or '')}},
             'subject':str(msg.get('Subject') or ''),'body':{'contentType':'text','content':text},
             'internetMessageHeaders':[{'name':'Auto-Submitted','value':str(msg.get('Auto-Submitted') or 'no')}]}
    imported,kind=sync_inbox_message(db,message)
    if imported:
        db.execute('UPDATE inbox_events SET body_text=? WHERE id=?',(text,source_id))
    return 'inbound_'+kind if imported else kind


def sync(db, *, limit=250, connection=None):
    if __package__:
        from .inbox_sync import ensure_inbox_schema
    else:
        from inbox_sync import ensure_inbox_schema
    ensure_inbox_schema(db)
    m=connection or imaplib.IMAP4_SSL('imap.gmail.com',993,ssl_context=ssl.create_default_context(),timeout=25)
    counts={}
    try:
        if connection is None: m.login(GOOGLE_MAILBOX,google_app_password())
        status,folders=m.list()
        if status!='OK': raise RuntimeError('Gmail folder listing failed')
        sent_folder=next((re.search(rb'("(?:[^"\\]|\\.)*")$',f).group(1).decode()
                          for f in folders if b'\\Sent' in f and re.search(rb'("(?:[^"\\]|\\.)*")$',f)),None)
        if not sent_folder: raise RuntimeError('Gmail Sent folder not found')
        for lane,folder in (('sent',sent_folder),('inbox','INBOX')):
            status,_=m.select(folder,readonly=True)
            if status!='OK': raise RuntimeError('Gmail folder selection failed: '+lane)
            validity=m.response('UIDVALIDITY')[1][0].decode()
            key='gmail_imap_'+lane+'_cursor'
            stored=json.loads(setting(db,key) or '{}')
            cursor=int(stored.get('uid',0)) if stored.get('validity')==validity else 0
            status,found=m.uid('search',None,'UID',f'{cursor+1}:*')
            if status!='OK': raise RuntimeError('Gmail search failed')
            uids=sorted(int(uid) for uid in found[0].split() if int(uid)>cursor)[:max(1,limit)]
            for offset in range(0,len(uids),40):
                batch=uids[offset:offset+40]
                status,parts=m.uid('fetch',','.join(map(str,batch)),'(UID X-GM-THRID INTERNALDATE BODY.PEEK[])')
                if status!='OK': raise RuntimeError('Gmail body fetch failed')
                fetched={}
                for part in parts:
                    if not isinstance(part,tuple):continue
                    uid_match=re.search(rb'UID (\d+)',part[0]); thread=re.search(rb'X-GM-THRID (\d+)',part[0])
                    if not uid_match or not thread:raise RuntimeError('Gmail response missing stable identity')
                    internal_date=re.search(rb'INTERNALDATE "([^"]+)"',part[0])
                    fetched[int(uid_match.group(1))]=(part[1],thread.group(1).decode(),internal_date.group(1).decode() if internal_date else None)
                if set(fetched)!=set(batch):raise RuntimeError('Gmail returned incomplete body batch; checkpoint retained')
                for uid in batch:
                    raw,thread,internal_date=fetched[uid]
                    result=import_message(db,raw,thread,sent=lane=='sent',internal_date=internal_date)
                    counts[result]=counts.get(result,0)+1
                setting(db,key,json.dumps({'validity':validity,'uid':batch[-1]}))
                db.commit()
            counts[lane+'_scanned']=len(uids)
        setting(db,'gmail_history_sync_status','active')
        setting(db,'gmail_history_sync_error','')
        setting(db,'gmail_history_sync_result',json.dumps(counts))
        db.execute("""INSERT INTO outreach_settings(key,value,updated_at) VALUES('gmail_history_sync_at',datetime('now'),datetime('now'))
          ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""")
        db.commit()
        return counts
    except BaseException:
        db.rollback()
        raise
    finally:
        try:m.logout()
        except (OSError,imaplib.IMAP4.error):pass


def run(db, *, limit=250):
    lock_path=Path(__file__).resolve().parents[1]/'.wrangler'/'gmail-history-sync.lock'
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return {'already_running':True}
        try:
            return sync(db,limit=limit)
        except Exception as exc:
            setting(db,'gmail_history_sync_status','error')
            setting(db,'gmail_history_sync_error',type(exc).__name__+': '+str(exc)[:500])
            db.commit()
            raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=250);args=parser.parse_args()
    db=connect(DEFAULT_DB)
    try:print(json.dumps(run(db,limit=args.limit),ensure_ascii=False))
    finally:db.close()
