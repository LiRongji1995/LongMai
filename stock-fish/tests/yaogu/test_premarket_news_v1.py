"""Synthetic TEST_FIXTURE HTML/HTTP only; never contacts a real primary site."""
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from stockfish_core.yaogu import premarket_news_v1 as news
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
from tests.yaogu.test_premarket_presentation_v1 import core, item


def cls_article(number, body='财联社1月5日讯，TEST_FIXTURE_STOCK_1与材料正文。'):
    article = dict(id=number, title='TEST_FIXTURE 焦点复盘', content=body,
        ctime=int(datetime(2026, 1, 5, 9, tzinfo=timezone.utc).timestamp()))
    return ('<script id="__NEXT_DATA__" type="application/json">' +
        json.dumps({'props': {'pageProps': {'articleDetail': article}}}, ensure_ascii=False) +
        '</script>').encode()


def stcn_article(body='TEST_FIXTURE 没有业务日期的正文'):
    return ('<div class="detail-title">TEST_FIXTURE 每日收评</div>'
        '<div class="detail-info"><span>2026-01-05 16:00</span>'
        '<span>来源：财联社</span><span>作者：TEST_FIXTURE记者</span></div>'
        '<div class="detail-content">' + body + '</div>').encode()


class Response:
    def __init__(self, raw=b'', status=200, headers=None):
        self.stream = BytesIO(raw)
        self.status = status
        self.headers = headers or {'content-type': 'text/html; charset=utf-8'}
        self.closed = False

    def getcode(self):
        return self.status

    def read(self, size):
        return self.stream.read(size)

    def close(self):
        self.closed = True


class Opener:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.get_method(), timeout))
        response = self.pages[request.full_url]
        if isinstance(response, Exception):
            raise response
        return response


class NewsFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='premarket-news-TEST_FIXTURE-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.core = core([item(1, 400000000)])

    def run_news(self, pages, **config):
        opener = Opener(pages)
        budget = Budget()
        with patch.object(news, '_make_opener', return_value=opener):
            result = news.collect_reported_news(core_run=self.core,
                output_dir=self.root / 'news', budget=budget, config=config)
        document = json.loads(Path(result['metadata']['path']).read_bytes())
        return result, document, opener, budget

    def test_prescreen_empty_targets_issue_no_requests_and_are_not_empty_news(self):
        with patch.object(news, '_make_opener', side_effect=AssertionError('NO_NETWORK_FOR_EMPTY_SCOPE')):
            result = news.collect_reported_news(core_run=self.core, output_dir=self.root/'empty',
                budget=Budget(), target_ids=[])
        self.assertEqual(result['summary']['HTTP_GET_attempts'], 0)
        self.assertEqual(result['summary']['status'], 'NOT_REQUESTED_NO_PRESCREEN_TARGET_NAMES')
        self.assertIsNone(result['summary']['acquisition_complete'])
        self.assertEqual(result['summary']['source_coverage'], 'UNKNOWN')

    def test_prescreen_only_fetches_matching_candidate_articles_without_mutating_core(self):
        before = json.dumps(self.core, sort_keys=True)
        opener = Opener({
            news.HOMES[0]: Response(('<a href="/detail/101">TEST_FIXTURE_STOCK_1</a>'
                '<a href="/detail/102">焦点复盘 其他个股</a>').encode()),
            news.HOMES[1]: Response(b'NO LINKS TEST_FIXTURE'),
            'https://www.cls.cn/detail/101': Response(cls_article(101))})
        with patch.object(news, '_make_opener', return_value=opener):
            result = news.collect_reported_news(core_run=self.core, output_dir=self.root/'targeted',
                budget=Budget(), target_ids=['000001.SZ'])
        self.assertEqual(len(opener.calls), 3)
        self.assertEqual(result['summary']['article_requests_planned'], 1)
        self.assertFalse(result['summary']['general_market_keyword_articles_included'])
        self.assertEqual(json.dumps(self.core, sort_keys=True), before)

    def test_prescreen_target_scope_rejects_duplicates_and_foreign_ids_before_io(self):
        for ids in (['000001.SZ', '000001.SZ'], ['600001.SH'], '000001.SZ', [None]):
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, 'NEWS_TARGET_IDS'):
                news.collect_reported_news(core_run=self.core, output_dir=self.root/'invalid',
                    budget=Budget(), target_ids=ids)
        self.assertFalse((self.root/'invalid').exists())

    def test_real_anchor_discovery_existing_parsers_and_unknown_date(self):
        result, document, opener, budget = self.run_news({
            news.HOMES[0]: Response(('<a href="/detail/101"><span>TEST_FIXTURE 焦点复盘</span></a>'
                '<a href="https://evil.test/detail/9">涨停揭秘</a>').encode()),
            news.HOMES[1]: Response('<a href="/article/detail/202.html">每日收评 TEST_FIXTURE</a>'.encode()),
            'https://www.cls.cn/detail/101': Response(cls_article(101)),
            'https://www.stcn.com/article/detail/202.html': Response(stcn_article()),
        })
        self.assertEqual(result['summary']['HTTP_GET_attempts'], 4)
        self.assertEqual(result['summary']['parsed_articles'], 2)
        self.assertEqual(budget.metrics['calls']['direct_http_get_attempts'], 4)
        self.assertEqual(budget.metrics['pending_request_results'], 0)
        self.assertTrue(all(method == 'GET' and timeout <= 90 for _, method, timeout in opener.calls))
        parsed = [article['parsed'] for article in document['articles']]
        self.assertEqual(parsed[0]['business_date_status'], 'AVAILABLE')
        self.assertEqual(parsed[1]['business_date_status'], 'UNKNOWN')
        self.assertEqual(parsed[1]['origin_family'], 'CLS')
        self.assertNotEqual(parsed[1]['publication_lower'], document['actual_generated_at'])
        self.assertTrue(all(row['economic_attribution'] == 'UNKNOWN' for row in parsed))
        self.assertEqual(result['summary']['economic_attribution'], 'UNKNOWN')
        self.assertIsNone(result['summary']['acquisition_complete'])
        for capture in document['original_HTTP_captures']:
            for role in ('raw', 'receipt'):
                budget.read(capture[role]['path'], expected=capture[role])
            self.assertIsNone(capture['capture']['source_published_at'])

    def test_offline_is_unknown_not_legal_empty_and_performs_no_get(self):
        with patch.object(news, '_make_opener', side_effect=AssertionError('NETWORK_FORBIDDEN')):
            result = news.collect_reported_news(core_run=self.core,
                output_dir=self.root / 'offline', budget=Budget(), config={'offline': True})
        self.assertEqual(result['summary']['HTTP_GET_attempts'], 0)
        self.assertEqual(result['summary']['status'], 'OFFLINE_NOT_COLLECTED_UNKNOWN')
        self.assertEqual(result['summary']['source_coverage'], 'UNKNOWN')
        self.assertIsNone(result['summary']['acquisition_complete'])

    def test_malicious_redirect_is_archived_and_never_followed(self):
        result, document, opener, _ = self.run_news({
            news.HOMES[0]: Response(b'REDIRECT TEST_FIXTURE', 302,
                {'location': 'https://cls.cn.evil.test/detail/5'}),
            news.HOMES[1]: Response(b'NO ANCHORS TEST_FIXTURE'),
        })
        self.assertEqual(result['summary']['HTTP_GET_attempts'], 2)
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(document['original_HTTP_captures'][0]['redirect_status'],
                         'DENIED_OUTSIDE_SAME_PRIMARY_FAMILY')
        self.assertIs(result['summary']['acquisition_complete'], False)

    def test_complete_declared_sampling_is_true_without_economic_authority(self):
        result, document, _, _ = self.run_news({
            news.HOMES[0]: Response(b'REDIRECT TEST_FIXTURE', 302,
                {'location': 'https://cls.cn/'}),
            'https://cls.cn/': Response('<a href="/detail/101">焦点复盘 TEST_FIXTURE</a>'.encode()),
            news.HOMES[1]: Response(b'NO MATCHING ANCHORS TEST_FIXTURE'),
            'https://www.cls.cn/detail/101': Response(cls_article(101)),
        })
        summary = result['summary']
        self.assertIs(summary['acquisition_complete'], True)
        self.assertEqual(summary['homepages_completed'], 2)
        self.assertEqual(summary['article_requests_planned'], 1)
        self.assertEqual(summary['business_date_status_counts'], {'AVAILABLE': 1})
        self.assertEqual(summary['HTTP_GET_attempts'], 4)
        self.assertEqual(summary['source_coverage'], 'UNKNOWN')
        self.assertEqual(summary['economic_attribution'], 'UNKNOWN')
        self.assertEqual(summary['EVENT_confirmation'], 'UNKNOWN_NOT_ISSUED')
        self.assertTrue(document['no_SOURCE_or_EVENT_capability_issued'])

    def test_ten_get_ceiling_includes_homepages_and_has_no_retry(self):
        anchors = ''.join(f'<a href="/detail/{index}">焦点复盘 TEST_FIXTURE {index}</a>'
                          for index in range(1, 20)).encode()
        pages = {news.HOMES[0]: Response(anchors), news.HOMES[1]: Response(b'NO ANCHORS')}
        pages.update({f'https://www.cls.cn/detail/{index}': Response(cls_article(index))
                      for index in range(1, 20)})
        result, _, opener, _ = self.run_news(pages)
        self.assertEqual(len(opener.calls), 10)
        self.assertEqual(result['summary']['articles_saved'], 8)
        self.assertEqual(result['summary']['automatic_retries'], 0)

    def test_oversized_and_failed_body_never_claim_complete_original(self):
        result, document, _, budget = self.run_news({
            news.HOMES[0]: Response(b'x' * 100), news.HOMES[1]: URLError('TEST_FIXTURE_NETWORK'),
        }, max_body_bytes=64)
        oversized, failed = document['original_HTTP_captures']
        self.assertEqual(oversized['capture']['status'], 'BODY_LIMIT_EXCEEDED')
        self.assertEqual(oversized['raw']['bytes'], 64)
        self.assertEqual(oversized['capture']['actual_body_bytes_returned'], 65)
        self.assertFalse(oversized['capture']['raw_capture_complete'])
        self.assertTrue(oversized['capture']['archived_prefix_only'])
        self.assertEqual(failed['capture']['status'], 'FETCH_FAILED')
        self.assertFalse(failed['capture']['raw_capture_complete'])
        self.assertEqual(result['summary']['parsed_articles'], 0)
        self.assertEqual(budget.metrics['calls']['http_actual'], 2)

    def test_request_limit_propagates_and_no_second_network_attempt_occurs(self):
        opener = Opener({news.HOMES[0]: Response(b'NO ANCHORS')})
        with patch.object(news, '_make_opener', return_value=opener):
            with self.assertRaises(BudgetExceeded):
                news.collect_reported_news(core_run=self.core, output_dir=self.root / 'limited',
                    budget=Budget({'max_requests': 1}))
        self.assertEqual(len(opener.calls), 1)

    def test_body_network_failure_retains_the_real_partial_prefix(self):
        class Interrupted(Response):
            def read(self, size):
                if self.stream.tell():
                    raise URLError('TEST_FIXTURE_INTERRUPTED_BODY')
                return super().read(4)
        result, document, _, _ = self.run_news({
            news.HOMES[0]: Interrupted(b'PARTIAL TEST_FIXTURE'),
            news.HOMES[1]: Response(b'NO ANCHORS'),
        })
        captured = document['original_HTTP_captures'][0]
        self.assertEqual(captured['capture']['status'], 'FETCH_FAILED')
        self.assertEqual(captured['capture']['actual_body_bytes_returned'], 4)
        self.assertEqual(Path(captured['raw']['path']).read_bytes(), b'PART')
        self.assertTrue(captured['capture']['archived_prefix_only'])
        self.assertFalse(captured['capture']['raw_capture_complete'])
        self.assertEqual(len(result['validation_references']), 5)

    def test_early_eof_with_known_content_length_is_not_complete(self):
        _, document, _, _ = self.run_news({
            news.HOMES[0]: Response(b'PREFIX', headers={'Content-Length': '100'}),
            news.HOMES[1]: Response(b'NO ANCHORS'),
        })
        record = document['original_HTTP_captures'][0]['capture']
        self.assertEqual(record['status'], 'CONTENT_LENGTH_MISMATCH_OR_INVALID')
        self.assertFalse(record['raw_capture_complete'])
        self.assertTrue(record['archived_prefix_only'])

    def test_timeout_must_not_exceed_registered_profile(self):
        opener = Opener({home: Response(b'NO ANCHORS') for home in news.HOMES})
        with patch.object(news, '_make_opener', return_value=opener):
            news.collect_reported_news(core_run=self.core, output_dir=self.root / 'timeout',
                budget=Budget({'request_timeout_seconds': 30}))
        self.assertTrue(all(timeout == 30 for _, _, timeout in opener.calls))
        with self.assertRaisesRegex(ValueError, 'TIMEOUT'):
            news.collect_reported_news(core_run=self.core, output_dir=self.root / 'invalid-timeout',
                budget=Budget({'request_timeout_seconds': 30}), config={'request_timeout_seconds': 90})
        self.assertFalse((self.root / 'invalid-timeout').exists())


if __name__ == '__main__':
    unittest.main()
