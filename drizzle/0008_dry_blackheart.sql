CREATE TABLE `creator_feedback` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`inbox_event_id` text NOT NULL,
	`contact_id` integer,
	`sender_email` text NOT NULL,
	`received_at` text NOT NULL,
	`category` text NOT NULL,
	`summary` text NOT NULL,
	`evidence` text NOT NULL,
	`review_state` text DEFAULT 'candidate' NOT NULL,
	`status` text DEFAULT 'new' NOT NULL,
	`target_version` text DEFAULT '' NOT NULL,
	`followup_status` text DEFAULT 'not_due' NOT NULL,
	`notes` text DEFAULT '' NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `creator_feedback_event_category` ON `creator_feedback` (`inbox_event_id`,`category`);
