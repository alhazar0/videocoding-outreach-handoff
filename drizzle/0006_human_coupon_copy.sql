UPDATE `outreach_templates`
SET `body` = 'Hi,

Sure — here’s the code for one month of VideoCoding Pro:

{{coupon_code}}

To activate it:

1. Sign in or create an account at https://videocoding.ai
2. Click Upgrade to Pro
3. Click Add promotion code and enter {{coupon_code}}
4. Make sure the total shows $0.00 before confirming

When you get a chance, try it with a talking-head video you would normally publish. If anything doesn’t work, just reply here and I’ll help.

Once you’ve tried it, let me know what you think. If it’s a good fit, we can talk about the promotion and your rate.

Best,
Alhazar',
    `version` = CASE WHEN `version` < 10 THEN 10 ELSE `version` + 1 END,
    `updated_at` = datetime('now')
WHERE `template_key` = 'coupon_test';
--> statement-breakpoint
