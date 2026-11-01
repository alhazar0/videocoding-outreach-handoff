#!/usr/bin/env python3
"""Daily public YouTube creator discovery followed by safe master-DB import."""

from __future__ import annotations

import fcntl
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from outreach_scheduler import DEFAULT_DB


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = PROJECT_ROOT / ".wrangler" / "lead-collector.lock"
DISCOVERY_SCRIPT = Path("/Users/OPERATOR/Documents/Playground/outputs/youtube_fresh_talking_head/discover_youtube_creators.py")
IMPORT_SCRIPT = PROJECT_ROOT / "scripts" / "import_public_creator_leads.py"


def record_error(message: str | None) -> None:
    db = sqlite3.connect(DEFAULT_DB)
    try:
        if message:
            db.execute(
                """UPDATE lead_sources SET last_error=?,last_run_at=datetime('now'),updated_at=datetime('now')
                   WHERE name='YouTube 公开频道简介'""",
                (message[:1000],),
            )
        else:
            db.execute(
                """UPDATE lead_sources SET last_error=NULL,last_run_at=datetime('now'),updated_at=datetime('now')
                   WHERE name='YouTube 公开频道简介'"""
            )
        db.commit()
    finally:
        db.close()


def main() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running"}, separators=(",", ":")))
            return 0
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            if not DISCOVERY_SCRIPT.exists():
                raise RuntimeError(f"Discovery script not found: {DISCOVERY_SCRIPT}")
            discovery = subprocess.run(
                [sys.executable, str(DISCOVERY_SCRIPT)],
                cwd=DISCOVERY_SCRIPT.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=2 * 60 * 60,
                check=False,
            )
            if discovery.returncode != 0:
                tail = discovery.stdout[-4000:] if discovery.stdout else "no output"
                raise RuntimeError(f"Discovery failed ({discovery.returncode}): {tail}")
            imported = subprocess.run(
                [sys.executable, str(IMPORT_SCRIPT), "--apply"],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
                timeout=20 * 60,
                check=False,
            )
            if imported.returncode != 0:
                raise RuntimeError(f"Import failed ({imported.returncode}): {(imported.stderr or imported.stdout)[-4000:]}")
            record_error(None)
            print(json.dumps({
                "status": "complete",
                "started_at": started,
                "discovery_tail": (discovery.stdout or "")[-1200:],
                "import": json.loads(imported.stdout.strip()),
            }, ensure_ascii=False, separators=(",", ":")))
            return 0
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            record_error(str(exc))
            print(json.dumps({"status": "failed", "started_at": started, "error": str(exc)}, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
