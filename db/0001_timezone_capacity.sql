ALTER TABLE contacts ADD COLUMN send_timezone TEXT;
ALTER TABLE contacts ADD COLUMN timezone_source TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE contacts ADD COLUMN timezone_confidence TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE contacts ADD COLUMN send_window TEXT;
ALTER TABLE contacts ADD COLUMN next_eligible_send_at TEXT;

ALTER TABLE outreach_send_log ADD COLUMN sender_email TEXT;
ALTER TABLE outreach_send_log ADD COLUMN recipient_timezone TEXT;
ALTER TABLE outreach_send_log ADD COLUMN timezone_source TEXT;
ALTER TABLE outreach_send_log ADD COLUMN send_window TEXT;
ALTER TABLE outreach_send_log ADD COLUMN scheduled_for TEXT;

CREATE TABLE IF NOT EXISTS outreach_sender_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender_email TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  status TEXT NOT NULL,
  daily_target INTEGER NOT NULL,
  daily_hard_cap INTEGER NOT NULL,
  min_interval_seconds INTEGER NOT NULL,
  reply_to_email TEXT NOT NULL,
  sending_domain TEXT NOT NULL,
  configuration_note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
