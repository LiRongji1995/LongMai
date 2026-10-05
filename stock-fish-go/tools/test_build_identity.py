import pathlib
import tempfile
import unittest
from build_identity import WORKER_CONFIG_FILES, manifest, worker_config_paths, worker_manifest

class IdentityTest(unittest.TestCase):
    def test_runtime_resources_and_required_frozen_configs_bind_all_three_identities(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_worker_resources_') as tmp:
            repo=pathlib.Path(tmp);root=repo/'stock-fish-go'
            (root/'tools').mkdir(parents=True)
            for name in ('go.mod','go.sum','tools/build_identity.py'):(root/name).write_text('TEST_FIXTURE')
            core=repo/'stock-fish/stockfish_core';core.mkdir(parents=True)
            (core/'yaogu').mkdir()
            resources=[core/'exchange_calendar_2026.json',core/'provider_classification.json',
                       core/'yaogu/TEST_FIXTURE_RUNTIME.yaml',core/'yaogu/TEST_FIXTURE_RUNTIME.yml']
            configs=worker_config_paths(root)
            (repo/'stock-fish/config').mkdir()
            watched=resources+configs
            for path in watched:path.write_text('{"TEST_FIXTURE_VERSION":1}\n')
            def identities():
                return (manifest(root)['sha256'],manifest(root,facts=True)['sha256'],worker_manifest(root)['sha256'])
            for path in watched:
                with self.subTest(resource=path.relative_to(repo).as_posix()):
                    before=identities()
                    path.write_text('{"TEST_FIXTURE_VERSION":2}\n')
                    after=identities()
                    for previous,current in zip(before,after):self.assertNotEqual(previous,current)
            files={entry['path'] for entry in worker_manifest(root)['files']}
            for path in watched:self.assertIn(path.relative_to(repo).as_posix(),files)
            self.assertEqual({p.name for p in configs},set(WORKER_CONFIG_FILES))
            self.assertEqual(len(configs),4)
            self.assertEqual(worker_manifest(root)['contract'],'FIXED_WORKER_SOURCE_MANIFEST_V2')
            before=identities()
            for directory in ('tests','testdata','fixtures','data','datasets','cache'):
                private=core/directory;private.mkdir()
                (private/'TEST_FIXTURE_NOT_SOURCE.json').write_text('{"TEST_FIXTURE_PRIVATE_DATA":1}')
                (private/'TEST_FIXTURE_NOT_SOURCE.yaml').write_text('TEST_FIXTURE_PRIVATE_DATA: 1')
            (repo/'stock-fish/config/TEST_FIXTURE_PRIVATE_CONFIG.json').write_text('{"TEST_FIXTURE_PRIVATE_CONFIG":1}')
            self.assertEqual(before,identities())
            files={entry['path'] for entry in worker_manifest(root)['files']}
            self.assertFalse(any('TEST_FIXTURE_NOT_SOURCE' in path or 'TEST_FIXTURE_PRIVATE_CONFIG' in path for path in files))

    def test_fixed_python_pipeline_and_pins_are_bound_but_tests_are_not(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_worker_identity_') as tmp:
            repo=pathlib.Path(tmp);root=repo/'stock-fish-go'
            (root/'tools').mkdir(parents=True)
            for name in ('go.mod','go.sum','tools/build_identity.py'):(root/name).write_text('fixture')
            pipeline=repo/'stock-fish/stockfish_core/yaogu';pipeline.mkdir(parents=True)
            code=pipeline/'data_pipeline.py';code.write_text('TEST_FIXTURE initial normalization')
            pins=repo/'stock-fish/requirements-rules.txt';pins.write_text('TEST_FIXTURE==1')
            for facts in (False,True):
                before=manifest(root,facts=facts)['sha256']
                code.write_text(code.read_text()+' changed evidence normalization')
                self.assertNotEqual(before,manifest(root,facts=facts)['sha256'])
                before=manifest(root,facts=facts)['sha256']
                pins.write_text(pins.read_text()+' changed SDK pin')
                self.assertNotEqual(before,manifest(root,facts=facts)['sha256'])
                before=manifest(root,facts=facts)['sha256']
                (pipeline/'test_pipeline.py').write_text('TEST_FIXTURE assertions changed')
                self.assertEqual(before,manifest(root,facts=facts)['sha256'])
            self.assertIn('stock-fish/stockfish_core/yaogu/data_pipeline.py',[p['path'] for p in worker_manifest(root)['files']])

    def test_business_change_and_nonbusiness_change(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_identity_') as tmp:
            root=pathlib.Path(tmp);(root/'facts').mkdir();(root/'tools').mkdir()
            for name in ('go.mod','go.sum','tools/build_identity.py'):(root/name).write_text('fixture')
            code=root/'facts/value.go';code.write_text('package facts\nconst value=1\n')
            initial=manifest(root)['sha256']
            (root/'README.md').write_text('new docs')
            (root/'facts/value_test.go').write_text('new tests')
            self.assertEqual(initial,manifest(root)['sha256'])
            code.write_text('package facts\nconst value=2\n')
            self.assertNotEqual(initial,manifest(root)['sha256'])
            before=manifest(root)['sha256'];(root/'go.sum').write_text('changed dependency')
            self.assertNotEqual(before,manifest(root)['sha256'])
            self.assertNotEqual(manifest(root,toolchain='fixture-Go-A')['sha256'],manifest(root,toolchain='fixture-Go-B')['sha256'])

    def test_history_codec_projection_and_schema_are_business_dependencies(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_identity_storage_') as tmp:
            root=pathlib.Path(tmp)
            (root/'tools').mkdir()
            storage=root/'infrastructure/sqlitev2';storage.mkdir(parents=True)
            for name in ('go.mod','go.sum','tools/build_identity.py'):
                (root/name).write_text('fixture')
            for name in ('history.go','store.go','migrate.go','schema.sql','verify_sets.go'):
                (storage/name).write_text('initial '+name)
            for facts in (False,True):
                for name in ('store.go','migrate.go','schema.sql','verify_sets.go'):
                    with self.subTest(facts=facts,dependency=name):
                        before=manifest(root,facts=facts)['sha256']
                        path=storage/name;old=path.read_text()
                        path.write_text(old+' changed contract')
                        self.assertNotEqual(before,manifest(root,facts=facts)['sha256'])
                        path.write_text(old)
                before=manifest(root,facts=facts)['sha256']
                (storage/'readonly.go').write_text('read-only rendering changed')
                (storage/'review.go').write_text('review consumer changed')
                (storage/'store_test.go').write_text('new test assertion')
                self.assertEqual(before,manifest(root,facts=facts)['sha256'])

if __name__=='__main__':unittest.main()
