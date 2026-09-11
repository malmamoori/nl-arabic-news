import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

import app
from news_translation import HTTP_TIMEOUT, NewsTranslator


class NewsReliabilityTests(unittest.TestCase):
    def setUp(self):
        for name, value in {
            '_news_cache': None, '_news_cache_time': 0.0, '_news_cache_sources': None,
            '_news_cache_has_errors': False, '_news_status': '', '_source_cache': {},
            '_article_cache': app.OrderedDict(), '_translation_retry_after': 0.0,
        }.items():
            state = patch.object(app, name, value)
            state.start()
            self.addCleanup(state.stop)
        app.cached_arabic_translation.cache_clear()
        self.addCleanup(app.cached_arabic_translation.cache_clear)

    def item(self, title='خبر عربي للاختبار', **extra):
        return dict(title=title, summary='تفاصيل الخبر باللغة العربية.',
                    link='https://example.com/news/' + quote(title, safe=''), **extra)

    def load(self, items):
        with patch.object(app, 'fetch_news', return_value=SimpleNamespace(entries=items)):
            return app.load_news()

    def test_malformed_metadata_cannot_crash_a_card(self):
        for extra in (
            {'media_content': [{'type': 123, 'url': 'https://example.com/image.jpg'}]},
            {'content': 17, 'media_thumbnail': True},
            {'media_content': 123, 'enclosures': False},
            {'image': {'url': ['not a URL']}},
        ):
            with self.subTest(extra=extra):
                record = app.build_news_item(self.item(**extra), 0)
                self.assertEqual(record['title'], 'خبر عربي للاختبار')
                self.assertIsNone(record['image'])
        item = self.item(content={'value': '<p>نص من محتوى الخبر.</p>'})
        item['summary'] = ''
        self.assertEqual(app.build_news_item(item, 0)['body'], 'نص من محتوى الخبر.')

    def test_single_media_object_is_supported(self):
        item = self.item(media_content={'type': 'IMAGE/JPEG', 'url': 'https://example.com/image.jpg'})
        self.assertEqual(app.get_news_image(item), 'https://example.com/image.jpg')

    def test_article_links_reject_unsafe_schemes_and_resolve_relative_paths(self):
        for link in ('javascript:alert(1)', 'data:text/html,bad', 'https://example.com:bad/x',
                     'https://user:password@example.com/x', 'java\nscript:alert(1)'):
            with self.subTest(link=link):
                item = self.item(source_url='https://source.example/feed.xml')
                item['link'] = link
                self.assertEqual(app.build_news_item(item, 0)['link'], 'https://source.example/')
        item['link'] = '/article/42'
        self.assertEqual(app.build_news_item(item, 0)['link'], 'https://source.example/article/42')

    def test_failed_refresh_preserves_last_good_news_and_shows_status(self):
        first = self.load([self.item()])
        app._news_cache_time = 0
        with patch.object(app, 'fetch_news', side_effect=requests.Timeout), \
                self.assertLogs(app.app.logger, level='WARNING'):
            response = app.app.test_client().get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(app._news_cache[0]['id'], first[0]['id'])
        page = BeautifulSoup(response.data, 'html.parser')
        self.assertIn('آخر الأخبار المتاحة', page.select_one('.news-status').get_text())
        self.assertTrue(app._news_cache_has_errors)

    def test_cold_failure_explains_fallback_and_retries_after_a_minute(self):
        with patch.object(app, 'fetch_news', return_value=SimpleNamespace(entries=[])):
            response = app.app.test_client().get('/')
        page = BeautifulSoup(response.data, 'html.parser')
        self.assertIn('روابط عامة للمصادر', page.select_one('.news-status').get_text())
        retry_time = app._news_cache_time + app.NEWS_RETRY_TTL + 1
        with patch.object(app.time, 'monotonic', return_value=retry_time), \
                patch.object(app, 'fetch_news', return_value=SimpleNamespace(entries=[self.item()])) as fetch:
            app.load_news()
        self.assertEqual(fetch.call_count, len(app.RSS_SOURCES))
        self.assertEqual(app._news_status, '')

    def test_one_failed_source_keeps_its_previous_items(self):
        def feed(url):
            index = next(index for index, source in enumerate(app.RSS_SOURCES) if source['url'] == url)
            return SimpleNamespace(entries=[self.item(f'خبر المصدر {index} رقم {number}') for number in range(5)])
        with patch.object(app, 'fetch_news', side_effect=feed):
            old = app.load_news()
        app._news_cache_time = 0
        failed_url = app.RSS_SOURCES[1]['url']
        def partial(url):
            if url == failed_url:
                raise requests.Timeout()
            return feed(url)
        with patch.object(app, 'fetch_news', side_effect=partial), self.assertLogs(app.app.logger, level='WARNING'):
            current = app.load_news()
        self.assertEqual(len(current), app.NEWS_LIMIT)
        self.assertEqual({item['id'] for item in old}, {item['id'] for item in current})

    def test_previous_article_link_survives_refresh_without_another_fetch(self):
        old = self.load([self.item()])[0]
        app._news_cache_time = 0
        self.load([self.item('خبر جديد مختلف')])
        with patch.object(app, 'fetch_news', side_effect=AssertionError('unexpected fetch')) as fetch:
            response = app.app.test_client().get('/article/' + old['id'])
        self.assertEqual(response.status_code, 200)
        self.assertIn(old['title'], response.get_data(as_text=True))
        fetch.assert_not_called()

    def test_article_body_appears_once(self):
        unique_intro = 'بداية فريدة لنص الخبر.'
        item = self.item()
        item['summary'] = unique_intro + ' تفاصيل إضافية في هذا الخبر.' * 35
        record = self.load([item])[0]
        page = BeautifulSoup(app.app.test_client().get('/article/' + record['id']).data, 'html.parser')
        self.assertEqual(page.select_one('.article-content').get_text().count(unique_intro), 1)
        self.assertEqual(page.select_one('.article-lead').get_text(), record['body'])

    def test_cached_page_remains_available_during_another_refresh(self):
        old = self.load([self.item()])
        app._news_cache_time = 0
        started, release = Event(), Event()
        def delayed_feed(url):
            started.set()
            if not release.wait(3):
                raise AssertionError('Refresh did not release')
            return SimpleNamespace(entries=[self.item('خبر بعد التحديث')])
        with patch.object(app, 'fetch_news', side_effect=delayed_feed) as fetch:
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(app.load_news)
                try:
                    self.assertTrue(started.wait(2))
                    self.assertIs(app.load_news(), old)
                finally:
                    release.set()
                self.assertEqual(pending.result(timeout=3)[0]['title'], 'خبر بعد التحديث')
            self.assertEqual(fetch.call_count, len(app.RSS_SOURCES))

    def test_translation_timeout_stops_repeated_requests_and_cached_success_still_works(self):
        with patch.object(app, 'NewsTranslator') as translator:
            translator.return_value.translate.side_effect = ['خبر مترجم محفوظ', requests.Timeout()]
            self.assertEqual(app.translate_to_arabic('Saved news', 'en'), 'خبر مترجم محفوظ')
            self.assertIsNone(app.translate_to_arabic('Timeout news', 'en'))
            self.assertIsNone(app.translate_to_arabic('Another news item', 'en'))
            self.assertEqual(app.translate_to_arabic('Saved news', 'en'), 'خبر مترجم محفوظ')
            self.assertEqual(translator.return_value.translate.call_count, 2)

    def test_feed_request_has_timeout_and_closes_response(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.content = b'<rss version="2.0"><channel><item><title>News</title></item></channel></rss>'
        response.headers = {'content-type': 'application/rss+xml'}
        with patch.object(app.requests, 'get', return_value=response) as get:
            self.assertEqual(len(app.fetch_news('https://example.com/feed').entries), 1)
        self.assertEqual(get.call_args.kwargs['timeout'], HTTP_TIMEOUT)
        response.raise_for_status.assert_called_once()
        response.__exit__.assert_called_once()

    def test_translation_transport_has_timeout_and_handles_both_html_formats(self):
        for css_class in ('result-container', 't0'):
            with self.subTest(css_class=css_class):
                response = MagicMock()
                response.__enter__.return_value = response
                response.text = f'<div class="{css_class}">ترجمة عربية</div>'
                with patch('news_translation.requests.get', return_value=response) as get:
                    self.assertEqual(NewsTranslator(source='nl').translate('Nieuws'), 'ترجمة عربية')
                self.assertEqual(get.call_args.kwargs['timeout'], HTTP_TIMEOUT)
                self.assertEqual(get.call_args.kwargs['params']['sl'], 'nl')
                response.__exit__.assert_called_once()


if __name__ == '__main__':
    unittest.main()
