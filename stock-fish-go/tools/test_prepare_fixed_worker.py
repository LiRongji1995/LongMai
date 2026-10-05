"""TEST_FIXTURE source/hash/platform and no-upgrade preparation boundaries."""
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import prepare_fixed_worker as prep

class PreparationTest(unittest.TestCase):
    def artifact(self,name='TEST_FIXTURE-1.0-py3-none-any.whl',kind='bdist_wheel'):
        return {'filename':name,'packagetype':kind,'url':'https://files.pythonhosted.org/TEST_FIXTURE/'+name,
                'size':4096,'digests':{'sha256':'a'*64}}

    def test_official_sources_only_including_redirect_targets(self):
        for url in ('http://pypi.org/file','https://evil.invalid/file','https://user:secret@pypi.org/file'):
            with self.assertRaisesRegex(ValueError,'OFFICIAL_HTTPS_SOURCE_REQUIRED'):prep.official_url(url)
        self.assertEqual(prep.official_url('https://pypi.org/pypi/TEST_FIXTURE/1/json'),'https://pypi.org/pypi/TEST_FIXTURE/1/json')

    def test_locked_compatible_wheel_and_yanked_refusal(self):
        file=self.artifact()
        self.assertEqual(prep.choose_wheel({'urls':[file]},'test-fixture','1.0'),file)
        file['yanked']=True
        with self.assertRaisesRegex(ValueError,'WHEEL_UNAVAILABLE'):
            prep.choose_wheel({'urls':[file]},'test-fixture','1.0')

    def test_only_registered_small_pure_source_can_build_offline(self):
        file=self.artifact('jsonpath-0.82.2.tar.gz','sdist')
        self.assertEqual(prep.choose_wheel({'urls':[file]},'jsonpath','0.82.2'),file)
        with self.assertRaisesRegex(ValueError,'WHEEL_UNAVAILABLE'):
            prep.choose_wheel({'urls':[file]},'unregistered-native-package','0.82.2')
        file['size']=4*1024**2+1
        with self.assertRaisesRegex(ValueError,'WHEEL_UNAVAILABLE'):
            prep.choose_wheel({'urls':[file]},'jsonpath','0.82.2')

    def test_nonfinite_or_expanded_time_cannot_create_environment(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_preparation_') as tmp:
            target=pathlib.Path(tmp)/'new'
            for seconds in (float('nan'),float('inf'),571,-1):
                with self.assertRaisesRegex(ValueError,'TIME_LIMIT_INVALID'):prep.prepare({},target,seconds)
            self.assertFalse(target.exists())

if __name__=='__main__':unittest.main()
