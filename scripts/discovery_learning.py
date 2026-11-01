"""Explainable search allocation from measured, deduplicated candidate yield."""
from datetime import datetime, timedelta

METRIC_COLUMNS = ('eligible_contacts', 'active_channels', 'inactive_channels',
                  'unknown_activity', 'metrics_version')
# Existing talking-head shards predate the creator_/knowledge_ namespaces.
# Keep the same partition in learning and claiming so these approved search
# tasks cannot become unreachable when all workers have a specific focus.
LEGACY_KNOWLEDGE = (
    'business', 'career', 'dev', 'education', 'finance', 'language', 'leadership',
    'marketing', 'news', 'productivity', 'saas', 'sales', 'science', 'startup',
    'study', 'tech',
)
LEGACY_CREATOR = (
    'creator', 'design', 'film', 'photo', 'podcast', 'video',
    'zh', 'es', 'pt', 'fr', 'de', 'ja', 'ko', 'hi', 'id', 'ms', 'fil', 'ro', 'fa', 'sw',
)

def legacy_focus_sql(names):
    return 'j.tier_key IN (' + ','.join("'talking_head_" + name + "'" for name in names) + ')'

FOCUS_SQL = {
    'ai': 'j.tier<=2',
    'creator': "j.tier=3 AND (substr(j.tier_key,1,8)='creator_' OR " + legacy_focus_sql(LEGACY_CREATOR) + ')',
    'knowledge': "j.tier=3 AND (substr(j.tier_key,1,10)='knowledge_' OR " + legacy_focus_sql(LEGACY_KNOWLEDGE) + ')',
}


def ensure_learning_schema(db):
    columns = {r[1] for r in db.execute('PRAGMA table_info(lead_discovery_runs)')}
    for name in METRIC_COLUMNS:
        if name not in columns:
            db.execute(f'ALTER TABLE lead_discovery_runs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
    for name in ('selection_mode', 'selection_reason'):
        if name not in columns:
            db.execute(f'ALTER TABLE lead_discovery_runs ADD COLUMN {name} TEXT')
    db.execute('CREATE INDEX IF NOT EXISTS discovery_learning_runs_idx '
               'ON lead_discovery_runs(started_at,job_id) WHERE metrics_version=1')
    db.execute('CREATE INDEX IF NOT EXISTS discovery_worker_runs_idx '
               'ON lead_discovery_runs(worker_key,id)')
    db.execute('CREATE INDEX IF NOT EXISTS discovery_job_lease_idx '
               'ON lead_discovery_jobs(status,lease_until)')
    db.execute("CREATE INDEX IF NOT EXISTS discovery_channel_lease_idx "
               "ON lead_discovery_channels(scan_lease_until) WHERE scan_status='scanning'")


def family_score(stats):
    """Smooth small samples; count qualified new contacts per worker-hour."""
    # One prior candidate per ten minutes avoids a lucky short run dominating.
    rate = 3600 * (stats['eligible'] + 1) / (stats['seconds'] + 600)
    novelty = (stats['new_channels'] + 5) / (stats['channels'] + 10)
    reliability = (stats['active'] + stats['inactive'] + 2) / (
        stats['active'] + stats['inactive'] + stats['unknown'] + 2)
    return rate * (0.5 + 0.5 * novelty) * reliability


def learned_family(db, focus, worker_key, now):
    """Keep one in four claims for coverage; exploit only measured successes.

    Called inside the claim transaction, so workers cannot crowd the same family.
    A family is a topic plus language; historical unmeasured runs never train it.
    """
    if focus not in FOCUS_SQL:
        return None, 'explore', '保持主题覆盖'
    claims = db.execute('SELECT count(*) FROM lead_discovery_runs '
                        'WHERE worker_key=? AND selection_mode IS NOT NULL', (worker_key,)).fetchone()[0]
    if claims % 4 == 0:
        return None, 'explore', '固定探索轮次：寻找尚未覆盖的新受众'
    cutoff = (datetime.fromisoformat(now.replace('Z', '+00:00'))-timedelta(days=14)).isoformat().replace('+00:00','Z')
    rows = db.execute(f'''SELECT j.tier_key,j.language,count(*) samples,
        sum(r.eligible_contacts) eligible,sum(r.new_channels) new_channels,
        sum(r.channels_found) channels,sum(r.active_channels) active,
        sum(r.inactive_channels) inactive,sum(r.unknown_activity) unknown,
        sum(max(30,(julianday(r.completed_at)-julianday(r.started_at))*86400)) seconds
      FROM lead_discovery_runs r JOIN lead_discovery_jobs j ON j.id=r.job_id
      WHERE r.metrics_version=1 AND r.status='completed' AND r.started_at>=?
        AND ({FOCUS_SQL[focus]})
      GROUP BY j.tier_key,j.language HAVING count(*)>=2 AND sum(r.eligible_contacts)>0''', (cutoff,)).fetchall()
    ranked = sorted(rows, key=family_score, reverse=True)
    for family in ranked:
        key = (family['tier_key'], family['language'])
        available = db.execute('''SELECT 1 FROM lead_discovery_jobs j
          WHERE tier_key=? AND language=? AND next_run_at<=?
            AND status IN ('queued','waiting','error') AND low_yield_streak<2
            AND NOT EXISTS(SELECT 1 FROM lead_discovery_jobs live
              WHERE live.tier_key=j.tier_key AND live.language=j.language AND live.status='running')
          LIMIT 1''', (*key, now)).fetchone()
        if available:
            reason = (f"近14天 {family['samples']} 轮，新增 {family['eligible']} 个合格候选；"
                      f"按耗时、去重率和核验成功率优先，评分 {family_score(family):.2f}")
            return key, 'yield', reason
    return None, 'explore', '有效样本不足或高产方向正在运行，继续探索新主题'


def recover_expired_leases(db, now):
    """Recover abandoned work without touching any valid lease; no commit here."""
    expired = db.execute('''SELECT id,claim_token FROM lead_discovery_jobs
      WHERE status='running' AND lease_until IS NOT NULL AND lease_until<?''', (now,)).fetchall()
    for job in expired:
        db.execute('''UPDATE lead_discovery_runs SET status='interrupted',completed_at=?,
          error='Recovered expired worker lease' WHERE job_id=? AND claim_token=? AND status='running' ''',
          (now, job['id'], job['claim_token']))
        db.execute('''UPDATE lead_discovery_jobs SET status='queued',next_run_at=?,claimed_by=NULL,
          claim_token=NULL,lease_until=NULL,heartbeat_at=NULL,last_error='Recovered expired worker lease',updated_at=?
          WHERE id=?''', (now, now, job['id']))
    db.execute('''UPDATE lead_discovery_channels SET scan_status='discovered',
      scan_claimed_by=NULL,scan_lease_until=NULL
      WHERE scan_status='scanning' AND scan_lease_until<?''', (now,))
    return len(expired)
