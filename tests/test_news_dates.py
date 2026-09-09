import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import feedparser
from bs4 import BeautifulSoup

import app


def parse_feed(items):
    entries = "".join(
        f"<item><title>{title}</title><pubDate>{date}</pubDate></item>"
        for title, date in items
    )
    return feedparser.parse(f"<rss version='2.0'><channel>{entries}</channel></rss>")


class NewsDateTests(unittest.TestCase):
    def setUp(self):
        for name, value in {
            '_news_cache_has_errors': False, '_news_status': '',
            '_source_cache': {}, '_article_cache': app.OrderedDict(),
            '_translation_retry_after': 0.0,
        }.items():
            state = patch.object(app, name, value)
            state.start()
            self.addCleanup(state.stop)
        self.cache = app._news_cache, app._news_cache_time, app._news_cache_sources
        app._news_cache = None
        app._news_cache_time = 0
        self.addCleanup(self.restore_cache)

    def restore_cache(self):
        app._news_cache, app._news_cache_time, app._news_cache_sources = self.cache

    def test_nl_times_text_date_uses_amsterdam_summer_time(self):
        item = parse_feed([("NL", "9 September 2026 - 14:30")]).entries[0]
        self.assertIsNone(item.get("published_parsed"))
        expected = datetime(2026, 9, 9, 12, 30, tzinfo=timezone.utc)
        self.assertEqual(app.get_news_timestamp(item), expected.timestamp())
        self.assertEqual(app.format_date(item), "الأربعاء • 14:30 • 9 سبتمبر 2026")

    def test_nl_times_text_date_uses_amsterdam_winter_time(self):
        item = {"published": "9 January 2026 - 14:30"}
        expected = datetime(2026, 1, 9, 13, 30, tzinfo=timezone.utc)
        self.assertEqual(app.get_news_timestamp(item), expected.timestamp())

    def test_standard_rss_and_nl_times_dates_identify_same_instant(self):
        standard = parse_feed([("Dutch", "Wed, 09 Sep 2026 12:30:00 +0000")]).entries[0]
        local = {"published": "9 September 2026 - 14:30"}
        self.assertEqual(app.get_news_timestamp(standard), app.get_news_timestamp(local))
        self.assertEqual(app.format_date(standard), app.format_date(local))

    def test_original_publication_takes_priority_over_update(self):
        item = {
            "published": "9 September 2026 - 14:30",
            "updated_parsed": datetime(2026, 9, 9, 18, tzinfo=timezone.utc).timetuple(),
        }
        expected = datetime(2026, 9, 9, 12, 30, tzinfo=timezone.utc)
        self.assertEqual(app.get_news_timestamp(item), expected.timestamp())

    def test_updated_dates_are_used_for_sorting_and_display(self):
        for item in (
            {"updated_parsed": datetime(2026, 9, 9, 12, 30, tzinfo=timezone.utc).timetuple()},
            {"published": "invalid", "updated": "9 September 2026 - 14:30"},
            {"published_parsed": (2026,), "published": "9 September 2026 - 14:30"},
        ):
            with self.subTest(item=item):
                expected = datetime(2026, 9, 9, 12, 30, tzinfo=timezone.utc)
                self.assertEqual(app.get_news_timestamp(item), expected.timestamp())
                self.assertIn("14:30", app.format_date(item))

    def test_missing_and_malformed_dates_are_safe(self):
        for item in ({}, {"published": "invalid"}, {"published": 123},
                     {"published_parsed": (2026,)}, {"published": "31 February 2026 - 14:30"}):
            with self.subTest(item=item):
                self.assertEqual(app.get_news_timestamp(item), 0)
                self.assertEqual(app.format_date(item), "تاريخ غير متاح")

    def test_homepage_interleaves_real_feed_formats_by_time(self):
        feeds = [
            parse_feed([
                ("NL-1430", "9 September 2026 - 14:30"),
                ("NL-1343", "9 September 2026 - 13:43"),
                ("NL-1259", "9 September 2026 - 12:59"),
            ]),
            parse_feed([
                ("Dutch-1455", "Wed, 09 Sep 2026 12:55:34 +0000"),
                ("Dutch-1323", "Wed, 09 Sep 2026 11:23:07 +0000"),
                ("Undated", "invalid"),
            ]),
        ]
        sources = [app.RSS_SOURCES[0], app.RSS_SOURCES[-1]]
        feeds_by_url = dict(zip((source["url"] for source in sources), feeds))
        with patch.object(app, "RSS_SOURCES", sources), \
                patch.object(app, "fetch_news", side_effect=lambda url: feeds_by_url[url]) as fetch, \
                patch.object(app, "translate_to_arabic", side_effect=lambda text, **kwargs: text):
            response = app.app.test_client().get("/")
            self.assertEqual(response.status_code, 200)
            page = BeautifulSoup(response.data, "html.parser")
            self.assertEqual(
                [heading.get_text(strip=True) for heading in page.select(".news-card h3")],
                ["Dutch-1455", "NL-1430", "NL-1343", "Dutch-1323", "NL-1259", "Undated"],
            )
            self.assertIn("14:55", page.select(".card-date")[0].get_text())
            app.load_news()
            self.assertEqual(fetch.call_count, 2)

    def test_empty_feeds_keep_fallback_news(self):
        with patch.object(app, "fetch_news", return_value=parse_feed([])), \
                patch.object(app, "translate_to_arabic", side_effect=lambda text, **kwargs: text):
            news = app.load_news()
            self.assertEqual(len(news), len(app.fallback_news))
            self.assertTrue(all(item["date"] == "تاريخ غير متاح" for item in news))


if __name__ == "__main__":
    unittest.main()
