"""Bounded read-only witness of real factor-review consumer behavior.

Only a scoped price context is constructed. It consumes original input
receipts/calendar/history identities and the actual canonical resolver and
production _bar/_window_return/_ma_value functions. No strategy qualification,
normalization, history write, outcome calculation, or score generation occurs.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

PROJECT = next(p / 'stock-fish' for p in Path(__file__).resolve().parents if (p / 'stock-fish/stockfish_core').is_dir())
sys.path.insert(0, str(PROJECT))
from stockfish_core.storage import ReadOnlySQLiteStore
from stockfish_core.yaogu.contracts import MarketInput, DailyBar, digest
from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore, history_resolver, history_receipts
from stockfish_core.yaogu.source_resolution import canonical_price_source_view, source_token
from stockfish_core.yaogu.strategy_experiments import _bar, _revision_key
from stockfish_core.yaogu.theme_capital_trend import _window_return, _ma_value
from stockfish_core.yaogu.visibility import stamp

VERSION = 'R16_R1_FACTOR_REVIEW_REAL_CONSUMPTION_V1'
CMP = ('comparable_open', 'comparable_high', 'comparable_low', 'comparable_close', 'comparable_basis')
CLOCKS = ('source_published_at', 'first_seen_at', 'ingested_at')


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding='utf-8')


def git_source_identity(project):
    root = Path(project).resolve().parent
    unknown = {'commit': None, 'tree': None, 'tracked_status': None,
               'git_root_matches': False, 'status': 'UNKNOWN_NO_MATCHING_GIT_ROOT'}
    try:
        top = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=root,
                                      timeout=5, text=True, stderr=subprocess.DEVNULL).strip()
        if Path(top).resolve() != root:
            return unknown
        identity = {'git_root_matches': True, 'status': 'MATCHING_GIT_ROOT'}
        for name, args in (('commit', ['rev-parse', 'HEAD']), ('tree', ['rev-parse', 'HEAD^{tree}']),
                           ('tracked_status', ['--no-optional-locks', 'status', '--short', '--untracked-files=no'])):
            identity[name] = subprocess.check_output(['git', *args], cwd=root, timeout=5,
                                                    text=True, stderr=subprocess.DEVNULL).strip()
        return identity
    except (OSError, subprocess.SubprocessError):
        return unknown


class BoundedReadOnlyStore(ReadOnlySQLiteStore):
    def __init__(self, path, deadline):
        self.deadline = deadline
        super().__init__(path, timeout=5)

    def _connect(self):
        con = super()._connect()
        # Also covers connections opened internally by the actual canonical
        # resolver/history store, not merely this harness's projection queries.
        con.set_progress_handler(lambda: int(time.monotonic() > self.deadline), 10000)
        return con


class Witness:
    def __init__(self, build_result, output, *, budget_seconds=60, max_rows=1000, max_visits=6000):
        self.started = time.monotonic()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.budget_seconds, self.max_rows, self.max_visits = budget_seconds, max_rows, max_visits
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.build_path = Path(build_result).resolve(strict=True)
        self.build = json.loads(self.build_path.read_text(encoding='utf-8'))
        self.store = BoundedReadOnlyStore(self.build['target_db'], self.started + budget_seconds)
        self.owner = HistoryArtifactStore(self.store)
        self.report = {'witness_version': VERSION, 'scope': 'SAME_ACTUAL_CONSUMER_WITH_SCOPED_IDENTITIES',
            'status': 'RUNNING', 'started_at': self.started_at, 'checks': [], 'input_projections': {},
            'rows_loaded_unique': 0, 'candidate_visits': 0, 'max_unique_rows': max_rows,
            'max_rows_per_snapshot': max_rows, 'max_cumulative_candidate_visits': max_visits, 'budget_seconds': budget_seconds,
            'build_result': str(self.build_path), 'build_result_sha256': sha_file(self.build_path),
            'source_access': 'SQLITE_URI_MODE_RO_QUERY_ONLY_NO_SERVICE_NO_WRITES',
            'script_sha256': sha_file(__file__), 'command_argv': sys.argv,
            'not_verified': ['FULL_UNIVERSE_UNAFFECTED_PROOF_NOT_CLAIMED', 'FULL_STRATEGY_RANK_AND_QUALIFICATION_NOT_RUN',
                             'REBUILD_IDEMPOTENCE_NOT_RERUN_USES_EXISTING_T1_REVIEW_REPORT',
                             'MA_UNKNOWN_MAY_INCLUDE_PREEXISTING_WINDOW_GAPS_NOT_ATTRIBUTED_SOLELY_TO_FACTOR_REVIEW']}
        self.hashes = set()
        self.view_candidate_hashes = {}
        self.binding_before = {}
        self.last_progress = 0
        self.report['source_file_sha256'] = {str(path.relative_to(PROJECT)): sha_file(path) for path in (
            PROJECT / 'stockfish_core/yaogu/source_resolution.py', PROJECT / 'stockfish_core/yaogu/strategy_experiments.py',
            PROJECT / 'stockfish_core/yaogu/theme_capital_trend.py', PROJECT / 'stockfish_core/yaogu/history_artifacts.py',
            PROJECT / 'stockfish_core/yaogu/visibility.py')}
        self.report['git'] = git_source_identity(PROJECT)
        self.report['build_source_identity_as_recorded'] = self.build.get('git')
        self.check('BUILD_RECONSTRUCTED', self.build.get('coverage_type') in {'RECONSTRUCTED_HISTORICAL', 'TEST_FIXTURE'})

    def budget(self):
        if time.monotonic() - self.started > self.budget_seconds:
            raise TimeoutError('H4_WITNESS_TIME_BUDGET_EXCEEDED')
        elapsed = time.monotonic() - self.started
        if elapsed - self.last_progress >= 5:
            event = {'status': 'RUNNING', 'elapsed_seconds': elapsed, 'checks_completed': len(self.report['checks']),
                     'unique_rows': len(self.hashes), 'budget_seconds': self.budget_seconds}
            write_json(self.output / 'checkpoint.json', event)
            with (self.output / 'progress.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(event) + '\n')
            print(json.dumps(event), flush=True)
            self.last_progress = elapsed

    def check(self, name, okay, **facts):
        self.report['checks'].append({'check_id': name, 'status': 'PASS' if okay else 'FAIL', **facts})

    @contextmanager
    def connection(self):
        con = self.store._connect()
        try:
            yield con
        finally:
            con.close()

    def input_hash(self, input_id):
        with self.connection() as con:
            row = con.execute('SELECT rowid,content_hash,length(CAST(payload AS BLOB)) AS bytes FROM documents '
                              'WHERE namespace=? AND document_id=?', ('candidate_inputs', input_id)).fetchone()
            if row is None:
                raise ValueError('H4_INPUT_MISSING:' + input_id)
            h = hashlib.sha256()
            with con.blobopen('documents', 'payload', row['rowid'], readonly=True) as blob:
                for block in iter(lambda: blob.read(1024 * 1024), b''):
                    self.budget(); h.update(block)
            actual = h.hexdigest()
            if actual != row['content_hash'] or actual != input_id.removeprefix('market_'):
                raise ValueError('H4_ORIGINAL_INPUT_HASH_MISMATCH')
            return {'sha256': actual, 'bytes': row['bytes']}

    def bundle(self, input_id, ids):
        self.budget()
        binding = self.input_hash(input_id)
        self.binding_before[input_id] = binding
        fields = ('observation_date', 'sessions', 'receipts', 'coverage_type', 'history_artifact_ref', 'history_artifact_hash')
        universe_fields = ('snapshot_id', 'source_refs', 'complete', 'expected_count', 'event_time',
                           'source_published_at', 'first_seen_at', 'ingested_at')
        paths = ['$.' + key for key in fields] + ['$.universe.' + key for key in universe_fields]
        with self.connection() as con:
            values = json.loads(con.execute('SELECT json_extract(payload,' + ','.join('?' for _ in paths) +
                     ') FROM documents WHERE namespace=? AND document_id=?', (*paths, 'candidate_inputs', input_id)).fetchone()[0])
            data = dict(zip(fields, values[:len(fields)]))
            data['universe'] = dict(zip(universe_fields, values[len(fields):]))
            query = ('SELECT j.value FROM documents d,json_each(d.payload,?) j WHERE d.namespace=? AND d.document_id=? '
                     "AND json_extract(j.value,'$.instrument_id') IN (" + ','.join('?' for _ in ids) + ') LIMIT ?')
            for key, path in (('bars', '$.bars'), ('securities', '$.universe.securities')):
                rows = [json.loads(row[0]) for row in con.execute(query, (path, 'candidate_inputs', input_id, *ids, self.max_rows + 1))]
                if len(rows) > self.max_rows:
                    raise ValueError('H4_INLINE_PROJECTION_ROW_BUDGET_EXCEEDED')
                if key == 'bars': data[key] = rows
                else: data['universe'][key] = rows
        bundle = MarketInput.model_validate(data)
        bundle._history_store = self.owner
        manifest = self.owner.manifest(bundle.history_artifact_ref, bundle.history_artifact_hash)
        projection_key = input_id + ':' + ','.join(ids)
        self.report['input_projections'][projection_key] = {**binding, 'input_id': input_id, 'history_ref': bundle.history_artifact_ref,
            'history_hash': bundle.history_artifact_hash, 'inline_bars': len(bundle.bars),
            'scoped_securities': len(bundle.universe.securities), 'retained_full_receipts': len(bundle.receipts),
            'calendar_sessions': len(bundle.sessions), 'original_universe_expected_count': bundle.universe.expected_count,
            'projection_fields': paths, 'scoped_instrument_ids': list(ids), 'row_projection_sql': query,
            'history_partition_count': len(manifest['partitions']), 'projection_sha256': digest(bundle)}
        write_json(self.output / ('input-projection-' + input_id + '-' + '-'.join(ids) + '.json'),
                   {'scope': 'SCOPED_ORIGINAL_FIELDS_NOT_FULL_INPUT_OR_QUALIFICATION',
                    'source_input_id': input_id, 'source_input_binding': binding, 'payload': bundle.model_dump(mode='json')})
        return bundle

    def view(self, bundle, cutoff, sessions, ids, label):
        self.budget()
        # Count exact immutable candidates before the real resolver reads them.
        # Repeated PIT cutoffs reuse the same row identities; visits are separate.
        manifest = self.owner.manifest(bundle.history_artifact_ref, bundle.history_artifact_hash)
        parts = [p['partition_hash'] for p in manifest['partitions'] if p['session'] in sessions]
        hashes = set()
        if parts:
            with self.connection() as con:
                query = ('SELECT DISTINCT row_hash FROM history_partition_members WHERE kind=? AND partition_hash IN (' +
                         ','.join('?' for _ in parts) + ') AND instrument_id IN (' + ','.join('?' for _ in ids) + ') LIMIT ?')
                hashes.update(row[0] for row in con.execute(query, ('bar', *parts, *ids, self.max_rows + 1)))
        inline = [row for row in bundle.bars if str(row.session) in sessions and row.instrument_id in ids]
        predicted_visits = len(hashes) + len(inline)
        if self.report['candidate_visits'] + predicted_visits > self.max_visits:
            raise ValueError('H4_CUMULATIVE_CANDIDATE_VISIT_BUDGET_EXCEEDED')
        hashes.update(digest(row) for row in inline)
        if len(hashes) > self.max_rows:
            raise ValueError('H4_SNAPSHOT_CANDIDATE_ROW_BUDGET_EXCEEDED')
        self.hashes.update(hashes)
        if len(self.hashes) > self.max_rows:
            raise ValueError('H4_UNIQUE_ROW_BUDGET_EXCEEDED')
        ctx = SimpleNamespace(bundle=bundle, cutoff=stamp(cutoff), sessions=[str(s) for s in bundle.sessions],
            scalar_cache={}, rejected_evidence=[], eligible_ids=list(ids),
            resolver=history_resolver(bundle, cutoff=stamp(cutoff)), bars_by_session={})
        view = canonical_price_source_view(ctx, sessions, ids)
        self.budget()
        self.report['candidate_visits'] += view['work_counters']['rows_considered']
        if self.report['candidate_visits'] > self.max_visits:
            raise ValueError('H4_CUMULATIVE_CANDIDATE_VISIT_BUDGET_EXCEEDED')
        self.view_candidate_hashes[label] = hashes
        self.report['rows_loaded_unique'] = len(self.hashes)
        raw = {'label': label, 'input_projection_sha256': digest(bundle), 'cutoff': str(cutoff),
               'tokens': [view['tokens'][key] for key in sorted(view['tokens'])],
               'selected': [{'instrument_id': key[0], 'session': key[1], 'row': row} for key, row in sorted(view['selected'].items())],
               'work_counters': view['work_counters'], 'rejected': ctx.rejected_evidence}
        write_json(self.output / (label + '.json'), raw)
        return ctx, view

    def consume(self, ctx, view, iid, label):
        class ActualSelectedRows:
            def __init__(self): self.reads = []
            def bar(self, session, instrument):
                self.reads.append((instrument, str(session)))
                return view['selected'].get((instrument, str(session)))
        adapter = ActualSelectedRows()
        ctx.bars_by_session = adapter
        end = len(ctx.sessions) - 1
        facts = {'instrument_id': iid, 'observation_date': ctx.sessions[-1],
                 'context_scope': 'ACTUAL_CANONICAL_SELECTED_ROWS_INTO_UNMODIFIED_PRODUCTION_FUNCTIONS', 'functions': {}}
        for name, call in (('_bar', lambda: _bar(ctx, ctx.sessions[-1], iid)),
                           ('_window_return_1', lambda: _window_return(ctx, iid, end, 1)),
                           ('_ma_value_20', lambda: _ma_value(ctx, iid, end, 20)),
                           ('_ma_value_60', lambda: _ma_value(ctx, iid, end, 60))):
            offset = len(adapter.reads)
            value = call()
            facts['functions'][name] = {'value': value, 'actual_read_keys': adapter.reads[offset:]}
        bar = facts['functions']['_bar']['value']
        okay = bool(bar) and all(bar.get(field) is None for field in CMP)
        okay = okay and facts['functions']['_window_return_1']['value'] is None
        okay = okay and all(facts['functions'][name]['value'][0] is None and facts['functions'][name]['actual_read_keys']
                            for name in ('_ma_value_20', '_ma_value_60'))
        self.check(label + '_REAL_CONSUMERS_UNKNOWN', okay, functions=['_bar', '_window_return', '_ma_value'])
        write_json(self.output / (label + '-consumer-facts.json'), facts)
        return facts

    def run(self):
        directory = self.build_path.parent
        normal_t = json.loads((directory / 'normalization-t-report.json').read_text(encoding='utf-8'))
        normal_t1 = json.loads((directory / 'normalization-report.json').read_text(encoding='utf-8'))
        review = normal_t.get('factor_revision_review') or {}
        repeat = normal_t1.get('factor_revision_review') or {}
        ids = sorted({item['instrument_id'] for item in review.get('conflicts', [])})
        if len(ids) != 1 or not review.get('reviews'):
            raise ValueError('H4_REQUIRES_ONE_REAL_CONFLICT_INSTRUMENT_AND_NONEMPTY_REVIEW')
        iid = ids[0]
        self.report['factor_conflict_instrument'] = iid
        self.report['factor_policy'] = review['policy']
        self.report['factor_capture_witness'] = review['conflicts']
        self.report['knowledge_cutoff'] = self.build['common_information_cutoff']
        original = self.bundle(self.build['input_t_original'], ids)
        current = self.bundle(self.build['input_t_corrected'], ids)
        next_day = self.bundle(self.build['input_t_plus_1_corrected'], ids)
        if original.coverage_type == 'TEST_FIXTURE':
            self.report['data_kind'] = 'TEST_FIXTURE'
        else:
            self.report['data_kind'] = 'REAL_ISOLATED_LOCAL_CAPTURE'
        cutoff = stamp(self.build['common_information_cutoff'])
        session_t = str(current.observation_date)
        sessions = [str(s) for s in current.sessions if s <= current.observation_date]
        ctx_t, v_t = self.view(current, cutoff, sessions, ids, 'corrected-t-current-cutoff')
        ctx_t1, v_t1 = self.view(next_day, cutoff, [str(s) for s in next_day.sessions], ids, 'corrected-t1-current-cutoff')
        parents = {r.receipt_id: r for r in history_receipts(current)}
        review_rows = []
        for item in review['reviews']:
            self.budget()
            old, new = DailyBar.model_validate(item['old_row']), DailyBar.model_validate(item['review_row'])
            refs = tuple(new.source_refs)
            expected_clocks = {field: max(getattr(old, field), *(getattr(parents[ref], field) for ref in refs)) for field in CLOCKS}
            old_values, new_values = old.model_dump(mode='json'), new.model_dump(mode='json')
            changed = {field for field in old_values if old_values[field] != new_values[field]}
            key = (iid, str(new.session))
            tokens = [source_token(view, *key) for view in (v_t, v_t1)]
            old_is_actual_candidate = digest(old) in self.view_candidate_hashes['corrected-t-current-cutoff']
            okay = (old_is_actual_candidate and digest(old) == item['old_row_hash'] and digest(new) == item['review_row_hash']
                    and all(getattr(new, field) is None for field in CMP)
                    and changed <= set(CMP) | set(CLOCKS) | {'source_refs'}
                    and all(getattr(new, field) == expected_clocks[field] for field in CLOCKS)
                    and _revision_key(new) > _revision_key(old)
                    and all(token['status'] == 'SELECTED' and token['row_hash'] == digest(new) for token in tokens))
            self.check('REVIEW_ROW_SELECTED_IN_BOTH_INPUTS', okay, instrument_id=iid, session=str(new.session),
                       old_row_hash=digest(old), review_row_hash=digest(new))
            review_rows.append({**item, 'old_row_hash_in_actual_canonical_candidates': old_is_actual_candidate,
                                't_token': tokens[0], 't1_token': tokens[1], 'verified': okay})
        write_json(self.output / 'all-review-rows-and-selected-tokens.json', review_rows)
        used_refs = sorted({ref for item in review_rows for row in (item['old_row'], item['review_row']) for ref in row['source_refs']})
        write_json(self.output / 'review-parent-receipts.json', [parents[ref].model_dump(mode='json') for ref in used_refs])
        self.consume(ctx_t, v_t, iid, 'corrected-t')
        self.consume(ctx_t1, v_t1, iid, 'corrected-t1')
        # This is a tested cutoff boundary, not an invented source clock.
        earliest_review_visible = min(max(stamp(item['review_row'][key]) for key in ('event_time', *CLOCKS)) for item in review['reviews'])
        old_cutoff = earliest_review_visible - timedelta(microseconds=1)
        self.report['old_cutoff'] = old_cutoff.isoformat()
        _, v_original_old = self.view(original, old_cutoff, sessions, ids, 'original-t-old-cutoff')
        _, v_corrected_old = self.view(current, old_cutoff, sessions, ids, 'corrected-t-old-cutoff')
        changed_old = []
        for session in sessions:
            left, right = source_token(v_original_old, iid, session), source_token(v_corrected_old, iid, session)
            # New historically captured missing-source repairs are separately
            # reported. Every original selected row must remain exactly selected.
            if left['status'] == 'SELECTED' and any(left.get(key) != right.get(key) for key in ('status', 'row_hash', 'values')):
                changed_old.append({'session': session, 'original': left, 'corrected': right})
        original_current = source_token(v_original_old, iid, session_t)
        self.check('OLD_CUTOFF_ORIGINAL_SELECTED_ROWS_UNCHANGED', not changed_old and original_current['status'] == 'SELECTED'
                   and original_current['values'].get('comparable_close') is not None,
                   originally_selected_count=sum(token['status'] == 'SELECTED' for token in v_original_old['tokens'].values()),
                   mismatches=changed_old, current_original_token=original_current)
        a = {p['session']: p['partition_hash'] for p in self.owner.manifest(current.history_artifact_ref)['partitions']}
        b = {p['session']: p['partition_hash'] for p in self.owner.manifest(next_day.history_artifact_ref)['partitions']}
        partition_changes = [{'session': session, 't': value, 't1': b.get(session)} for session, value in a.items() if b.get(session) != value]
        self.check('T1_REVIEW_APPLICATION_ZERO_NEW_ROWS', repeat.get('new_review_rows') == 0,
                   saved_review_status=repeat.get('status'), new_review_rows=repeat.get('new_review_rows'))
        self.check('T1_OLD_PARTITIONS_UNCHANGED', not partition_changes, partitions_checked=len(a), changes=partition_changes)
        with self.connection() as con:
            sample = con.execute("SELECT json_extract(j.value,'$.instrument_id') FROM documents d,json_each(d.payload,'$.bars') j "
                "WHERE d.namespace='candidate_inputs' AND d.document_id=? AND json_extract(j.value,'$.session')=? "
                "AND json_extract(j.value,'$.instrument_id')<>? ORDER BY json_extract(j.value,'$.instrument_id') LIMIT 1",
                (self.build['input_t_original'], session_t, iid)).fetchone()
        if sample:
            other = sample[0]
            sample_o, sample_t = self.bundle(self.build['input_t_original'], [other]), self.bundle(self.build['input_t_corrected'], [other])
            _, vo = self.view(sample_o, cutoff, [session_t], [other], 'unaffected-sample-original')
            _, vt = self.view(sample_t, cutoff, [session_t], [other], 'unaffected-sample-corrected')
            left, right = source_token(vo, other, session_t), source_token(vt, other, session_t)
            self.check('ONE_UNAFFECTED_INSTRUMENT_SAMPLE', left['status'] == 'SELECTED' and
                       all(left.get(key) == right.get(key) for key in ('status', 'row_hash', 'values')),
                       instrument_id=other, session=session_t, original=left, corrected=right, scope='ONE_SAMPLE_NOT_FULL_UNIVERSE')
        else:
            self.check('ONE_UNAFFECTED_INSTRUMENT_SAMPLE', False, reason='NO_SAMPLE_AVAILABLE')
        for input_id, before in self.binding_before.items():
            self.check('ORIGINAL_INPUT_BYTES_UNCHANGED', before == self.input_hash(input_id), input_id=input_id, binding=before)
        self.owner.manifest(original.history_artifact_ref, original.history_artifact_hash)
        self.check('ORIGINAL_HISTORY_REFERENCE_STILL_HASH_VALID', True, history_ref=original.history_artifact_ref)
        self.report['normalization_report_bindings'] = [
            {'path': str(directory / name), 'sha256': sha_file(directory / name)} for name in ('normalization-t-report.json', 'normalization-report.json')]
        self.report['new_review_rows_t'] = review.get('new_review_rows')
        self.report['new_review_rows_t1'] = repeat.get('new_review_rows')
        self.report['status'] = 'PASS' if all(row['status'] == 'PASS' for row in self.report['checks']) else 'FAIL'
        self.report.update(finished_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic()-self.started)
        write_json(self.output / 'result.json', self.report)
        write_json(self.output / 'checkpoint.json', {'status': self.report['status'],
                   'elapsed_seconds': self.report['elapsed_seconds'], 'checks': len(self.report['checks'])})
        artifacts = [{'path': path.name, 'bytes': path.stat().st_size, 'sha256': sha_file(path)}
                     for path in sorted(self.output.iterdir()) if path.is_file()]
        write_json(self.output / 'artifact_manifest.json', artifacts)
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-result', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budget-seconds', type=float, default=60)
    parser.add_argument('--max-rows', type=int, default=1000)
    parser.add_argument('--max-visits', type=int, default=6000)
    args = parser.parse_args(argv)
    if not 0 < args.budget_seconds <= 60 or not 1 <= args.max_rows <= 1000 or not 1 <= args.max_visits <= 6000:
        parser.error('budget must be <=60 seconds, unique/per-view rows <=1000, cumulative candidate visits <=6000')
    witness = None
    try:
        witness = Witness(args.build_result, args.output, budget_seconds=args.budget_seconds,
                          max_rows=args.max_rows, max_visits=args.max_visits)
        result = witness.run()
        print(json.dumps({key: result[key] for key in ('status', 'elapsed_seconds', 'rows_loaded_unique', 'candidate_visits')}, default=str))
        return 0 if result['status'] == 'PASS' else 1
    except Exception as exc:
        if witness:
            witness.report.update(status='FAIL_OR_NOT_VERIFIED', error_type=type(exc).__name__, error=str(exc),
                                  elapsed_seconds=time.monotonic()-witness.started)
            write_json(witness.output / 'result.json', witness.report)
        print(json.dumps({'status':'FAIL_OR_NOT_VERIFIED','error_type':type(exc).__name__,'error':str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
