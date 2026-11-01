-- Schema only from the stopped operational database. No rows or credentials.
CREATE TABLE approved_reply_batch_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          work_item_id INTEGER NOT NULL UNIQUE,
          inbox_event_id TEXT NOT NULL,
          contact_id INTEGER,
          template_key TEXT NOT NULL,
          template_version INTEGER NOT NULL,
          status TEXT NOT NULL,
          queued_at TEXT NOT NULL,
          sent_at TEXT,
          error TEXT,
          updated_at TEXT NOT NULL
        );

CREATE TABLE automation_rules (
      rule_key TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      mode TEXT NOT NULL,
      risk_level TEXT NOT NULL,
      description TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL
    );

CREATE TABLE contacts (
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
    outreach_status TEXT NOT NULL DEFAULT 'not_contacted',
    first_sent_at TEXT,
    followup1_due TEXT,
    followup2_due TEXT,
    reply_status TEXT NOT NULL DEFAULT 'no_reply',
    deal_status TEXT NOT NULL DEFAULT 'not_started',
    do_not_contact INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
  , reply_received_at TEXT, reply_from TEXT, reply_summary TEXT, rate_info TEXT, next_action TEXT, send_timezone TEXT, timezone_source TEXT NOT NULL DEFAULT 'unknown', timezone_confidence TEXT NOT NULL DEFAULT 'unknown', send_window TEXT, next_eligible_send_at TEXT, planned_sender_email TEXT, initial_template_key TEXT);

CREATE TABLE coupon_reply_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          inbox_event_id TEXT NOT NULL UNIQUE,
          contact_id INTEGER,
          recipient_email TEXT NOT NULL,
          conversation_id TEXT,
          variant TEXT NOT NULL,
          status TEXT NOT NULL,
          queued_at TEXT NOT NULL,
          sent_at TEXT,
          error TEXT
        );

CREATE TABLE creator_activity_cache(
      channel_url TEXT PRIMARY KEY,channel_id TEXT,status TEXT NOT NULL,
      published_at TEXT,video_url TEXT,checked_at TEXT NOT NULL,detail TEXT);

CREATE TABLE creator_data_agent_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          status TEXT NOT NULL,
          usage_checked INTEGER NOT NULL DEFAULT 0,
          usage_updated INTEGER NOT NULL DEFAULT 0,
          coupons_redeemed INTEGER NOT NULL DEFAULT 0,
          memberships_granted INTEGER NOT NULL DEFAULT 0,
          template_replies_sent INTEGER NOT NULL DEFAULT 0,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          error TEXT
        );

CREATE TABLE creator_feedback (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 inbox_event_id TEXT NOT NULL,
 contact_id INTEGER,
 sender_email TEXT NOT NULL,
 received_at TEXT NOT NULL,
 category TEXT NOT NULL,
 summary TEXT NOT NULL,
 evidence TEXT NOT NULL,
 review_state TEXT NOT NULL DEFAULT 'candidate' CHECK(review_state IN ('candidate','reviewed','dismissed')),
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','investigating','planned','fixed','closed')),
 target_version TEXT NOT NULL DEFAULT '',
 followup_status TEXT NOT NULL DEFAULT 'not_due' CHECK(followup_status IN ('not_due','ready','contacted','done')),
 notes TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(inbox_event_id,category)
);

