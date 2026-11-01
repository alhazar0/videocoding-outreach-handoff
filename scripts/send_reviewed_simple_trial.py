"""Send only the original approved ten replies or an explicit reviewed manifest."""
import argparse
import hashlib
import fcntl
import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import send_reviewed_offer_trial as guard
from scripts import send_approved_reply_batch as batch
from scripts import creator_data_agent as product
from scripts import inbox_sync

IDS = (19903, 19856, 19783, 19749, 19748, 19746, 19743, 19722, 19706, 19698)
CONTEXT_REVIEW_KEYS = {
    'reviewed_scope_before_trial_20260913',
    'reviewed_payment_direction_20260913',
    'reviewed_scope_with_code_20260913',
    'reviewed_arabic_code_20260913',
    'reviewed_affiliate_code_20260913',
    'reviewed_rate_scope_with_code_20260921',
    'reviewed_faceless_supported_with_code_20260922',
    'reviewed_product_identity_20260922',
}
NON_CODE_INITIAL_KEYS = {
    'reviewed_scope_before_trial_20260913',
    'reviewed_payment_direction_20260913',
    'reviewed_product_identity_20260922',
}
POST_TEST_REVIEW_KEYS = {
    'reviewed_test_decline_processing_20260922',
    'reviewed_post_test_budget_ask_rate_20260922',
    'marketing_listed',
    'reviewed_completed_test_followup_20260922',
}

# Individual copy is allowed only in an explicit, hashed operator review. The
# reference template still determines all live account/stage requirements.
INDIVIDUAL_COPY_KEYS = {
    'reviewed_scope_before_trial_20260913',
    'rate_received', 'ask_rate', 'test_planned_ack', 'ask_registered_email',
    'campaign_details_after_coupon', 'reviewed_completed_test_followup_20260922',
    'product_question_hold', 'coupon_test_rate_received', 'coupon_test_campaign_details',
    'membership_activated',
}


def reviewed_body(review, default_body):
    if 'reviewed_body' not in review:
        return default_body
    body = review['reviewed_body']
    if review.get('template_key') not in INDIVIDUAL_COPY_KEYS:
        raise ValueError('individual copy outside reviewed reply scope')
    if not all(review.get(k) for k in ('context_digest', 'template_digest', 'review_note', 'questions_answered')):
        raise ValueError('individual copy requires complete explicit context review')
    if not isinstance(body, str) or not body.strip() or len(body) > 12000 or '{{' in body or '\\n' in body:
        raise ValueError('invalid individual reply body')
    if hashlib.sha256(body.encode()).hexdigest() != review.get('reviewed_body_digest'):
        raise ValueError('individual reply changed after review')
    if 'FREETEST100' in body and review['template_key'] not in ('campaign_details_after_coupon', 'coupon_test_rate_received', 'coupon_test_campaign_details'):
        raise ValueError('individual copy cannot introduce trial access')
    if review['template_key'] in ('coupon_test_rate_received', 'coupon_test_campaign_details'):
        disclosure = default_body.split('To activate it:', 1)
        if len(disclosure) != 2:
            raise ValueError('approved coupon activation disclosure unavailable')
        required = 'To activate it:' + disclosure[1].split("Once you’ve tested", 1)[0].split('Please try it with', 1)[0]
        if required.strip() not in body:
            raise ValueError('individual coupon reply must preserve approved activation and renewal disclosure')
    if review['template_key'] == 'membership_activated':
        activation = default_body.split('Done —', 1)
        if len(activation) != 2 or 'Once you’ve tried it,' not in activation[1]:
            raise ValueError('approved cardless activation disclosure unavailable')
        required = 'Done —' + activation[1].split('Once you’ve tried it,', 1)[0]
        if required.strip() not in body:
            raise ValueError('individual activation reply must preserve exact account and cardless expiry disclosure')
    return body


def render_reviewed_template(prepared, verified_registered_email=None):
    # The latest message may omit an email already verified in the account
    # guard. Render that exact address instead of stale or missing extraction.
    prepared = dict(prepared)
    if verified_registered_email:
        extracted = json.loads(prepared.get('extracted_json') or '{}')
        extracted['registered_email'] = verified_registered_email
        prepared['extracted_json'] = json.dumps(extracted)
    return batch.render(prepared)


