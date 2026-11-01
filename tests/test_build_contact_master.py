import unittest

from scripts.build_contact_master import is_parser_artifact


class ParserArtifactTests(unittest.TestCase):
    def test_rejects_sentry_and_asset_urls(self):
        self.assertTrue(is_parser_artifact("hash@sentry.wixpress.com"))
        self.assertTrue(is_parser_artifact("banner@2x.png"))
        self.assertTrue(is_parser_artifact("/hash@sentry.wixpress.com"))

    def test_keeps_real_business_email(self):
        self.assertFalse(is_parser_artifact("hello@creatorstudio.com"))


if __name__ == "__main__":
    unittest.main()
