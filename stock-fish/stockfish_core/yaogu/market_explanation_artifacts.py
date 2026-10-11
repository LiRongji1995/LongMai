"""Explicit original-byte market explanation generation and archived display.

Saved receipt/map files locate original evidence; they never restore a native
market capability. Generation calls both native producers over authenticated
originals. Archived display does not issue evidence or change a saved selector.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .contracts import MarketInput
from .history_artifacts import digest_market_input, digest_json_stream
from .market_descriptive_context import (
    run_market_descriptive_context, validate_market_descriptive_context,
)
from .research_context_artifacts import SourceArtifactReader, RETURN_BASIS, SH
from .theme_market_stream import SourceMarketStream
from .visibility import stamp

VERSION = 'MARKET_EXPLANATION_ARTIFACTS_V1'
READ_LIMIT = 256 * 1024 * 1024


def source_identity():
    """Bind this new coordination layer without altering native identities."""
    from .market_explanation import source_identity as explanation_identity
    root = Path(__file__).resolve().parents[2]
    paths = (Path(__file__), root / 'scripts' / 'codex_research.py')
    value = {'implementation_version': VERSION,
        'source_file_hashes': {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in paths},
        'explanation_source_hash': explanation_identity()['source_hash']}
    return {**value, 'source_hash': digest_json_stream(value)}


def _read(path):
    path = Path(path)
    if path.stat().st_size > READ_LIMIT:
        raise ValueError('MARKET_EXPLANATION_SAVED_READ_LIMIT')
    raw = path.read_bytes()
    if len(raw) > READ_LIMIT:
        raise ValueError('MARKET_EXPLANATION_SAVED_READ_LIMIT')
    return json.loads(raw), {'path': str(path.resolve()), 'bytes': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest()}


def _check_files(files):
    for meta in files.values():
        path = Path(meta['path'])
        if path.stat().st_size != meta['bytes']:
            raise ValueError('MARKET_EXPLANATION_SAVED_PARENT_CHANGED')
        h = hashlib.sha256()
        with path.open('rb') as handle:
            for raw in iter(lambda: handle.read(1024 * 1024), b''):
                h.update(raw)
        if h.hexdigest() != meta['sha256']:
            raise ValueError('MARKET_EXPLANATION_SAVED_PARENT_CHANGED')


def _save(directory, name, value):
    raw = value.encode('utf-8') if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    with (directory / name).open('xb') as handle:
        handle.write(raw)
    return {'path': str((directory / name).resolve()), 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest()}


def run_market_explanation(*, input_path, run_path, source_receipts_path,
                           source_map_path, output, cutoff, as_of=None):
    """Generate an independent attachment; no selector, network or DB access.

    Original run file authentication here is a byte/content binding, not native
    reauthentication of its selector. All original market parents are checked
    by the native stream and fact issuer; no saved context JSON is accepted.
    """
    from .market_explanation import build_market_explanation, render_market_explanation

    directory = Path(output)
    if directory.exists():
        raise ValueError('MARKET_EXPLANATION_OUTPUT_EXISTS_PRESERVED')
    decision = stamp(cutoff)
    coordination_identity = source_identity()
    now = datetime.now(timezone.utc)
    if decision > now:
        raise ValueError('MARKET_EXPLANATION_FUTURE_CUTOFF')
    paths = {'input': input_path, 'run': run_path,
             'source_receipts': source_receipts_path, 'source_map': source_map_path}
    documents, parents = {}, {}
    for key, path in paths.items():
        documents[key], parents[key] = _read(path)
    original = documents.pop('input')
    if not isinstance(original, dict):
        raise ValueError('MARKET_EXPLANATION_INPUT_DOCUMENT_REQUIRED')
    bundle = MarketInput.model_validate(original.get('input', original))
    del original
    run = documents.pop('run')
    if not isinstance(run, dict):
        raise ValueError('MARKET_EXPLANATION_SAVED_RUN_DOCUMENT_REQUIRED')
    if (str(bundle.observation_date) != str(run.get('observation_date'))
            or run.get('input_hash') != digest_market_input(bundle)):
        raise ValueError('MARKET_EXPLANATION_SAVED_RUN_INPUT_MISMATCH')
    observed_calendar = tuple(day for day in bundle.sessions if day <= bundle.observation_date)
    if (bundle.observation_date > decision.astimezone(SH).date()
            or not observed_calendar or observed_calendar[-1] != bundle.observation_date):
        raise ValueError('MARKET_EXPLANATION_OBSERVATION_CALENDAR_MISMATCH')
    # This is a new live reader of original bytes, not restoration of an issued
    # fact capability. Native producers enforce SHA, complete envelope, dates,
    # return basis and original capture clocks against each consumed parent.
    receipts = documents['source_receipts']
    mapping = documents['source_map']
    if not isinstance(receipts, (list, dict)) or not isinstance(mapping, dict):
        raise ValueError('MARKET_EXPLANATION_ORIGINAL_READER_DOCUMENT_REQUIRED')
    receipt_rows = receipts.get('source_receipts', receipts) if isinstance(receipts, dict) else receipts
    original_paths = mapping.get('mapping', mapping)
    if not isinstance(receipt_rows, (list, tuple)) or not isinstance(original_paths, dict):
        raise ValueError('MARKET_EXPLANATION_ORIGINAL_READER_DOCUMENT_REQUIRED')
    reader = SourceArtifactReader(receipt_rows, original_paths)
    sessions = observed_calendar[-21:]
    _check_files(parents)
    directory.mkdir(parents=True, exist_ok=False)
    _save(directory, 'request.json', {'version': VERSION, 'parents': parents,
        'cutoff': decision.isoformat(), 'observation_date': str(bundle.observation_date),
        'requested_market_sessions': [str(day) for day in sessions],
        'network_calls': 0, 'source_database_written': False,
        'selector_reauthenticated': False, 'saved_context_capability_restored': False})
    try:
        stream = SourceMarketStream(reader, bundle, cutoff=decision,
                                    sessions=sessions, return_basis=RETURN_BASIS)
        capability = run_market_descriptive_context(bundle, cutoff=decision,
            market_stream=stream, sessions=(bundle.observation_date,),
            evidence_directory=directory / 'native-market-context')
        explanation = build_market_explanation(bundle, capability, cutoff=decision, run=run)
        _check_files(parents)
        validate_market_descriptive_context(capability, bundle=bundle, cutoff=decision)
        if source_identity() != coordination_identity:
            raise ValueError('MARKET_EXPLANATION_COORDINATION_IMPLEMENTATION_CHANGED')
        report_as_of = stamp(as_of) if as_of is not None else datetime.now(timezone.utc)
        rendered = render_market_explanation(explanation, as_of=report_as_of)
        files = {'explanation': _save(directory, 'market-explanation.json', explanation),
                 'markdown': _save(directory, 'market-explanation.md', rendered)}
        # Saved outputs are not advertised as a successful generation until
        # originals, frozen input/run bytes and native registries pass again.
        _check_files(parents)
        validate_market_descriptive_context(capability, bundle=bundle, cutoff=decision)
        if source_identity() != coordination_identity:
            raise ValueError('MARKET_EXPLANATION_COORDINATION_IMPLEMENTATION_CHANGED')
        manifest = {'version': VERSION, 'status': 'SAVED_NATIVE_MARKET_EXPLANATION',
            'run_id': run['run_id'], 'input_hash': run['input_hash'],
            'observation_date': str(bundle.observation_date),
            'original_run_cutoff': run.get('decision_cutoff', run.get('cutoff')),
            'context_cutoff': decision.isoformat(), 'report_as_of': report_as_of.isoformat(),
            'parents': parents, 'files': files, 'native_context_hash': capability.context_hash,
            'coordination_source_identity': coordination_identity,
            'native_context_files': capability.artifact_files,
            'source_database_written': False, 'network_calls': 0,
            'selector_reauthenticated': False, 'selection_changed': False,
            'market_edge': 'NOT_ESTABLISHED'}
        _save(directory, 'manifest.json', manifest)
        return {key: manifest[key] for key in ('status', 'run_id', 'observation_date',
            'original_run_cutoff', 'context_cutoff', 'report_as_of', 'selection_changed',
            'source_database_written', 'market_edge')} | {'output_directory': str(directory.resolve()), 'files': files}
    except Exception as exc:
        _save(directory, 'failure.json', {'version': VERSION, 'status': 'FAILED',
            'exception_type': type(exc).__name__, 'source_database_written': False,
            'successful_attachment_claimed': False})
        raise


def archived_explanation(path, *, snapshot, as_of):
    """Display one saved attachment associated with this exact report snapshot.

    The returned saved facts gain no live capability or selector authority.
    Run identity and source-run hash are checked before time-filtered rendering.
    """
    from .market_explanation import render_market_explanation

    document, parent = _read(path)
    if not isinstance(document, dict):
        raise ValueError('MARKET_EXPLANATION_SAVED_ATTACHMENT_DOCUMENT_REQUIRED')
    binding = document.get('frozen_run_binding', {})
    if not isinstance(snapshot, dict):
        raise ValueError('MARKET_EXPLANATION_REPORT_RUN_MISMATCH')
    identity = snapshot.get('identity', {})
    if not isinstance(binding, dict) or not isinstance(identity, dict):
        raise ValueError('MARKET_EXPLANATION_REPORT_BINDING_DOCUMENT_REQUIRED')
    expected = {'run_id': identity.get('run_id'),
                'input_hash': (snapshot or {}).get('input_hash'),
                'observation_date': identity.get('observation_date')}
    if any(value is None or binding.get(key) != value for key, value in expected.items()):
        raise ValueError('MARKET_EXPLANATION_REPORT_RUN_MISMATCH')
    source_hash = (snapshot or {}).get('source_run_hash')
    if source_hash is not None and binding.get('declared_run_content_hash') != source_hash:
        raise ValueError('MARKET_EXPLANATION_REPORT_SOURCE_RUN_HASH_MISMATCH')
    rendered = render_market_explanation(document, as_of=as_of)
    _check_files({'attachment': parent})
    return {'artifact_role': 'ARCHIVED_EXPLANATION_DISPLAY_NOT_LIVE_CAPABILITY',
            'file': parent, 'run_id': expected['run_id'], 'markdown': rendered,
            'source_run_hash_verification': 'AVAILABLE' if source_hash is not None else 'UNKNOWN',
            'source_run_hash_reason_codes': [] if source_hash is not None else ['SOURCE_RUN_HASH_NOT_CAPTURED'],
            'selection_changed': False, 'market_edge': 'NOT_ESTABLISHED'}
