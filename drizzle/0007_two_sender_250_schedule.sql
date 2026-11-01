ALTER TABLE `contacts` ADD `planned_sender_email` text;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `contacts_sender_queue_idx`
ON `contacts` (`planned_send_date`,`planned_sender_email`,`next_eligible_send_at`);
--> statement-breakpoint
UPDATE `outreach_sender_accounts`
SET `daily_target`=200,`daily_hard_cap`=200,`min_interval_seconds`=120,`updated_at`=datetime('now')
WHERE `sender_email`='team@videocoding.ai';
--> statement-breakpoint
UPDATE `outreach_sender_accounts`
SET `daily_target`=50,`daily_hard_cap`=50,`min_interval_seconds`=120,`updated_at`=datetime('now')
WHERE `sender_email`='creator@outreach.videocoding.ai';
