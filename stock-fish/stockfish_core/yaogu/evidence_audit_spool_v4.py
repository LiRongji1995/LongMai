"""Explicit dictionary-row audit journal with bounded, process-local LRU.

Every ordinary lookup reads/authenticates its complete physical frame even on
cache hits. Complete canonical iteration never uses the LRU. This is an audit
backend, not a source capability, and no default caller is changed.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import struct
import zlib

from . import evidence_audit_spool_v2 as v2
from . import evidence_audit_spool_v3 as v3
from .history_artifacts import digest_json_stream
from .visibility import EvidenceResolver, POLICY_ID

VERSION = 'EXACT_EVIDENCE_ROW_AUDIT_DICTIONARY_LRU_JOINT_SPOOL_V4'
SEGMENT_BYTES = v2.SEGMENT_BYTES
CHUNK_BYTES = v3.CHUNK_BYTES
FP_FIELDS = v2.FP_FIELDS
INDEX_STRUCT = struct.Struct('>32s32sQIII')
INDEX_ENTRY_BYTES = 84
INDEX_HEADER_BYTES = 4096
EvidenceAuditIntegrityError = v2.EvidenceAuditIntegrityError


def _require(value, reason):
    if not value:
        raise EvidenceAuditIntegrityError('EVIDENCE_AUDIT_V4_' + reason)


def _cache_limit(value, maximum, name):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('EVIDENCE_AUDIT_V4_' + name + '_INVALID')
    return value


class DictionaryRowJournalV4(v3._CompressedRowJournal):
    """An owned append journal; JSON, even a valid manifest, cannot restore it."""
    def __init__(self, directory, segment_bytes, logical_read_callback, *,
                 cache_max_bytes, cache_max_entries):
        self.cache_max_bytes = _cache_limit(cache_max_bytes, 2**63-1, 'CACHE_BYTES')
        self.cache_max_entries = _cache_limit(cache_max_entries, 2**32-1, 'CACHE_ENTRIES')
        self._cache_limit_identity = (self.cache_max_bytes, self.cache_max_entries)
        self.logical_read_callback = logical_read_callback
        self.logical_read_bytes = self.logical_read_calls = 0
        self._fatal = None
        self._logical_sizes, self._logical_hashers = [], []
        self._dictionaries, self._dictionary_paths, self._first_keys = [], [], []
        self._cache = OrderedDict()
        self._cache_bytes = self._cache_peak_bytes = self._cache_peak_entries = 0
        self._cache_counters = dict(ordinary_accesses=0, ordinary_frame_reads=0,
            hits=0, misses=0, evictions=0, uncacheable_rows=0)
        self._read_buckets = {}
        self._read_phase = 'other'
        self._generation = os.urandom(16).hex()
        self._cache_owner = object()
        self._written_entries, self._append_slots = {}, {}
        self._final_index_written = False
        self._header = self._segment_manifest_bytes = None
        self._sorted_keys = ()
        self._verified_pre = self._verified_pre_sha = None
        if type(segment_bytes) is not int or not 0 < segment_bytes <= SEGMENT_BYTES:
            raise ValueError('EVIDENCE_AUDIT_V4_SEGMENT_LIMIT_INVALID')
        self.directory = Path(os.path.abspath(directory))
        for ancestor in reversed((self.directory.parent, *self.directory.parent.parents)):
            if ancestor.exists() or ancestor.is_symlink():
                _require(not ancestor.is_symlink() and ancestor.is_dir(), 'DIRECTORY_PATH_NOT_REGULAR')
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self._directory_identity = self._identity(self.directory.lstat())
        self.limit = segment_bytes
        self._index, self._order, self._ledger = {}, [], {}
        self.segments = []
        self.writer = None
        self.append_index_path = self.directory / 'record-index-append.bin'
        self.sorted_index_path = self.directory / 'record-index.bin'
        self.segment_manifest_path = self.directory / 'dictionary-segments.json'
        self.index_path = self.append_index_path
        self.index_writer = self._new_writer(self.append_index_path)
        self.closed = False
        self.sealed = None
        self._manifest_pin = None
        self.read_bytes = self.read_calls = 0
        self.failures = []

    def _guard(self):
        super()._guard()
        if (type(self.cache_max_bytes) is not int or type(self.cache_max_entries) is not int or
                (self.cache_max_bytes, self.cache_max_entries) != self._cache_limit_identity):
            exc = EvidenceAuditIntegrityError('EVIDENCE_AUDIT_V4_CACHE_CONFIG_CHANGED')
            self._failed(exc, 'CACHE_CONFIG')
            raise exc

    @contextmanager
    def _phase(self, name):
        before = self._read_phase
        self._read_phase = name
        try:
            yield
        finally:
            self._read_phase = before

    def _read(self, source, size, *, line=False):
        raw = super()._read(source, size, line=line)
        self._read_buckets[self._read_phase] = self._read_buckets.get(self._read_phase, 0) + len(raw)
        return raw

    @staticmethod
    def _pack(key, position):
        number, offset, compressed, logical, frame_sha = position
        _require(type(key) is str and len(key) == 64 and all(c in '0123456789abcdef' for c in key), 'KEY_FORMAT')
        _require(type(frame_sha) is str and len(frame_sha) == 64 and all(c in '0123456789abcdef' for c in frame_sha), 'FRAME_HASH_FORMAT')
        _require(all(type(v) is int for v in (number, offset, compressed, logical)) and
            0 <= number < 2**32 and 0 <= offset < 2**64 and
            0 < compressed < 2**32 and 0 < logical < 2**32, 'INDEX_INTEGER_RANGE')
        return INDEX_STRUCT.pack(bytes.fromhex(key), bytes.fromhex(frame_sha),
                                 offset, compressed, logical, number)

    @staticmethod
    def _unpack(raw):
        _require(type(raw) is bytes and len(raw) == INDEX_ENTRY_BYTES, 'INDEX_SLOT_SHORT_READ')
        key, frame, offset, compressed, logical, number = INDEX_STRUCT.unpack(raw)
        return key.hex(), (number, offset, compressed, logical, frame.hex())

    def _write_owned(self, path, raw):
        handle = self._new_writer(path)
        primary = None
        try:
            self._append(path, handle, raw)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException as exc:
            primary = exc
            self.failures.append({'phase':'OWNED_WRITE_PRIMARY', 'error':type(exc).__name__+':'+str(exc)})
            raise
        finally:
            try:
                handle.close()
            except BaseException as exc:
                self.failures.append({'phase':'OWNED_WRITE_CLEANUP', 'error':type(exc).__name__+':'+str(exc)})
                if primary is None:
                    raise

    def _new_segment(self, raw):
        if self.writer is not None:
            self.writer.flush()
            os.fsync(self.writer.fileno())
            self.writer.close()
        number = len(self.segments)
        _require(number < 2**32, 'SEGMENT_NUMBER_OVERFLOW')
        dictionary = raw[-32768:]
        _require(type(dictionary) is bytes and 0 < len(dictionary) <= 32768, 'DICTIONARY_LENGTH')
        path = self.directory / ('dictionary-%06d.bin' % number)
        self._write_owned(path, dictionary)
        self._dictionaries.append(dictionary)
        self._dictionary_paths.append(path)
        self._first_keys.append(hashlib.sha256(raw).hexdigest())
        path = self.directory / ('rows-%06d.zdictframes' % number)
        self.segments.append(path)
        self._logical_sizes.append(0)
        self._logical_hashers.append(hashlib.sha256())
        self.writer = self._new_writer(path)

    @staticmethod
    def _compress(logical, dictionary):
        compressor = zlib.compressobj(level=6, method=zlib.DEFLATED, wbits=15, zdict=dictionary)
        return compressor.compress(logical) + compressor.flush(zlib.Z_FINISH)

    def __setitem__(self, key, value):
        self._guard()
        raw = v2._json(value)
        try:
            _require(not self.closed, 'ALREADY_CLOSED')
            _require(hashlib.sha256(raw).hexdigest() == key, 'ROW_KEY_MISMATCH')
            if key in self._index:
                _require(self[key] == v2._parse(raw), 'ROW_HASH_CONFLICT')
                return
            logical = raw + b'\n'
            _require(len(logical) <= self.limit, 'ROW_EXCEEDS_SEGMENT_LIMIT')
            if self.writer is None or self._logical_sizes[-1] + len(logical) > self.limit:
                self._new_segment(raw)
            number = len(self.segments)-1
            frame = self._compress(logical, self._dictionaries[number])
            _require(0 < len(frame) <= 2*len(logical)+CHUNK_BYTES, 'FRAME_SIZE_BOUND')
            position = (number, self.writer.tell(), len(frame), len(logical), hashlib.sha256(frame).hexdigest())
            entry = self._pack(key, position)
            self._append(self.segments[number], self.writer, frame)
            self._append_slots[key] = len(self._order)
            self._append(self.append_index_path, self.index_writer, entry)
            self._logical_sizes[number] += len(logical)
            self._logical_hashers[number].update(logical)
            self._index[key] = position
            self._written_entries[key] = entry
            self._order.append(key)
        except BaseException as exc:
            self._failed(exc, 'APPEND')
            raise

    def __delitem__(self, key):
        raise TypeError('EVIDENCE_AUDIT_V4_ROWS_ARE_APPEND_ONLY')

    def __getitem__(self, key):
        self._guard()
        try:
            self.flush()
            return self._read_row(key, ordinary=True)
        except BaseException as exc:
            self._failed(exc, 'ROW_ACCESS')
            raise

    def _finish_writers(self):
        # Attempt both original handles even after a primary callback failure.
        v2._RowJournal._finish_writers(self)
        if self._fatal is not None or self._final_index_written:
            return
        try:
            self._sorted_keys = tuple(sorted(self._index))
            _require(len(self._sorted_keys) == len(self._order) == len(self._written_entries) and
                     len(set(self._order)) == len(self._order), 'INDEX_KEY_COVERAGE')
            segments = [{'segment':n, 'dictionary':p.name, 'dictionary_bytes':len(self._dictionaries[n]),
                'dictionary_sha256':hashlib.sha256(self._dictionaries[n]).hexdigest(),
                'dictionary_first_record_hash':self._first_keys[n], 'frames':self.segments[n].name,
                'logical_bytes':self._logical_sizes[n], 'logical_sha256':self._logical_hashers[n].hexdigest()}
                for n,p in enumerate(self._dictionary_paths)]
            manifest = v2._json({'schema_version':VERSION, 'generation':self._generation, 'segments':segments})
            self._write_owned(self.segment_manifest_path, manifest)
            self._segment_manifest_bytes = manifest
            header = v2._json({'magic':'EVIDENCE_INDEX_V4', 'schema_version':VERSION,
                'byte_order':'BIG_ENDIAN', 'entry_bytes':INDEX_ENTRY_BYTES,
                'entry_count':len(self._sorted_keys), 'generation':self._generation,
                'directory_identity':list(self._directory_identity),
                'dictionary_manifest_sha256':hashlib.sha256(manifest).hexdigest()}) + b'\n'
            _require(len(header) <= INDEX_HEADER_BYTES, 'INDEX_HEADER_LIMIT')
            self._header = header + b'\0'*(INDEX_HEADER_BYTES-len(header))
            handle = self._new_writer(self.sorted_index_path)
            primary = None
            try:
                self._append(self.sorted_index_path, handle, self._header)
                for key in self._sorted_keys:
                    entry = self._written_entries[key]
                    _require(entry == self._pack(key, self._index[key]), 'OWNED_INDEX_ENTRY_CHANGED')
                    self._append(self.sorted_index_path, handle, entry)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException as exc:
                primary = exc
                raise
            finally:
                try:handle.close()
                except BaseException as exc:
                    self.failures.append({'phase':'INDEX_CLOSE_CLEANUP', 'error':type(exc).__name__+':'+str(exc)})
                    if primary is None:raise
            self.index_path = self.sorted_index_path
            self._final_index_written = True
        except BaseException as exc:
            self._failed(exc, 'FINAL_INDEX_BUILD')
            raise

    def _index_entry(self, key, pre, ordinary):
        position = self._index[key]
        expected_entry = self._pack(key, position)
        _require(expected_entry == self._written_entries[key], 'OWNED_INDEX_ENTRY_CHANGED')
        path = self.index_path
        expected = (pre['files'][path.name]['fp'] if pre is not None else
                    self.sealed['files'][path.name]['fp'] if self.sealed is not None else None)
        if pre is not None:
            # This exact live object came from this journal's full whole scan.
            # PRE and POST still read both indexes in full; repeating every
            # binary-search/header read per canonical row adds no authority.
            return position, expected_entry, expected
        phase = 'ordinary_index' if ordinary else 'canonical_index'
        with self._reader(path, expected) as (source, pin):
            if self._final_index_written:
                with self._phase('ordinary_header' if ordinary else 'canonical_header'):
                    _require(self._read(source, INDEX_HEADER_BYTES) == self._header, 'INDEX_HEADER_CHANGED')
                low, high = 0, len(self._sorted_keys)-1
                found = None
                with self._phase(phase):
                    while low <= high:
                        middle = (low+high)//2
                        source.seek(INDEX_HEADER_BYTES+middle*INDEX_ENTRY_BYTES)
                        entry = self._read(source, INDEX_ENTRY_BYTES)
                        candidate, value = self._unpack(entry)
                        _require(entry == self._written_entries[self._sorted_keys[middle]] and
                                 candidate == self._sorted_keys[middle], 'SORTED_INDEX_SLOT_CHANGED')
                        if candidate == key:found=entry;break
                        if candidate < key:low=middle+1
                        else:high=middle-1
                _require(found == expected_entry, 'INDEX_KEY_NOT_FOUND_OR_CHANGED')
            else:
                with self._phase(phase):
                    source.seek(self._append_slots[key]*INDEX_ENTRY_BYTES)
                    _require(self._read(source, INDEX_ENTRY_BYTES) == expected_entry, 'APPEND_INDEX_SLOT_CHANGED')
        return position, expected_entry, pin

    def _dictionary(self, number, pre, ordinary):
        path = self._dictionary_paths[number]
        expected = (pre['files'][path.name]['fp'] if pre is not None else
                    self.sealed['files'][path.name]['fp'] if self.sealed is not None else None)
        if pre is not None:
            dictionary = self._dictionaries[number]
            _require(type(dictionary) is bytes and 0 < len(dictionary) <= 32768 and
                     hashlib.sha256(dictionary).hexdigest() == pre['files'][path.name]['sha256'],
                     'VERIFIED_DICTIONARY_IDENTITY')
            manifest_pin = (pre['files'][self.segment_manifest_path.name]['fp']
                            if self._final_index_written else None)
            return dictionary, expected, manifest_pin
        with self._phase('ordinary_dictionary' if ordinary else 'canonical_dictionary'):
            with self._reader(path, expected) as (source, pin):
                dictionary = self._read(source, len(self._dictionaries[number]))
                _require(dictionary == self._dictionaries[number] and type(dictionary) is bytes and
                         0 < len(dictionary) <= 32768 and not self._read(source, 1), 'DICTIONARY_SHA_OR_LENGTH')
        manifest_pin = None
        if self._final_index_written:
            with self._phase('ordinary_segment_manifest' if ordinary else 'canonical_segment_manifest'):
                expected = (pre['files'][self.segment_manifest_path.name]['fp'] if pre is not None else
                            self.sealed['files'][self.segment_manifest_path.name]['fp'] if self.sealed is not None else None)
                with self._reader(self.segment_manifest_path, expected) as (source, manifest_pin):
                    _require(self._read(source, len(self._segment_manifest_bytes)) == self._segment_manifest_bytes and
                             not self._read(source,1), 'SEGMENT_MANIFEST_CHANGED')
        return dictionary, pin, manifest_pin

    @contextmanager
    def _row_metadata_operation(self, pins):
        primary = None
        try:
            for path, pin in pins.items():
                _require(v2._fp(path.lstat()) == pin, 'ROW_METADATA_7FP_CHANGED')
            self._directory_check()
            yield
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                for path, pin in pins.items():
                    _require(v2._fp(path.lstat()) == pin, 'ROW_METADATA_7FP_CHANGED')
                self._directory_check()
            except BaseException as exc:
                if primary is not None:
                    self.failures.append({'phase':'ROW_METADATA_PRIMARY',
                        'error':type(primary).__name__+':'+str(primary)})
                self.failures.append({'phase':'ROW_METADATA_CLEANUP',
                    'error':type(exc).__name__+':'+str(exc)})
                if primary is None:
                    raise

    def _inflate(self, frame, dictionary, logical_bytes, key):
        _require(len(frame) >= 10 and frame[0] & 15 == 8 and frame[0] >> 4 == 7 and
                 ((frame[0]<<8)+frame[1]) % 31 == 0 and frame[1] & 32, 'ZLIB_HEADER_OR_DICTIONARY_FLAG')
        _require(struct.unpack('>I',frame[2:6])[0] == zlib.adler32(dictionary)&0xffffffff,
                 'DICTIONARY_ADLER_ID')
        decoder = zlib.decompressobj(wbits=15,zdict=dictionary)
        chunks, returned = [], 0
        try:
            pending = frame
            while True:
                decoded = decoder.decompress(pending, CHUNK_BYTES)
                self._logical_returned(decoded)
                chunks.append(decoded)
                returned += len(decoded)
                _require(returned <= logical_bytes and returned <= self.limit, 'DECOMPRESSED_LENGTH_LIMIT')
                pending = decoder.unconsumed_tail
                if decoder.eof:
                    _require(not decoder.unused_data and not pending, 'TRAILING_OR_MULTIPLE_ZLIB_FRAME')
                    break
                if pending or len(decoded) == CHUNK_BYTES:
                    continue
                _require(False, 'ZLIB_ADLER_OR_EOF_INCOMPLETE')
        except zlib.error as exc:
            raise EvidenceAuditIntegrityError('EVIDENCE_AUDIT_V4_ZLIB_ADLER_OR_FORMAT_INVALID') from exc
        logical = b''.join(chunks)
        _require(returned == logical_bytes and logical.endswith(b'\n') and
                 hashlib.sha256(logical[:-1]).hexdigest() == key, 'ORIGINAL_LOGICAL_ROW_SHA_MISMATCH')
        _require(struct.unpack('>I',frame[-4:])[0] == zlib.adler32(logical)&0xffffffff, 'LOGICAL_ADLER_TRAILER')
        row = v2._parse(logical[:-1])
        _require(v2._json(row)+b'\n' == logical, 'LOGICAL_ROW_NOT_CANONICAL')
        return logical

    def _cache_put(self, key, logical):
        _require(type(logical) is bytes, 'CACHE_VALUE_NOT_IMMUTABLE_BYTES')
        if not self.cache_max_bytes or not self.cache_max_entries or len(logical)>self.cache_max_bytes:
            self._cache_counters['uncacheable_rows'] += 1
            return
        while self._cache and (self._cache_bytes+len(logical)>self.cache_max_bytes or
                                len(self._cache)>=self.cache_max_entries):
            _,old = self._cache.popitem(last=False)
            self._cache_bytes -= len(old)
            self._cache_counters['evictions'] += 1
        self._cache[key] = logical
        self._cache_bytes += len(logical)
        self._cache_peak_bytes = max(self._cache_peak_bytes,self._cache_bytes)
        self._cache_peak_entries = max(self._cache_peak_entries,len(self._cache))

    def _read_row(self, key, pre=None, *, ordinary=False):
        self._guard()
        try:
            if pre is not None:
                _require(pre is self._verified_pre and
                         hashlib.sha256(v2._json(pre)).hexdigest() == self._verified_pre_sha,
                         'PRE_NOT_CURRENT_OWNED_WHOLE_EPOCH')
            number,offset,compressed,logical,frame_sha = self._index[key]
            _require(0 <= number < len(self.segments) and 0 < logical <= self.limit and
                     0 < compressed <= 2*logical+CHUNK_BYTES, 'FRAME_POSITION_OR_LENGTH_MISMATCH')
            _require(type(ordinary) is bool and not (ordinary and pre is not None), 'CACHE_PATH_NOT_ORDINARY')
            if ordinary:self._cache_counters['ordinary_accesses'] += 1
            if ordinary and self.sealed is not None:
                with self._phase('ordinary_guard_manifest'):
                    self._manifest_check()
            _,entry,index_pin = self._index_entry(key,pre,ordinary)
            dictionary,dict_pin,manifest_pin = self._dictionary(number,pre,ordinary)
            path = self.segments[number]
            expected = (pre['files'][path.name]['fp'] if pre is not None else
                        self.sealed['files'][path.name]['fp'] if self.sealed is not None else None)
            cache_miss = False
            with self._phase('ordinary_frame' if ordinary else 'canonical_frame'):
                with self._reader(path,expected) as (source,frame_pin):
                    source.seek(offset)
                    parts,remaining,hasher = [],compressed,hashlib.sha256()
                    while remaining:
                        size=min(CHUNK_BYTES,remaining);raw=self._read(source,size)
                        _require(len(raw)==size,'FRAME_TRUNCATED')
                        parts.append(raw);hasher.update(raw);remaining-=len(raw)
                    _require(hasher.hexdigest()==frame_sha,'FRAME_PHYSICAL_SHA_MISMATCH')
                    frame=b''.join(parts)
                    cache_key=(self._cache_owner,self._generation,self._directory_identity,entry,
                        tuple(index_pin[k] for k in FP_FIELDS),tuple(frame_pin[k] for k in FP_FIELDS),
                        tuple(dict_pin[k] for k in FP_FIELDS),hashlib.sha256(dictionary).digest(),
                        None if manifest_pin is None else tuple(manifest_pin[k] for k in FP_FIELDS))
                    pins={self.index_path:index_pin,self._dictionary_paths[number]:dict_pin}
                    if manifest_pin is not None:pins[self.segment_manifest_path]=manifest_pin
                    if self._manifest_pin is not None:pins[self.directory/'manifest.json']=self._manifest_pin
                    with self._row_metadata_operation(pins):
                        if ordinary:
                            self._cache_counters['ordinary_frame_reads'] += 1
                            cached=self._cache.get(cache_key)
                            if cache_key in self._cache:
                                _require(type(cached) is bytes,'CACHE_VALUE_NOT_IMMUTABLE_BYTES')
                                self._cache_counters['hits'] += 1
                                self._cache.move_to_end(cache_key)
                                logical_raw=cached
                            else:
                                self._cache_counters['misses'] += 1
                                cache_miss=True
                                logical_raw=self._inflate(frame,dictionary,logical,key)
                        else:
                            logical_raw=self._inflate(frame,dictionary,logical,key)
                        _require(type(logical_raw) is bytes and len(logical_raw)==logical and logical_raw.endswith(b'\n') and
                            hashlib.sha256(logical_raw[:-1]).hexdigest()==key,'CACHED_LOGICAL_ROW_SHA_OR_LENGTH')
                        # A new nested object is returned on every access.
                        row=v2._parse(logical_raw[:-1])
                        _require(v2._json(row)+b'\n'==logical_raw,'CACHED_ROW_NOT_CANONICAL')
            # Do not admit a row until frame/index/dictionary post-guards pass.
            if cache_miss:self._cache_put(cache_key,logical_raw)
            return row
        except BaseException as exc:
            self._failed(exc,'LOGICAL_ROW_READ')
            raise

    def verify(self, expected=None):
        actual = super().verify(expected)
        # Object identity is process-local. Equal saved JSON is not this owner.
        self._verified_pre = actual
        self._verified_pre_sha = hashlib.sha256(v2._json(actual)).hexdigest()
        return actual

    def _manifest_check(self):
        if self._read_phase != 'other':
            return super()._manifest_check()
        with self._phase('guard_manifest'):
            return super()._manifest_check()

    def _scan_file(self, path, phase, expected_raw=None):
        with self._phase(phase):
            with self._reader(path) as (source,pin):
                hasher=hashlib.sha256();parts=[]
                while raw:=self._read(source,CHUNK_BYTES):
                    hasher.update(raw)
                    if expected_raw is not None:parts.append(raw)
                _require(hasher.hexdigest()==self._ledger[path.name]['hasher'].hexdigest(), 'WRITE_LEDGER_SHA_MISMATCH')
                if expected_raw is not None:_require(b''.join(parts)==expected_raw,'OWNED_FILE_CONTENT_CHANGED')
        return {'bytes':pin['st_size'],'sha256':hasher.hexdigest(),'fp':pin}

    def _scan(self):
        """All physical files and exact append/sorted coverage; no LRU/decode."""
        self.flush()
        _require(len(self._order)==len(self._index)==len(self._written_entries) and
                 len(set(self._order))==len(self._order),'INDEX_KEY_COVERAGE')
        files={}
        with self._phase('verify_append_index'):
            with self._reader(self.append_index_path) as (source,pin):
                hasher=hashlib.sha256()
                for slot,key in enumerate(self._order):
                    raw=self._read(source,INDEX_ENTRY_BYTES);hasher.update(raw)
                    _require(self._append_slots[key]==slot and raw==self._written_entries[key]==self._pack(key,self._index[key]),'INDEX_POSITION_OR_COVERAGE_MISMATCH')
                _require(not self._read(source,1),'INDEX_TRAILING_BYTES')
                _require(hasher.hexdigest()==self._ledger[self.append_index_path.name]['hasher'].hexdigest(),'WRITE_LEDGER_SHA_MISMATCH')
        files[self.append_index_path.name]={'bytes':pin['st_size'],'sha256':hasher.hexdigest(),'fp':pin}
        if self._final_index_written:
            files[self.segment_manifest_path.name]=self._scan_file(self.segment_manifest_path,'verify_segment_manifest',self._segment_manifest_bytes)
            with self._phase('verify_sorted_index'):
                with self._reader(self.sorted_index_path) as (source,pin):
                    raw=self._read(source,INDEX_HEADER_BYTES);_require(raw==self._header,'INDEX_HEADER_CHANGED')
                    hasher=hashlib.sha256(raw);previous=None
                    _require(self._sorted_keys==tuple(sorted(self._index)),'SORTED_INDEX_KEY_COVERAGE')
                    for key in self._sorted_keys:
                        raw=self._read(source,INDEX_ENTRY_BYTES);hasher.update(raw)
                        found,position=self._unpack(raw)
                        _require((previous is None or previous<found) and found==key and
                                 raw==self._written_entries[key]==self._pack(key,self._index[key]),'SORTED_INDEX_ORDER_OR_POSITION')
                        previous=found
                    _require(not self._read(source,1),'INDEX_TRAILING_BYTES')
                    _require(hasher.hexdigest()==self._ledger[self.sorted_index_path.name]['hasher'].hexdigest(),'WRITE_LEDGER_SHA_MISMATCH')
            files[self.sorted_index_path.name]={'bytes':pin['st_size'],'sha256':hasher.hexdigest(),'fp':pin}
        order=iter(self._order);key=next(order,None)
        for number,path in enumerate(self.segments):
            dictionary=self._dictionaries[number]
            _require(type(dictionary) is bytes and 0<len(dictionary)<=32768,'DICTIONARY_LENGTH')
            files[self._dictionary_paths[number].name]=self._scan_file(self._dictionary_paths[number],'verify_dictionary',dictionary)
            with self._phase('verify_frames'):
                with self._reader(path) as (source,pin):
                    hasher=hashlib.sha256();offset=logical_total=0;first_key=key
                    while key is not None and self._index[key][0]==number:
                        segment,position,compressed,logical,frame_sha=self._index[key]
                        _require(segment==number and position==offset and 0<logical<=self.limit and
                                 0<compressed<=2*logical+CHUNK_BYTES,'FRAME_POSITION_OR_LENGTH_MISMATCH')
                        frame_hasher=hashlib.sha256();remaining=compressed
                        while remaining:
                            size=min(CHUNK_BYTES,remaining);raw=self._read(source,size)
                            _require(len(raw)==size,'FRAME_TRUNCATED')
                            hasher.update(raw);frame_hasher.update(raw);remaining-=len(raw)
                        _require(frame_hasher.hexdigest()==frame_sha,'WRITTEN_FRAME_SHA_MISMATCH')
                        offset+=compressed;logical_total+=logical
                        _require(logical_total<=self.limit,'LOGICAL_SEGMENT_LIMIT')
                        key=next(order,None)
                    _require(first_key==self._first_keys[number] and offset==pin['st_size'] and
                             logical_total==self._logical_sizes[number],'FRAME_WHOLE_POSITION_OR_COVERAGE_MISMATCH')
            _require(hasher.hexdigest()==self._ledger[path.name]['hasher'].hexdigest(),'WRITE_LEDGER_SHA_MISMATCH')
            files[path.name]={'bytes':pin['st_size'],'sha256':hasher.hexdigest(),'fp':pin,
                             'logical_bytes':logical_total,'logical_sha256':self._logical_hashers[number].hexdigest()}
        _require(key is None,'ROW_COVERAGE_MISMATCH')
        return {'schema_version':VERSION,'policy_id':POLICY_ID,'row_count':len(self._index),
            'segment_bytes_limit':self.limit,'complete_allowed_row_audit':False,
            'physical_write_ledger_audit_complete':True,'logical_decode_validation':'NOT_PERFORMED_BY_PHYSICAL_SCAN',
            'logical_canonical_bytes':sum(self._logical_sizes),'index_entry_bytes':INDEX_ENTRY_BYTES,
            'append_index_bytes':self._ledger[self.append_index_path.name]['bytes'],
            'sorted_index_bytes':self._ledger[self.sorted_index_path.name]['bytes'] if self._final_index_written else 0,
            'dictionary_bytes':sum(len(x) for x in self._dictionaries),'header_bytes':INDEX_HEADER_BYTES if self._final_index_written else 0,
            'segment_manifest_bytes':len(self._segment_manifest_bytes) if self._final_index_written else 0,
            'authority':'CURRENT_OWNED_APPEND_LEDGER_AND_ACTUAL_WHOLE_SHA_7FP','files':files}


_RowJournal = DictionaryRowJournalV4
_JointRecords = v2._JointRecords


class SpoolingEvidenceResolverV4(v3.SpoolingEvidenceResolverV3):
    def __init__(self, receipts, *, evidence_directory, cache_max_bytes, cache_max_entries,
                 segment_bytes=SEGMENT_BYTES, logical_read_callback=None, **kwargs):
        if kwargs.get('record_rows') is False:
            raise ValueError('EVIDENCE_AUDIT_V4_COMPLETE_ROW_RECORDING_REQUIRED')
        if logical_read_callback is not None and not callable(logical_read_callback):
            raise TypeError('EVIDENCE_AUDIT_V4_LOGICAL_READ_CALLBACK_REQUIRED')
        if kwargs.get('coverage_type')!='TEST_FIXTURE' and logical_read_callback is None:
            raise ValueError('EVIDENCE_AUDIT_V4_LIVE_LOGICAL_ACCOUNTING_REQUIRED')
        _cache_limit(cache_max_bytes,2**63-1,'CACHE_BYTES')
        _cache_limit(cache_max_entries,2**32-1,'CACHE_ENTRIES')
        kwargs['record_rows']=True
        EvidenceResolver.__init__(self,receipts,**kwargs)
        self._receipt_identity=v2._json([self.receipts[k].model_dump(mode='json') for k in sorted(self.receipts)])
        self.rows=DictionaryRowJournalV4(evidence_directory,segment_bytes,logical_read_callback,
            cache_max_bytes=cache_max_bytes,cache_max_entries=cache_max_entries)

    def joint_dependency_digest_and_audit_manifest(self):
        self.rows._guard()
        try:
            self.rows._finish_writers()
            pre=self.rows.verify()
            if self.rows.sealed is None:self.rows._publish(pre)
            sources=self._sources()
            dependencies={'policy_id':POLICY_ID,'sources':sources,'records':_JointRecords(self.rows,pre)}
            before=self.rows.logical_read_bytes
            dependency_hash=digest_json_stream(dependencies)
            _require(self.rows.logical_read_bytes-before==pre['logical_canonical_bytes'],'COMPLETE_CANONICAL_LOGICAL_BYTE_COVERAGE')
            post=self.rows.verify(pre)
            _require(self._sources()==sources,'USED_SOURCE_CHANGED_DURING_JOINT')
            complete=deepcopy(post)
            complete.update(complete_allowed_row_audit=True,
                logical_decode_validation='ALL_CANONICAL_ROWS_ZLIB_DICTIONARY_ADLER_EOF_SHA_NEWLINE',
                canonical_decoded_row_count=len(self.rows))
            return {'schema_version':VERSION,'dependency_hash':dependency_hash,'audit_manifest':complete,
                'row_count':len(self.rows),'complete_allowed_row_audit':True,
                'validation':'PRE_POST_WHOLE_SHA_7FP_ALL_DICTIONARY_FRAMES_ADLER_EOF_EXACT_84B_INDEX',
                'read_metrics':self.read_metrics}
        except BaseException as exc:
            self.rows._failed(exc,'JOINT')
            raise

    @property
    def read_metrics(self):
        result=dict(super().read_metrics)
        row=self.rows;buckets=row._read_buckets
        result.update(scope='V4_EXPLICIT_PHYSICAL_PLUS_ACTUAL_LOGICAL_DECODER_RETURNS_NOT_ALL_SOURCE_OR_KERNEL_IO',
            ordinary_physical_returned_bytes=sum(v for k,v in buckets.items() if k.startswith('ordinary_')),
            ordinary_index_returned_bytes=buckets.get('ordinary_index',0),
            ordinary_header_returned_bytes=buckets.get('ordinary_header',0),
            ordinary_dict_returned_bytes=buckets.get('ordinary_dictionary',0),
            ordinary_segment_manifest_returned_bytes=buckets.get('ordinary_segment_manifest',0),
            ordinary_guard_manifest_returned_bytes=buckets.get('ordinary_guard_manifest',0),
            ordinary_frame_returned_bytes=buckets.get('ordinary_frame',0),
            physical_read_buckets=dict(buckets),cache_bytes_current=row._cache_bytes,
            cache_bytes_peak=row._cache_peak_bytes,cache_entries_current=len(row._cache),
            cache_entries_peak=row._cache_peak_entries,cache_max_bytes=row.cache_max_bytes,
            cache_max_entries=row.cache_max_entries,cache_payload_bytes_not_OS_RSS=True,
            **row._cache_counters)
        return result


def implementation_source_identity():
    root=Path(__file__).parents[2]
    paths=(Path(__file__),Path(v2.__file__),Path(v3.__file__),
        root/'stockfish_core/yaogu/visibility.py',root/'stockfish_core/yaogu/history_artifacts.py',
        root/'docs/EVIDENCE_AUDIT_SPOOL_V2.md',root/'docs/EVIDENCE_AUDIT_SPOOL_V3.md',
        root/'docs/EVIDENCE_AUDIT_SPOOL_V4.md')
    hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return {'implementation_version':VERSION,'policy_id':POLICY_ID,'source_file_hashes':hashes,
        'source_hash':digest_json_stream(hashes),'role':'IMPLEMENTATION_IDENTITY_NOT_SOURCE_CAPABILITY',
        'union_scope':'DIRECT_BACKEND_DEPENDENCIES_NOT_COMPLETE_CALLER_RUNTIME_UNION'}


def source_identity():
    return implementation_source_identity()
