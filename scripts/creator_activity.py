"""Exact recent-upload evidence from public YouTube channel feeds, with caching."""
import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

CHANNEL_ID = re.compile(r'UC[A-Za-z0-9_-]{22}')
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')
EXACT_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})')


def now_utc():
    return datetime.now(timezone.utc)


def parse_time(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def activity_days(db):
    row = db.execute("SELECT value FROM outreach_settings WHERE key='creator_recent_activity_days'").fetchone()
    return max(0, int(row[0])) if row else 0


def ensure_activity_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS creator_activity_cache(
      channel_url TEXT PRIMARY KEY,channel_id TEXT,status TEXT NOT NULL,
      published_at TEXT,video_url TEXT,checked_at TEXT NOT NULL,detail TEXT)''')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lead_discovery_channels'").fetchone():
        db.execute('CREATE INDEX IF NOT EXISTS discovery_channel_url_idx ON lead_discovery_channels(channel_url)')
    db.commit()


def recent(result, days=30, now=None):
    published = parse_time(result.get('published_at'))
    now = now or now_utc()
    return bool(result.get('status') == 'recent' and published
                and now - timedelta(days=days) <= published <= now)


def cached_activity(db, channel_url, days=30):
    row = db.execute('SELECT * FROM creator_activity_cache WHERE channel_url=?', (channel_url,)).fetchone()
    if not row:
        return None
    result = dict(row)
    checked = parse_time(result['checked_at'])
    ttl = timedelta(hours=48 if result['status']=='recent' else 72 if result['status']=='inactive' else 1)
    if not checked or checked > now_utc() or now_utc() - checked >= ttl:
        return None
    if result['status']=='recent' and not recent(result, days):
        return None  # Evidence has crossed the activity cutoff; refresh it.
    return result


def save_activity(db, channel_url, result):
    db.execute('''INSERT INTO creator_activity_cache VALUES(?,?,?,?,?,?,?)
      ON CONFLICT(channel_url) DO UPDATE SET channel_id=excluded.channel_id,status=excluded.status,
      published_at=excluded.published_at,video_url=excluded.video_url,
      checked_at=excluded.checked_at,detail=excluded.detail''',
      (channel_url, result.get('channel_id'), result['status'], result.get('published_at'),
       result.get('video_url'), result['checked_at'], result.get('detail')))
    db.commit()


def get_text(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'VideoCodingPublicCreatorDiscovery/1.0 (+https://videocoding.ai)'})
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.read(3_000_000).decode('utf-8', errors='replace')


def channel_id_from_page(page):
    match = re.search(r'\bytInitialData\s*=\s*', page)
    if not match:
        return None
    data, _ = json.JSONDecoder().raw_decode(page[match.end():])
    value = data.get('metadata', {}).get('channelMetadataRenderer', {}).get('externalId', '')
    return value if CHANNEL_ID.fullmatch(value) else None


def activity_from_feed(feed, channel_id, days=30, now=None):
    now = now or now_utc()
    # This fixed public feed format needs only entry channel IDs and ISO dates.
    # No XML entities, DTDs, external resources, or relative dates are evaluated.
    entries = []
    for entry in re.findall(r'<entry\b[^>]*>(.*?)</entry>', feed, flags=re.S):
        cid = re.search(r'<yt:channelId>([^<]+)</yt:channelId>', entry)
        video = re.search(r'<yt:videoId>([A-Za-z0-9_-]{11})</yt:videoId>', entry)
        published = re.search(r'<published>([^<]+)</published>', entry)
        date = parse_time(published[1]) if published else None
        if cid and cid[1] == channel_id and video and date and date <= now:
            entries.append((date, video[1]))
    result = {'channel_id': channel_id, 'status': 'unknown', 'published_at': None,
              'video_url': None, 'checked_at': now.isoformat(), 'detail': 'No verifiable published entry'}
    if entries:
        date, video = max(entries)
        result.update(status='recent' if date >= now-timedelta(days=days) else 'inactive',
                      published_at=date.isoformat(), video_url=f'https://www.youtube.com/watch?v={video}',
                      detail='Public channel feed; exact publication timestamp')
    return result


def page_data(page, name):
    match = re.search(r'\b' + re.escape(name) + r'\s*=\s*', page)
    if not match:
        return {}
    data, _ = json.JSONDecoder().raw_decode(page[match.end():])
    return data if isinstance(data, dict) else {}


def public_channel_video_ids(page, channel_id):
    """Only read video cards in this channel's selected Videos tab."""
    data = page_data(page, 'ytInitialData')
    identity = data.get('metadata', {}).get('channelMetadataRenderer', {}).get('externalId')
    if identity != channel_id:
        return []
    tabs = data.get('contents', {}).get('twoColumnBrowseResultsRenderer', {}).get('tabs', [])
    ids = []
    for tab in tabs:
        tab = tab.get('tabRenderer', {})
        if not tab.get('selected') or tab.get('title') != 'Videos':
            continue
        for item in tab.get('content', {}).get('richGridRenderer', {}).get('contents', []):
            content = item.get('richItemRenderer', {}).get('content', {})
            card = content.get('lockupViewModel', {})
            video = card.get('contentId') if card.get('contentType') == 'LOCKUP_CONTENT_TYPE_VIDEO' else None
            video = video or content.get('videoRenderer', {}).get('videoId')
            if isinstance(video, str) and VIDEO_ID.fullmatch(video) and video not in ids:
                ids.append(video)
    return ids[:2]


def activity_from_video_page(page, channel_id, video_id, days=30, now=None):
    """An owned public video with an exact publication timestamp can prove activity."""
    now = now or now_utc()
    result = {'channel_id': channel_id, 'status': 'unknown', 'published_at': None,
              'video_url': None, 'checked_at': now.isoformat(),
              'detail': 'No verified recent public video; limited sample cannot prove inactivity'}
    data = page_data(page, 'ytInitialPlayerResponse')
    video = data.get('videoDetails', {})
    meta = data.get('microformat', {}).get('playerMicroformatRenderer', {})
    stamp = meta.get('publishDate')
    if (data.get('playabilityStatus', {}).get('status') != 'OK'
            or video.get('videoId') != video_id or video.get('channelId') != channel_id
            or meta.get('externalChannelId') != channel_id
            or video.get('isPrivate') or video.get('isUpcoming') or meta.get('isUnlisted')
            or not isinstance(stamp, str) or not EXACT_TIMESTAMP.fullmatch(stamp)):
        return result
    published = parse_time(stamp)
    if published and now - timedelta(days=days) <= published <= now:
        result.update(status='recent', published_at=published.isoformat(),
                      video_url=f'https://www.youtube.com/watch?v={video_id}',
                      detail='Public video page; exact publication timestamp and matching channel identity')
    return result


def public_video_activity(channel_id, days=30):
    page = get_text(f'https://www.youtube.com/channel/{channel_id}/videos?hl=en')
    result = {'channel_id': channel_id, 'status': 'unknown', 'checked_at': now_utc().isoformat(),
              'detail': 'No verified recent public video; limited sample cannot prove inactivity'}
    for video_id in public_channel_video_ids(page, channel_id):
        page = get_text(f'https://www.youtube.com/watch?v={video_id}&hl=en')
        result = activity_from_video_page(page, channel_id, video_id, days)
        if result['status'] == 'recent':
            return result
    return result


def fetch_activity(channel_url, channel_id=None, days=30):
    result = {'status': 'unknown', 'checked_at': now_utc().isoformat()}
    try:
        parsed = urllib.parse.urlparse(channel_url or '')
        if parsed.scheme not in ('http','https') or parsed.hostname not in ('youtube.com','www.youtube.com','m.youtube.com'):
            return {**result, 'detail': 'Not a public YouTube channel URL'}
        if not channel_id or not CHANNEL_ID.fullmatch(channel_id):
            match = re.fullmatch(r'/channel/(UC[A-Za-z0-9_-]{22})/?', parsed.path)
            channel_id = match[1] if match else channel_id_from_page(get_text(
                'https://www.youtube.com' + parsed.path.rstrip('/') + '?hl=en'))
        if not channel_id or not CHANNEL_ID.fullmatch(channel_id):
            return {**result, 'detail': 'Channel identity unavailable'}
        try:
            feed = get_text('https://www.youtube.com/feeds/videos.xml?' + urllib.parse.urlencode({'channel_id': channel_id}))
        except urllib.error.HTTPError as error:
            # A failed public feed is not evidence of inactivity. Try the ordinary
            # public Videos page once, but do not work around auth/rate-limit responses.
            if error.code not in (404, 500, 502, 503, 504) or (error.headers or {}).get('Retry-After'):
                raise
            fallback = public_video_activity(channel_id, days)
            fallback['detail'] += f'; feed HTTP {error.code}'
            return fallback
        return activity_from_feed(feed, channel_id, days)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        return {**result, 'detail': f'{type(error).__name__}: {error}'[:250]}


def check_activity(db, channel_url, channel_id=None, days=30):
    ensure_activity_schema(db)
    cached = cached_activity(db, channel_url, days)
    if cached:
        return cached
    if not channel_id:
        try:
            row = db.execute('SELECT channel_id FROM lead_discovery_channels WHERE channel_url=? AND channel_id IS NOT NULL LIMIT 1', (channel_url,)).fetchone()
            channel_id = row[0] if row else None
        except sqlite3.OperationalError:
            pass
    result = fetch_activity(channel_url, channel_id, days)
    save_activity(db, channel_url, result)
    return result


def refresh_pending_activity(db, limit=100):
    """Warm activity evidence ahead of sending; never sends or approves copy."""
    days = activity_days(db)
    if not days:
        return {'checked':0}
    ensure_activity_schema(db)
    rows = db.execute('''SELECT DISTINCT c.channel_url FROM contacts c
      LEFT JOIN creator_activity_cache a ON a.channel_url=c.channel_url
      WHERE c.platform='YouTube' AND c.validation_status='send_ready'
        AND c.outreach_status='not_contacted' AND c.first_sent_at IS NULL AND c.do_not_contact=0
        AND c.initial_template_key IS NOT NULL AND c.planned_send_date IS NOT NULL
        AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=c.id AND l.campaign_kind='creator_initial')
        AND (a.channel_url IS NULL
          OR julianday(a.checked_at)<julianday('now')-(CASE a.status WHEN 'recent' THEN 48 WHEN 'inactive' THEN 72 ELSE 1 END)/24.0
          OR (a.status='recent' AND julianday(a.published_at)<julianday('now')-?))
      ORDER BY c.planned_send_date,c.id LIMIT ?''',(days,limit)).fetchall()
    candidates=[]
    for row in rows:
        found=db.execute('SELECT channel_id FROM lead_discovery_channels WHERE channel_url=? AND channel_id IS NOT NULL LIMIT 1',(row[0],)).fetchone()
        candidates.append((row[0],found[0] if found else None))
    counts={'checked':len(candidates),'recent':0,'inactive':0,'unknown':0}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (url,_), result in zip(candidates,pool.map(lambda item: fetch_activity(item[0],item[1],days),candidates)):
            save_activity(db,url,result)
            counts[result['status']]+=1
    return counts
