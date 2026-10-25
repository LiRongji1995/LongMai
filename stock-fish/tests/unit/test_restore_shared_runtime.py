"""TEST_FIXTURE only: no supplier calls, network downloads, or real runtime install."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import restore_shared_runtime as subject


class Response(io.BytesIO):
    def __init__(self, payload, length=None):
        super().__init__(payload)
        self.headers = {} if length is None else {'Content-Length': str(length)}


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, url, timeout):
        self.calls.append((url, timeout))
        return self.response


def package(raw=b'TEST_FIXTURE_artifact'):
    return {'url': 'https://files.pythonhosted.org/packages/test.whl',
            'filename': 'TEST_FIXTURE_artifact.whl', 'sha256': hashlib.sha256(raw).hexdigest()}


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_restore_')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.stage = self.root / 'stage'
        self.stage.mkdir()
        self.meter = subject.Meter(self.root, download_limit=1024*1024, file_limit=512*1024,
                                   disk_limit=16*1024*1024, min_free=0, cache_read_limit=1024*1024)

    def test_download_hash_and_measurement(self):
        raw = b'TEST_FIXTURE_artifact'
        opener = FakeOpener(Response(raw, len(raw)))
        subject.download(package(raw), self.stage, self.meter, opener=opener)
        self.assertEqual((self.stage/package()['filename']).read_bytes(), raw)
        self.assertEqual(self.meter.data['download_bytes'], len(raw))
        self.assertEqual(opener.calls, [(package()['url'], 30)])

    def test_no_content_length_still_streams_bounded(self):
        raw = b'TEST_FIXTURE'
        subject.download(package(raw), self.stage, self.meter, opener=FakeOpener(Response(raw)))
        self.assertEqual(self.meter.data['downloaded_artifacts'], 1)

    def test_content_length_oversize_fails_before_body(self):
        response = Response(b'', self.meter.file_limit + 1)
        with self.assertRaisesRegex(subject.RestoreError, 'DOWNLOAD_FILE_LIMIT'):
            subject.download(package(), self.stage, self.meter, opener=FakeOpener(response))
        self.assertEqual(self.meter.data['download_bytes'], 0)

    def test_false_small_content_length_does_not_bypass_actual_limit(self):
        raw = b'x' * 128
        self.meter.file_limit = 64
        with self.assertRaisesRegex(subject.RestoreError, 'DOWNLOAD_LENGTH_MISMATCH|ARTIFACT_HASH_MISMATCH'):
            subject.download(package(raw), self.stage, self.meter,
                             opener=FakeOpener(Response(raw, 1)))
        self.assertLessEqual((self.stage/package()['filename']).stat().st_size, 64)
        self.assertEqual(self.meter.data['download_bytes'], 64)

    def test_total_budget_preflight(self):
        self.meter.data['download_bytes'] = self.meter.download_limit-1
        with self.assertRaisesRegex(subject.RestoreError, 'DOWNLOAD_TOTAL_LIMIT'):
            subject.download(package(), self.stage, self.meter,
                             opener=FakeOpener(Response(b'xx', 2)))

    def test_hash_mismatch_not_counted_success(self):
        with self.assertRaisesRegex(subject.RestoreError, 'ARTIFACT_HASH_MISMATCH'):
            subject.download(package(), self.stage, self.meter,
                             opener=FakeOpener(Response(b'wrong')))
        self.assertEqual(self.meter.data['downloaded_artifacts'], 0)

    def test_bad_length_fails(self):
        with self.assertRaisesRegex(subject.RestoreError, 'DOWNLOAD_LENGTH_MISMATCH'):
            subject.download(package(b'x'), self.stage, self.meter,
                             opener=FakeOpener(Response(b'x', 2)))

    def test_nonofficial_url_rejected_without_network(self):
        item = package()
        item['url'] = 'http://files.pythonhosted.org/unsafe.whl'
        opener = FakeOpener(Response(b''))
        with self.assertRaisesRegex(subject.RestoreError, 'OFFICIAL_ARTIFACT_REQUIRED'):
            subject.download(item, self.stage, self.meter, opener=opener)
        self.assertEqual(opener.calls, [])

    def test_redirect_rejected(self):
        with self.assertRaisesRegex(subject.RestoreError, 'ARTIFACT_REDIRECT_REJECTED'):
            subject.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other/')

    def test_cache_hash_reuse_and_meter(self):
        raw = b'TEST_FIXTURE_artifact'
        cache = self.root/'pip'; body = cache/'http-v2'/'a'/'hash.body'
        body.parent.mkdir(parents=True); body.write_bytes(raw)
        missing = subject.reuse_cache([package(raw)], [cache], self.stage, self.meter)
        self.assertEqual(missing, [])
        self.assertEqual(self.meter.data['cache_read_bytes'], 2*len(raw))
        self.assertEqual(self.meter.data['cache_reused_bytes'], len(raw))
        self.assertEqual(self.meter.data['http_requests'], 0)

    def test_cache_ignores_env_other_subdirs_and_symlinks(self):
        cache = self.root/'pip'; cache.mkdir()
        (cache/'.env').write_text('TEST_FIXTURE_not_read')
        (cache/'private').mkdir(); (cache/'private'/'bad.body').write_text('TEST_FIXTURE')
        (cache/'wheels').mkdir(); (cache/'wheels'/'bad.whl').symlink_to(cache/'.env')
        result = subject.reuse_cache([package()], [cache], self.stage, self.meter)
        self.assertEqual(result, [package()])
        self.assertEqual(self.meter.data['cache_read_bytes'], 0)

    def test_cache_budget_does_not_expand(self):
        cache = self.root/'pip'; body = cache/'http-v2'/'hash.body'
        body.parent.mkdir(parents=True); body.write_bytes(b'x'*100)
        self.meter.cache_read_limit = 50
        self.assertEqual(subject.reuse_cache([package()], [cache], self.stage, self.meter), [package()])
        self.assertEqual(self.meter.data['cache_read_bytes'], 0)

    def test_cache_copy_failure_removes_only_partial_and_allows_download(self):
        raw = b'TEST_FIXTURE_artifact'
        cache = self.root/'pip'; body = cache/'http-v2'/'hash.body'
        body.parent.mkdir(parents=True); body.write_bytes(raw)
        original_digest = subject.digest_file
        def changed_during_copy(path, meter, *, copy_to=None):
            if copy_to is not None:
                copy_to.write_bytes(b'TEST_FIXTURE_partial')
                raise subject.RestoreError('CACHE_FILE_CHANGED')
            return original_digest(path, meter)
        with mock.patch.object(subject, 'digest_file', side_effect=changed_during_copy):
            missing = subject.reuse_cache([package(raw)], [cache], self.stage, self.meter)
        self.assertEqual(missing, [package(raw)])
        self.assertEqual(list(self.stage.iterdir()), [])
        subject.download(package(raw), self.stage, self.meter, opener=FakeOpener(Response(raw)))
        self.assertEqual((self.stage/package()['filename']).read_bytes(), raw)

    def test_exact_download_limit_never_reads_extra_probe_byte(self):
        raw = b'TEST_FIXTURE'
        self.meter.download_limit = len(raw)
        subject.download(package(raw), self.stage, self.meter,
                         opener=FakeOpener(Response(raw+b'not_requested')))
        self.assertEqual(self.meter.data['download_bytes'], len(raw))
        self.assertEqual((self.stage/package()['filename']).read_bytes(), raw)

    def test_cache_scan_metadata_count_bound(self):
        cache = self.root/'pip'; base = cache/'http-v2'; base.mkdir(parents=True)
        for index in range(20):
            (base/str(index)).mkdir()
        self.assertEqual(list(subject.cache_candidates([cache], max_entries=5)), [])

    def test_capacity_fails_before_write(self):
        with self.assertRaisesRegex(subject.RestoreError, 'RESTORE_DISK_BUDGET_EXCEEDED'):
            self.meter.check(self.meter.disk_limit+1)
        self.assertFalse((self.stage/package()['filename']).exists())

    def test_tree_does_not_follow_link(self):
        source = self.root/'outside'; source.mkdir(); (source/'big').write_bytes(b'x'*1024)
        (self.stage/'linked').symlink_to(source, target_is_directory=True)
        result = subject.tree_bytes(self.stage)
        self.assertEqual(result['entries'], 1)
        self.assertLess(result['logical_bytes'], 1024)

    def test_tree_tolerates_disappearing_installer_temp_only(self):
        real = self.stage/'TEST_FIXTURE_kept'; real.write_bytes(b'kept')
        with mock.patch.object(subject.os, 'walk', return_value=[
                (str(self.stage), [], ['TEST_FIXTURE_missing.pyc.tmp', real.name])]):
            result = subject.tree_bytes(self.stage)
        self.assertEqual(result['logical_bytes'], 4)

    def test_tree_permission_error_still_fails(self):
        original_lstat = Path.lstat
        def fail_one(path):
            if path.name == 'TEST_FIXTURE_denied':
                raise PermissionError('TEST_FIXTURE_denied')
            return original_lstat(path)
        with mock.patch.object(subject.os, 'walk', return_value=[
                (str(self.stage), [], ['TEST_FIXTURE_denied'])]), \
             mock.patch.object(Path, 'lstat', fail_one):
            with self.assertRaises(PermissionError):
                subject.tree_bytes(self.stage)

    def test_tree_rejects_root_symlink(self):
        alias = self.root/'TEST_FIXTURE_alias'; alias.symlink_to(self.stage, target_is_directory=True)
        with self.assertRaisesRegex(subject.RestoreError, 'RESTORE_DIRECTORY_REQUIRED'):
            subject.tree_bytes(alias)

    def test_wheel_and_sdist_expansion_count(self):
        wheel = self.stage/'TEST_FIXTURE.whl'
        with zipfile.ZipFile(wheel, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('TEST_FIXTURE/data', b'x'*4096)
        self.assertEqual(subject.expanded_bytes(wheel), 4096)
        tar = self.stage/'TEST_FIXTURE.tar.gz'
        with tarfile.open(tar, 'w:gz') as archive:
            item = tarfile.TarInfo('TEST_FIXTURE/data'); item.size = 32
            archive.addfile(item, io.BytesIO(b'x'*32))
        self.assertEqual(subject.expanded_bytes(tar), 32)

    def test_subprocess_timeout_stops_owned_group(self):
        (self.root/'restore-tmp').mkdir()
        with self.assertRaisesRegex(subject.RestoreError, 'RESTORE_STEP_TIMEOUT'):
            subject.run_step([sys.executable, '-I', '-B', '-c', 'import time;time.sleep(10)'],
                             self.root, self.meter, 'TEST_FIXTURE_timeout', timeout=.05)
        self.assertIsNotNone(self.meter.data['steps'][0]['exit_code'])

    def test_subprocess_growth_fails_watchdog(self):
        (self.root/'restore-tmp').mkdir()
        self.meter.disk_limit = 64*1024
        code = "import pathlib,time;pathlib.Path('TEST_FIXTURE_large').write_bytes(b'x'*131072);time.sleep(10)"
        with self.assertRaisesRegex(subject.RestoreError, 'RESTORE_DISK_BUDGET_EXCEEDED'):
            subject.run_step([sys.executable, '-I', '-B', '-c', code],
                             self.root, self.meter, 'TEST_FIXTURE_disk', timeout=2)
        self.assertLess(self.meter.data['steps'][0]['wall_seconds'], 2)

    def test_existing_destination_preserved(self):
        sentinel = self.root/'TEST_FIXTURE_existing'; sentinel.write_bytes(b'preserved')
        with mock.patch.object(subject.original, 'validate_lock', return_value={}), \
             mock.patch.object(subject.original, 'native_identity', return_value={}):
            with self.assertRaisesRegex(subject.RestoreError, 'DESTINATION_ALREADY_EXISTS'):
                subject.restore(self.root)
        self.assertEqual(sentinel.read_bytes(), b'preserved')

    def test_original_lock_still_validates(self):
        self.assertEqual(len(subject.original.validate_lock()['packages']), 58)

    def test_failed_restore_records_failure_and_removes_only_owned_stage(self):
        destination = self.root/'TEST_FIXTURE_failed_runtime'
        manifest = {'packages': [package()]}
        with mock.patch.object(subject.original, 'validate_lock', return_value=manifest), \
             mock.patch.object(subject.original, 'native_identity', return_value={}), \
             mock.patch.object(subject, 'download', side_effect=subject.RestoreError('TEST_FIXTURE_failure')):
            with self.assertRaisesRegex(subject.RestoreError, 'TEST_FIXTURE_failure'):
                subject.restore(destination, min_free=0)
        result = json.loads((destination/'measurement.json').read_text())
        self.assertEqual(result['status'], 'FAILED')
        self.assertFalse(any(destination.glob('restore-stage-*')))
        self.assertTrue((destination/'restore-tmp').is_dir())
        self.assertFalse(result['database_copied'])

    def test_fixture_orchestration_installs_offline_then_uses_original_verifier(self):
        destination = self.root/'TEST_FIXTURE_runtime'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('TEST_FIXTURE/data', b'TEST_FIXTURE')
        raw = buffer.getvalue(); artifact = package(raw)
        calls = []
        def fixture_download(item, stage, meter):
            (stage/item['filename']).write_bytes(raw)
        def fixture_step(argv, destination, meter, label, *, timeout=900):
            calls.append((label, [str(value) for value in argv]))
        with mock.patch.object(subject.original, 'validate_lock', return_value={'packages': [artifact]}), \
             mock.patch.object(subject.original, 'native_identity', return_value={'TEST_FIXTURE': True}), \
             mock.patch.object(subject, 'download', side_effect=fixture_download), \
             mock.patch.object(subject, 'run_step', side_effect=fixture_step):
            result = subject.restore(destination, min_free=0)
        self.assertEqual(result['status'], 'ACCEPTED_TEST_FIXTURE_RUNTIME')
        self.assertEqual([name for name, _ in calls],
                         ['create-venv', 'ensurepip', 'build-tools', 'runtime', 'verify'])
        for _, argv in calls[2:4]:
            for flag in ['--no-index', '--no-deps', '--require-hashes', '--no-build-isolation', '--no-cache-dir']:
                self.assertIn(flag, argv)
        self.assertIn(str(Path(subject.original.__file__).resolve()), calls[-1][1])
        self.assertFalse(any(destination.glob('restore-stage-*')))


if __name__ == '__main__':
    unittest.main()