def other_creator_message_digest(message):
    evidence = [message.get('id'), message.get('conversationId'), message.get('receivedDateTime') or message.get('sentDateTime'),
                ' '.join(inbox_sync.message_text(message).split())]
    return hashlib.sha256(json.dumps(evidence, ensure_ascii=False).encode()).hexdigest()


def reviewed_other_creator_message(db, row, message, review, folder='inbox'):
    if folder not in ('inbox', 'sentitems'):
        return False
    proofs = review.get('reviewed_other_creator_inbound' if folder == 'inbox' else 'reviewed_other_creator_outbound') or {}
    proof = proofs.get(message.get('id'))
    if not proof or not all(review.get(k) for k in
                            ('context_digest', 'template_digest', 'review_note', 'questions_answered')):
        return False
    source = ('SELECT contact_id,conversation_id,sender_email FROM inbox_events WHERE id=?'
              if folder == 'inbox' else
              'SELECT contact_id,conversation_id,recipient_email AS sender_email FROM outreach_sent_messages WHERE id=?')
    known = db.execute(source,
                       (message['id'],)).fetchone()
    return bool(known and known['contact_id'] and known['contact_id'] != row['contact_id'] and
                known['contact_id'] == proof.get('contact_id') and
                known['conversation_id'] == message.get('conversationId') and
                known['conversation_id'] != row['conversation_id'] and
                str(known['sender_email']).casefold() == str(row['sender_email']).casefold() and
                proof.get('review_note') and
                proof.get('message_digest') == other_creator_message_digest(message))


def check_addresses(token, row, require_unused_access=True, *, db=None, review=None):
    for folder, field, search in [('inbox', 'receivedDateTime', 'from'), ('sentitems', 'sentDateTime', 'to')]:
        query = urllib.parse.urlencode({'$search': f'"{search}:{row["sender_email"]}"',
                                       '$top': '100', '$select': f'id,conversationId,{field},body'})
        data = inbox_sync.graph_json(token, f'https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages?{query}')
        if data.get('@odata.nextLink'):
            raise ValueError('address history incomplete')
        for message in data.get('value', []):
            if (db is not None and review and
                    reviewed_other_creator_message(db, row, message, review, folder)):
                continue
            if inbox_sync.parse_timestamp(message[field]) > inbox_sync.parse_timestamp(row['received_at']):
                raise ValueError('newer address-level ' + folder + ' message')
            if require_unused_access and folder == 'sentitems' and 'FREETEST100' in (message.get('body') or {}).get('content', ''):
                raise ValueError('coupon already sent in another thread')


def check_reply_target(token, row):
    # An empty conversation search does not prove that an old reply target
    # still exists. Deleted or moved Graph IDs otherwise fail only at POST.
    message_id = str(row['inbox_event_id'] or '')
    if not message_id or message_id.startswith('gmail:'):
        raise ValueError('reply requires an existing Microsoft message target')
    query = urllib.parse.urlencode({'$select':
        'id,internetMessageId,conversationId,receivedDateTime,from'})
    message = inbox_sync.graph_json(token,
        'https://graph.microsoft.com/v1.0/me/messages/' +
        urllib.parse.quote(message_id, safe='') + '?' + query)
    actual_sender = ((message.get('from') or {}).get('emailAddress') or {}).get('address', '')
    if (message.get('id') != message_id or
            message.get('conversationId') != row['conversation_id'] or
            actual_sender.casefold() != str(row['sender_email']).casefold() or
            not message.get('receivedDateTime') or
            inbox_sync.parse_timestamp(message['receivedDateTime']) !=
            inbox_sync.parse_timestamp(row['received_at']) or
            (row['internet_message_id'] and
             message.get('internetMessageId') != row['internet_message_id'])):
        raise ValueError('provider reply target differs from reviewed inbound')


def context_digest(db, wid):
    row = db.execute('SELECT contact_id,inbox_event_id FROM outreach_work_items WHERE id=?', (wid,)).fetchone()
    if not row:
        raise ValueError('missing reviewed item')
    history = {
        'event': row['inbox_event_id'],
        'in': [tuple(r) for r in db.execute('SELECT id,received_at,body_text,body_preview FROM inbox_events WHERE contact_id=? ORDER BY id', (row['contact_id'],))],
        'out': [tuple(r) for r in db.execute('SELECT id,sent_at,body_text FROM outreach_sent_messages WHERE contact_id=? ORDER BY id', (row['contact_id'],))],
    }
    return hashlib.sha256(json.dumps(history, ensure_ascii=False).encode()).hexdigest()


