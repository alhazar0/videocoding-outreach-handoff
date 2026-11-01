ALTER TABLE outreach_send_log ADD COLUMN provider TEXT;
ALTER TABLE outreach_send_log ADD COLUMN experiment_id TEXT;
ALTER TABLE outreach_send_log ADD COLUMN variant TEXT;

INSERT INTO outreach_sender_accounts(
  sender_email,provider,status,daily_target,daily_hard_cap,min_interval_seconds,
  reply_to_email,sending_domain,configuration_note,created_at,updated_at
) VALUES(
  'creator@outreach.videocoding.ai','google_workspace_smtp','configuration_required',20,20,240,
  'team@videocoding.ai','outreach.videocoding.ai',
  'Workspace created. Complete SPF, DKIM, DMARC and local SMTP authorization before enabling the 20/day warm-up.',
  datetime('now'),datetime('now')
) ON CONFLICT(sender_email) DO UPDATE SET
  provider=excluded.provider,daily_target=excluded.daily_target,daily_hard_cap=excluded.daily_hard_cap,
  min_interval_seconds=excluded.min_interval_seconds,reply_to_email=excluded.reply_to_email,
  sending_domain=excluded.sending_domain,updated_at=excluded.updated_at;

INSERT OR IGNORE INTO outreach_templates(
  template_key,name,stage,subject,body,risk_level,enabled,version,updated_at
) VALUES(
  'first_contact_legacy_light','首次邀请 · Legacy-light（历史最佳修正版）','first_contact',
  'A paid collaboration with VideoCoding',
  'Hi {{channel_name}},

I’m Alhazar from VideoCoding. I’d like to explore a paid collaboration with you.

VideoCoding turns a talking-head cut into a finished video with B-roll, motion graphics, captions, music, and sound design.

You can see it here:
https://videocoding.ai

If you’d like to test it, reply and I’ll send you a creator code for one month of VideoCoding Pro at $0. After you try it on one real video, we can discuss the promotion format and your rate.

Best,
Alhazar Anwar
VideoCoding',
  'medium',1,1,datetime('now')
);
