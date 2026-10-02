"""Read-only saved Go result reader; verifies bytes before exposing JSON."""
import gzip
import hashlib
import io
import json
import pathlib
import sqlite3


class SavedGo:
    def __init__(self, *, database=None, run_id=None, export=None):
        self.connection = None
        if export:
            self.export = json.loads(pathlib.Path(export).read_text())
            self.manifest = self.export['manifest']
            self.summary = self.export['summary']
            if isinstance(self.manifest, str): self.manifest = json.loads(self.manifest)
            if isinstance(self.summary, str): self.summary = json.loads(self.summary)
            self.strategy = self.export.get('strategy_id', self.manifest.get('strategy_id'))
            return
        if not database or not run_id: raise ValueError('GO_DATABASE_AND_RUN_ID_REQUIRED')
        self.connection = sqlite3.connect(pathlib.Path(database).resolve().as_uri()+'?mode=ro', uri=True)
        self.connection.execute('PRAGMA query_only=ON')
        self.connection.execute('BEGIN')
        self.run_id = run_id
        self.header = self.connection.execute('SELECT manifest,manifest_hash,summary,summary_hash,state,item_count,members_hash,strategy_id FROM app_runs WHERE run_id=?',(run_id,)).fetchone()
        if self.header is None or self.header[4] != 'PUBLISHED': raise ValueError('GO_RUN_NOT_PUBLISHED')
        if hashlib.sha256(self.header[0]).hexdigest() != self.header[1] or hashlib.sha256(self.header[2]).hexdigest() != self.header[3]: raise ValueError('GO_HEADER_IDENTITY')
        self.manifest = json.loads(self.header[0]); self.summary = json.loads(self.header[2]); self.strategy = self.header[7]

    def close(self):
        if self.connection is not None:
            self.connection.close(); self.connection = None

    def records(self):
        if self.connection is None:
            for item in self.export['items']:
                row = item['detail']
                if isinstance(row, str): row = json.loads(row)
                if row['fact']['instrument_id'] != item['instrument_id']: raise ValueError('GO_ITEM_INSTRUMENT_IDENTITY')
                yield row
            return
        members = hashlib.sha256(); count = 0
        for iid, ordinal, encoded, raw_hash, raw_size in self.connection.execute('SELECT instrument_id,ordinal,payload,raw_hash,raw_size FROM app_items WHERE run_id=? ORDER BY ordinal',(self.run_id,)):
            if ordinal != count or not 0 <= raw_size <= 128*1024**2: raise ValueError('GO_ITEM_ORDINAL_OR_SIZE')
            with gzip.GzipFile(fileobj=io.BytesIO(encoded)) as stream: raw = stream.read(raw_size+1)
            if len(raw) != raw_size or hashlib.sha256(raw).hexdigest() != raw_hash: raise ValueError('GO_ITEM_RAW_IDENTITY')
            members.update(f'{ordinal}\0{iid}\0{raw_hash}\n'.encode()); count += 1
            row = json.loads(raw)
            if row['fact']['instrument_id'] != iid: raise ValueError('GO_ITEM_INSTRUMENT_IDENTITY')
            self.current_raw_hash = raw_hash
            self.current_ordinal = ordinal
            yield row
        if count != self.header[5] or members.hexdigest() != self.header[6]: raise ValueError('GO_RUN_MEMBERSHIP_IDENTITY')
