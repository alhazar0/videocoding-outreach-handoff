ALTER TABLE `lead_discovery_emails` ADD `was_new_contact` integer DEFAULT 0 NOT NULL;
--> statement-breakpoint
UPDATE `lead_discovery_emails` SET `was_new_contact`=1
WHERE `contact_id` IN (
  SELECT `id` FROM `contacts` WHERE `source_file` LIKE 'continuous_lead_discovery:%'
);