CREATE TABLE creator_followups (
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

CREATE TABLE creator_profiles (
      contact_id INTEGER PRIMARY KEY,
      workflow_stage TEXT NOT NULL DEFAULT 'lead',
      registered_email TEXT,
      account_status TEXT NOT NULL DEFAULT 'unknown',
      membership_status TEXT NOT NULL DEFAULT 'none',
      membership_expires_at TEXT,
      coupon_code TEXT,
      coupon_sent_at TEXT,
      test_status TEXT NOT NULL DEFAULT 'not_started',
      test_started_at TEXT,
      test_completed_at TEXT,
      price_dedicated REAL,
      price_integration REAL,
      price_shorts REAL,
      currency TEXT,
      turnaround TEXT,
      marketing_status TEXT NOT NULL DEFAULT 'not_ready',
      updated_at TEXT NOT NULL
    , product_user_id TEXT, usage_verification_status TEXT NOT NULL DEFAULT 'not_checked', usage_total_tasks INTEGER NOT NULL DEFAULT 0, usage_active_tasks INTEGER NOT NULL DEFAULT 0, usage_completed_tasks INTEGER NOT NULL DEFAULT 0, usage_last_login_at TEXT, usage_last_task_at TEXT, usage_verified_at TEXT, usage_source TEXT, coupon_redemption_status TEXT NOT NULL DEFAULT 'not_checked', coupon_redeemed_code TEXT, coupon_redemption_verified_at TEXT, usage_failed_tasks INTEGER NOT NULL DEFAULT 0, usage_canceled_tasks INTEGER NOT NULL DEFAULT 0, usage_last_seen_at TEXT, product_sync_error TEXT);

CREATE TABLE inbox_events (
    id TEXT PRIMARY KEY,
    internet_message_id TEXT,
    conversation_id TEXT,
    received_at TEXT NOT NULL,
    sender_name TEXT NOT NULL DEFAULT '',
    sender_email TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body_preview TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    contact_id INTEGER,
    matched_email TEXT,
    web_link TEXT,
    processed_at TEXT NOT NULL
  , body_text TEXT);

CREATE TABLE lead_discovery_channels (
          channel_key TEXT PRIMARY KEY,
          channel_id TEXT,
          channel_name TEXT NOT NULL,
          channel_url TEXT NOT NULL,
          best_tier INTEGER NOT NULL,
          matched_query TEXT NOT NULL,
          matched_video_title TEXT,
          matched_video_url TEXT,
          language TEXT NOT NULL,
          scan_status TEXT NOT NULL DEFAULT 'discovered',
          email_count INTEGER NOT NULL DEFAULT 0,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          last_contact_scan_at TEXT,
          last_error TEXT
        , scan_claimed_by TEXT, scan_lease_until TEXT);

CREATE TABLE lead_discovery_emails (
          email TEXT PRIMARY KEY,
          channel_key TEXT NOT NULL,
          source_url TEXT NOT NULL,
          source_type TEXT NOT NULL,
          validation_status TEXT NOT NULL,
          contact_id INTEGER,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL
        , was_new_contact INTEGER NOT NULL DEFAULT 0);

CREATE TABLE lead_discovery_jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tier INTEGER NOT NULL,
          tier_key TEXT NOT NULL,
          query TEXT NOT NULL,
          search_mode TEXT NOT NULL DEFAULT 'recent_public_videos',
          language TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued',
          priority INTEGER NOT NULL,
          run_count INTEGER NOT NULL DEFAULT 0,
          videos_scanned INTEGER NOT NULL DEFAULT 0,
          channels_found INTEGER NOT NULL DEFAULT 0,
          public_emails_found INTEGER NOT NULL DEFAULT 0,
          contacts_imported INTEGER NOT NULL DEFAULT 0,
          last_started_at TEXT,
          last_completed_at TEXT,
          next_run_at TEXT NOT NULL,
          last_error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, claimed_by TEXT, claim_token TEXT, lease_until TEXT, heartbeat_at TEXT, consecutive_errors INTEGER NOT NULL DEFAULT 0, last_new_channels INTEGER NOT NULL DEFAULT 0, last_candidates INTEGER NOT NULL DEFAULT 0, low_yield_streak INTEGER NOT NULL DEFAULT 0,
          UNIQUE(query, search_mode)
        );

CREATE TABLE lead_discovery_merge_state (
             id INTEGER PRIMARY KEY CHECK(id=1),
             last_run_id INTEGER NOT NULL DEFAULT 0,
             last_contact_updated_at TEXT NOT NULL DEFAULT '',
             last_channel_seen_at TEXT NOT NULL DEFAULT '',
             last_email_seen_at TEXT NOT NULL DEFAULT '',
             last_job_updated_at TEXT NOT NULL DEFAULT '',
             last_provenance_at TEXT NOT NULL DEFAULT '',
             last_merged_at TEXT,
             last_error TEXT,
             updated_at TEXT NOT NULL
           , last_job_id INTEGER NOT NULL DEFAULT 0, last_contact_id INTEGER NOT NULL DEFAULT 0, last_channel_key TEXT NOT NULL DEFAULT '', last_email_key TEXT NOT NULL DEFAULT '', last_provenance_id INTEGER NOT NULL DEFAULT 0, last_activity_checked_at TEXT NOT NULL DEFAULT '', last_activity_url TEXT NOT NULL DEFAULT '');

CREATE TABLE lead_discovery_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          videos_scanned INTEGER NOT NULL DEFAULT 0,
          channels_found INTEGER NOT NULL DEFAULT 0,
          public_emails_found INTEGER NOT NULL DEFAULT 0,
          contacts_imported INTEGER NOT NULL DEFAULT 0,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          error TEXT
        , worker_key TEXT, claim_token TEXT, channels_scanned INTEGER NOT NULL DEFAULT 0, new_channels INTEGER NOT NULL DEFAULT 0, duplicate_channels INTEGER NOT NULL DEFAULT 0, eligible_contacts INTEGER NOT NULL DEFAULT 0, active_channels INTEGER NOT NULL DEFAULT 0, inactive_channels INTEGER NOT NULL DEFAULT 0, unknown_activity INTEGER NOT NULL DEFAULT 0, metrics_version INTEGER NOT NULL DEFAULT 0, selection_mode TEXT, selection_reason TEXT);

