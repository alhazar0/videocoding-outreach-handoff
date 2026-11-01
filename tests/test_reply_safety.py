import sqlite3
import unittest

from scripts import reply_safety as safety


class ReplySafetyTests(unittest.TestCase):
    def test_zero_cost_trial_is_not_a_creator_quote(self):
        self.assertNotIn('rate_signal', safety.safety_flags('Please send the creator code for Pro access at $0.'))
        self.assertIn('rate_signal', safety.safety_flags('Please send a trial code. My rate is $900.'))

    def test_rate_card_after_testing_is_not_a_current_quote(self):
        self.assertNotIn('rate_signal', safety.safety_flags("Please send the creator code. I'll share my rate card after testing."))
        self.assertNotIn('rate_signal', safety.safety_flags('Once I have tried it, I can share my rate card at that point if useful.'))
        self.assertIn('rate_signal', safety.safety_flags('My rate is $900. I can share my rate card after testing.'))
        self.assertIn('rate_signal', safety.safety_flags('Here is my rate card.'))
    def test_localized_quoted_invitation_does_not_supply_a_price(self):
        for header in ('Alhazar schrieb am Sa., 26. Sept. 2026, 00:02:',
                       '2026년 9월 26일 Alhazar님이 작성:'):
            text = safety.current_message('Please send a code.\n\n' + header + '\nPro access at $0.')
            self.assertEqual(text, 'Please send a code.')
            self.assertNotIn('rate_signal', safety.safety_flags(text))
    def test_current_message_ignores_quoted_old_thread(self):
        message = safety.current_message(
            "I completed the test.\n\nFrom: Alhazar\nCould you share your rate and budget?"
        )
        self.assertEqual(message, "I completed the test.")
        self.assertIsNone(safety.pre_send_hold_reason("ask_rate", message))

    def test_current_price_blocks_ask_rate(self):
        reason = safety.pre_send_hold_reason(
            "ask_rate",
            "The test went well. My rate is $250 for a dedicated video.",
        )
        self.assertIn("已经包含报价", reason or "")

    def test_rejection_has_highest_priority(self):
        reason = safety.pre_send_hold_reason(
            "coupon_test",
            "I tested it, but it is too basic and I cannot showcase it. What is your budget?",
        )
        self.assertIn("拒绝", reason or "")

    def test_future_test_update_is_not_a_campaign_question(self):
        message = "I am on vacation. I will test it and share the details with you before Thursday."
        self.assertNotIn("campaign_question", safety.safety_flags(message))

    def test_code_request_does_not_leak_into_later_campaign_sentence(self):
        message = (
            "Please send me the free one-month code and instructions.\n\n"
            "Once tested, we can discuss the promotion details and my rate."
        )
        self.assertNotIn("campaign_question", safety.safety_flags(message))

    def test_paid_collaboration_and_free_test_is_not_a_paid_test_condition(self):
        message = (
            "I'd be delighted to collaborate on a paid campaign and test VideoCoding. "
            "Please send the creator code."
        )
        self.assertNotIn("conditional_deal", safety.safety_flags(message))

    def test_optional_telegram_handle_is_not_an_external_process_condition(self):
        message = "Yes, I am interested. Telegram: @creator_handle"
        self.assertNotIn("conditional_deal", safety.safety_flags(message))

    def test_explicit_move_to_telegram_remains_a_condition(self):
        message = "Please continue the negotiation with me on Telegram."
        self.assertIn("conditional_deal", safety.safety_flags(message))

    def test_activation_details_are_not_campaign_details(self):
        message = "I want to test it. Please send the code and the details I need to get started."
        self.assertNotIn("campaign_question", safety.safety_flags(message))

    def test_collaboration_details_remain_a_campaign_question(self):
        message = "Could you share the collaboration details and next steps?"
        self.assertIn("campaign_question", safety.safety_flags(message))

    def test_promotional_signature_is_removed_before_safety_check(self):
        message = safety.current_message(
            "Awesome, thank you. I will get back once done.\n\n"
            "Contacts & Connections\n*** Call/Whatsapp Us +123"
        )
        self.assertEqual(message, "Awesome, thank you. I will get back once done.")
        self.assertNotIn("conditional_deal", safety.safety_flags(message))

    def test_missing_or_non_auto_rule_fails_closed(self):
        db = sqlite3.connect(":memory:")
        self.assertEqual(safety.automation_rule_mode(db, "coupon_reply"), "missing")
        db.executescript(
            """
            CREATE TABLE automation_rules(
              rule_key TEXT PRIMARY KEY,mode TEXT,risk_level TEXT,enabled INTEGER
            );
            INSERT INTO automation_rules VALUES('coupon_reply','draft','medium',1);
            """
        )
        self.assertFalse(safety.template_allows_auto_send(db, "coupon_test"))
        db.execute("UPDATE automation_rules SET mode='auto' WHERE rule_key='coupon_reply'")
        self.assertTrue(safety.template_allows_auto_send(db, "coupon_test"))

    def test_combined_coupon_uses_coupon_policy_without_automating_price_acceptance(self):
        db = sqlite3.connect(":memory:")
        db.executescript(
            """
            CREATE TABLE automation_rules(rule_key TEXT PRIMARY KEY,mode TEXT,enabled INTEGER);
            INSERT INTO automation_rules VALUES('coupon_reply','auto',1);
            INSERT INTO automation_rules VALUES('price_received','manual',1);
            """
        )
        self.assertTrue(safety.template_allows_auto_send(db, "coupon_test"))
        self.assertTrue(safety.template_allows_auto_send(db, "coupon_test_rate_received"))
        self.assertFalse(safety.template_allows_auto_send(db, "rate_received"))
        db.close()

    def test_campaign_details_template_is_allowed_to_answer_campaign_question(self):
        message = "Could you share the campaign budget, deliverables, and timeline?"
        self.assertIsNotNone(safety.pre_send_hold_reason("coupon_test", message))
        self.assertIsNone(safety.pre_send_hold_reason("coupon_test_campaign_details", message))
        self.assertIsNone(safety.pre_send_hold_reason("campaign_details_after_coupon", message))

    def test_post_coupon_campaign_template_holds_uncovered_questions_and_rates(self):
        self.assertIsNotNone(safety.pre_send_hold_reason(
            "campaign_details_after_coupon",
            "Could you share examples of edits the tool has produced?",
        ))
        self.assertIsNotNone(safety.pre_send_hold_reason(
            "campaign_details_after_coupon",
            "Is this collaboration for my YouTube channel?",
        ))
        self.assertIsNotNone(safety.pre_send_hold_reason(
            "campaign_details_after_coupon",
            "My rate is $900. What format are you looking for?",
        ))

    def test_french_quoted_coupon_is_removed_before_rate_detection(self):
        message = """Hi, does the video need to show me on camera?

Le jeu. 20 août 2026 à 3:18 AM, Alhazar Anwar a écrit :
Hi,
Make sure the total shows $0.00 before confirming.
"""
        current = safety.current_message(message)
        self.assertNotIn("$0.00", current)
        self.assertIsNone(safety.pre_send_hold_reason("campaign_details_after_coupon", current))

    def test_chinese_gmail_quote_is_removed_before_coupon_rate_detection(self):
        message = """Please send the creator code. I'll test it first.

Alhazar Anwar >于2026年9月15日 周二01:40写道：
Hi,
Make sure the total shows $0.00 before confirming.
"""
        current = safety.current_message(message)
        self.assertNotIn("$0.00", current)
        self.assertNotIn("rate_signal", safety.safety_flags(current))


if __name__ == "__main__":
    unittest.main()
