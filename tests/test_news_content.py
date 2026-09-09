import unittest
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import feedparser
from bs4 import BeautifulSoup

import app


class NewsContentTests(unittest.TestCase):
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
        app.cached_arabic_translation.cache_clear()
        self.addCleanup(self.restore_state)

    def restore_state(self):
        app._news_cache, app._news_cache_time, app._news_cache_sources = self.cache
        app.cached_arabic_translation.cache_clear()

    def entries(self, source, count, hour=12):
        return [dict(title=f'{source} news {index}', source_name=source,
                     link=f'https://example.com/{source}/{index}',
                     published_parsed=datetime(2026, 9, 9, hour, index, tzinfo=timezone.utc).timetuple())
                for index in range(count)]

    def test_fifteen_articles_are_selected_from_each_source_in_date_order(self):
        items = sum((self.entries(source, 9, hour) for source, hour in [('A', 10), ('B', 11), ('C', 12)]), [])
        selected = app.select_news_entries(items)
        self.assertEqual(len(selected), 15)
        self.assertEqual(Counter(item['source_name'] for item in selected), {'A': 5, 'B': 5, 'C': 5})
        stamps = [app.get_news_timestamp(item) for item in selected]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        self.assertEqual([item['title'] for item in selected if item['source_name'] == 'A'],
                         [f'A news {index}' for index in range(8, 3, -1)])

    def test_missing_source_places_are_filled_and_duplicates_do_not_count(self):
        items = self.entries('A', 10) + self.entries('B', 10)
        items += [dict(items[0], source_name='C', title=' A NEWS 0 ')]
        selected = app.select_news_entries(items)
        self.assertEqual(len(selected), 15)
        self.assertEqual(len({item['title'].strip().casefold() for item in selected}), 15)
        self.assertEqual(len(app.select_news_entries(self.entries('A', 20))), 15)

    def test_extracts_real_media_rss_and_enclosure_formats(self):
        for media in ('<media:content url="https://cdn.example.com/photo.jpg" medium="image"/>',
                      '<media:thumbnail url="https://cdn.example.com/photo.jpg"/>',
                      '<enclosure url="https://cdn.example.com/photo.jpg" type="image/jpeg" length="2000"/>'):
            with self.subTest(media=media):
                feed = feedparser.parse(f'<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">'
                                        f'<channel><item><title>Image</title>{media}</item></channel></rss>')
                self.assertEqual(app.get_news_image(feed.entries[0]), 'https://cdn.example.com/photo.jpg')

    def test_extracts_dutchnews_html_relative_urls_and_content_images(self):
        cases = [
            ({'summary': '<img width="360" height="240" src="//cdn.example.com/photo.jpg">Text'}, 'https://cdn.example.com/photo.jpg'),
            ({'summary': '<img src="/photo.jpg?a=1&amp;b=2">'}, 'https://example.com/photo.jpg?a=1&b=2'),
            ({'content': [{'value': '<img data-src="/photo.jpg">'}]}, 'https://example.com/photo.jpg'),
            ({'image': {'href': '/photo.jpg'}}, 'https://example.com/photo.jpg'),
        ]
        for item, expected in cases:
            with self.subTest(item=item):
                self.assertEqual(app.get_news_image(dict(item, link='https://example.com/news/story')), expected)

    def test_skips_unsafe_urls_videos_tracking_pixels_and_missing_images(self):
        item = dict(link='https://example.com/news',
                    media_content=[{'url': 'https://example.com/video.mp4', 'medium': 'video'}],
                    summary='<img src="javascript:alert(1)"><img width="1" src="/track.gif"><img src="/real.jpg">')
        self.assertEqual(app.get_news_image(item), 'https://example.com/real.jpg')
        self.assertIsNone(app.get_news_image({'summary': '<img src="data:image/png;base64,aaa">'}))
        self.assertIsNone(app.get_news_image({}))

    def test_translation_cleans_markup_and_reuses_successful_results(self):
        with patch.object(app, 'NewsTranslator') as translator:
            translator.return_value.translate.return_value = '<p>أخبار هولندا اليوم</p>'
            for _ in range(2):
                self.assertEqual(app.translate_to_arabic('<p>Dutch &amp; local news</p>', 'en'), 'أخبار هولندا اليوم')
            translator.assert_called_once_with(source='en', target='ar')
            translator.return_value.translate.assert_called_once_with('Dutch & local news')

    def test_translation_failures_are_not_cached_and_can_recover(self):
        with patch.object(app, 'NewsTranslator') as translator:
            translator.return_value.translate.side_effect = [RuntimeError('offline'), 'أخبار هولندا اليوم']
            self.assertIsNone(app.translate_to_arabic('Dutch news'))
            self.assertEqual(app.translate_to_arabic('Dutch news'), 'أخبار هولندا اليوم')

    def test_empty_unchanged_and_invalid_translations_use_fallback(self):
        for result in (None, '', 'Dutch news', '<script>bad()</script>'):
            with self.subTest(result=result), patch.object(app, 'NewsTranslator') as translator:
                translator.return_value.translate.return_value = result
                self.assertIsNone(app.translate_to_arabic('Dutch news'))
        with patch.object(app, 'NewsTranslator') as translator:
            self.assertEqual(app.translate_to_arabic('أخبار هولندا'), 'أخبار هولندا')
            translator.assert_not_called()

    def test_explicit_language_failure_retries_auto_and_caches_recovery(self):
        with patch.object(app, 'NewsTranslator') as translator:
            translator.return_value.translate.side_effect = [RuntimeError('no translation'), 'خبر من الحكومة الهولندية']
            for _ in range(2):
                self.assertEqual(app.translate_to_arabic('Nederlands nieuws', 'nl'), 'خبر من الحكومة الهولندية')
            self.assertEqual([call.kwargs['source'] for call in translator.call_args_list], ['nl', 'auto'])

    def test_inburgering_is_translated_as_civic_integration_in_english_news(self):
        with patch.object(app, 'NewsTranslator') as translator:
            translator.return_value.translate.return_value = 'امتحان الاندماج المدني'
            app.translate_to_arabic('An inburgering exam', 'en')
            translator.return_value.translate.assert_called_once_with('An civic integration exam')

    def test_long_text_stays_within_service_limit(self):
        with patch.object(app, 'NewsTranslator') as translator:
            translator.return_value.translate.return_value = 'ملخص الخبر باللغة العربية'
            app.translate_to_arabic('Some long news. ' * 600)
            self.assertLess(len(translator.return_value.translate.call_args.args[0]), 5000)

    def test_translation_outage_keeps_readable_cards_originals_and_sources(self):
        item = dict(title='<b>Original English headline</b>', summary='<img src="/photo.jpg"><p>Original summary.</p>',
                    link='https://example.com/story')
        with patch.object(app, 'fetch_news', return_value=SimpleNamespace(entries=[item])), \
                patch.object(app, 'NewsTranslator', side_effect=RuntimeError('offline')):
            client = app.app.test_client()
            news = app.load_news()
            self.assertEqual(len(news), 1)
            record = news[0]
            self.assertEqual(record['title'], 'Original English headline')
            self.assertTrue(record['translation_notice'])
            self.assertTrue(app.is_arabic_text(record['summary']))
            page = BeautifulSoup(client.get('/').data, 'html.parser')
            self.assertEqual(page.select_one('.news-card h3')['dir'], 'auto')
            self.assertEqual(page.select_one('.card-image img')['loading'], 'lazy')
            self.assertIsNotNone(page.select_one('.source-badge bdi'))
            self.assertIsNotNone(page.select_one('.translation-notice'))
            article = BeautifulSoup(client.get('/article/' + record['id']).data, 'html.parser')
            self.assertEqual(article.select_one('.original-excerpt p').get_text(), 'Original summary.')
            self.assertEqual(article.select_one('.article-source-link')['href'], item['link'])

    def test_missing_summary_is_not_presented_as_translation_failure(self):
        with patch.object(app, 'fetch_news', return_value=SimpleNamespace(entries=[{'title': 'خبر عربي'}])):
            record = app.load_news()[0]
            self.assertFalse(record['translation_notice'])
            self.assertIn('لم يوفّر المصدر', record['summary'])
            self.assertIsNone(record['image'])

    def test_homepage_shows_nine_cards_and_cache_is_reused(self):
        feeds = [SimpleNamespace(entries=self.entries(source['name'], 8)) for source in app.RSS_SOURCES]
        feeds_by_url = dict(zip((source['url'] for source in app.RSS_SOURCES), feeds))
        with patch.object(app, 'fetch_news', side_effect=lambda url: feeds_by_url[url]) as fetch, \
                patch.object(app, 'translate_to_arabic', return_value='خبر مترجم باللغة العربية'):
            client = app.app.test_client()
            page = BeautifulSoup(client.get('/').data, 'html.parser')
            self.assertEqual(len(page.select('.news-card')), app.HOME_PAGE_SIZE)
            self.assertEqual(page.select_one('.article-count').get_text(strip=True), '9 من 15 خبرًا')
            self.assertEqual(len(page.select('.card-image')), 0)
            self.assertTrue({badge.get_text() for badge in page.select('.source-badge bdi')})
            client.get('/category/netherlands')
            self.assertEqual(fetch.call_count, len(app.RSS_SOURCES))

    def test_seo_routes_and_home_metadata_are_present(self):
        client = app.app.test_client()
        with patch.object(app, 'load_news', return_value=[]):
            robots = client.get('/robots.txt')
            self.assertEqual(robots.status_code, 200)
            self.assertIn('Sitemap: https://nl-arabic-news.onrender.com/sitemap.xml', robots.get_data(as_text=True))

            sitemap = client.get('/sitemap.xml')
            self.assertEqual(sitemap.status_code, 200)
            self.assertIn('<urlset', sitemap.get_data(as_text=True))

            page = BeautifulSoup(client.get('/').data, 'html.parser')
            self.assertEqual(page.select_one('meta[name="robots"]')['content'].split(',')[0], 'index')
            self.assertEqual(page.select_one('link[rel="canonical"]')['href'], 'https://nl-arabic-news.onrender.com/')
            self.assertIsNotNone(page.select_one('meta[name="description"]'))


if __name__ == '__main__':
    unittest.main()