CREATE TABLE lead_discovery_state (
          worker_key TEXT PRIMARY KEY,
          enabled INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'starting',
          current_job_id INTEGER,
          active_tier INTEGER,
          current_query TEXT,
          heartbeat_at TEXT,
          last_started_at TEXT,
          last_completed_at TEXT,
          last_error TEXT,
          updated_at TEXT NOT NULL
        );

CREATE TABLE lead_provenance (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          contact_id INTEGER,
          email TEXT NOT NULL,
          source_type TEXT NOT NULL,
          source_url TEXT NOT NULL,
          source_file TEXT NOT NULL,
          contact_basis TEXT NOT NULL,
          domain_validation TEXT NOT NULL,
          discovered_at TEXT NOT NULL,
          UNIQUE(email, source_url)
        );

CREATE TABLE lead_sources (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      source_type TEXT NOT NULL,
      location TEXT NOT NULL,
      policy_status TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      discovered_count INTEGER NOT NULL DEFAULT 0,
      imported_count INTEGER NOT NULL DEFAULT 0,
      last_run_at TEXT,
      last_error TEXT,
      updated_at TEXT NOT NULL
    );

CREATE TABLE outreach_audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      action TEXT NOT NULL,
      entity_type TEXT NOT NULL,
      entity_id TEXT NOT NULL,
      actor TEXT NOT NULL,
      detail_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL
    );

CREATE TABLE outreach_conversations (
          conversation_id TEXT PRIMARY KEY,
          contact_id INTEGER NOT NULL,
          recipient_email TEXT NOT NULL,
          subject TEXT NOT NULL DEFAULT '',
          sent_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

CREATE TABLE outreach_send_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          contact_id INTEGER NOT NULL,
          campaign_kind TEXT NOT NULL DEFAULT 'creator_initial',
          campaign_day_et TEXT NOT NULL,
          status TEXT NOT NULL,
          queued_at TEXT NOT NULL,
          sent_at TEXT,
          error TEXT, template_version TEXT, sender_email TEXT, recipient_timezone TEXT, timezone_source TEXT, send_window TEXT, scheduled_for TEXT, provider TEXT, experiment_id TEXT, variant TEXT,
          UNIQUE(contact_id, campaign_kind)
        );

CREATE TABLE outreach_sender_accounts (
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

CREATE TABLE outreach_sent_messages (
          id TEXT PRIMARY KEY,
          conversation_id TEXT,
          contact_id INTEGER NOT NULL,
          recipient_email TEXT NOT NULL,
          subject TEXT NOT NULL DEFAULT '',
          body_text TEXT NOT NULL DEFAULT '',
          sent_at TEXT NOT NULL,
          synced_at TEXT NOT NULL
        );

CREATE TABLE outreach_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );

CREATE TABLE outreach_templates (
      template_key TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      stage TEXT NOT NULL,
      subject TEXT NOT NULL,
      body TEXT NOT NULL,
      risk_level TEXT NOT NULL DEFAULT 'medium',
      enabled INTEGER NOT NULL DEFAULT 1,
      version INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL
    , approval_status TEXT NOT NULL DEFAULT 'approved', intent_signature TEXT, context_requirements_json TEXT NOT NULL DEFAULT '[]', source_work_item_id INTEGER, creation_reason TEXT NOT NULL DEFAULT '', example_count INTEGER NOT NULL DEFAULT 0);

CREATE TABLE outreach_work_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      inbox_event_id TEXT UNIQUE,
      contact_id INTEGER,
      kind TEXT NOT NULL,
      workflow_stage TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      risk_level TEXT NOT NULL,
      confidence REAL,
      template_key TEXT,
      subject TEXT NOT NULL DEFAULT '',
      draft_body TEXT NOT NULL DEFAULT '',
      extracted_json TEXT NOT NULL DEFAULT '{}',
      proposed_action_json TEXT NOT NULL DEFAULT '[]',
      decision_reason TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      resolved_at TEXT,
      resolved_by TEXT
    );

