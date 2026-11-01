CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  channel_name TEXT NOT NULL,
  channel_url TEXT NOT NULL,
  platform TEXT NOT NULL,
  category TEXT NOT NULL,
  ai_match INTEGER NOT NULL DEFAULT 0,
  curated_ai INTEGER NOT NULL DEFAULT 0,
  ai_confidence TEXT NOT NULL,
  validation_status TEXT NOT NULL,
  verification_proof TEXT NOT NULL,
  email_domain TEXT NOT NULL,
  subscribers INTEGER,
  country TEXT NOT NULL,
  language TEXT NOT NULL,
  topic TEXT NOT NULL,
  priority TEXT NOT NULL,
  fit_score INTEGER NOT NULL,
  source_file TEXT NOT NULL,
  source_count INTEGER NOT NULL,
  source_rows INTEGER NOT NULL,
  planned_send_date TEXT,
  planned_sender_email TEXT,
  initial_template_key TEXT,
  send_timezone TEXT,
  timezone_source TEXT NOT NULL DEFAULT 'unknown',
  timezone_confidence TEXT NOT NULL DEFAULT 'unknown',
  send_window TEXT,
  next_eligible_send_at TEXT,
  outreach_status TEXT NOT NULL DEFAULT 'not_contacted',
  first_sent_at TEXT,
  followup1_due TEXT,
  followup2_due TEXT,
  reply_status TEXT NOT NULL DEFAULT 'no_reply',
  deal_status TEXT NOT NULL DEFAULT 'not_started',
  do_not_contact INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS contacts_ai_queue_idx
ON contacts(curated_ai, validation_status, planned_send_date, fit_score);
CREATE INDEX IF NOT EXISTS contacts_sender_queue_idx
ON contacts(planned_send_date, planned_sender_email, next_eligible_send_at);
CREATE INDEX IF NOT EXISTS contacts_filters_idx
ON contacts(category, validation_status, outreach_status);
CREATE TABLE IF NOT EXISTS outreach_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
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

CREATE TABLE IF NOT EXISTS creator_followups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL,
  source_kind TEXT NOT NULL,
  source_id TEXT NOT NULL,
  sequence INTEGER NOT NULL DEFAULT 1,
  template_key TEXT NOT NULL,
  sender_email TEXT NOT NULL,
  conversation_id TEXT,
  source_sent_at TEXT NOT NULL,
  due_at TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  queued_at TEXT NOT NULL,
  sent_at TEXT,
  error TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(source_kind,source_id,sequence)
);
CREATE INDEX IF NOT EXISTS creator_followups_due_idx ON creator_followups(status,due_at);

CREATE TABLE IF NOT EXISTS promotion_creator_list (
  contact_id INTEGER PRIMARY KEY,
  creator_email TEXT NOT NULL,
  registered_email TEXT,
  channel_name TEXT NOT NULL,
  channel_url TEXT,
  subscribers INTEGER,
  category TEXT,
  country TEXT,
  language TEXT,
  test_status TEXT NOT NULL,
  completed_videos INTEGER NOT NULL DEFAULT 0,
  usage_verified_at TEXT,
  interest_status TEXT NOT NULL DEFAULT 'ready_to_promote',
  interest_evidence TEXT NOT NULL DEFAULT '',
  price_dedicated REAL,
  price_integration REAL,
  price_shorts REAL,
  currency TEXT,
  turnaround TEXT,
  rate_info TEXT,
  quote_source_text TEXT,
  quote_received_at TEXT,
  promotion_formats TEXT,
  list_status TEXT NOT NULL DEFAULT 'waiting',
  added_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS promotion_creator_list_status_idx ON promotion_creator_list(list_status,updated_at DESC);
