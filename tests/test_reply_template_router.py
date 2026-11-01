import unittest

from scripts.reply_template_router import MAX_BODY_CHARS, compact_item, compact_template, valid_decisions


class ReplyTemplateRouterTests(unittest.TestCase):
    def test_compact_item_limits_untrusted_email_text(self):
        item = compact_item({
            "id": 12,
            "subject": "s" * 700,
            "body_preview": "p" * 3_000,
            "body_text": "b" * (MAX_BODY_CHARS + 500),
            "unknown": "must not be included",
        })
        self.assertEqual(len(item["subject"]), 500)
        self.assertEqual(len(item["body_preview"]), 2_000)
        self.assertEqual(len(item["body_text"]), MAX_BODY_CHARS)
        self.assertNotIn("unknown", item)

    def test_valid_decisions_rejects_unknown_and_duplicate_ids(self):
        payload = {
            "decisions": [
                {"id": 1, "intent": "wants_test"},
                {"id": 1, "intent": "other"},
                {"id": 99, "intent": "wants_test"},
                {"id": "2", "intent": "plans_to_test"},
                {"id": "bad", "intent": "other"},
            ]
        }
        self.assertEqual(valid_decisions(payload, {1, 2}), [
            {"id": 1, "intent": "wants_test"},
            {"id": "2", "intent": "plans_to_test"},
        ])

    def test_compact_item_keeps_operational_context(self):
        item = compact_item({
            "id": 8,
            "body_text": "I tested it and my rate is $500.",
            "thread_context": "creator | newest\n---\nvideocoding | prior reply",
            "usage_completed_tasks": 1,
            "membership_status": "active",
            "rate_info": "USD 500",
        })
        self.assertEqual(item["usage_completed_tasks"], 1)
        self.assertEqual(item["membership_status"], "active")
        self.assertEqual(item["rate_info"], "USD 500")
        self.assertIn("videocoding", item["thread_context"])

    def test_compact_template_exposes_actual_copy_for_coverage_check(self):
        template = compact_template({
            "template_key": "coupon_test",
            "name": "Coupon",
            "stage": "coupon_ready",
            "subject": "Access",
            "body": "Here is the code, but no campaign budget answer.",
            "risk_level": "medium",
            "enabled": 1,
        })
        self.assertIn("no campaign budget answer", template["body"])
        self.assertNotIn("enabled", template)


if __name__ == "__main__":
    unittest.main()
