"""TEST_FIXTURE actual loopback HTTP admission; no public provider or SDK IO."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pathlib
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / 'stock-fish'))
from stockfish_core.yaogu import provider_budget, provider_requests_worker as worker


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def response(self, status, body=b'TEST_FIXTURE', location=None):
        self.send_response(status)
        if location:
            self.send_header('Location', location)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def record(self):
        with self.server.fixture_lock:
            self.server.fixture_hits.append((self.command, self.path))
            return sum(path == self.path for _, path in self.server.fixture_hits)

    def do_GET(self):
        count = self.record()
        if self.path.startswith('/redirect/'):
            remaining = int(self.path.rsplit('/', 1)[-1])
            if remaining:
                return self.response(302, location='/redirect/' + str(remaining - 1))
        elif self.path == '/cross':
            return self.response(302, location=self.server.fixture_cross + '/redirect/1')
        elif self.path == '/retry' and count <= 2:
            return self.response(503)
        elif self.path == '/error':
            return self.response(500)
        elif self.path == '/disconnect':
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.response(200)

    def do_CONNECT(self):
        self.record()
        self.response(502)


class HttpAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_http_admission_')
        self.addCleanup(self.temp.cleanup)
        self.ledger = pathlib.Path(self.temp.name) / 'TEST_FIXTURE_transport.sqlite3'
        self.original_transport, self.original_counts = worker._transport, worker._attempt_counts
        self.addCleanup(self.restore)
        self.servers = []
        for _ in range(2):
            server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
            server.daemon_threads = True
            server.fixture_lock, server.fixture_hits = threading.Lock(), []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.servers.append((server, thread))
        self.addCleanup(self.close_servers)
        self.url = 'http://127.0.0.1:' + str(self.servers[0][0].server_port)
        self.servers[0][0].fixture_cross = 'http://localhost:' + str(self.servers[1][0].server_port)

    def restore(self):
        worker._transport, worker._attempt_counts = self.original_transport, self.original_counts

    def close_servers(self):
        for server, thread in self.servers:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def budget(self, maximum):
        self.budget_id = provider_budget.initialize(self.ledger, maximum)
        worker._transport = dict(budget_path=str(self.ledger), budget_id=self.budget_id,
                                 queue_request_id='TEST_FIXTURE_REQUEST', provider='TEST_FIXTURE',
                                 endpoint='TEST_FIXTURE_HTTP', max_http_requests=maximum)
        worker._attempt_counts = {'HTTP': 0, 'SDK': 0}

    def counts(self):
        return provider_budget.counts(self.ledger, self.budget_id)

    def session(self, retries=None):
        session = requests.Session()
        session.trust_env = False
        if retries is not None:
            session.mount('http://', HTTPAdapter(max_retries=retries))
        self.addCleanup(session.close)
        return session

    def test_requests_cross_host_redirects_each_charge_before_send(self):
        self.budget(3)
        with worker.http_attempt_admission():
            response = self.session().get(self.url + '/cross', timeout=2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts()['HTTP'], 3)
        self.assertEqual(worker._attempt_counts['HTTP'], 3)
        self.assertEqual(sum(len(server.fixture_hits) for server, _ in self.servers), 3)

    def test_redirect_over_limit_never_sends_next_hop(self):
        self.budget(2)
        with worker.http_attempt_admission(), self.assertRaises(provider_budget.HttpBudgetExhausted):
            self.session().get(self.url + '/redirect/2', timeout=2)
        self.assertEqual(self.counts()['HTTP'], 2)
        self.assertEqual(len(self.servers[0][0].fixture_hits), 2)

    def test_urllib3_adapter_status_retries_share_hard_admission(self):
        self.budget(3)
        retries = Retry(total=3, status=3, status_forcelist=[503], allowed_methods={'GET'}, backoff_factor=0)
        with worker.http_attempt_admission():
            response = self.session(retries).get(self.url + '/retry', timeout=2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts()['HTTP'], 3)
        self.assertEqual(len(self.servers[0][0].fixture_hits), 3)

    def test_urllib3_retry_limit_prevents_third_real_request(self):
        self.budget(2)
        retries = Retry(total=3, status=3, status_forcelist=[503], allowed_methods={'GET'}, backoff_factor=0)
        with worker.http_attempt_admission(), self.assertRaises(provider_budget.HttpBudgetExhausted):
            self.session(retries).get(self.url + '/retry', timeout=2)
        self.assertEqual(self.counts()['HTTP'], 2)
        self.assertEqual(len(self.servers[0][0].fixture_hits), 2)

    def test_stdlib_urllib_redirects_use_same_ledger(self):
        self.budget(3)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with worker.http_attempt_admission(), opener.open(self.url + '/redirect/2', timeout=2) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.counts()['HTTP'], 3)
        self.assertEqual(len(self.servers[0][0].fixture_hits), 3)

    def test_failed_response_and_connection_attempt_stay_charged(self):
        self.budget(2)
        with worker.http_attempt_admission():
            with self.assertRaises(requests.HTTPError):
                self.session().get(self.url + '/error', timeout=2).raise_for_status()
            with self.assertRaises(requests.ConnectionError):
                self.session().get(self.url + '/disconnect', timeout=2)
            with self.assertRaises(provider_budget.HttpBudgetExhausted):
                self.session().get(self.url + '/redirect/0', timeout=2)
        self.assertEqual(self.counts()['HTTP'], 2)
        self.assertEqual(len(self.servers[0][0].fixture_hits), 2)

    def test_tls_connection_failure_before_headers_is_charged(self):
        self.budget(1)
        with worker.http_attempt_admission(), self.assertRaises(requests.ConnectionError):
            self.session().get(self.url.replace('http:', 'https:') + '/TEST_FIXTURE_TLS', timeout=2)
        self.assertEqual(self.counts()['HTTP'], 1)

    def test_durable_limit_survives_worker_counters_restart_and_sdk_is_separate(self):
        self.budget(2)
        with worker.http_attempt_admission():
            self.session().get(self.url + '/redirect/0', timeout=2)
            worker._attempt_counts = {'HTTP': 0, 'SDK': 0}
            for _ in range(3):
                worker.reserve_attempt('SDK')  # only fixture accounting; no SDK operation
            self.session().get(self.url + '/redirect/0', timeout=2)
            with self.assertRaises(provider_budget.HttpBudgetExhausted):
                self.session().get(self.url + '/redirect/0', timeout=2)
        self.assertEqual(self.counts(), {'HTTP': 2, 'SDK': 3})
        self.assertEqual(len(self.servers[0][0].fixture_hits), 2)

    def test_unregistered_adapter_blocks_before_network(self):
        self.budget(1)
        class UnregisteredAdapter(HTTPAdapter):
            pass
        session = self.session()
        session.mount('http://', UnregisteredAdapter())
        with worker.http_attempt_admission(), self.assertRaisesRegex(worker.UnsupportedHttpTransport, 'ONLINE_BLOCKED'):
            session.get(self.url + '/redirect/0', timeout=2)
        self.assertEqual(self.counts(), {})
        self.assertEqual(len(self.servers[0][0].fixture_hits), 0)

    def test_connect_tunnel_is_an_additional_charged_http_attempt(self):
        self.budget(2)
        connection = http.client.HTTPConnection('127.0.0.1', self.servers[0][0].server_port, timeout=2)
        self.addCleanup(connection.close)
        connection.set_tunnel('TEST_FIXTURE.invalid', 80)
        with worker.http_attempt_admission(), self.assertRaises(OSError):
            connection.request('GET', '/TEST_FIXTURE_TUNNEL')
        self.assertEqual(self.counts()['HTTP'], 2)
        self.assertEqual(self.servers[0][0].fixture_hits[0][0], 'CONNECT')


if __name__ == '__main__':
    unittest.main()
