#!/usr/bin/env python3
"""Authorize and verify the Google Workspace warm-up sender safely.

The Google app password is requested in a hidden native dialog and stored in
macOS Keychain. It is never written to the project database, logs, or stdout.
"""

from __future__ import annotations

import argparse
import smtplib
import sqlite3
import ssl
import subprocess
from pathlib import Path

from outreach_scheduler import (
    DEFAULT_DB,
    GOOGLE_KEYCHAIN_SERVICE,
    GOOGLE_MAILBOX,
    GOOGLE_PROVIDER,
    ensure_schema,
    google_app_password,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DKIM_SELECTOR = "google"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--store-app-password", action="store_true")
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args()


def hidden_app_password() -> str:
    script = '''
display dialog "Paste the 16-character Google app password for creator@outreach.videocoding.ai" default answer "" with hidden answer buttons {"Cancel", "Save in Keychain"} default button "Save in Keychain" with title "VideoCoding Gmail sender"
text returned of result
'''
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("App-password setup was cancelled")
    password = "".join(result.stdout.split())
    if len(password) != 16:
        raise RuntimeError("Google app passwords contain 16 characters")
    return password


def store_in_keychain(password: str) -> None:
    result = subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-a",
            GOOGLE_MAILBOX,
            "-s",
            GOOGLE_KEYCHAIN_SERVICE,
            "-l",
            "VideoCoding Google Workspace SMTP",
            "-w",
        ],
        input=password + "\n",
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("macOS Keychain rejected the credential")


def txt_records(name: str) -> list[str]:
    result = subprocess.run(
        ["/usr/bin/dig", "+short", "TXT", name],
        check=False,
        capture_output=True,
        text=True,
    )
    return [line.replace('" "', "").strip('"') for line in result.stdout.splitlines()]


def dns_checks() -> dict[str, bool]:
    return {
        "spf": any("include:_spf.google.com" in item for item in txt_records("outreach.videocoding.ai")),
        "dkim": any("v=DKIM1" in item for item in txt_records(f"{DKIM_SELECTOR}._domainkey.outreach.videocoding.ai")),
        "dmarc": any(item.startswith("v=DMARC1") for item in txt_records("_dmarc.outreach.videocoding.ai")),
    }


def verify_smtp(password: str) -> None:
    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=ssl.create_default_context(),
        timeout=30,
    ) as smtp:
        smtp.login(GOOGLE_MAILBOX, password)
        smtp.noop()


def update_sender(db_path: Path, checks: dict[str, bool], smtp_ok: bool) -> str:
    db = sqlite3.connect(db_path, timeout=30)
    db.row_factory = sqlite3.Row
    ensure_schema(db)
    ready = all(checks.values()) and smtp_ok
    status = "active_warmup" if ready else "configuration_required"
    note = (
        "SPF, DKIM, DMARC and local SMTP authorization verified. Channel is capped at 50/day with a 2-minute interval."
        if ready
        else "Workspace exists; activation remains blocked until SPF, DKIM, DMARC and local SMTP authorization all pass."
    )
    db.execute(
        """UPDATE outreach_sender_accounts
           SET provider=?,status=?,daily_target=50,daily_hard_cap=50,min_interval_seconds=120,
               reply_to_email='team@videocoding.ai',sending_domain='outreach.videocoding.ai',
               configuration_note=?,updated_at=datetime('now')
           WHERE sender_email=?""",
        (GOOGLE_PROVIDER, status, note, GOOGLE_MAILBOX),
    )
    db.commit()
    db.close()
    return status


def main() -> int:
    args = parse_args()
    if not args.store_app_password and not args.verify:
        raise RuntimeError("Choose --store-app-password or --verify")
    if args.store_app_password:
        store_in_keychain(hidden_app_password())
    password = google_app_password()
    verify_smtp(password)
    checks = dns_checks()
    status = update_sender(args.db, checks, smtp_ok=True)
    print(
        "Google sender verification: "
        + ", ".join(f"{name}={'pass' if passed else 'pending'}" for name, passed in checks.items())
        + f", smtp=pass, status={status}"
    )
    return 0 if status == "active_warmup" else 2


if __name__ == "__main__":
    raise SystemExit(main())
