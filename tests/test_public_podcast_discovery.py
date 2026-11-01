import unittest
from unittest.mock import patch

from scripts.public_podcast_discovery import SEARCHES, official_site_from_feed


class PublicPodcastSourceTests(unittest.TestCase):
    def test_search_inventory_is_multilingual_and_multicountry(self):
        countries = {country for _, country, _ in SEARCHES}
        languages = {language.split(" · ", 1)[0] for _, _, language in SEARCHES}
        self.assertGreaterEqual(len(countries), 12)
        self.assertGreaterEqual(len(languages), 10)

    def test_official_site_is_read_from_public_feed(self):
        feed = "<rss><channel><link>https://creator.example/about</link></channel></rss>"
        with patch("scripts.public_podcast_discovery.is_public_http_url", return_value=True):
            self.assertEqual(
                official_site_from_feed(feed, "https://feeds.example/show.xml"),
                "https://creator.example/about",
            )


if __name__ == "__main__":
    unittest.main()
