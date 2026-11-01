#!/usr/bin/env python3
"""Read-only, offline cohort audit. No production modules, credentials or network.

Usage: python handoff/analyze_outreach.py --db /absolute/private.sqlite \
    --out handoff/metrics.json --private-out /outside/repository/private-detail.json
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import sqlite3
import statistics

UTC = dt.timezone.utc
HUMAN_CANDIDATE_TYPES = {"reply", "interested", "opted_out"}
# Conservative automatic-message screen. It is NOT a full semantic human audit.
AUTO_SUBJECT = re.compile(r"out of office|auto(?:matic)?[ -]?(?:reply|response)|automatic reply|delivery status|undeliver|未送达|自动回复|absence|abwesen|autoreply", re.I)
AUTO_BODY = re.compile(r"(?:this is an automated|automatically generated|ticket (?:number|id)|your (?:request|ticket) (?:has been|was) (?:received|created)|currently out of (?:the )?office|away from (?:my|the) office)", re.I)


def timestamp(value):
    if not value:
        return None
    try:
        x = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Legacy naive database timestamps are treated as UTC, separately counted.
        return x.replace(tzinfo=UTC) if x.tzinfo is None else x.astimezone(UTC)
    except ValueError:
        return None


def wilson(k, n):
    if not n:
        return None
    z = 1.96
    p = k / n
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    half = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/(1 + z*z/n)
    return [round(100*(center-half), 2), round(100*(center+half), 2)]


def rate(k, n):
    return round(100*k/n, 3) if n else None


def summarize(rows):
    mature = [r for r in rows if r["mature_7d"]]
    reply = [r for r in mature if r["reply_h"] is not None and r["reply_h"] <= 168]
    speeds = [r["reply_h"] for r in reply]
    return {
        "accepted_contacts": len(rows), "mature_7d": len(mature),
        "reply_7d": len(reply), "reply_7d_pct": rate(len(reply), len(mature)),
        "reply_7d_wilson95_pct": wilson(len(reply), len(mature)),
        "reply_24h": sum(x <= 24 for x in speeds),
        "reply_24h_pct": rate(sum(x <= 24 for x in speeds), len(mature)),
        "reply_48h_pct": rate(sum(x <= 48 for x in speeds), len(mature)),
        "median_reply_hours_among_7d_responders": round(statistics.median(speeds), 2) if speeds else None,
        "reply_by_observation_end": sum(r["reply_h"] is not None for r in rows),
        "interest_label_7d": sum(r["interest_h"] is not None and r["interest_h"] <= 168 for r in mature),
        "confirmed_usage_completed_contacts": sum(r["used"] for r in rows),
        "coupon_sent_profile_contacts": sum(r["coupon_sent"] for r in rows),
        "followup_before_reply_contacts": sum(r["followup_before_reply"] for r in rows),
    }


def grouped(rows, key):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[str(r.get(key) or "unknown")].append(r)
    return [{key: k, **summarize(v)} for k,v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]


def analyze(path):
    db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    def query(q, args=()):
        return [dict(x) for x in db.execute(q, args)]
    contacts = {r["id"]:r for r in query("SELECT * FROM contacts")}
    profiles = {r["contact_id"]:r for r in query("SELECT * FROM creator_profiles")}
    sends = query("SELECT * FROM outreach_send_log WHERE campaign_kind='creator_initial'")
    inbox = query("SELECT * FROM inbox_events")
    newest_message = max(timestamp(r["received_at"]) for r in inbox if timestamp(r["received_at"]))
    sync_rows = query("SELECT key,value FROM outreach_settings WHERE key IN ('inbox_background_sync_last_run_at','gmail_history_sync_at')")
    sync_times = [timestamp(r["value"]) for r in sync_rows if timestamp(r["value"])]
    obs = min(sync_times) if len(sync_times) == 2 else newest_message
    incoming = collections.defaultdict(list)
    excluded_auto = []
    for e in inbox:
        if e["event_type"] not in HUMAN_CANDIDATE_TYPES:
            continue
        body = e.get("body_text") or e.get("body_preview") or ""
        # Only examine the newest pre-quote paragraph window, not old quoted mail.
        head = re.split(r"\n(?:On .{0,180}wrote:|From:|发件人:|_{3,}|>{1,})", body, maxsplit=1)[0][:1200]
        if AUTO_SUBJECT.search(e["subject"] or "") or AUTO_BODY.search(head):
            excluded_auto.append(e["id"])
            continue
        if e["contact_id"] is not None:
            incoming[e["contact_id"]].append(e)
    follows = collections.defaultdict(list)
    for f in query("SELECT contact_id,sent_at FROM creator_followups WHERE status='sent'"):
        if timestamp(f["sent_at"]): follows[f["contact_id"]].append(timestamp(f["sent_at"]))
    rows = []; preexisting = []; naive = 0
    for s in sends:
        if s["status"] != "sent": continue
        start = timestamp(s["sent_at"])
        if not start or start > obs: continue
        if not re.search(r"(?:Z|[+-]\d\d:\d\d)$", s["sent_at"]): naive += 1
        c = contacts[s["contact_id"]]; p = profiles.get(c["id"], {})
        events = [(timestamp(e["received_at"]), e) for e in incoming[c["id"]]]
        if any(t and t < start for t,e in events): preexisting.append(c["id"])
        # Prior conversations make this unsuitable as a fresh-contact cohort.
        eligible = not any(t and t < start for t,e in events)
        post = sorted([(t,e) for t,e in events if t and start <= t <= obs], key=lambda x:x[0])
        reply_h = (post[0][0]-start).total_seconds()/3600 if post else None
        interest = [t for t,e in post if e["event_type"] == "interested"]
        used = p.get("usage_verification_status") == "completed" and p.get("account_status") == "verified" and bool(p.get("product_user_id")) and p.get("usage_source") == "product_outreach_api"
        subs = c.get("subscribers")
        band = "unknown" if not subs or subs < 0 else ("under_10k" if subs < 10000 else "10k_100k" if subs < 100000 else "100k_1m" if subs < 1000000 else "1m_plus")
        r = {"contact_id":c["id"], "channel_name":c["channel_name"], "category":c["category"],
             "country":c["country"], "language_raw":c["language"], "subscriber_band":band,
             "provider":s.get("provider") or "unknown_legacy", "template_version":s.get("template_version") or "unknown",
             "variant":s.get("variant") or "unknown", "month":start.strftime("%Y-%m"),
             "week":start.strftime("%G-W%V"), "sent_at":s["sent_at"],
             "reply_h":reply_h, "interest_h":(interest[0]-start).total_seconds()/3600 if interest else None,
             "mature_7d":(obs-start).total_seconds() >= 168*3600, "new_contact_cohort":eligible,
             "used":used, "coupon_sent":bool(p.get("coupon_sent_at")),
             "followup_before_reply":bool(post and any(start < t < post[0][0] for t in follows[c["id"]]))}
        rows.append(r)
    cohort = [r for r in rows if r["new_contact_cohort"]]
    valid_usage = [p for p in profiles.values() if p.get("usage_verification_status") == "completed" and p.get("account_status") == "verified" and p.get("product_user_id") and p.get("usage_source") == "product_outreach_api"]
    accounts = collections.defaultdict(list)
    for p in valid_usage: accounts[p["product_user_id"]].append(p)
    top = []
    for uid, ps in accounts.items():
        p = max(ps, key=lambda x:timestamp(x.get("usage_verified_at")) or dt.datetime.min.replace(tzinfo=UTC))
        c=contacts[p["contact_id"]]
        top.append({"contact_id":c["id"], "channel_name":c["channel_name"], "category":c["category"],
                    "contact_email":c["email"],"registered_email":p.get("registered_email"), "product_user_id":uid,
                    "completed_tasks":p["usage_completed_tasks"],"total_tasks":p["usage_total_tasks"],
                    "coupon_status":p["coupon_redemption_status"],"verified_at":p["usage_verified_at"],
                    "profile_count_for_account":len(ps),"membership_status":p["membership_status"]})
    top.sort(key=lambda r:(-r["completed_tasks"],r["contact_id"]))
    by_usage_category=[]
    for cat in sorted(set(c["category"] for c in contacts.values())):
        ps=[p for p in profiles.values() if contacts[p["contact_id"]]["category"]==cat]
        valid=[p for p in valid_usage if contacts[p["contact_id"]]["category"]==cat]
        by_usage_category.append({"category":cat,"profile_count":len(ps),"code_sent_profiles":sum(bool(p["coupon_sent_at"]) for p in ps),"usage_checked_profiles":sum(p["usage_verification_status"]!='not_checked' for p in ps),"verified_completed_profiles":len(valid),"completed_tasks":sum(p["usage_completed_tasks"] for p in valid)})
    # Compare common week x category x provider strata, not the rollout averages alone.
    comparisons=[]
    for a,b in [("first_contact-v1","first_contact_legacy_light-v1"),("first_contact-v1","first_contact-v2"),("first_contact-v2","first_contact-v3")]:
        strata=collections.defaultdict(lambda:collections.defaultdict(list))
        for r in cohort:
            if r["mature_7d"] and r["template_version"] in [a,b]:
                strata[(r["week"],r["category"],r["provider"])][r["template_version"]].append(r)
        overlap=[g for g in strata.values() if len(g[a])>=20 and len(g[b])>=20]
        total=sum(min(len(g[a]),len(g[b])) for g in overlap)
        values={v:round(sum(min(len(g[a]),len(g[b]))*sum(r["reply_h"] is not None and r["reply_h"]<=168 for r in g[v])/len(g[v]) for g in overlap)/total*100,3) if total else None for v in [a,b]}
        comparisons.append({"templates":[a,b],"strata_min20_each":len(overlap),"weight_sum_min_group_size":total,"standardized_reply_7d_pct":values,"causal":False})
    metrics={
        "report_date":"2026-11-01", "observation_end_utc":obs.isoformat(),
        "latest_inbound_at":newest_message.isoformat(), "inbox_sync_watermarks":sync_rows,
        "source_max_usage_verified_at":max((p.get("usage_verified_at") or "") for p in profiles.values()),
        "contacts_total":len(contacts), "profiles_total":len(profiles),
        "initial_outcomes":query("SELECT status,coalesce(provider,'unknown_legacy') provider,count(*) n FROM outreach_send_log WHERE campaign_kind='creator_initial' GROUP BY 1,2"),
        "accepted_total":sum(s["status"]=='sent' for s in sends),
        "all_accepted_observed":summarize(rows), "fresh_cohort":summarize(cohort),
        "preexisting_conversation_contacts_excluded":len(preexisting),"legacy_naive_sent_timestamps_assumed_utc":naive,
        "automatic_message_candidates_excluded":len(excluded_auto),
        "inbox_by_type":query("SELECT event_type,count(*) n,count(distinct contact_id) contacts FROM inbox_events GROUP BY 1"),
        "inbox_unlinked":sum(e["contact_id"] is None for e in inbox),
        "category":grouped(cohort,"category"),"templates":grouped(cohort,"template_version"),
        "provider":grouped(cohort,"provider"),"subscriber_band":grouped(cohort,"subscriber_band"),
        "month":grouped(cohort,"month"),"language_raw":grouped(cohort,"language_raw"),
        "common_strata_template_comparisons":comparisons,
        "coupon_status":query("SELECT coupon_redemption_status,count(*) n FROM creator_profiles GROUP BY 1"),
        "coupon_code_profiles":query("SELECT coupon_code,count(*) n,sum(coupon_sent_at is not null) sent_at_present FROM creator_profiles WHERE coupon_code is not null GROUP BY 1"),
        "usage_status":query("SELECT usage_verification_status,count(*) n FROM creator_profiles GROUP BY 1"),
        "usage_verified_completed_profiles":len(valid_usage),"usage_unique_completed_accounts":len(accounts),
        "usage_deduplicated_completed_tasks":sum(x["completed_tasks"] for x in top),
        "stale_completed_counts_on_noncompleted_profiles":query("SELECT usage_verification_status,count(*) n,sum(usage_completed_tasks) tasks FROM creator_profiles WHERE usage_completed_tasks>0 AND usage_verification_status<>'completed' GROUP BY 1"),
        "usage_by_category":by_usage_category,
        "cardless_operation_table":query("SELECT operation,status,count(*) n FROM reviewed_cardless_operations GROUP BY 1,2"),
        "grant_audit_actions":query("SELECT action,count(*) n FROM outreach_audit_log WHERE action LIKE '%grant%' OR action LIKE '%membership%' GROUP BY 1"),
        "work_status":query("SELECT status,count(*) n,count(distinct contact_id) contacts FROM outreach_work_items GROUP BY 1"),
        "durable_holds":db.execute("SELECT count(*) FROM reviewed_contact_holds WHERE active=1").fetchone()[0],
        "protected_service_work":db.execute("SELECT count(*) FROM reviewed_service_cases WHERE status='pending'").fetchone()[0],
        "protected_service_contacts":db.execute("SELECT count(distinct contact_id) FROM reviewed_service_cases WHERE status='pending'").fetchone()[0],
        "daily_accepted":query("SELECT campaign_day_et,coalesce(provider,'unknown_legacy') provider,count(*) n FROM outreach_send_log WHERE status='sent' GROUP BY 1,2 ORDER BY 1"),
        "deal_status":query("SELECT deal_status,count(*) n FROM contacts GROUP BY 1"),
        "initial_templates_current":query("SELECT template_key,subject,body,version,updated_at FROM outreach_templates WHERE template_key IN ('first_contact','first_contact_legacy_light','first_contact_t2v_knowledge_v1')"),
    }
    private={"top_usage_accounts":top, "contact_cohort_rows":rows,"automatic_excluded_ids":excluded_auto,
             "preexisting_contact_ids":preexisting,
             "protected_service_cases":query("SELECT s.*,c.channel_name,c.email FROM reviewed_service_cases s JOIN contacts c ON c.id=s.contact_id WHERE s.status='pending'"),
             "durable_holds":query("SELECT h.*,c.channel_name,c.email FROM reviewed_contact_holds h JOIN contacts c ON c.id=h.contact_id WHERE h.active=1")}
    db.close()
    return metrics,private


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=pathlib.Path,required=True);p.add_argument('--out',type=pathlib.Path,required=True);p.add_argument('--private-out',type=pathlib.Path)
    args=p.parse_args();metrics,private=analyze(args.db)
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(metrics,ensure_ascii=False,indent=2)+'\n')
    if args.private_out:
        repo=pathlib.Path(__file__).resolve().parents[1]
        if args.private_out.resolve().is_relative_to(repo):
            raise SystemExit('Private details must be outside the repository')
        args.private_out.parent.mkdir(parents=True,exist_ok=True);args.private_out.write_text(json.dumps(private,ensure_ascii=False,indent=2)+'\n');args.private_out.chmod(0o600)
    print(json.dumps({k:metrics[k] for k in ['observation_end_utc','contacts_total','accepted_total','fresh_cohort','usage_unique_completed_accounts','usage_deduplicated_completed_tasks']},ensure_ascii=False))


if __name__=='__main__': main()
