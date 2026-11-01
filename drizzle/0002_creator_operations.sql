ALTER TABLE `inbox_events` ADD `body_text` text;
--> statement-breakpoint
CREATE TABLE `automation_rules` (
	`rule_key` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`mode` text NOT NULL,
	`risk_level` text NOT NULL,
	`description` text NOT NULL,
	`enabled` integer NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `creator_profiles` (
	`contact_id` integer PRIMARY KEY NOT NULL,
	`workflow_stage` text NOT NULL,
	`registered_email` text,
	`account_status` text NOT NULL,
	`membership_status` text NOT NULL,
	`membership_expires_at` text,
	`coupon_code` text,
	`coupon_sent_at` text,
	`test_status` text NOT NULL,
	`test_started_at` text,
	`test_completed_at` text,
	`product_user_id` text,
	`usage_verification_status` text NOT NULL DEFAULT 'not_checked',
	`usage_total_tasks` integer NOT NULL DEFAULT 0,
	`usage_active_tasks` integer NOT NULL DEFAULT 0,
	`usage_completed_tasks` integer NOT NULL DEFAULT 0,
	`usage_last_login_at` text,
	`usage_last_task_at` text,
	`usage_verified_at` text,
	`usage_source` text,
	`price_dedicated` real,
	`price_integration` real,
	`price_shorts` real,
	`currency` text,
	`turnaround` text,
	`marketing_status` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `lead_provenance` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`contact_id` integer,
	`email` text NOT NULL,
	`source_type` text NOT NULL,
	`source_url` text NOT NULL,
	`source_file` text NOT NULL,
	`contact_basis` text NOT NULL,
	`domain_validation` text NOT NULL,
	`discovered_at` text NOT NULL,
	UNIQUE(`email`,`source_url`)
);
--> statement-breakpoint
CREATE INDEX `lead_provenance_contact_idx` ON `lead_provenance` (`contact_id`);
--> statement-breakpoint
CREATE TABLE `lead_sources` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`name` text NOT NULL,
	`source_type` text NOT NULL,
	`location` text NOT NULL,
	`policy_status` text NOT NULL,
	`enabled` integer NOT NULL,
	`discovered_count` integer NOT NULL,
	`imported_count` integer NOT NULL,
	`last_run_at` text,
	`last_error` text,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `lead_sources_name_unique` ON `lead_sources` (`name`);
--> statement-breakpoint
CREATE TABLE `outreach_audit_log` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`action` text NOT NULL,
	`entity_type` text NOT NULL,
	`entity_id` text NOT NULL,
	`actor` text NOT NULL,
	`detail_json` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `outreach_templates` (
	`template_key` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`stage` text NOT NULL,
	`subject` text NOT NULL,
	`body` text NOT NULL,
	`risk_level` text NOT NULL,
	`enabled` integer NOT NULL,
	`version` integer NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `outreach_work_items` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`inbox_event_id` text,
	`contact_id` integer,
	`kind` text NOT NULL,
	`workflow_stage` text NOT NULL,
	`status` text NOT NULL,
	`risk_level` text NOT NULL,
	`confidence` real,
	`template_key` text,
	`subject` text NOT NULL,
	`draft_body` text NOT NULL,
	`extracted_json` text NOT NULL,
	`proposed_action_json` text NOT NULL,
	`decision_reason` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`resolved_at` text,
	`resolved_by` text
);
--> statement-breakpoint
CREATE UNIQUE INDEX `outreach_work_items_inbox_event_id_unique` ON `outreach_work_items` (`inbox_event_id`);