def run(send, reviews=None, review_source='analysis/next-simple-trial-batch-2026-09-05.md', *, gmail=False):
    db = product.connect(product.DEFAULT_DB)
    batch.ensure_batch_schema(db)
    token = None if gmail else product.refresh_access_token(db)
    if gmail:
        from scripts import gmail_reviewed_reply as google_reply
        if not reviews:
            raise ValueError('Google replies require an explicit reviewed manifest')
    product_token = product.keychain_token()
    results = []
    last_send = 0
    reviews = reviews if reviews is not None else [{'id': wid, 'template_key': 'coupon_test'} for wid in IDS]
    for index, review in enumerate(reviews):
        wid = int(review['id'])
        attempted = False
        try:
            key = review['template_key']
            if key not in CONTEXT_REVIEW_KEYS and key not in POST_TEST_REVIEW_KEYS and key not in ('coupon_test', 'offer_trial_first_20260905', 'ask_rate', 'coupon_test_campaign_details', 'test_planned_ack', 'rate_received', 'test_claim_checking', 'ask_registered_email', 'account_not_found', 'membership_activated', 'campaign_details_after_coupon', 'coupon_test_rate_received', 'budget_under_5000_trial_20260913', 'coupon_test_t2v_knowledge_v1', 'product_question_hold'):
                raise ValueError('template outside reviewed reply scope')
            if key == 'product_question_hold' and not review.get('reviewed_body'):
                raise ValueError('product reply requires individually reviewed factual answer')
            unused = key in CONTEXT_REVIEW_KEYS or key in ('coupon_test', 'offer_trial_first_20260905', 'coupon_test_campaign_details', 'coupon_test_rate_received', 'budget_under_5000_trial_20260913', 'coupon_test_t2v_knowledge_v1')
            if key in CONTEXT_REVIEW_KEYS and (not review.get('context_digest') or not review.get('template_digest') or not review.get('questions_answered')):
                raise ValueError('context-specific reply requires reviewed history, exact copy and question coverage')
            if key in POST_TEST_REVIEW_KEYS and (not review.get('context_digest') or not review.get('template_digest')):
                raise ValueError('post-test reply requires reviewed history and exact copy')
            if key == 'coupon_test_t2v_knowledge_v1' and (not review.get('context_digest') or not review.get('template_digest')):
                raise ValueError('T2V reply requires an explicit reviewed manifest')
            if not gmail and index and index % 20 == 0:
                token = product.refresh_access_token(db)
            if send and last_send:
                time.sleep(max(0, (120 if gmail else 30) - (time.monotonic() - last_send)))
            row = guard.check_local(db, wid, unused)
            if key in CONTEXT_REVIEW_KEYS and row['rate_info'] and key != 'reviewed_rate_scope_with_code_20260921':
                raise ValueError('context-specific initial reply cannot ignore a supplied quote')
            if key == 'budget_under_5000_trial_20260913':
                if not review.get('context_digest') or not review.get('template_digest'):
                    raise ValueError('budget reply requires an explicit reviewed manifest')
                if row['rate_info']:
                    raise ValueError('budget trial template cannot re-ask an already supplied rate')
            if key == 'ask_rate' and (row['rate_info'] or not row['registered_email']):
                raise ValueError('rate already known or registration unconfirmed')
            if key == 'coupon_test_campaign_details' and row['rate_info']:
                raise ValueError('campaign template cannot ignore an existing quote')
            if review.get('context_digest') and context_digest(db, wid) != review['context_digest']:
                raise ValueError('reviewed context changed')
            if gmail:
                google_reply.check_mailbox(db, row, unused)
            else:
                check_reply_target(token, row)
                guard.mailbox_check(token, row, unused)
                if review.get('reviewed_other_creator_inbound') or review.get('reviewed_other_creator_outbound'):
                    check_addresses(token, row, unused, db=db, review=review)
                else:
                    check_addresses(token, row, unused)
            status, payload = product.creator_lookup('https://videocoding.ai', product_token,
                                                     row['registered_email'] or row['email'])
            if status != 200 or not payload.get('success'):
                raise ValueError('product lookup unavailable')
            creator = payload.get('creator') or {}
            if key == 'account_not_found':
                expected_email = str(review.get('expected_registered_email') or '').strip().casefold()
                if (not expected_email or expected_email != str(row['registered_email'] or '').casefold()
                        or payload.get('found') is not False
                        or str(payload.get('email') or '').casefold() != expected_email):
                    raise ValueError('account-not-found reply requires exact reviewed email and a current negative lookup')
            if key == 'membership_activated':
                expected_email = str(review.get('expected_registered_email') or '').strip().casefold()
                subscription = creator.get('subscription') or {}
                if (not expected_email or expected_email != str(row['registered_email'] or '').casefold()
                        or not all(review.get(k) for k in ('context_digest', 'template_digest', 'review_note', 'questions_answered'))
                        or not payload.get('found') or creator.get('emailVerified') is not True
                        or str(creator.get('email') or '').casefold() != expected_email
                        or not review.get('expected_product_user_id')
                        or creator.get('id') != review['expected_product_user_id']
                        or subscription.get('stripeBacked') is not False
                        or not product.confirmed_month_grant(subscription)
                        or not review.get('expected_membership_expires_at')
                        or subscription.get('currentPeriodEnd') != review['expected_membership_expires_at']):
                    raise ValueError('activation reply requires the exact reviewed verified account and confirmed cardless month grant')
            if key == 'ask_registered_email':
                if row['registered_email'] or (payload.get('found') and (
                        (creator.get('subscription') or {}).get('status') in ('active', 'trialing') or
                        int((creator.get('activity') or {}).get('totalTasks') or 0) > 0)):
                    raise ValueError('registration known or possible existing access; do not re-ask')
            if key in ('test_planned_ack', 'campaign_details_after_coupon'):
                prior_code = db.execute("SELECT 1 FROM outreach_sent_messages WHERE contact_id=? AND body_text LIKE '%FREETEST100%' LIMIT 1", (row['contact_id'],)).fetchone()
                if not row['coupon_sent_at'] and not prior_code and row['membership_status'] not in ('active', 'trialing'):
                    raise ValueError('no verified prior trial access invitation')
                if row['test_status'] in ('claimed', 'completed') or (payload.get('found') and int((creator.get('activity') or {}).get('totalTasks') or 0) > 0):
                    raise ValueError('testing state advanced; acknowledgement needs new review')
                if key == 'campaign_details_after_coupon' and (row['rate_info'] or not prior_code):
                    raise ValueError('post-coupon campaign reply requires prior code and no known quote')
            if key in ('rate_received', 'coupon_test_rate_received', 'reviewed_rate_scope_with_code_20260921') and not review.get('latest_rate_summary'):
                raise ValueError('reviewed quote required before acknowledging rates')
            if key == 'test_claim_checking' and (row['registered_email'] or
                    (payload.get('found') and int((creator.get('activity') or {}).get('completedTasks') or 0) > 0)):
                raise ValueError('account already known or test verified; do not re-ask account identity')
            if key == 'ask_rate' and (not payload.get('found') or int((creator.get('activity') or {}).get('completedTasks') or 0) < 1):
                raise ValueError('backend has not verified a completed test')
            if key in POST_TEST_REVIEW_KEYS and (not payload.get('found') or int((creator.get('activity') or {}).get('completedTasks') or 0) < 1):
                raise ValueError('backend has not verified the completed test required by this reply')
            if unused and payload.get('found') and ((creator.get('subscription') or {}).get('status') in ('active', 'trialing')
                                        or int((creator.get('activity') or {}).get('totalTasks') or 0) > 0):
                raise ValueError('already has access or testing activity')
            row = guard.check_local(db, wid, unused)
            if key in ('account_not_found', 'membership_activated') and str(row['registered_email'] or '').casefold() != expected_email:
                raise ValueError('registered email changed during account lookup')
            template = db.execute('SELECT * FROM outreach_templates WHERE template_key=?', (key,)).fetchone()
            if not template or not template['enabled'] or template['approval_status'] != 'approved':
                raise ValueError('template not approved')
            if review.get('template_digest') and hashlib.sha256(template['body'].encode()).hexdigest() != review['template_digest']:
                raise ValueError('reviewed template changed')
            if review.get('context_digest') and context_digest(db, wid) != review['context_digest']:
                raise ValueError('context changed during provider checks')
            prepared = dict(row)
            prepared.update(template_key=key, template_version=template['version'],
                            template_subject=template['subject'], template_body=template['body'])
            _, body = render_reviewed_template(
                prepared, expected_email if key in ('account_not_found', 'membership_activated') else None)
            body = reviewed_body(review, body)
            if not unused:
                normalized = ' '.join(body.split())
                if any(' '.join((h['body_text'] or '').split()) == normalized for h in db.execute(
                        'SELECT body_text FROM outreach_sent_messages WHERE contact_id=?', (row['contact_id'],))):
                    raise ValueError('identical acknowledgement already sent; do not loop')
            if unused and key not in NON_CODE_INITIAL_KEYS and 'FREETEST100' not in body:
                raise ValueError('fixed coupon missing')
            # A dry run must validate the same quote snapshot as a real send.
            # Otherwise an existing unpinned quote is reported ready, then held
            # only after delivery starts for the rest of the reviewed batch.
            if review.get('latest_rate_summary'):
                current_rate = db.execute('SELECT rate_info FROM contacts WHERE id=?', (row['contact_id'],)).fetchone()['rate_info']
                if review.get('stored_rate_digest'):
                    current_digest = hashlib.sha256(str(current_rate or '').encode()).hexdigest()
                    if current_digest != review['stored_rate_digest']:
                        raise ValueError('quote changed since review')
                elif current_rate != review.get('stored_rate_info'):
                    raise ValueError('quote changed since review')
            if send:
                if review.get('latest_rate_summary'):
                    db.execute("UPDATE contacts SET rate_info=?,updated_at=datetime('now') WHERE id=?",
                               (review['latest_rate_summary'], row['contact_id']))
                    batch.record_audit(db, 'reviewed_creator_quote_updated', wid, {
                        'previous_rate_info': current_rate,
                        'latest_rate_summary': review['latest_rate_summary'],
                        'review_source': review_source, 'accepted': False})
                if review.get('quoted_rates'):
                    db.execute("UPDATE contacts SET rate_info=?,updated_at=datetime('now') WHERE id=? AND coalesce(rate_info,'')=''",
                               (review['quoted_rates'], row['contact_id']))
                    batch.record_audit(db, 'reviewed_creator_quote_recorded', wid, {
                        'quoted_rates': review['quoted_rates'], 'accepted': False})
                batch.record_audit(db, 'user_approved_simple_trial_review', wid, {
                    'review_source': review_source,
                    'context_digest': review.get('context_digest'),
                    'reviewed_body_digest': review.get('reviewed_body_digest'),
                    'review_note': review.get('review_note'),
                    'previous_template': row['template_key'], 'product_lookup_confirmed': True})
                db.execute("UPDATE outreach_work_items SET template_key=?,draft_body=?,updated_at=datetime('now') WHERE id=? AND status='pending'", (key, body, wid))
                if gmail:
                    # Repeat the live history check after the product query,
                    # then commit the stable outgoing identity before SMTP.
                    target = google_reply.check_mailbox(db, row, unused)
                    google_message = google_reply.build_message(row, body, target)
                    batch.record_audit(db, 'reviewed_google_reply_identity', wid, {
                        'message_id': str(google_message['Message-ID']),
                        'in_reply_to': row['internet_message_id'],
                        'sender': google_reply.scheduler.GOOGLE_MAILBOX,
                        'recipient': row['sender_email'], 'initial_contact': False})
                batch.record_sending(db, prepared, gmail=gmail) if gmail else batch.record_sending(db, prepared)
                attempted = True
                ok, detail = google_reply.submit(google_message) if gmail else batch.send_reply(token, row['inbox_event_id'], body)
                last_send = time.monotonic()
                batch.record_result(db, prepared, ok, detail)
                if ok and key in NON_CODE_INITIAL_KEYS:
                    db.execute("UPDATE contacts SET next_action='已回复预算和合作问题，等待对方决定是否愿意试用；尚未发码或确认合作，不自动催测试。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'ask_rate':
                    db.execute("UPDATE contacts SET next_action='等待博主提供推广报价及制作周期；未承诺合作。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'rate_received':
                    db.execute("UPDATE contacts SET next_action='报价已记录，等待营销团队人工评估匹配的推广机会；未确认合作。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'test_claim_checking':
                    db.execute("UPDATE contacts SET next_action='等待博主确认测试所用注册邮箱，再核实实际使用记录；未确认推广。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'reviewed_test_decline_processing_20260922':
                    db.execute("UPDATE contacts SET outreach_status='closed',reply_status='not_interested',do_not_contact=1,next_action='博主完成测试后因处理时间不适合而拒绝合作；反馈已记录，停止跟进。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'reviewed_post_test_budget_ask_rate_20260922':
                    db.execute("UPDATE contacts SET next_action='已告知单个博主预算低于5000美元；等待对方提供YouTube和TikTok的形式、交付内容及报价，尚未确认合作。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'marketing_listed':
                    db.execute("UPDATE contacts SET next_action='已完成测试并记录报价；已加入推广候选名单，等待营销团队匹配活动，尚未确认合作。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'reviewed_completed_test_followup_20260922':
                    db.execute("UPDATE contacts SET next_action='后台确认已完成测试；已询问测试反馈，并在适合受众时请求推广报价和制作周期。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'ask_registered_email':
                    db.execute("UPDATE contacts SET next_action='等待博主确认注册邮箱，再由人工处理免绑卡开通；尚未开通。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'account_not_found':
                    db.execute("UPDATE contacts SET next_action='已核验提供的注册邮箱尚无账号；等待博主完成注册后再核验免绑卡试用，尚未开通。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'membership_activated':
                    db.execute("UPDATE contacts SET next_action='后台已确认免绑卡一个月试用，已发送开通确认；等待真实视频测试反馈，未确认付费合作。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                if ok and key == 'product_question_hold':
                    db.execute("UPDATE contacts SET next_action='已根据完整上下文回复产品问题；等待对方补充排查信息或确认情况，未承诺修复日期。',updated_at=datetime('now') WHERE id=?", (row['contact_id'],))
                    db.commit()
                results.append({'id': wid, 'status': 'sent' if ok else 'delivery_unknown'})
                print(json.dumps(results[-1]), flush=True)
                if not ok:
                    break
            else:
                results.append({'id': wid, 'status': 'ready'})
                print(json.dumps(results[-1]), flush=True)
        except Exception as error:
            results.append({'id': wid, 'status': 'delivery_unknown' if attempted else 'held', 'reason': str(error)[:200]})
            print(json.dumps(results[-1]), flush=True)
            if attempted:
                break
    print(json.dumps({'send': send, 'results': results}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--review-manifest', type=Path)
    parser.add_argument('--wait-lock', action='store_true', help='Queue behind an existing reviewed sender; never send concurrently.')
    parser.add_argument('--gmail', action='store_true', help='Reply only to original Google inbound via the original Google sender.')
    args = parser.parse_args()
    handles = []
    try:
        # Google accepts only Google inbound IDs; Microsoft transports reject
        # those IDs. Serialize Google replies independently so its conversations
        # do not wait behind a long Microsoft backlog. Per-contact freshness,
        # unique delivery markers and Google's 120-second interval still apply.
        locks = [batch.ROOT / '.wrangler' / 'gmail-reviewed-replies.lock'] if args.gmail else [
            batch.DEFAULT_LOCK, batch.SAFE_WORKFLOW_LOCK, batch.COUPON_LOCK]
        for path in locks:
            handle = path.open('a+')
            handles.append(handle)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if args.wait_lock else fcntl.LOCK_NB))
        reviews = None
        if args.review_manifest:
            reviews = json.loads(args.review_manifest.read_text())['reviews']
            if len({r['id'] for r in reviews}) != len(reviews) or any(not r.get('context_digest') or not r.get('template_digest') for r in reviews):
                raise ValueError('invalid explicit review manifest')
        run(args.send, reviews, str(args.review_manifest) if args.review_manifest else 'analysis/next-simple-trial-batch-2026-09-05.md', gmail=args.gmail)
    finally:
        for handle in handles:
            handle.close()
