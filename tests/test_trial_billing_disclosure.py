import unittest
from pathlib import Path
from scripts import send_coupon_replies as sender


class TrialDisclosureTests(unittest.TestCase):
    def test_every_coupon_fallback_explains_renewal_and_cardless_alternative(self):
        bodies = [sender.standard_body(), sender.rate_first_body(),
                  sender.t2v_knowledge_body(), sender.waitlist_body(),
                  sender.redirected_body('creator@example.com')]
        for body in bodies:
            with self.subTest(body=body[:90]):
                self.assertIn('renews automatically at the price shown at checkout', body)
                self.assertIn('cancel before renewal', body)
                self.assertIn('without adding a card or automatic renewal', body)

    def test_dashboard_coupon_templates_also_disclose_renewal(self):
        source = (Path(__file__).parents[1] / 'app/api/operations/route.ts').read_text()
        for key in ['coupon_test', 'coupon_test_t2v_knowledge_v1',
                    'coupon_test_rate_received', 'coupon_test_campaign_details',
                    'campaign_details_after_coupon']:
            block = source.split(f'key: "{key}",', 1)[1].split('body: `', 1)[1].split('`,', 1)[0]
            with self.subTest(key=key):
                self.assertIn('renews automatically at the price shown at checkout', block)
                self.assertIn('cancel before renewal', block)

