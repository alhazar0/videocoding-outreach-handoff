UPDATE outreach_sender_accounts
SET daily_target=300,daily_hard_cap=300,min_interval_seconds=120,
    configuration_note='Current human reply mailbox; campaign target raised to 300/day by operator on 2026-08-14.',
    updated_at=datetime('now')
WHERE sender_email='team@videocoding.ai';

INSERT INTO outreach_settings(key,value,updated_at)
VALUES('queue_policy','two-sender-300-plus-50-v7-eastern-8am',datetime('now'))
ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at;
