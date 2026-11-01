"""Conservative, evidence-backed first-invitation routing; never sends mail.

Search queries/categories alone are not channel-content evidence. Keep unknown
segments and identity conflicts held. Only the three reviewed template bodies
below are eligible; changing copy requires another routing review.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
import re
import sqlite3
import time
from urllib.parse import urlparse, unquote
import urllib.parse
import urllib.error
import urllib.request

try:
    from .reply_safety import template_automation_mode
    from .creator_activity import activity_days, ensure_activity_schema, cached_activity, fetch_activity, save_activity, recent
except ImportError:
    from reply_safety import template_automation_mode
    from creator_activity import activity_days, ensure_activity_schema, cached_activity, fetch_activity, save_activity, recent

BODY_HASHES = {
    'first_contact_legacy_light': 'c5571e898b7167070524fe6809f28ca93d3e1cecd5528b4a2e298dc069773d44',
    'first_contact': 'a4e47df9b5bde9f04758be52e879c87ff110c750bdae507214935668fd2cc23d',
    'first_contact_t2v_knowledge_v1': 'a6ab223392e9987dd026b557858bc817d1a5e1fcf705115a688f49176429de59',
}
KNOWLEDGE = {'Education & Science', 'History & Society', 'Finance & Economics', 'News & Commentary'}
CREATORS = {'Creative & Media', 'Lifestyle & Reviews', 'Business & Marketing', 'AI & Tech'}
GENERAL_CREATOR_CATEGORIES = KNOWLEDGE | CREATORS | {'Podcast & Interviews', 'Health & Fitness', 'Career & Growth', 'Professional Services'}
EXCLUDE = re.compile(r'\b(kids?|nursery|toddler|cartoon|song|music video|gameplay|gaming|minecraft|roblox|dance|trailer|prank|compilation|highlights)\b', re.I)
EDITING = re.compile(r'\b(video editing|ai video|video editor|b.roll|talking.head|remotion|motion graphics)\b', re.I)
TEACHING = re.compile(r'\b(tutorial|how to|explained|explainers?|what is|what are|how does|how do|step.by.step|beginners?|course|learn)\b', re.I)
AI_TOOL = re.compile(r'\b(chatgpt|claude code|openai|ai tools?|ai video|text.to.video|video editing)\b', re.I)
KNOWLEDGE_SUBJECT = re.compile(r'\b(science|physics|biology|chemistry|maths?|mathematics|history|ancient|geography|economics|finance|financial|money|investing|language|grammar|vocabulary|english|toefl|ielts|visa[s]?|law|policy|artificial intelligence|ai|chatgpt|deepseek|python|programming|coding)\b', re.I)

def youtube_path(value):
    u = urlparse(value or '')
    if u.scheme not in ('http', 'https') or u.hostname not in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        return ''
    return unquote(u.path).rstrip('/').casefold()

def classify(contact, evidence):
    """Pure decision; all evidence linked to the email must agree on identity."""
    if contact['category'] == 'Needs Review':
        return None, 'category_needs_review'
    if not evidence:
        return None, 'missing_video_evidence'
    keys = set()
    for e in evidence:
        cp, ep = youtube_path(contact['channel_url']), youtube_path(e['channel_url'])
        id_match = bool(e['channel_id'] and cp == '/channel/' + e['channel_id'].casefold())
        if not cp or not ep or (cp != ep and not id_match) or contact['channel_name'].strip().casefold() != e['channel_name'].strip().casefold():
            return None, 'channel_identity_conflict'
        keys.add(e['channel_key'])
    if len(keys) != 1:
        return None, 'multiple_channels_for_email'
    e = evidence[0]
    title = e['matched_video_title'] or ''
    if not youtube_path(e['matched_video_url']).startswith('/watch') or not title.strip():
        return None, 'missing_video_evidence'
    if EXCLUDE.search(contact['channel_name'] + ' ' + title):
        return None, 'content_requires_review'
    category = contact['category']
    if category in KNOWLEDGE and TEACHING.search(title) and KNOWLEDGE_SUBJECT.search(title):
        return 'first_contact_t2v_knowledge_v1', 'knowledge_video_title'
    if category in CREATORS and EDITING.search(title) and (TEACHING.search(title) or re.search(r'\b(remotion|talking.head|workflow)\b', title, re.I)):
        return 'first_contact', 'video_creation_title'
    if category in (KNOWLEDGE | CREATORS) and AI_TOOL.search(title) and TEACHING.search(title):
        return 'first_contact', 'ai_tool_tutorial_title'
    if category in GENERAL_CREATOR_CATEGORIES:
        # This approved copy merely describes our tool and asks whether they want
        # to try it. It does not claim to have watched them or infer their intent.
        return 'first_contact_legacy_light', 'verified_creator_general_invitation'
    return None, 'no_reviewed_template_fit'

def valid_recipient(email):
    """Conservative unquoted mailbox syntax; never repair an address by guessing."""
    if not isinstance(email,str) or len(email)>254 or email!=email.strip() or email.count('@')!=1:
        return False
    local, domain=email.rsplit('@',1)
    # A mailto URL can leak leading encoded whitespace into the extracted
    # address. Hold it for source review; do not guess a decoded recipient.
    if re.match(r'%(?:20|09|0[a-d])', local, re.I):
        return False
    return (0<len(local)<=64 and not local.startswith('.') and not local.endswith('.') and '..' not in local
            and bool(re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+",local))
            and bool(re.fullmatch(r'(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}',domain)))

def candidates(db):
    return db.execute("""WITH blocked_emails AS (
        SELECT lower(old.email) email FROM outreach_send_log l JOIN contacts old ON old.id=l.contact_id WHERE l.campaign_kind='creator_initial'
        UNION SELECT lower(sender_email) FROM inbox_events
        UNION SELECT lower(recipient_email) FROM outreach_sent_messages
        UNION SELECT lower(email) FROM contacts GROUP BY lower(email) HAVING count(*)>1
      ), blocked_ids AS (
        SELECT contact_id FROM outreach_send_log WHERE campaign_kind='creator_initial'
        UNION SELECT contact_id FROM inbox_events
        UNION SELECT contact_id FROM outreach_sent_messages
      ) SELECT c.* FROM contacts c
      WHERE c.validation_status='send_ready' AND coalesce(c.subscribers,0)>=1000
        AND c.outreach_status='not_contacted' AND c.first_sent_at IS NULL
        AND c.do_not_contact=0 AND c.initial_template_key IS NULL
        AND c.platform='YouTube'
        AND lower(c.email) NOT IN (SELECT email FROM blocked_emails WHERE email IS NOT NULL)
        AND c.id NOT IN (SELECT contact_id FROM blocked_ids WHERE contact_id IS NOT NULL)
      ORDER BY c.fit_score DESC,c.id""").fetchall()

def verify_public_watch_page(review):
    """Embedding can be disabled on a public video; verify its watch metadata."""
    if youtube_path(review.get('video_url')) != '/watch':
        return False
    video_id = urllib.parse.parse_qs(urlparse(review['video_url']).query).get('v', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        return False
    request = urllib.request.Request(
        'https://www.youtube.com/watch?' + urllib.parse.urlencode({'v': video_id, 'hl': 'en'}),
        headers={'User-Agent': 'Mozilla/5.0 (compatible; VideoCodingCreatorVerifier/1.0)'},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            page = response.read(4_000_000).decode('utf-8', errors='replace')
        match = re.search(r'\bytInitialPlayerResponse\s*=\s*', page)
        if not match:
            return False
        player, _ = json.JSONDecoder().raw_decode(page[match.end():])
        details = player.get('videoDetails') or {}
        norm = lambda value: ' '.join(html.unescape(value or '').split()).casefold()
        expected_id = review.get('evidence_channel_id')
        if not expected_id:
            # Older audit records may carry a canonical /channel/ URL instead.
            channel_url = review.get('evidence_channel_url') or ''
            channel_path = urlparse(channel_url).path.rstrip('/') if youtube_path(channel_url) else ''
            expected_id = channel_path.removeprefix('/channel/') if channel_path.startswith('/channel/') else ''
        same_channel = bool(re.fullmatch(r'UC[A-Za-z0-9_-]{22}', expected_id)) and details.get('channelId') == expected_id
        return bool(
            same_channel
            and details.get('videoId') == video_id
            # Channel names and @handles can change. The exact canonical
            # channel ID and video ID are the identity proof here.
            and norm(details.get('title'))
            and not details.get('isPrivate', False)
            and player.get('playabilityStatus', {}).get('status') == 'OK'
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def verify_review(review):
    """Recheck that the video exists and belongs to the reviewed channel.

    YouTube localizes titles in oEmbed responses, so the live title can differ
    from the discovery title even when the video ID and author are unchanged.
    Identity is protected by the exact author name and canonical channel URL;
    require only a non-empty live title instead of byte-for-byte title equality.
    """
    url='https://www.youtube.com/oembed?' + urllib.parse.urlencode({'url':review['video_url'],'format':'json'})
    try:
        with urllib.request.urlopen(url,timeout=8) as response:
            data=json.load(response)
        norm=lambda s: ' '.join(html.unescape(s or '').split()).casefold()
        matches = (bool(norm(data.get('title')))
                and norm(data.get('author_name'))==norm(review['channel'])
                and youtube_path(data.get('author_url'))==youtube_path(review['evidence_channel_url']))
        return matches or verify_public_watch_page(review)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return verify_public_watch_page(review)
        return False
    except Exception:
        return False

def route_unassigned(db, *, apply=False, limit=50):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {'lead_discovery_emails', 'lead_discovery_channels', 'inbox_events', 'outreach_sent_messages', 'outreach_templates', 'outreach_audit_log'}
    if not required <= tables:
        return {'status': 'routing_schema_missing', 'assigned': 0}
    verified = {}
    attempted = {}
    verification = {'attempted': 0, 'passed': 0, 'failed': 0, 'cooling_down': 0, 'deferred': 0}
    if apply:
        preview = route_unassigned(db,apply=False)
        previous = db.execute("SELECT value FROM outreach_settings WHERE key='initial_template_verification_retry_after'").fetchone()
        try:
            retry_after = json.loads(previous[0]) if previous else {}
        except (ValueError, TypeError):
            retry_after = {}
        now = time.time()
        retry_after = {k:v for k,v in retry_after.items() if isinstance(v,(int,float)) and v>now}
        # Bound each scheduler tick; unprocessed candidates are picked up next run.
        # Failed metadata lookups cool down so the first 50 cannot starve later rows.
        available = [x for x in preview.get('reviews',[]) if str(x['id']) not in retry_after]
        proposed = available[:max(1,min(limit,200))]
        verification['cooling_down'] = len(preview.get('reviews',[])) - len(available)
        verification['deferred'] = len(available) - len(proposed)
        days = activity_days(db)
        activity_cache = {}
        if days:
            ensure_activity_schema(db)
            activity_cache = {r['id']: cached_activity(db, r['channel_url'], days) for r in proposed}
        def verify_candidate(review):
            activity = activity_cache.get(review['id'])
            if activity and not recent(activity, days):
                return False, activity
            if not verify_review(review):
                return False, None
            if days:
                activity = activity or fetch_activity(review['channel_url'], review.get('evidence_channel_id'), days)
                return recent(activity, days), activity
            return True, None
        with ThreadPoolExecutor(max_workers=4) as pool:
            for review, (ok, activity) in zip(proposed,pool.map(verify_candidate,proposed)):
                if activity:
                    save_activity(db, review['channel_url'], activity)
                    if not ok:
                        verification['recent_activity_held'] = verification.get('recent_activity_held',0)+1
                attempted[str(review['id'])] = None if ok else now+3600
                if ok:
                    verified[review['id']]=review
        verification.update(attempted=len(attempted), passed=len(verified), failed=len(attempted)-len(verified))
    try:
        approved = set()
        for key, digest in BODY_HASHES.items():
            t = db.execute('SELECT body,enabled,approval_status FROM outreach_templates WHERE template_key=?', (key,)).fetchone()
            if t and t['enabled'] and t['approval_status']=='approved' and template_automation_mode(db,key)=='auto' and hashlib.sha256(t['body'].encode()).hexdigest()==digest:
                approved.add(key)
        evidence = defaultdict(list)
        for r in db.execute('''SELECT e.contact_id,e.email,dc.* FROM lead_discovery_emails e
            JOIN lead_discovery_channels dc ON dc.channel_key=e.channel_key'''):
            evidence[(r['contact_id'], r['email'].casefold())].append(dict(r))
        held = Counter(); reviews = []
        for c in candidates(db):
            if not valid_recipient(c['email']):
                held['invalid_email_syntax'] += 1
                continue
            ev = evidence[(c['id'], c['email'].casefold())]
            key, reason = classify(c, ev)
            if key and key not in approved:
                key, reason = None, 'template_or_automation_not_approved_or_copy_changed'
            if not key:
                held[reason] += 1
                continue
            review = {'id':c['id'],'channel':c['channel_name'],'template_key':key,'reason':reason,
                      'channel_url':c['channel_url'],'video_title':ev[0]['matched_video_title'],
                      'video_url':ev[0]['matched_video_url'],'evidence_channel_url':ev[0]['channel_url'],
                      'evidence_channel_id':ev[0]['channel_id'],
                      'template_sha256':BODY_HASHES[key]}
            if apply:
                if verified.get(c['id']) != review:
                    held['live_video_unverified_or_changed_or_deferred'] += 1
                    continue
            reviews.append(review)
        result = {'status':'routed' if apply else 'preview','assigned':len(reviews) if apply else 0,
                  'eligible':len(reviews),'held':dict(held),'reviews':reviews,
                  'verification':verification}
        if apply:
            # All network calls and full-table evidence scans above are read
            # work.  Acquire the writer only for the small, bounded mutation
            # set so D1/API and reply workers are not blocked for minutes.
            db.execute('BEGIN IMMEDIATE')
            for review in reviews:
                changed = db.execute(
                    """UPDATE contacts SET initial_template_key=?,updated_at=datetime('now')
                       WHERE id=? AND initial_template_key IS NULL""",
                    (review['template_key'], review['id']),
                ).rowcount
                if changed != 1:
                    raise RuntimeError('routing target changed')
                db.execute("""INSERT INTO outreach_audit_log(action,entity_type,entity_id,actor,detail_json,created_at)
                  VALUES('initial_template_evidence_routed','contact',?,'approved_initial_router',?,datetime('now'))""",
                  (str(review['id']),json.dumps(review,ensure_ascii=False)))
            current=db.execute("SELECT value FROM outreach_settings WHERE key='initial_template_verification_retry_after'").fetchone()
            try:
                retries=json.loads(current[0]) if current else {}
            except (ValueError,TypeError):
                retries={}
            retries={k:v for k,v in retries.items() if isinstance(v,(int,float)) and v>time.time()}
            for key,until in attempted.items():
                if until:
                    retries[key]=until
                else:
                    retries.pop(key,None)
            db.execute("""INSERT INTO outreach_settings(key,value,updated_at) VALUES('initial_template_verification_retry_after',?,datetime('now'))
              ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",(json.dumps(retries),))
            compact = {k:v for k,v in result.items() if k!='reviews'}
            db.execute("""INSERT INTO outreach_settings(key,value,updated_at) VALUES('initial_template_router_last_result',?,datetime('now'))
              ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""", (json.dumps(compact),))
            db.commit()
        return result
    except Exception:
        if apply:
            db.rollback()
        raise

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        from .outreach_scheduler import connect, DEFAULT_DB
    except ImportError:
        from outreach_scheduler import connect, DEFAULT_DB
    print(json.dumps(route_unassigned(connect(DEFAULT_DB),apply=args.apply),ensure_ascii=False))
