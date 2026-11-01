"""Credential access is opt-in in this sanitized handoff copy only."""
import os


def require_live_opt_in():
    if os.environ.get('VIDEOCODING_ALLOW_LIVE_OPERATIONS') != '1':
        raise RuntimeError('Handoff copy: live credential access is disabled. Read docs/04_OPERATIONS.md before an explicitly authorized activation.')
