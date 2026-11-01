ALTER TABLE `contacts` ADD `reply_received_at` text;
--> statement-breakpoint
ALTER TABLE `contacts` ADD `reply_from` text;
--> statement-breakpoint
ALTER TABLE `contacts` ADD `reply_summary` text;
--> statement-breakpoint
ALTER TABLE `contacts` ADD `rate_info` text;
--> statement-breakpoint
ALTER TABLE `contacts` ADD `next_action` text;
--> statement-breakpoint
CREATE TABLE `inbox_events` (
	`id` text PRIMARY KEY NOT NULL,
	`internet_message_id` text,
	`conversation_id` text,
	`received_at` text NOT NULL,
	`sender_name` text NOT NULL,
	`sender_email` text NOT NULL,
	`subject` text NOT NULL,
	`body_preview` text NOT NULL,
	`event_type` text NOT NULL,
	`contact_id` integer,
	`matched_email` text,
	`web_link` text,
	`processed_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `inbox_events_received_idx` ON `inbox_events` (`received_at`);
--> statement-breakpoint
CREATE INDEX `inbox_events_contact_idx` ON `inbox_events` (`contact_id`,`event_type`);
