"""Explicit full-archive V2 verification; V1 files and receipts remain untouched.

The orchestration below retains every V1 scope/semantic/terminal assertion.
Only duplicate RAW decoding and the decompressed temporary representation
change. State-machine/CLI functions reuse the original code in private globals.
"""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_core_archive as legacy
from scripts import codex_verification_v2 as verification
# Import the unchanged helpers, without rebinding their defining module.
for _name, _value in vars(legacy).items():
    if not _name.startswith("__") and _name not in {"ROOT", "legacy", "verification"}:
        globals()[_name] = _value
VERSION = "CODEX_FULL_CORE_ARCHIVE_V2"
PROFILE = "FULL_CORE_ARCHIVE_READONLY_V2"


def _verifier(session):
    return legacy._verifier(session) + [legacy._ref(session, Path(path).resolve())
        for path in (__file__, verification.__file__)]


def _session(limits=None):
    session = legacy._session(limits)
    session.read = verification.timed("source_read", session.read)
    session.terminal = verification.timed("full_terminal", session.terminal)
    return session


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
    document = verification.decompress_document(session.read(artifacts['input.json.gz']['path'], artifacts['input.json.gz']), session)
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
    run = verification.decompress_document(session.read(artifacts['canonical-run.json.gz']['path'], artifacts['canonical-run.json.gz']), session)
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
        result, receipt, raw_path, raw_clock = verification.trio_with_clock(session, item, sealed, receipts, proofs,
            request['coverage_type'], _clock(request['cutoff']))
        _need(raw_clock['present'] and raw_clock['value'] is not None and
              _clock(raw_clock['value']) == receipt.first_seen_at, 'ARCHIVE_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED')
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


_api = verification.bind_functions(legacy,
    ("_operation_identity", "_validate_evidence", "register_archive", "reuse_archive", "archive_status"),
    dict(VERSION=VERSION, PROFILE=PROFILE, _verifier=_verifier, _session=_session,
         _authenticate=verification.timed("archive_authentication", _authenticate)))


def register_archive(*args, **kwargs):
    with verification.metrics_scope() as metrics:
        result = _api["register_archive"](*args, **kwargs)
        result["verification_v2_metrics"] = metrics.report()
        return result


def reuse_archive(*args, **kwargs):
    with verification.metrics_scope() as metrics:
        result = _api["reuse_archive"](*args, **kwargs)
        result["verification_v2_metrics"] = metrics.report()
        return result


archive_status = _api["archive_status"]
main = verification.bind_functions(legacy, ("main",), dict(PROFILE=PROFILE,
    register_archive=register_archive, reuse_archive=reuse_archive, archive_status=archive_status))["main"]

if __name__ == "__main__":
    raise SystemExit(main())
