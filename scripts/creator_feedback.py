"""Evidence-backed feedback ledger; collecting feedback never sends email."""
import re
try:
    from scripts.reply_safety import current_message
except ModuleNotFoundError:
    from reply_safety import current_message

SCHEMA = """CREATE TABLE IF NOT EXISTS creator_feedback (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 inbox_event_id TEXT NOT NULL,
 contact_id INTEGER,
 sender_email TEXT NOT NULL,
 received_at TEXT NOT NULL,
 category TEXT NOT NULL,
 summary TEXT NOT NULL,
 evidence TEXT NOT NULL,
 review_state TEXT NOT NULL DEFAULT 'candidate' CHECK(review_state IN ('candidate','reviewed','dismissed')),
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','investigating','planned','fixed','closed')),
 target_version TEXT NOT NULL DEFAULT '',
 followup_status TEXT NOT NULL DEFAULT 'not_due' CHECK(followup_status IN ('not_due','ready','contacted','done')),
 notes TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(inbox_event_id,category)
)"""

PATTERNS = {
 'access': r'(card|payment).{0,80}(details|required|asking|declined|authenticate|accept)|(?:asking|requires?).{0,50}(?:card|visa|mastercard)|绑卡|信用卡',
 'performance': r'timed? out|timeout|1800s|too slow|quite slow|little (?:bit )?(?:slow|longer)|processing.{0,50}long|超时|太慢',
 'reference_input': r'reference.{0,100}(?:not|don.t|doesn.t|unable|detect)|not.{0,70}reference|参考.{0,40}(?:无法|不能|失败)',
 'quota': r'(?:allowance|quota|credits).{0,80}(?:used|exceed|exhaust|limit)|额度.{0,30}(?:用完|不足)',
 'verification': r'(?:not|fail|unable).{0,70}(?:verify|verifying|verification)|验证.{0,30}(?:失败|不了)',
 'language': r'(?:didn.t work|not work|poor|bad).{0,80}(?:indonesian|bahasa|language|translation)|(?:language|translation).{0,70}(?:issue|problem|wrong)',
 'positive': r'(?:tested|tried|used|generated).{0,600}(?:impressed|really useful|really like|amazing|works well|great fit)|测试.{0,100}(?:好用|满意|不错)',
 'negative_fit': r'(?:output|result).{0,100}(?:not|don.t|isn.t).{0,50}(?:fit|good)|测试.{0,60}(?:不适合|不好)',
}

def ensure_schema(db):
    db.execute(SCHEMA)

def collect(db, since=None):
    ensure_schema(db)
    query = "SELECT * FROM inbox_events WHERE event_type NOT IN ('bounce','automated','sender_restricted','delivery_delayed','delivery_failed')"
    args = ()
    if since:
        query += ' AND datetime(received_at)>=datetime(?)'
        args = (since,)
    added = 0
    for r in db.execute(query, args).fetchall():
        evidence = current_message(r['body_text'] or r['body_preview'] or '').strip()
        if not evidence:
            continue
        for category, pattern in PATTERNS.items():
            if not re.search(pattern, evidence, re.I | re.S):
                continue
            cursor = db.execute('''INSERT OR IGNORE INTO creator_feedback
                (inbox_event_id,contact_id,sender_email,received_at,category,summary,evidence,updated_at)
                VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)''', (r['id'],r['contact_id'],r['sender_email'],r['received_at'],
                category,'自动发现的反馈候选，需阅读上下文确认。',evidence))
            added += max(0,cursor.rowcount)
    return added
