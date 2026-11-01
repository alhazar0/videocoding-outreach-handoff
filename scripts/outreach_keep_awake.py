"""Prevent idle sleep only during the approved local sending window.

Does not prevent user shutdown, lid closure, or network loss. macOS releases the
assertion when the bounded caffeinate process exits.
"""
from datetime import datetime,time,timezone
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.outreach_scheduler import CAMPAIGN_ZONE,SEND_WINDOW_HOURS

def remaining_window_seconds(now):
    local=now.astimezone(CAMPAIGN_ZONE)
    if not 8<=local.hour<8+SEND_WINDOW_HOURS:
        return 0
    end=datetime.combine(local.date(),time(8+SEND_WINDOW_HOURS),CAMPAIGN_ZONE)
    return max(0,int((end-local).total_seconds()))

if __name__=='__main__':
    seconds=remaining_window_seconds(datetime.now(timezone.utc))
    if seconds and sys.platform=='darwin':
        subprocess.run(['/usr/bin/caffeinate','-i','-t',str(seconds)],check=False)
