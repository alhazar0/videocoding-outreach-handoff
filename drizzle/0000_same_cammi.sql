CREATE TABLE `contacts` (
	`id` integer PRIMARY KEY NOT NULL,
	`email` text NOT NULL,
	`channel_name` text NOT NULL,
	`channel_url` text NOT NULL,
	`platform` text NOT NULL,
	`category` text NOT NULL,
	`ai_match` integer NOT NULL,
	`curated_ai` integer NOT NULL,
	`ai_confidence` text NOT NULL,
	`validation_status` text NOT NULL,
	`verification_proof` text NOT NULL,
	`email_domain` text NOT NULL,
	`subscribers` integer,
	`country` text NOT NULL,
	`language` text NOT NULL,
	`topic` text NOT NULL,
	`priority` text NOT NULL,
	`fit_score` integer NOT NULL,
	`source_file` text NOT NULL,
	`source_count` integer NOT NULL,
	`source_rows` integer NOT NULL,
	`planned_send_date` text,
	`outreach_status` text NOT NULL,
	`first_sent_at` text,
	`followup1_due` text,
	`followup2_due` text,
	`reply_status` text NOT NULL,
	`deal_status` text NOT NULL,
	`do_not_contact` integer NOT NULL,
	`notes` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `contacts_email_unique` ON `contacts` (`email`);--> statement-breakpoint
CREATE TABLE `outreach_settings` (
	`key` text PRIMARY KEY NOT NULL,
	`value` text NOT NULL,
	`updated_at` text NOT NULL
);
