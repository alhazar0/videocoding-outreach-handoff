CREATE TABLE `lead_discovery_channels` (
	`channel_key` text PRIMARY KEY NOT NULL,
	`channel_id` text,
	`channel_name` text NOT NULL,
	`channel_url` text NOT NULL,
	`best_tier` integer NOT NULL,
	`matched_query` text NOT NULL,
	`matched_video_title` text,
	`matched_video_url` text,
	`language` text NOT NULL,
	`scan_status` text NOT NULL,
	`email_count` integer DEFAULT 0 NOT NULL,
	`first_seen_at` text NOT NULL,
	`last_seen_at` text NOT NULL,
	`last_contact_scan_at` text,
	`last_error` text
);
--> statement-breakpoint
CREATE TABLE `lead_discovery_emails` (
	`email` text PRIMARY KEY NOT NULL,
	`channel_key` text NOT NULL,
	`source_url` text NOT NULL,
	`source_type` text NOT NULL,
	`validation_status` text NOT NULL,
	`contact_id` integer,
	`first_seen_at` text NOT NULL,
	`last_seen_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `lead_discovery_jobs` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`tier` integer NOT NULL,
	`tier_key` text NOT NULL,
	`query` text NOT NULL,
	`search_mode` text NOT NULL,
	`language` text NOT NULL,
	`status` text NOT NULL,
	`priority` integer NOT NULL,
	`run_count` integer DEFAULT 0 NOT NULL,
	`videos_scanned` integer DEFAULT 0 NOT NULL,
	`channels_found` integer DEFAULT 0 NOT NULL,
	`public_emails_found` integer DEFAULT 0 NOT NULL,
	`contacts_imported` integer DEFAULT 0 NOT NULL,
	`last_started_at` text,
	`last_completed_at` text,
	`next_run_at` text NOT NULL,
	`last_error` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	UNIQUE(`query`,`search_mode`)
);
--> statement-breakpoint
CREATE INDEX `lead_discovery_jobs_queue_idx` ON `lead_discovery_jobs` (`tier`,`next_run_at`,`priority`);
--> statement-breakpoint
CREATE TABLE `lead_discovery_runs` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`job_id` integer NOT NULL,
	`status` text NOT NULL,
	`videos_scanned` integer DEFAULT 0 NOT NULL,
	`channels_found` integer DEFAULT 0 NOT NULL,
	`public_emails_found` integer DEFAULT 0 NOT NULL,
	`contacts_imported` integer DEFAULT 0 NOT NULL,
	`started_at` text NOT NULL,
	`completed_at` text,
	`error` text
);
--> statement-breakpoint
CREATE TABLE `lead_discovery_state` (
	`worker_key` text PRIMARY KEY NOT NULL,
	`enabled` integer NOT NULL,
	`status` text NOT NULL,
	`current_job_id` integer,
	`active_tier` integer,
	`current_query` text,
	`heartbeat_at` text,
	`last_started_at` text,
	`last_completed_at` text,
	`last_error` text,
	`updated_at` text NOT NULL
);
