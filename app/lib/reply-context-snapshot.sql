-- Shared verbatim by the API and Python senders. Do not include routing estimates,
-- polling timestamps or work status: those are outputs, not conversation evidence.
WITH target AS (
  SELECT w.id AS work_id,w.contact_id,e.* FROM outreach_work_items w
  JOIN inbox_events e ON e.id=w.inbox_event_id WHERE w.id=?
), history AS (
  SELECT 'inbound:' || i.id AS id,i.conversation_id,i.received_at AS at,
    CASE WHEN i.event_type IN ('bounce','sender_restricted','delivery_delayed','delivery_failed','automated')
      THEN 'system' ELSE 'creator' END AS direction,i.subject,coalesce(nullif(i.body_text,''),i.body_preview,'') AS body,
    CASE WHEN trim(coalesce(i.body_text,''))='' THEN 1 ELSE 0 END AS preview_only
  FROM inbox_events i,target t
  WHERE i.id=t.id OR (t.contact_id IS NOT NULL AND i.contact_id=t.contact_id)
    OR (nullif(t.conversation_id,'') IS NOT NULL AND i.conversation_id=t.conversation_id)
  UNION ALL
  SELECT 'sent:' || s.id,s.conversation_id,s.sent_at,'videocoding',s.subject,coalesce(s.body_text,''),0
  FROM outreach_sent_messages s,target t
  WHERE (t.contact_id IS NOT NULL AND s.contact_id=t.contact_id)
    OR (nullif(t.conversation_id,'') IS NOT NULL AND s.conversation_id=t.conversation_id)
  UNION ALL
  SELECT 'reply-log:' || w.id,i.conversation_id,coalesce(w.resolved_at,w.updated_at),
    'videocoding',w.subject,coalesce(w.draft_body,''),0
  FROM outreach_work_items w JOIN inbox_events i ON i.id=w.inbox_event_id,target t
  WHERE w.status='sent' AND coalesce(w.resolved_by,'') NOT IN ('operations_queue_reconciler','inbox_sync') AND ((t.contact_id IS NOT NULL AND w.contact_id=t.contact_id)
    OR (nullif(t.conversation_id,'') IS NOT NULL AND i.conversation_id=t.conversation_id))
    AND NOT EXISTS(SELECT 1 FROM outreach_sent_messages s
      WHERE (s.contact_id=w.contact_id OR s.conversation_id=i.conversation_id)
        AND trim(s.body_text)=trim(w.draft_body)
        AND abs((julianday(s.sent_at)-julianday(coalesce(w.resolved_at,w.updated_at)))*86400)<600)
  UNION ALL
  SELECT 'followup-log:' || f.id,f.conversation_id,f.sent_at,'videocoding',f.subject,coalesce(f.body,''),0
  FROM creator_followups f,target t
  WHERE f.status='sent' AND ((t.contact_id IS NOT NULL AND f.contact_id=t.contact_id)
    OR (nullif(t.conversation_id,'') IS NOT NULL AND f.conversation_id=t.conversation_id))
    AND NOT EXISTS(SELECT 1 FROM outreach_sent_messages s
      WHERE (s.contact_id=f.contact_id OR s.conversation_id=f.conversation_id)
        AND trim(s.body_text)=trim(f.body)
        AND abs((julianday(s.sent_at)-julianday(f.sent_at))*86400)<600)
)
SELECT json_object(
  'current_id',t.id,'conversation_id',t.conversation_id,'received_at',t.received_at,
  'subject',t.subject,'body',coalesce(nullif(t.body_text,''),t.body_preview,''),
  'sender_email',t.sender_email,'contact_id',t.contact_id,
  'contact',json_object('email',c.email,'channel_name',c.channel_name,
    'initial_template_key',c.initial_template_key,'rate_info',c.rate_info,'do_not_contact',c.do_not_contact),
  'profile',json_object('registered_email',p.registered_email,'account_status',p.account_status,
    'test_status',p.test_status,'membership_status',p.membership_status,'membership_expires_at',p.membership_expires_at,
    'coupon_code',p.coupon_code,'coupon_sent_at',p.coupon_sent_at,'coupon_redemption_status',p.coupon_redemption_status,
    'usage_verification_status',p.usage_verification_status,'usage_total_tasks',p.usage_total_tasks,
    'usage_active_tasks',p.usage_active_tasks,'usage_completed_tasks',p.usage_completed_tasks,
    'price_dedicated',p.price_dedicated,'price_integration',p.price_integration,'price_shorts',p.price_shorts,
    'currency',p.currency,'turnaround',p.turnaround,'marketing_status',p.marketing_status),
  'captured_sent_count',(SELECT count(*) FROM outreach_sent_messages s
    WHERE s.contact_id=t.contact_id AND datetime(s.sent_at)<=datetime(t.received_at) AND trim(s.body_text)<>''),
  'expected_sent_count',
    (SELECT count(*) FROM outreach_send_log s WHERE s.contact_id=t.contact_id AND s.status='sent' AND datetime(s.sent_at)<=datetime(t.received_at))+
    (SELECT count(*) FROM outreach_work_items w WHERE w.contact_id=t.contact_id AND w.status='sent' AND coalesce(w.resolved_by,'') NOT IN ('operations_queue_reconciler','inbox_sync') AND datetime(coalesce(w.resolved_at,w.updated_at))<=datetime(t.received_at))+
    (SELECT count(*) FROM creator_followups f WHERE f.contact_id=t.contact_id AND f.status='sent' AND datetime(f.sent_at)<=datetime(t.received_at)),
  'messages',json((SELECT json_group_array(json_object('id',id,'conversation_id',conversation_id,
    'at',at,'direction',direction,'subject',subject,'body',body,'preview_only',preview_only))
    FROM (SELECT * FROM history ORDER BY datetime(at),id))),
  'templates',json((SELECT json_group_array(json_object('key',template_key,'subject',subject,'body',body,'version',version))
    FROM (SELECT * FROM outreach_templates WHERE enabled=1 AND approval_status='approved' ORDER BY template_key)))
) AS snapshot_json
FROM target t LEFT JOIN contacts c ON c.id=t.contact_id LEFT JOIN creator_profiles p ON p.contact_id=t.contact_id
