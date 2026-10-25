"""Authenticate and register an existing full native core; never select or download.

This V1 accepts the bounded private supervisor protocol used before shared
registration existed. It does not import a capability or change the old job.
First registration independently authenticates originals and normalizes once;
subsequent reads verify every sealed original without normalization or selection.
Compressed relocated originals require a separate protocol and are rejected.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_workspace as workspace
from scripts import codex_premarket as premarket

VERSION = 'CODEX_FULL_CORE_ARCHIVE_V1'
PROFILE = 'FULL_CORE_ARCHIVE_READONLY_V1'
PRIVATE_VERSION = 'PRIVATE_NATIVE_CORE_THEN_CONTEXT_20261026_V1'
PRIVATE_WORKER_SHA256 = '976da66f5cc37b1b4cf208c39215fce681303acd83a01ed6e75272bc9ae8b0bc'
STRATEGY = 'SHORT_HORIZON_STRENGTH_R2'
MIB, GIB = 1024**2, 1024**3
LIMITS = dict(max_total_read_bytes=4*GIB, max_unique_bytes=GIB, max_files=4096,
              max_seconds=900, max_rss_bytes=5*GIB//2)
MAX_EVIDENCE = MIB
MAX_CAPTURE = 1024


class ArchiveError(ValueError):
    pass


def _need(value, code):
    if not value:
        raise ArchiveError(code)


def _digest(value):
    return hashlib.sha256(workspace._canonical(value)).hexdigest()


def _path(value):
    path = Path(value).expanduser().absolute()
    _need('..' not in path.parts and path.resolve(strict=True) == path,
          'ARCHIVE_ORIGINAL_PATH_ALIAS_FORBIDDEN')
    # Refuse redirecting parent components, not only a symlink leaf.
    with workspace._directory_fd(path if path.is_dir() else path.parent):
        pass
    return path


def _clock(value):
    _need(isinstance(value, str), 'ARCHIVE_EXPLICIT_CLOCK_REQUIRED')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ArchiveError('ARCHIVE_INVALID_CLOCK') from None
    _need(result.tzinfo is not None and result.utcoffset() is not None,
          'ARCHIVE_AWARE_CLOCK_REQUIRED')
    _need(result <= datetime.now(timezone.utc), 'ARCHIVE_FUTURE_CLOCK')
    return result


def _session(limits=None):
    from stockfish_core.yaogu.short_horizon_archive_bridge_v1 import ArchiveVerificationSession
    selected = dict(LIMITS)
    if limits is not None:
        _need(isinstance(limits, dict) and set(limits) <= set(selected), 'ARCHIVE_LIMIT_KEYS')
        for key, value in limits.items():
            _need(type(value) in (int, float) and 0 < value <= selected[key],
                  'ARCHIVE_LIMIT_MAY_ONLY_TIGHTEN')
            selected[key] = value
    class Session(ArchiveVerificationSession):
        def read(self, path, *args, **kwargs):
            _path(path)
            return super().read(path, *args, **kwargs)
    return Session(**selected)


def _read(session, path, expected=None, maximum=16*MIB):
    from stockfish_core.yaogu.short_horizon_archive_bridge_v1 import _json
    return _json(session.read(path, expected, limit=maximum))


def _ref(session, path, expected=None, maximum=256*MIB):
    path = _path(path)
    session.read(path, expected, limit=maximum, retain=False)
    return deepcopy(session.files[str(path)])


def _reference_map(values):
    from stockfish_core.yaogu.short_horizon_archive_bridge_v1 import _ref as validate
    _need(isinstance(values, list) and 0 < len(values) <= 4096, 'ARCHIVE_REFERENCE_SET_REQUIRED')
    result = {}
    for value in values:
        validate(value)
        _need(value['path'] not in result, 'ARCHIVE_DUPLICATE_REFERENCE')
        result[value['path']] = value
    return result


def _verifier(session):
    """Pin the narrow new coordinator and interpreter, separate from old core."""
    paths = (Path(__file__).resolve(), Path(workspace.__file__).resolve(),
             Path(premarket.__file__).resolve(), Path(sys.executable).resolve())
    return [_ref(session, path, maximum=256*MIB) for path in paths]


def _request(session, path, fixture):
    value = _read(session, path, maximum=8*MIB)
    _need(isinstance(value, dict) and set(value) == {'schema_version','captures','observation_date',
          'target_session','cutoff','coverage_type'} and
          value['schema_version'] == 'CODEX_FROZEN_CORE_REQUEST_V1', 'ARCHIVE_REQUEST_SCHEMA')
    _need(value['coverage_type'] in {'TEST_FIXTURE','PROSPECTIVE_CAPTURED','RECONSTRUCTED_HISTORICAL'} and
          (value['coverage_type'] == 'TEST_FIXTURE') == fixture, 'ARCHIVE_FIXTURE_MUST_BE_EXPLICIT')
    observed, target = date.fromisoformat(value['observation_date']), date.fromisoformat(value['target_session'])
    _need(target > observed, 'ARCHIVE_TARGET_AFTER_OBSERVATION_REQUIRED')
    _clock(value['cutoff'])
    _need(isinstance(value['captures'], list) and 0 < len(value['captures']) <= MAX_CAPTURE,
          'ARCHIVE_CAPTURE_LIMIT')
    return value


def _full_scope(results, observed):
    """Check full last25/identity/factor query scope independently of PASS flags."""
    from stockfish_core.yaogu.premarket_native_core_v1 import _calendar_sessions
    from stockfish_core.yaogu.premarket_capture_cache_v1 import _listing, ORDINARY, _identity_rows, factor_contract
    by_endpoint = {}
    for value in results:
        _identity_rows(value['endpoint'], value['rows'], value['parameters'])
        if value['endpoint'] == 'adjustment_factors':
            factor_contract(value)
        if value['endpoint'] in ('market_daily','master_all','mainboard_sse','master_szse','stock_basic_all'):
            _need(bool(value['rows']), 'ARCHIVE_COMPLETE_CORE_SCOPE_CANNOT_BE_EMPTY')
        _need(value['status'] == 'COMPLETE' and value['pagination_complete'] is True,
              'ARCHIVE_COMPLETE_SOURCE_REQUIRED')
        by_endpoint.setdefault(value['endpoint'], []).append(value)
    _need(len(by_endpoint.get('calendar', [])) == 1, 'ARCHIVE_ONE_CALENDAR_REQUIRED')
    sessions = _calendar_sessions(results, observed)
    _need(len(sessions) >= 25 and sessions[-1] == observed, 'ARCHIVE_LAST25_REQUIRED')
    last25 = {str(day) for day in sessions[-25:]}
    for endpoint in ('market_daily','master_all'):
        days = [value['parameters'].get('date') for value in by_endpoint.get(endpoint, [])]
        _need(len(days) == 25 and set(days) == last25, 'ARCHIVE_EXACT_LAST25_SCOPE_' + endpoint)
    roster, mainboard = {}, set()
    for endpoint in ('mainboard_sse','master_szse','delisting_sse'):
        values = by_endpoint.get(endpoint, [])
        _need(len(values) == 1, 'ARCHIVE_ONE_OFFICIAL_SCOPE_' + endpoint)
        if endpoint == 'delisting_sse':
            continue
        for row in values[0]['rows']:
            sse = endpoint == 'mainboard_sse'
            code = ('sh.'+row['证券代码']) if sse else ('sz.'+row['A股代码'])
            _need(code not in roster, 'ARCHIVE_DUPLICATE_OFFICIAL_IDENTITY')
            roster[code] = row
            if sse or row.get('板块') == '主板':
                mainboard.add(code)
    basic = {}
    for value in results:
        if value['endpoint'] in ('stock_basic','stock_basic_all'):
            for row in value['rows']:
                old = basic.setdefault(row['code'], row)
                _need(old == row, 'ARCHIVE_BASIC_IDENTITY_CONFLICT')
    raw_union = {row['code'] for value in by_endpoint['market_daily'] for row in value['rows']}
    for code in raw_union:
        row = roster.get(code, {})
        listed = _listing(row.get('上市日期') if code.startswith('sh.') else row.get('A股上市日期'))
        listed = listed or _listing(basic.get(code, {}).get('ipoDate'))
        _need(listed is not None and listed <= observed, 'ARCHIVE_RAW_LISTING_IDENTITY_GAP')
        out = _listing(basic.get(code, {}).get('outDate'))
        _need(code not in roster or out is None or out > observed, 'ARCHIVE_OFFICIAL_OUTDATE_CONFLICT')
    current = next(value for value in by_endpoint['master_all'] if value['parameters']['date'] == str(observed))
    codes = sorted({row['code'] for row in current['rows'] if row['code'] in mainboard and ORDINARY.fullmatch(row['code'])})
    expected = [dict(codes=codes[n:n+50], start_date='1990-01-01', end_date=str(observed))
                for n in range(0, len(codes), 50)]
    actual = [value['parameters'] for value in by_endpoint.get('adjustment_factors', [])]
    _need(sorted(actual, key=_digest) == sorted(expected, key=_digest), 'ARCHIVE_EXACT_FACTOR_SCOPE_REQUIRED')
    _need(set(by_endpoint) <= {'calendar','market_daily','master_all','mainboard_sse','master_szse',
          'delisting_sse','stock_basic','stock_basic_all','adjustment_factors'}, 'ARCHIVE_UNKNOWN_CAPTURE_SCOPE')
    return sessions


def _controls(session, runtime, manifest_path, worker, supervisor, fixture):
    report = _path(runtime.profile['report_root'])
    _need(worker.parent == supervisor.parent == report/'bootstrap' and
          worker.name.startswith('core-context-') and supervisor.name == 'supervisor-'+worker.name.removeprefix('core-context-'),
          'ARCHIVE_EXACT_PARENT_WORKER_LAYOUT_REQUIRED')
    request = _request(session, manifest_path, fixture)
    invocation = _read(session, supervisor/'invocation.json')
    parent = _read(session, supervisor/'completion.json')
    _need(invocation.get('schema_version') == parent.get('schema_version') == PRIVATE_VERSION and
          invocation.get('worker_job') == parent.get('worker_job') == str(worker) and
          invocation.get('request_manifest') == str(manifest_path), 'ARCHIVE_SUPERVISOR_BINDING')
    _need(parent.get('status') == 'COMPLETED_WITH_UNKNOWNS' and
          parent.get('external_worker_exit_code') == 0 and
          all(parent.get(key) is True for key in ('external_worker_reaped','core_verified','core_terminal_binding_verified')) and
          parent.get('source_capability_restored_from_JSON') is False and parent.get('full_premarket_ready') is False,
          'ARCHIVE_SUCCESSFUL_PARENT_TERMINAL_REQUIRED')
    _need(parent['worker_completion']['path'] == str(worker/'completion.json'), 'ARCHIVE_WORKER_CONTROL_PATH')
    completion = _read(session, worker/'completion.json', parent['worker_completion'])
    _need(completion.get('schema_version') == PRIVATE_VERSION and completion.get('status') == parent['status'] and
          completion.get('output_directory') == str(worker) and completion.get('core_verified') is True and
          completion.get('database_written') is False and completion.get('source_capability_serialized') is False and
          completion.get('full_premarket_ready') is False and completion.get('with_theme') is False and
          completion.get('phase_failures') == {} and
          all(completion.get('phases', {}).get(key) == 'COMPLETE' for key in ('input','core','presentation','terminal')),
          'ARCHIVE_WORKER_TERMINAL_MISMATCH')
    ready_ref = parent['core_ready_reference']
    _need(ready_ref == completion['core_ready_reference'] and ready_ref['path'] == str(worker/'core-ready.json'),
          'ARCHIVE_READY_CONTROL_BINDING')
    ready = _read(session, ready_ref['path'], ready_ref)
    _need(ready.get('schema_version') == PRIVATE_VERSION and ready.get('status') == 'VERIFIED_ORIGINAL_R2_CORE' and
          ready.get('strategy_id') == STRATEGY and ready.get('source_capability_serialized') is False and
          ready.get('full_premarket_ready') is False and ready.get('DAILY_V1') == 'UNKNOWN_NOT_EXECUTED',
          'ARCHIVE_READY_SCOPE_INVALID')
    _need(_clock(request['cutoff']) <= _clock(invocation['actual_started_at']) <= _clock(ready['core_verified_at']),
          'ARCHIVE_CAPTURE_INVOCATION_READY_CLOCK_ORDER')
    sealed = {}
    for filename, ref in (('core-validation-references.json',ready['validation_references_reference']),
                          ('terminal-validation-references.json',completion['terminal_validation_reference'])):
        _need(ref['path'] == str(worker/filename), 'ARCHIVE_VALIDATION_CONTROL_PATH')
        value = _read(session, ref['path'], ref)
        _need(value.get('schema_version') == PRIVATE_VERSION, 'ARCHIVE_VALIDATION_VERSION')
        current = _reference_map(value['references'])
        if sealed:
            _need(current == sealed, 'ARCHIVE_CORE_TERMINAL_REFERENCE_SET_CHANGED')
        sealed = current
    _need(ready.get('validation_reference_count') == len(sealed), 'ARCHIVE_VALIDATION_COUNT_MISMATCH')
    _need(sealed.get(str(manifest_path)) == session.files[str(manifest_path)], 'ARCHIVE_REQUEST_NOT_SEALED')
    artifacts = ready['original_artifacts']
    _need(set(artifacts) == premarket.CORE_FILES, 'ARCHIVE_EXACT_EIGHT_ARTIFACTS_REQUIRED')
    for name, ref in artifacts.items():
        _need(ref['path'] == str(worker/'core'/name) and sealed.get(ref['path']) == ref,
              'ARCHIVE_ORIGINAL_ARTIFACT_BINDING')
        _ref(session, ref['path'], ref)
    _need(parent.get('display') == completion.get('display') and isinstance(parent.get('display'),dict),
          'ARCHIVE_DISPLAY_PARENT_MISMATCH')
    display = parent['display'].get('artifacts', {})
    _need(set(display) == {'text','json','csv'}, 'ARCHIVE_ORIGINAL_DISPLAY_REQUIRED')
    for role, ref in display.items():
        name = {'text':'premarket.txt','json':'premarket.json','csv':'premarket.csv'}[role]
        _need(ref['path'] == str(worker/'display'/name), 'ARCHIVE_DISPLAY_OUTSIDE_WORKER')
        _ref(session, ref['path'], ref)
    worker_invocation = _read(session, worker/'invocation.json')
    _need(worker_invocation.get('schema_version') == PRIVATE_VERSION and
          worker_invocation.get('manifest_path') == str(manifest_path) and
          worker_invocation.get('source_root') == runtime.profile['source_root'] and
          worker_invocation.get('interpreter') == runtime.profile['python'] and
          worker_invocation.get('with_theme') is False, 'ARCHIVE_WORKER_INVOCATION_MISMATCH')
    _clock(worker_invocation['actual_started_at'])
    code_refs = _reference_map(invocation['implementation_references'])
    source = _path(runtime.profile['source_root'])
    _need(source == ROOT, 'ARCHIVE_EXECUTING_SOURCE_MUST_MATCH_RUNTIME')
    closure = {str(path) for path in (source/'stockfish_core').rglob('*.py')} | {
        str(source/name) for name in ('premarket','premarket.ps1','scripts/stockfish.py')}
    outside = set(code_refs)-closure
    _need(closure <= set(code_refs) and len(outside) == 1, 'ARCHIVE_COMPLETE_ORIGINAL_IMPLEMENTATION_CLOSURE_REQUIRED')
    helper = code_refs[next(iter(outside))]
    _need(helper['sha256'] == worker_invocation.get('script_sha256') and
          (helper['sha256'] == PRIVATE_WORKER_SHA256 or
           fixture and session.read(helper['path'], helper, limit=65536) == b'TEST_FIXTURE_PRIVATE_SUPERVISOR\n'),
          'ARCHIVE_UNRECOGNIZED_PRIVATE_WORKER_IMPLEMENTATION')
    _ref(session, helper['path'], helper)
    return request, ready, artifacts, sealed, [code_refs[path] for path in sorted(closure)]


def _authenticate(session, runtime, manifest_path, worker, supervisor, fixture, *, normalize=True):
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    from stockfish_core.yaogu.contracts import SourceReceipt
    from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
    from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader
    from stockfish_core.yaogu.short_horizon_participation import short_horizon_source_identity
    request, ready, artifacts, sealed, code = _controls(session, runtime, manifest_path, worker, supervisor, fixture)
    native = _read(session, artifacts['manifest.json']['path'], artifacts['manifest.json'])
    _need(native.get('schema_version') == 'PREMARKET_NATIVE_CORE_V1' and native.get('status') == 'PASS' and
          native.get('artifacts') == {k:v for k,v in artifacts.items() if k != 'manifest.json'} and
          native.get('source_capability_serialized') is False and native.get('source_database_written') is False,
          'ARCHIVE_NATIVE_MANIFEST_BINDING')
    document = bridge._decompress(session.read(artifacts['input.json.gz']['path'], artifacts['input.json.gz']), session)
    _need(document.get('history_artifact_ref') is None and document.get('history_artifact_hash') is None and
          not any(row.get('historical_time_proof') is not None for row in document['receipts']),
          'ARCHIVE_INLINE_INPUT_REQUIRED')
    _need(document['coverage_type'] == request['coverage_type'] and document['observation_date'] == request['observation_date'],
          'ARCHIVE_INPUT_REQUEST_CONTEXT')
    input_hash = bridge.digest_json_stream(document, checkpoint=session.check)
    original_sessions = tuple(date.fromisoformat(day) for day in document['sessions'])
    saved_receipts = {row['receipt_id']:SourceReceipt.model_validate(row) for row in document['receipts']}
    # This small projection is archived data only, never a MarketInput owner
    # or source capability. The complete document hash was checked above.
    from types import SimpleNamespace
    projection = SimpleNamespace(coverage_type=document['coverage_type'],
        observation_date=date.fromisoformat(document['observation_date']),
        universe=SimpleNamespace(securities=[SimpleNamespace(instrument_id=row['instrument_id'],name=row['name'])
            for row in document['universe']['securities']]))
    _need(len(saved_receipts) == len(document['receipts']), 'ARCHIVE_DUPLICATE_INPUT_RECEIPT')
    del document
    gc.collect()
    run = bridge._decompress(session.read(artifacts['canonical-run.json.gz']['path'], artifacts['canonical-run.json.gz']), session)
    _need(input_hash == run.get('input_hash') == native.get('input_hash') == ready.get('input_hash'), 'ARCHIVE_INPUT_HASH_MISMATCH')
    for key in ('run_id','observation_date','target_session','decision_cutoff'):
        _need(run.get(key) == native.get(key) == ready.get(key), 'ARCHIVE_READY_RUN_CONTEXT_' + key)
    _need(run['decision_cutoff'] == request['cutoff'] and run['target_session'] == request['target_session'],
          'ARCHIVE_REQUEST_RUN_CONTEXT')
    _need(run.get('content_hash') == native.get('canonical_content_hash') == ready.get('canonical_content_hash'),
          'ARCHIVE_CANONICAL_CONTENT_HASH_MISMATCH')
    _need(_clock(run['decision_cutoff']) <= _clock(run['generated_at']) <= _clock(ready['core_verified_at']),
          'ARCHIVE_RUN_CLOCK_ORDER')
    root, _, proofs = bridge._source_map(session, code, {}, run)
    _need(root == ROOT, 'ARCHIVE_SOURCE_ROOT_CHANGED')
    identity = short_horizon_source_identity()
    for key, expected in identity.items():
        if key in run:
            _need(run[key] == expected, 'ARCHIVE_FROZEN_STRATEGY_IDENTITY_CHANGED_' + key)
    typed = [SourceReceipt.model_validate(row) for row in native['source_receipts']]
    receipts = {row.receipt_id:row for row in typed}
    _need(len(receipts) == len(typed) == len(request['captures']) and receipts == saved_receipts,
          'ARCHIVE_EXACT_SOURCE_RECEIPT_SET_REQUIRED')
    results, paths, used = [], {}, set()
    for item in request['captures']:
        result, receipt, raw_path = bridge._trio(session, item, sealed, receipts, proofs,
            request['coverage_type'], _clock(request['cutoff']))
        raw = _read(session,item['raw']['path'],item['raw'],256*MIB)
        _need(raw.get('first_seen_at') is not None and
              _clock(raw['first_seen_at']) == receipt.first_seen_at, 'ARCHIVE_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED')
        del raw
        is_fixture = (str(result.get('provider','')).startswith('TEST_FIXTURE') or
                      str(result.get('field_version','')).startswith('TEST_FIXTURE') or
                      any('TEST_FIXTURE.invalid' in str(row.get('source_url','')) for row in result.get('receipts',[])))
        _need(is_fixture == fixture, 'ARCHIVE_MIXED_TEST_FIXTURE_FORBIDDEN')
        _need(receipt.receipt_id not in used, 'ARCHIVE_DUPLICATE_CAPTURE')
        used.add(receipt.receipt_id)
        results.append(result)
        paths[receipt.receipt_id] = raw_path
    _need(used == set(receipts), 'ARCHIVE_SOURCE_OMISSION')
    sessions = _full_scope(results, date.fromisoformat(request['observation_date']))
    _need(sessions == original_sessions, 'ARCHIVE_INPUT_SESSION_CALENDAR_MISMATCH')
    session.check()
    if normalize:
        bundle, normalization = normalize_capture_with_report(results, date.fromisoformat(request['observation_date']),
            _clock(request['cutoff']), sessions, coverage_type=request['coverage_type'])
        session.check()
        _need(bridge.digest_market_input(bundle, checkpoint=session.check) == input_hash and
              normalization == native['normalization_report'], 'ARCHIVE_FRESH_NORMALIZATION_DIFFERS_FROM_ORIGINAL')
        # Original eligibility validation is independent of selection. Checking
        # only saved IDs/counts would allow a consistently truncated pool.
        from stockfish_core.yaogu.strategy_experiments import _prepare_context
        from stockfish_core.yaogu.contracts import digest
        qualification = _prepare_context(bundle, _clock(request['cutoff']))
        _need(run.get('qualification') == qualification.qualification and
              run['results'][STRATEGY].get('qualification') == qualification.qualification and
              run['results'][STRATEGY].get('eligible_pool_ids') == list(qualification.eligible_ids) and
              run.get('universe_hash') == digest(qualification.qualification),
              'ARCHIVE_COMPLETE_ORIGINAL_ELIGIBILITY_MISMATCH')
        del qualification
    else:
        bundle = projection
    del results
    gc.collect()
    bridge._validate_run(run, bundle, native, artifacts, session, input_hash)
    class Reader(SourceArtifactReader):
        def __call__(self, receipt_id):
            return session.read(paths[receipt_id], receipts[receipt_id].content_hash)
    Reader(tuple(receipts.values()), paths).validate_target_calendar_context(
        run['target_calendar_evidence'], bundle=bundle, cutoff=_clock(request['cutoff']))
    _need(run['results'][STRATEGY]['counts'] == ready['counts'], 'ARCHIVE_READY_COUNTS_MISMATCH')
    _need(ready['normalization_gap_count'] == len(native['normalization_report'].get('gaps',[])),
          'ARCHIVE_NORMALIZATION_GAP_COUNT_MISMATCH')
    for ref in sealed.values():
        _ref(session, ref['path'], ref)
    summary = {key:deepcopy(ready[key]) for key in ('run_id','input_hash','canonical_content_hash','counts',
        'observation_date','target_session','decision_cutoff','normalization_gap_count')}
    summary.update(strategy_id=STRATEGY, coverage_type=request['coverage_type'], parameter_status='UNVALIDATED',
        market_edge='NOT_ESTABLISHED', DAILY_V1='UNKNOWN_NOT_EXECUTED', full_premarket_ready=False,
        source_authority_restored=False, result_role='AUTHENTICATED_ARCHIVED_CORE_RESEARCH')
    del bundle, run
    gc.collect()
    return summary, artifacts, [deepcopy(item['verification_locator']) for item in request['captures']
                                if item.get('verification_locator') is not None]


def _operation_identity(runtime, manifest, worker, supervisor, fixture):
    _need(runtime is not None and runtime.profile.get('backend') == 'premarket', 'ARCHIVE_REGISTERED_RUNTIME_REQUIRED')
    _need(Path(sys.executable).absolute() == Path(runtime.profile['python']), 'ARCHIVE_REGISTERED_INTERPRETER_REQUIRED')
    return dict(schema_version=VERSION, operation='AUTHENTICATE_EXISTING_FULL_CORE',
        manifest=str(manifest), worker=str(worker), supervisor=str(supervisor),
        strategy_id=STRATEGY, profile=PROFILE, allow_test_fixture=fixture)


def _validate_evidence(session, runtime, receipt, identity):
    _need(receipt.get('schema_version') == VERSION and receipt.get('identity') == identity,
          'ARCHIVE_RECEIPT_IDENTITY_MISMATCH')
    reference = receipt['evidence']
    path = Path(reference['path'])
    _need(path.parent == Path(runtime.profile['report_root'])/'core-archives' and
          re.fullmatch(r'[0-9a-f]{64}\.json',path.name), 'ARCHIVE_EVIDENCE_OUTSIDE_REGISTERED_ROOT')
    evidence = _read(session, path, reference, MAX_EVIDENCE)
    _need(evidence.get('schema_version') == VERSION and evidence.get('identity') == identity and
          evidence.get('summary') == receipt.get('summary') and evidence.get('first_registration_normalizer_calls') == 1,
          'ARCHIVE_EVIDENCE_IDENTITY_MISMATCH')
    _clock(evidence['authenticated_at'])
    _need(identity == _operation_identity(runtime,Path(identity['manifest']),Path(identity['worker']),
          Path(identity['supervisor']),identity['allow_test_fixture']), 'ARCHIVE_OPERATION_CONTRACT_CHANGED')
    _need(evidence['verifier_references'] == _verifier(session), 'ARCHIVE_VERIFIER_IMPLEMENTATION_CHANGED')
    refs = _reference_map(evidence['references'])
    # Independently rebuild the necessary closure and saved result semantics;
    # an edited evidence JSON cannot omit a RAW, producer, or code dependency,
    # or invent a different summary merely by recomputing its own hash.
    summary, artifacts, locators = _authenticate(session,runtime,Path(identity['manifest']),
        Path(identity['worker']),Path(identity['supervisor']),identity['allow_test_fixture'],normalize=False)
    _need(summary == evidence['summary'] and artifacts == evidence['artifacts'] and locators == evidence['locators'],
          'ARCHIVE_EVIDENCE_DIFFERS_FROM_ORIGINAL_RESULT')
    control_paths = {str(path), str(runtime.directory/'operations'/(path.stem+'.json'))}
    _need(all(refs.get(name) == ref for name,ref in session.files.items() if name not in control_paths),
          'ARCHIVE_REQUIRED_ORIGINAL_CLOSURE_MISSING')
    for reference in refs.values():
        _ref(session, reference['path'], reference)
    from stockfish_core.yaogu.premarket_native_core_v1 import _LocatorBinding
    for declaration in evidence['locators']:
        session.locators.append(_LocatorBinding(declaration))
    session.terminal()
    # Controls are locators, never an owner. The authenticated whole-original
    # set includes request, native input/run/exports, producer/code and finals.
    _need(all(str(Path(identity[key])) in refs for key in ('manifest',)), 'ARCHIVE_MISSING_REQUEST_REFERENCE')
    for filename in ('invocation.json','completion.json'):
        _need(str(Path(identity['supervisor'])/filename) in refs, 'ARCHIVE_MISSING_PARENT_REFERENCE')
    _need(set(evidence['artifacts']) == premarket.CORE_FILES and all(refs.get(ref['path']) == ref
          for ref in evidence['artifacts'].values()), 'ARCHIVE_ARTIFACT_SET_NOT_SEALED')
    return dict(verified=True, profile=PROFILE, source_authority_restored=False,
        currentness_recomputed=False, full_premarket_ready=False, normalizer_calls=0,
        selector_calls=0, provider_calls=0, new_large_artifact_bytes=0,
        summary=deepcopy(evidence['summary']), artifacts=deepcopy(evidence['artifacts']),
        metrics=session.audit())


def register_archive(runtime, manifest_path, worker_dir, supervisor_dir, *, profile=None,
                     allow_test_fixture=False, limits=None):
    _need(type(allow_test_fixture) is bool, 'ARCHIVE_FIXTURE_FLAG_MUST_BE_BOOLEAN')
    _need(profile == PROFILE, 'ARCHIVE_EXPLICIT_READONLY_PROFILE_REQUIRED')
    manifest, worker, supervisor = (_path(path) for path in (manifest_path,worker_dir,supervisor_dir))
    identity = _operation_identity(runtime,manifest,worker,supervisor,bool(allow_test_fixture))
    session = _session(limits)
    archive_id = workspace._digest({'scope':runtime.scope,'identity':identity})
    output = Path(runtime.profile['report_root'])/'core-archives'/(archive_id+'.json')
    normalizers = 0
    def action():
        nonlocal normalizers
        admission = runtime.preflight(expected_growth_bytes=MAX_EVIDENCE, destination=output.parent)
        _need(admission['allowed'], 'ARCHIVE_METADATA_CAPACITY_BLOCKED')
        verifier = _verifier(session)
        summary, artifacts, locators = _authenticate(session,runtime,manifest,worker,supervisor,bool(allow_test_fixture))
        normalizers += 1
        session.terminal()
        evidence = dict(schema_version=VERSION, identity=identity, authenticated_at=workspace._stamp(),
            summary=summary, artifacts=artifacts, locators=locators, verifier_references=verifier,
            references=deepcopy(list(session.files.values())), first_registration_normalizer_calls=1,
            source_authority_restored=False, original_archive_modified=False, metrics=session.audit())
        _need(len(workspace._canonical(evidence)) <= MAX_EVIDENCE, 'ARCHIVE_EVIDENCE_SIZE_BUDGET')
        workspace._write(output,evidence)
        return dict(schema_version=VERSION, identity=identity, evidence=_ref(session,output), summary=summary)
    result = runtime.run_once(identity,action,lambda receipt:_validate_evidence(session,runtime,receipt,identity))
    result.update(archive_id=archive_id, summary=deepcopy(result['receipt']['summary']),
        normalizer_calls=normalizers, selector_calls=0, provider_calls=0, new_large_artifact_bytes=0,
        metrics=session.audit())
    return result


def reuse_archive(runtime, archive_id, *, profile=None, allow_test_fixture=False, limits=None,
                  observation_date=None, target_session=None, cutoff=None, manifest_path=None):
    _need(type(allow_test_fixture) is bool, 'ARCHIVE_FIXTURE_FLAG_MUST_BE_BOOLEAN')
    _need(profile == PROFILE, 'ARCHIVE_EXPLICIT_READONLY_PROFILE_REQUIRED')
    _need(isinstance(archive_id,str) and re.fullmatch(r'[0-9a-f]{64}',archive_id), 'ARCHIVE_INVALID_ID')
    _need(runtime is not None, 'ARCHIVE_REGISTERED_RUNTIME_REQUIRED')
    session = _session(limits)
    with runtime.exclusive():
        record = _read(session,runtime.directory/'operations'/(archive_id+'.json'),maximum=workspace.MAX_CONTROL)
        identity = record.get('identity')
        _need(record.get('schema_version') == workspace.VERSION and record.get('scope') == runtime.scope and
              record.get('operation_id') == archive_id and record.get('phase') == 'RECORDED' and
              isinstance(identity,dict) and identity.get('schema_version') == VERSION and
              identity.get('profile') == PROFILE and identity.get('allow_test_fixture') is bool(allow_test_fixture) and
              archive_id == workspace._digest({'scope':runtime.scope,'identity':identity}),
              'ARCHIVE_RECORDED_OPERATION_REQUIRED_NO_RETRY')
        validation = _validate_evidence(session,runtime,record['receipt'],identity)
        for field, expected in (('observation_date',observation_date),('target_session',target_session),('decision_cutoff',cutoff)):
            if expected is not None:
                _need(validation['summary'][field] == expected, 'ARCHIVE_EXACT_QUERY_CONTEXT_MISMATCH_' + field)
        if manifest_path is not None:
            query = _request(session,_path(manifest_path),bool(allow_test_fixture))
            original = _request(session,Path(identity['manifest']),bool(allow_test_fixture))
            _need(query == original, 'ARCHIVE_EXACT_QUERY_SOURCE_MANIFEST_MISMATCH')
        runtime.check_binding()
        session.terminal()
        return dict(status='REUSED', archive_id=archive_id, reused=True, summary=validation['summary'],
            artifacts=validation['artifacts'], normalizer_calls=0,selector_calls=0,provider_calls=0,
            new_large_artifact_bytes=0,source_authority_restored=False,full_premarket_ready=False,
            validation=validation,metrics=session.audit())


def archive_status(runtime, *, limit=100):
    """Bounded locator listing only; call reuse before displaying any stock data."""
    _need(runtime is not None and type(limit) is int and 0 < limit <= 100, 'ARCHIVE_STATUS_LIMIT')
    with runtime.exclusive():
        directory = runtime.directory/'operations'
        entries = []
        if directory.exists():
            _path(directory)
            _need(sum(1 for _ in directory.iterdir()) <= 4096, 'ARCHIVE_OPERATION_SCAN_LIMIT')
            for path in sorted(directory.glob('*.json')):
                record = workspace._read(path)
                identity = record.get('identity',{})
                if identity.get('schema_version') == VERSION:
                    _need(len(entries) < limit, 'ARCHIVE_STATUS_LIMIT_EXCEEDED_NO_TRUNCATION')
                    entries.append(dict(archive_id=record.get('operation_id'),phase=record.get('phase'),
                        worker=identity.get('worker'), source_authority_restored=False))
        return dict(status='LOCATORS_ONLY_REUSE_VALIDATION_REQUIRED',archives=entries)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime')
    parser.add_argument('--registry-root')
    sub = parser.add_subparsers(dest='command',required=True)
    register = sub.add_parser('register')
    register.add_argument('--manifest',required=True)
    register.add_argument('--worker-dir',required=True)
    register.add_argument('--supervisor-dir',required=True)
    reuse = sub.add_parser('reuse')
    reuse.add_argument('--archive-id',required=True)
    reuse.add_argument('--observation-date')
    reuse.add_argument('--target-session')
    reuse.add_argument('--cutoff')
    reuse.add_argument('--manifest')
    sub.add_parser('status')
    for command in (register,reuse):
        command.add_argument('--profile',choices=[PROFILE],required=True)
        command.add_argument('--allow-test-fixture',action='store_true')
    args = parser.parse_args(argv)
    try:
        runtime = workspace.load_runtime(args.runtime,args.registry_root)
        if args.command == 'register':
            result = register_archive(runtime,args.manifest,args.worker_dir,args.supervisor_dir,
                profile=args.profile,allow_test_fixture=args.allow_test_fixture)
        elif args.command == 'reuse':
            result = reuse_archive(runtime,args.archive_id,profile=args.profile,
                allow_test_fixture=args.allow_test_fixture,observation_date=args.observation_date,
                target_session=args.target_session,cutoff=args.cutoff,manifest_path=args.manifest)
        else:
            result = archive_status(runtime)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0
    except Exception as error:
        print(json.dumps(dict(status='BLOCKED',reason=str(error) if isinstance(error,(ArchiveError,workspace.WorkspaceError))
            else 'ARCHIVE_ORIGINAL_VALIDATION_FAILED',error_type=type(error).__name__),ensure_ascii=False))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
