import pathlib
import tempfile
import unittest
from build_identity import manifest

class IdentityTest(unittest.TestCase):
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

if __name__=='__main__':unittest.main()
