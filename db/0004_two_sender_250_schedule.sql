ALTER TABLE contacts ADD COLUMN planned_sender_email TEXT;

CREATE INDEX IF NOT EXISTS contacts_sender_queue_idx
ON contacts(planned_send_date, planned_sender_email, next_eligible_send_at);

UPDATE outreach_sender_accounts
SET daily_target=200,daily_hard_cap=200,min_interval_seconds=120,updated_at=datetime('now')
WHERE sender_email='team@videocoding.ai';

UPDATE outreach_sender_accounts
SET daily_target=50,daily_hard_cap=50,min_interval_seconds=120,updated_at=datetime('now')
WHERE sender_email='creator@outreach.videocoding.ai';

INSERT INTO outreach_settings(key,value,updated_at)
VALUES('queue_policy','two-sender-200-plus-50-v6-eastern-8am',datetime('now'))
ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at;
