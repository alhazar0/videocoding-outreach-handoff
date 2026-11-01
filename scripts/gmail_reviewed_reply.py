"""Original-channel Gmail transport for explicitly reviewed replies only.

All Mail is opened read-only; a complete address/thread read, stable message
identity and the local review are required. SMTP uncertainty is never retried.
"""
from __future__ import annotations

import hashlib
import imaplib
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import getaddresses, format_datetime
from datetime import datetime, timezone

from scripts import outreach_scheduler as scheduler
from scripts import gmail_history_sync as history
from scripts import inbox_sync

SYSTEM_EVENTS = {'bounce', 'sender_restricted', 'delivery_delayed', 'delivery_failed', 'automated'}


def validate_route(db, row):
    event = db.execute('SELECT event_type FROM inbox_events WHERE id=?',
                       (row['inbox_event_id'],)).fetchone()
    if (not str(row['inbox_event_id']).startswith('gmail:') or
            not str(row['conversation_id']).startswith('gmail-thread:') or
            not row['internet_message_id'] or not event or event['event_type'] in SYSTEM_EVENTS):
        raise ValueError('Google reply requires a genuine original Google inbound')
    if not db.execute("""SELECT 1 FROM outreach_send_log WHERE contact_id=?
            AND sender_email=? AND provider='google_workspace_smtp' AND status='sent'""",
            (row['contact_id'], scheduler.GOOGLE_MAILBOX)).fetchone():
        raise ValueError('Google reply has no original Google outreach evidence')
    account = db.execute('SELECT status FROM outreach_sender_accounts WHERE sender_email=?',
                         (scheduler.GOOGLE_MAILBOX,)).fetchone()
    if not account or account['status'] != 'active':
        raise ValueError('original Google sender is not active')
    recent = db.execute("""SELECT max(when_sent) AS latest FROM (
        SELECT sent_at AS when_sent FROM outreach_send_log WHERE sender_email=? AND status='sent'
        UNION ALL
        SELECT l.sent_at AS when_sent FROM approved_reply_batch_log l
          JOIN outreach_audit_log a ON a.entity_id=cast(l.work_item_id AS TEXT)
          AND a.action='reviewed_google_reply_identity' WHERE l.status='sent')""",
        (scheduler.GOOGLE_MAILBOX,)).fetchone()
    if recent and recent['latest']:
        elapsed = (datetime.now(timezone.utc) - inbox_sync.parse_timestamp(recent['latest'])).total_seconds()
        if elapsed < 120:
            raise ValueError('original Google 120-second sending interval has not elapsed')


def all_mail_connection():
    m = imaplib.IMAP4_SSL('imap.gmail.com', 993,
                         ssl_context=ssl.create_default_context(), timeout=25)
    try:
        m.login(scheduler.GOOGLE_MAILBOX, scheduler.google_app_password())
        status, folders = m.list()
        if status != 'OK':
            raise ValueError('Google folder listing failed')
        folder = next((re.search(rb'("(?:[^"\\]|\\.)*")$', f).group(1).decode()
                       for f in folders if b'\\All' in f and
                       re.search(rb'("(?:[^"\\]|\\.)*")$', f)), None)
        if not folder or m.select(folder, readonly=True)[0] != 'OK':
            raise ValueError('Google All Mail is unavailable')
        return m
    except Exception:
        try:
            m.logout()
        except Exception:
            pass
        raise


def quoted(value):
    if any(ch in str(value) for ch in '\r\n\x00'):
        raise ValueError('invalid IMAP search value')
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def search(m, *criteria):
    status, data = m.uid('search', None, *criteria)
    if status != 'OK' or not data or data[0] is None:
        raise ValueError('Google history search failed')
    return set(data[0].split())


