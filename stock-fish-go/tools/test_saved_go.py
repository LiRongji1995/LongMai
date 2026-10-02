"""TEST_FIXTURE: exported detail shape and DB byte/member corruption boundaries."""
import gzip
import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest
from saved_go import SavedGo

class SavedGoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='TEST_FIXTURE-saved-go-');self.root=pathlib.Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.detail={'fact':{'instrument_id':'TEST_FIXTURE_A','selected_evidence_hashes':['projection_fixture']},'decision':{'candidate_status':'PASS'},'shared_market_ref':'summary.market_context'}
        self.summary={'market_context':{'ecosystem_state':'REPAIR'}};self.manifest={'strategy_id':'STAGE_AWARE_RESEARCH_R1'}
    def database(self):
        path=self.root/'fixture.sqlite';db=sqlite3.connect(path)
        db.executescript('CREATE TABLE app_runs(run_id,manifest,manifest_hash,summary,summary_hash,state,item_count,members_hash,strategy_id); CREATE TABLE app_items(run_id,instrument_id,ordinal,payload,raw_hash,raw_size);')
        manifest=json.dumps(self.manifest).encode();summary=json.dumps(self.summary).encode();raw=json.dumps(self.detail).encode();h=hashlib.sha256(raw).hexdigest()
        members=hashlib.sha256(f'0\0TEST_FIXTURE_A\0{h}\n'.encode()).hexdigest()
        db.execute('INSERT INTO app_runs VALUES(?,?,?,?,?,?,?,?,?)',('fixture',manifest,hashlib.sha256(manifest).hexdigest(),summary,hashlib.sha256(summary).hexdigest(),'PUBLISHED',1,members,self.manifest['strategy_id']))
        db.execute('INSERT INTO app_items VALUES(?,?,?,?,?,?)',('fixture','TEST_FIXTURE_A',0,gzip.compress(raw),h,len(raw)));db.commit();db.close();return path
    def test_export_uses_detail_not_payload(self):
        path=self.root/'export.json';path.write_text(json.dumps({'manifest':self.manifest,'summary':self.summary,'strategy_id':self.manifest['strategy_id'],'items':[{'instrument_id':'TEST_FIXTURE_A','detail':self.detail}]}))
        source=SavedGo(export=path);self.assertEqual(list(source.records()),[self.detail]);source.close()
    def test_database_stream_and_member_hash(self):
        path=self.database();source=SavedGo(database=path,run_id='fixture');self.assertEqual(list(source.records()),[self.detail]);source.close()
        db=sqlite3.connect(path);db.execute("UPDATE app_runs SET members_hash='corrupt'");db.commit();db.close()
        source=SavedGo(database=path,run_id='fixture')
        with self.assertRaisesRegex(ValueError,'MEMBERSHIP'):list(source.records())
        source.close()
    def test_corrupt_payload_rejected_before_json(self):
        path=self.database();db=sqlite3.connect(path);db.execute("UPDATE app_items SET raw_hash='corrupt'");db.commit();db.close()
        source=SavedGo(database=path,run_id='fixture')
        with self.assertRaisesRegex(ValueError,'RAW_IDENTITY'):list(source.records())
        source.close()
    def test_missing_database_never_created(self):
        path=self.root/'missing.sqlite'
        with self.assertRaises(sqlite3.OperationalError):SavedGo(database=path,run_id='fixture')
        self.assertFalse(path.exists())

if __name__=='__main__':unittest.main()