CREATE TABLE promotion_creator_list (
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

CREATE TABLE reply_context_checks (work_item_id INTEGER PRIMARY KEY,checked_at TEXT NOT NULL);

CREATE TABLE reviewed_cardless_operations (
 work_item_id INTEGER PRIMARY KEY,account_email TEXT,operation TEXT NOT NULL,status TEXT NOT NULL,
 idempotency_key TEXT UNIQUE,detail_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL);

CREATE TABLE reviewed_contact_holds(
        contact_id INTEGER PRIMARY KEY, source_work_item_id INTEGER NOT NULL,
        reason TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE reviewed_service_cases(
        work_item_id INTEGER PRIMARY KEY, contact_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', reason TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE safe_workflow_reply_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          work_item_id INTEGER NOT NULL UNIQUE,
          contact_id INTEGER,
          template_key TEXT NOT NULL,
          status TEXT NOT NULL,
          queued_at TEXT NOT NULL,
          sent_at TEXT,
          error TEXT,
          updated_at TEXT NOT NULL
        );

CREATE INDEX approved_reply_batch_log_status_idx
          ON approved_reply_batch_log(status,sent_at);

CREATE INDEX contacts_ai_queue_idx
   ON contacts(curated_ai, validation_status, planned_send_date, fit_score);

CREATE INDEX contacts_channel_name_normalized_idx
          ON contacts(lower(trim(channel_name)));

CREATE INDEX contacts_email_normalized_idx
          ON contacts(lower(email));

CREATE INDEX contacts_filters_idx
   ON contacts(category, validation_status, outreach_status);

CREATE INDEX contacts_sender_queue_idx
           ON contacts(planned_send_date,planned_sender_email,next_eligible_send_at);

CREATE INDEX coupon_reply_log_status_idx
          ON coupon_reply_log(status, sent_at);

CREATE INDEX creator_followups_due_idx
          ON creator_followups(status,due_at);

CREATE INDEX discovery_channel_url_idx ON lead_discovery_channels(channel_url);

CREATE INDEX discovery_learning_runs_idx ON lead_discovery_runs(started_at,job_id) WHERE metrics_version=1;

CREATE INDEX discovery_worker_runs_idx ON lead_discovery_runs(worker_key,id);

CREATE INDEX inbox_events_contact_idx
    ON inbox_events(contact_id, event_type);

CREATE INDEX inbox_events_contact_received_idx
      ON inbox_events(contact_id,received_at DESC,id DESC);

CREATE INDEX inbox_events_conversation_received_idx
      ON inbox_events(conversation_id,received_at DESC,id DESC);

CREATE INDEX inbox_events_received_idx
    ON inbox_events(received_at DESC);

CREATE INDEX lead_discovery_jobs_queue_idx
          ON lead_discovery_jobs(tier, next_run_at, priority DESC);

CREATE INDEX lead_provenance_contact_idx ON lead_provenance(contact_id);

CREATE INDEX outreach_conversations_contact_idx
          ON outreach_conversations(contact_id);

CREATE INDEX outreach_send_log_day_idx
          ON outreach_send_log(campaign_day_et, status);

CREATE INDEX outreach_sent_messages_contact_idx
          ON outreach_sent_messages(contact_id,sent_at DESC);

CREATE INDEX outreach_sent_messages_conversation_sent_idx
      ON outreach_sent_messages(conversation_id,sent_at DESC);

CREATE INDEX outreach_work_items_contact_status_idx
      ON outreach_work_items(contact_id,status,id DESC);

CREATE INDEX outreach_work_items_status_idx
      ON outreach_work_items(status, risk_level, created_at DESC);

CREATE INDEX promotion_creator_list_status_idx
          ON promotion_creator_list(list_status,updated_at DESC);

CREATE INDEX safe_workflow_reply_log_status_idx
          ON safe_workflow_reply_log(status,sent_at);

CREATE TRIGGER preserve_reviewed_contact_hold
        AFTER UPDATE OF next_action,followup1_due,followup2_due ON contacts
        WHEN coalesce(NEW.do_not_contact,0)=0
          AND EXISTS(SELECT 1 FROM reviewed_contact_holds h WHERE h.contact_id=NEW.id AND h.active=1
            AND (coalesce(NEW.next_action,'')<>h.reason OR NEW.followup1_due IS NOT NULL OR NEW.followup2_due IS NOT NULL))
        BEGIN
          UPDATE contacts SET next_action=(SELECT reason FROM reviewed_contact_holds WHERE contact_id=NEW.id AND active=1),
            followup1_due=NULL,followup2_due=NULL WHERE id=NEW.id;
        END;

CREATE TRIGGER preserve_reviewed_service_case
        AFTER UPDATE OF status,template_key,kind,risk_level,resolved_at,resolved_by,decision_reason
        ON outreach_work_items
        WHEN EXISTS(SELECT 1 FROM reviewed_service_cases s
          WHERE s.work_item_id=NEW.id AND s.status='pending'
            AND (NEW.status<>'pending' OR NEW.template_key IS NOT NULL
              OR NEW.kind<>'human_review' OR NEW.risk_level<>'high'
              OR NEW.resolved_at IS NOT NULL OR NEW.resolved_by IS NOT NULL
              OR coalesce(NEW.decision_reason,'')<>s.reason))
        BEGIN
          UPDATE outreach_work_items SET status='pending',template_key=NULL,
            kind='human_review',risk_level='high',resolved_at=NULL,resolved_by=NULL,
            decision_reason=(SELECT reason FROM reviewed_service_cases WHERE work_item_id=NEW.id)
          WHERE id=NEW.id;
        END;
