INSERT INTO outreach_templates(
  template_key,name,stage,subject,body,risk_level,enabled,version,updated_at,
  approval_status,intent_signature,context_requirements_json,creation_reason,example_count
) VALUES(
  'reviewed_rate_scope_with_code_20260921',
  '已报价并询问推广细节 · 记录报价后先测试（用户批准）',
  'coupon_ready',
  'Re: {{original_subject}}',
  'Hi,

Thanks for sharing your rates. I’ve recorded them for review. I’m not confirming a campaign, fee, or booking yet.

Our budget for an individual collaboration is under $5,000, depending on the format and scope.

We’re currently exploring paid promotion on YouTube. We haven’t agreed on the format, video length, number of posts, additional platforms, creative brief, or publishing date yet. Payment terms, usage rights, paid-ad use, and exclusivity would also need to be discussed separately.

Before we go further, we’d like you to try VideoCoding with one of your own videos and see whether it’s a good fit for your audience. This is only a product test—it isn’t a request to publish a promotional video.

Here’s your code for one free month of VideoCoding Pro:

{{coupon_code}}

To activate it:

1. Sign in or create an account at https://videocoding.ai
2. Click Upgrade to Pro
3. Click Add promotion code and enter {{coupon_code}}
4. Make sure the total shows $0.00 before confirming

Try it with one of your own videos and let me know how it goes. If it feels like a good fit, we can then discuss the campaign details and your rate.

Let me know if you run into any issues!

Best,
Alhazar',
  'high',1,1,datetime('now'),'approved',
  'price_received+wants_test+asks_campaign_details',
  '["complete_thread_review","exact_rate_recorded","no_unanswered_product_or_privacy_question","no_specific_contract_acceptance","no_existing_access_or_test"]',
  'Exact combined rate, campaign-scope, and trial copy approved by the user on 2026-09-21; explicit reviewed manifests only.',
  18
)
ON CONFLICT(template_key) DO UPDATE SET
  name=excluded.name,stage=excluded.stage,subject=excluded.subject,body=excluded.body,
  risk_level=excluded.risk_level,enabled=excluded.enabled,version=excluded.version,
  updated_at=excluded.updated_at,approval_status=excluded.approval_status,
  intent_signature=excluded.intent_signature,
  context_requirements_json=excluded.context_requirements_json,
  creation_reason=excluded.creation_reason,example_count=excluded.example_count;

INSERT INTO outreach_audit_log(action,entity_type,entity_id,actor,detail_json,created_at)
VALUES(
  'user_approved_template_created','outreach_template',
  'reviewed_rate_scope_with_code_20260921','user',
  '{"approved_on":"2026-09-21","auto_send":false,"requires_explicit_review_manifest":true}',
  datetime('now')
);