def mailbox_snapshot(row):
    m = all_mail_connection()
    try:
        uids = search(m, 'X-GM-THRID', str(row['conversation_id']).removeprefix('gmail-thread:'))
        for address in {row['sender_email'], row['email']}:
            uids |= search(m, 'OR', 'FROM', quoted(address), 'TO', quoted(address))
        if not uids or len(uids) > 200:
            raise ValueError('Google history is missing or exceeds the complete-review bound')
        messages = []
        ordered = sorted(uids, key=int)
        for offset in range(0, len(ordered), 25):
            requested = set(ordered[offset:offset + 25])
            status, parts = m.uid('fetch', b','.join(sorted(requested, key=int)).decode(),
                                  '(UID X-GM-THRID INTERNALDATE BODY.PEEK[])')
            if status != 'OK':
                raise ValueError('Google full-message fetch failed')
            seen = set()
            for part in parts:
                if not isinstance(part, tuple):
                    continue
                uid = re.search(rb'UID (\d+)', part[0])
                thread = re.search(rb'X-GM-THRID (\d+)', part[0])
                internal = re.search(rb'INTERNALDATE "([^"]+)"', part[0])
                if not uid or not thread or uid.group(1) not in requested:
                    raise ValueError('Google fetched message lacks expected identity')
                msg, body, when = history.parse_message(part[1], internal.group(1).decode() if internal else None)
                seen.add(uid.group(1))
                messages.append({'uid': uid.group(1).decode(),
                                 'thread': 'gmail-thread:' + thread.group(1).decode(),
                                 'message_id': str(msg.get('Message-ID') or ''),
                                 'from': [a.lower() for _, a in getaddresses([str(msg.get('From') or '')])],
                                 'to': [a.lower() for _, a in getaddresses([str(msg.get('To') or '')])],
                                 'date': when, 'body': body, 'subject': str(msg.get('Subject') or ''),
                                 'references': str(msg.get('References') or '')})
            if seen != requested:
                raise ValueError('Google full-message history is incomplete')
        return messages
    finally:
        try:
            m.logout()
        except Exception:
            pass


def check_messages(row, messages, unused):
    targets = [m for m in messages if m['message_id'] == row['internet_message_id']]
    if len(targets) != 1:
        raise ValueError('Google original message is missing or ambiguous')
    target = targets[0]
    if (target['thread'] != row['conversation_id'] or
            target['from'] != [row['sender_email'].lower()] or
            scheduler.GOOGLE_MAILBOX.lower() not in target['to'] or
            inbox_sync.parse_timestamp(target['date']) != inbox_sync.parse_timestamp(row['received_at']) or
            target['body'].strip() != row['body_text'].strip()):
        raise ValueError('Google original message differs from reviewed inbound')
    cutoff = inbox_sync.parse_timestamp(row['received_at'])
    for message in messages:
        if inbox_sync.parse_timestamp(message['date']) > cutoff:
            raise ValueError('Google provider has a newer address or thread message')
        if unused and scheduler.GOOGLE_MAILBOX.lower() in message['from'] and 'FREETEST100' in message['body']:
            raise ValueError('Google provider already has a coupon reply')
    return target


def check_mailbox(db, row, unused):
    validate_route(db, row)
    return check_messages(row, mailbox_snapshot(row), unused)


def build_message(row, body, target):
    message = EmailMessage()
    message['From'] = f'Alhazar Anwar <{scheduler.GOOGLE_MAILBOX}>'
    message['To'] = row['sender_email']
    subject = row['original_subject']
    message['Subject'] = subject if re.match(r'^re:', subject, re.I) else 'Re: ' + subject
    message['Date'] = format_datetime(datetime.now(timezone.utc))
    message['Message-ID'] = f'<videocoding-reviewed-{row["id"]}-{hashlib.sha256(body.encode()).hexdigest()[:20]}@outreach.videocoding.ai>'
    message['In-Reply-To'] = row['internet_message_id']
    refs = re.findall(r'<[^<>\r\n]+>', target['references'])
    message['References'] = ' '.join(dict.fromkeys(refs + [row['internet_message_id']]))
    message.set_content(body)
    return message


def submit(message):
    # Never open a second SMTP session after submission was attempted.
    try:
        with scheduler.gmail_smtp_connection(timeout=30) as smtp:
            smtp.login(scheduler.GOOGLE_MAILBOX, scheduler.google_app_password())
            refused = smtp.send_message(message,
                                        from_addr=scheduler.GOOGLE_MAILBOX,
                                        to_addrs=[str(message['To'])])
            if refused:
                return False, 'Google SMTP recipient refused; verify before any retry'
        return True, 'Google SMTP accepted; Message-ID=' + str(message['Message-ID'])
    except (OSError, smtplib.SMTPException) as error:
        return False, 'Google SMTP result uncertain: ' + type(error).__name__ + ': ' + str(error)[:300]
