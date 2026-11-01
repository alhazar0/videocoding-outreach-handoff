"""Distinguish our provider's sender restriction from an invalid recipient."""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlparse

RESTRICTION_REASON = "550 5.1.8 AS(42004): Microsoft restricted the outbound sender; administrator review required."
RESTRICTION_SUMMARY = "微软发件账号受限；不是收件地址无效。等待管理员处理，不自动重发。"


def is_sender_restriction(sender_email: str, text: str) -> bool:
    # Only provider-generated NDRs can change the global sending state.
    provider = re.fullmatch(r"microsoftexchange[0-9a-f]+@[a-z0-9.-]+\.onmicrosoft\.com", sender_email, re.I)
    return bool(provider and re.search(r"\b550\s+5\.1\.8\b|\bbad outbound sender\b|\bAS\(42004\)", text, re.I))


def is_graph_delivery(url: str, method: str = "POST") -> bool:
    parsed = urlparse(url)
    return (method.upper() == "POST" and parsed.hostname == "graph.microsoft.com"
            and parsed.path.rstrip("/").rsplit("/", 1)[-1].lower()
            in {"sendmail", "reply", "replyall", "forward", "send"})


def block_reason(db: sqlite3.Connection) -> str | None:
    row = db.execute("SELECT value FROM outreach_settings WHERE key='microsoft_outbound_block_reason'").fetchone()
    if row and str(row[0]).strip():
        return str(row[0])
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outreach_sender_accounts'").fetchone():
        row = db.execute("SELECT status FROM outreach_sender_accounts WHERE lower(sender_email)='team@videocoding.ai'").fetchone()
        if row and row[0] == "restricted":
            return RESTRICTION_REASON
    return None


def record_restriction(db: sqlite3.Connection, received_at: str) -> None:
    # Commit together with the inbox event; callers own the transaction.
    for key, value in (("microsoft_outbound_block_reason", RESTRICTION_REASON),
                       ("microsoft_outbound_restriction_last_seen_at", received_at),
                       ("scheduler_status", "sender_restricted")):
        db.execute("""INSERT INTO outreach_settings(key,value,updated_at) VALUES(?,?,datetime('now'))
          ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""", (key, value))
    db.execute("""UPDATE outreach_sender_accounts SET status='restricted',updated_at=datetime('now')
      WHERE lower(sender_email)='team@videocoding.ai'""")
