"""Build an R05 review ZIP from current bytes and observed execution records.

No Git/network operations. Raw market data and private state are reference-only.
Historical failures remain visible; exit-zero is never investment validation.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
SOURCE_EXTENSIONS = {'.py', '.js', '.ts', '.tsx', '.jsx', '.css', '.scss', '.html',
    '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.sql', '.md', '.txt', '.rst',
    '.ps1', '.sh', '.bat', '.cmd', '.lock', '.svg'}
SPECIAL_NAMES = {'license', 'notice', 'authors', 'dockerfile', 'makefile',
    '.gitignore', '.gitattributes', '.dockerignore', '.env.example'}
PRUNED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.docker-backups', 'mlruns', 'models', 'model_weights',
    'checkpoints', 'provider-cache', 'provider_cache'}
ROOT_EXCLUSIONS = {'data', 'memory', 'review', 'work', 'reports', 'batch_results',
    'simulation_output', 'mirofish', 'bettafish', '.claude'}
SOURCE_PATH_EXCLUSIONS = {('qlib-zh', 'data')}
EVIDENCE_PRUNED = PRUNED | {'supply', 'raw', 'raw-inputs', 'raw_inputs', 'cache',
    'caches', 'private', 'state', 'credentials', 'databases'}
FORBIDDEN_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.pkl', '.pickle', '.parquet',
    '.npy', '.npz', '.bin', '.pt', '.pth', '.onnx', '.pem', '.key'}
EXECUTION_NAMES = {'execution.json', 'result.json', 'run.json'}
SUMMARY_NAMES = {'runtime-summary.json', 'endpoint-status.json', 'image-source-comparison.json',
                 'source-comparison.json'}
REPLAY_NAMES = {'replay_plan.json', 'historical_attempts_20_sessions.json',
    'daily_attempt.json', 'input_reference.json', 'qualification_audit.json', 'rank.json',
    'data_gaps.json', 'focus.csv', 'pv_full.csv', 'attempt.json', 'failure.json',
    'canonical_outcomes.jsonl.gz', 'tracking.json', 'paper_trades.jsonl',
    'outcome_input_reference.json', 'outcomes.jsonl', 'revision_selection.jsonl',
    'outcomes.csv', 'maturity_matrix.csv', 'statistics_by_date.json',
    'statistics_primary_5_sessions.json', 'statistics_attempt_20_sessions.json',
    'statistics_mature_supplement.json', 'paper_execution_summary.json',
    'production_feedback_5_20_60.json', 'scope_and_comparators.json',
    'execution_assumptions.yaml', 'execution_assumptions.json', 'post_exit_shadow.jsonl',
    'outcome_execution_plan.json'}
RAW_KEYS = {'raw_rows', 'raw_responses', 'raw_pages', 'bars_by_instrument',
    'price_bars', 'master_records', 'market_input', 'raw_market_input'}
RUN_FIELDS = {'command', 'args', 'cwd', 'started_at', 'ended_at', 'finished_at',
    'start', 'end', 'elapsed_seconds', 'exit_code', 'execution_state', 'result',
    'environment', 'python', 'evidence_class', 'source_identity', 'stdin',
    'implementation_hash', 'worker_source_hashes', 'source_manifest',
    'working_directory', 'duration_seconds', 'script_sha256', 'status', 'console_log', 'summary_sha256'}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode('utf-8')


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def private_name(path):
    name = path.name.lower()
    return (((name == '.env' or name.startswith('.env.')) and name != '.env.example')
            or path.suffix.lower() in FORBIDDEN_SUFFIXES or name.endswith(('-wal', '-shm')))


def walk_files(root, *, prune=PRUNED, root_exclusions=()):
    root = Path(root).resolve()
    if not root.is_dir():
        return
    for directory, dirs, files in os.walk(root, followlinks=False):
        current = Path(directory)
        dirs[:] = sorted(d for d in dirs if d.lower() not in prune
            and not (current == root and d.lower() in root_exclusions)
            and not (current / d).is_symlink()
            and not getattr(current / d, 'is_junction', lambda: False)())
        for name in sorted(files):
            path = current / name
            if not path.is_symlink() and path.resolve().is_relative_to(root):
                yield path


def source_allowed(path, relative):
    parts = tuple(part.lower() for part in relative.parts)
    if (private_name(path) or path.name.startswith('._')
            or any(parts[:len(prefix)] == prefix for prefix in SOURCE_PATH_EXCLUSIONS)):
        return False
    return (path.name.lower() in SPECIAL_NAMES or path.name.startswith('Dockerfile')
        or path.suffix.lower() in SOURCE_EXTENSIONS
        or (relative.parts[0] == 'tests' and path.suffix.lower() in {'.csv', '.xml'})
        or (relative.parts[0] == 'static' and path.suffix.lower() in
            {'.png', '.jpg', '.jpeg', '.ico', '.woff', '.woff2'}))


class Redactor:
    def __init__(self):
        self.secrets = sorted({v for k, v in os.environ.items()
            if re.search(r'(TOKEN|PASSWORD|SECRET|API_KEY|PRIVATE_KEY)', k, re.I)
            and len(v) >= 8}, key=len, reverse=True)

    def text(self, value):
        for secret in self.secrets:
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}', r'\1[REDACTED]', value)
        return re.sub(r'''(?i)((?:api[_-]?key|access[_-]?token|tushare[_-]?token|token|password|secret)["']?\s*[=:]\s*["']?)([A-Za-z0-9_./+~-]{12,})''',
                      r'\1[REDACTED]', value)


def safe_json(path, max_bytes=8 * 1024 * 1024):
    if path.stat().st_size > max_bytes:
        raise ValueError(f'metadata too large; use reference only: {path}')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def contains_raw(value):
    if isinstance(value, dict):
        return bool(RAW_KEYS.intersection(value)) or any(contains_raw(v) for v in value.values())
    return isinstance(value, list) and any(contains_raw(v) for v in value)


def junit_counts(path):
    raw = path.read_bytes()
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('XML entities/DOCTYPE are not accepted')
    root = ET.fromstring(raw)
    if root.tag not in {'testsuites', 'testsuite'}:
        raise ValueError('not JUnit')
    cases = list(root.iter('testcase'))
    if cases:
        counts = {'tests': len(cases),
            'failures': sum(c.find('failure') is not None for c in cases),
            'errors': sum(c.find('error') is not None for c in cases),
            'skipped': sum(c.find('skipped') is not None for c in cases)}
    else:
        leaves = [s for s in root.iter('testsuite') if not list(s.findall('testsuite'))]
        counts = {k: sum(int(s.get(k, '0')) for s in leaves)
                  for k in ('tests', 'failures', 'errors', 'skipped')}
    counts['passed'] = max(0, counts['tests'] - counts['failures'] - counts['errors'] - counts['skipped'])
    return counts


def dimension(path, command):
    value = str(path).lower() + ' ' + str(command).lower()
    if 'docker' in value:
        return 'DOCKER_RUNTIME'
    if 'pytest' in value or 'test' in str(path).lower():
        return 'ENGINEERING_TEST'
    if 'replay' in value:
        return 'HISTORICAL_REPLAY'
    if any(w in value for w in ('capture', 'tushare', 'supply', 'factor', 'stock-basic')):
        return 'PROVIDER_DATA'
    if any(w in value for w in ('browser', 'http', 'api-smoke')):
        return 'HTTP_OR_BROWSER'
    return 'OTHER_EXECUTION'


def run_references_path(run, target):
    if Path(run['record_path']).parent == target.parent:
        return True
    source = run.get('SOURCE_RUN', {})
    command = source.get('command', source.get('args', []))
    if not isinstance(command, list):
        return False
    for idx, argument in enumerate(command):
        if argument.startswith('--junitxml=') or argument.startswith('--junit-xml='):
            value = argument.split('=', 1)[1]
        elif argument in ('--junitxml', '--junit-xml') and idx + 1 < len(command):
            value = command[idx + 1]
        else:
            continue
        # Command arguments follow the recorded host's separators.
        value = value.replace('\\', '/')
        path = Path(value)
        if not path.is_absolute():
            path = Path(source.get('cwd', Path(run['record_path']).parent)) / path
        if path.resolve() == target.resolve():
            return True
    return False


class Bundle:
    def __init__(self, max_file_bytes=32 * 1024 * 1024):
        self.entries, self.manifest, self.exclusions = {}, [], []
        self.redactor, self.max_file_bytes = Redactor(), max_file_bytes

    def add_bytes(self, name, value, **metadata):
        name = str(name).replace('\\', '/')
        if name.startswith('/') or '..' in Path(name).parts or name in self.entries:
            raise ValueError(f'unsafe or duplicate archive path: {name}')
        self.entries[name] = value
        self.manifest.append({'archive_path': name, 'bytes': len(value),
            'sha256': digest_bytes(value), **metadata})

    def add(self, path, name, *, kind, transform=False):
        if path.stat().st_size > self.max_file_bytes:
            raise ValueError(f'allowlisted file exceeds --max-file-mib: {path}')
        raw = path.read_bytes() if transform or path.suffix.lower() in SOURCE_EXTENSIONS else None
        binary = path.suffix.lower() in {'.gz', '.png', '.jpg', '.jpeg', '.ico', '.woff', '.woff2', '.pdf', '.docx'}
        value = self.redactor.text(raw.decode('utf-8-sig', errors='replace')).encode('utf-8') if transform else None
        if not transform and not binary:
            # Actual environment secret values are fatal in byte-exact source.
            # Generic credential-shaped test literals must stay unchanged.
            text = (raw if raw is not None else path.read_bytes()).decode('utf-8-sig', errors='replace')
            if any(secret in text for secret in self.redactor.secrets):
                raise ValueError(f'environment credential appears in source/artifact: {path}')
        if transform:
            self.add_bytes(name, value, kind=kind, source_path=str(path),
                           source_sha256=digest_bytes(raw), transformed=value != raw)
        else:
            # Large replay artifacts are streamed when writing/verifying the ZIP.
            # A mutation after this hash is detected by verify_zip, never certified.
            name = str(name).replace('\\', '/')
            if name.startswith('/') or '..' in Path(name).parts or name in self.entries:
                raise ValueError(f'unsafe or duplicate archive path: {name}')
            digest = digest_bytes(raw) if raw is not None else sha256(path)
            self.entries[name] = path
            self.manifest.append({'archive_path': name, 'bytes': path.stat().st_size,
                'sha256': digest, 'source_sha256': digest, 'source_path': str(path),
                'kind': kind, 'transformed': False})


def collect_sources(bundle, dev_root, project_root, attachment_root):
    for path in walk_files(dev_root, root_exclusions=ROOT_EXCLUSIONS):
        relative = path.relative_to(dev_root)
        if source_allowed(path, relative):
            bundle.add(path, 'source/stock-fish/' + relative.as_posix(), kind='CURRENT_SOURCE')
    for path in sorted(project_root.iterdir()):
        if path.is_file() and not path.is_symlink() and source_allowed(path, Path(path.name)):
            bundle.add(path, 'source/project/' + path.name, kind='PROJECT_DEPLOYMENT_SOURCE')
    for scope in ('reviews', 'reference', 'tasks', 'evidence/expanded/probes',
                  'evidence/expanded/previous_probes/probes'):
        for path in walk_files(attachment_root / scope):
            if source_allowed(path, path.relative_to(attachment_root)):
                bundle.add(path, 'review-context/' + path.relative_to(attachment_root).as_posix(),
                           kind='ORIGINAL_REVIEW_CONTEXT')
    legacy = []
    for relative in ('MiroFish/backend/scripts/test_profile_format.py',
                     'MiroFish/backend/app/utils/locale.py', 'MiroFish/locales/languages.json'):
        path = dev_root / relative
        legacy.append({'path': str(path), 'exists_at_packaging': path.is_file(),
                       'sha256': sha256(path) if path.is_file() else None})
        if path.is_file():
            bundle.add(path, 'legacy-collection-context/' + relative,
                       kind='ORIGINAL_LEGACY_FAILURE_CONTEXT')
    bundle.add_bytes('legacy-collection-context/current-file-facts.json', json_bytes({
        'files': legacy, 'note': 'Current existence facts; actual historical collection failures remain in SOURCE_RUN/JUnit/log evidence. The optional full MiroFish dependency tree is not included.'}),
        kind='GENERATED_INDEX')


def collect_public_context(bundle, evidence_root):
    """Narrow source/document scopes, not arbitrary review JSON or provider feeds."""
    for path in walk_files(evidence_root / 'upstream-research'):
        if source_allowed(path, path.relative_to(evidence_root / 'upstream-research')):
            bundle.add(path, 'public-sources/' + path.relative_to(evidence_root).as_posix(),
                       kind='PINNED_UPSTREAM_SOURCE_OR_LICENSE')
    for path in walk_files(evidence_root / 'sources'):
        if path.suffix.lower() in {'.md', '.txt', '.html', '.pdf', '.docx', '.png'}:
            bundle.add(path, 'public-sources/' + path.relative_to(evidence_root).as_posix(),
                       kind='OFFICIAL_DOCUMENT_CAPTURE')
    for path in walk_files(evidence_root / 'browser-evidence'):
        if path.suffix.lower() in {'.txt', '.png'}:
            bundle.add(path, 'browser-evidence/' + path.relative_to(evidence_root / 'browser-evidence').as_posix(),
                       kind='BROWSER_CAPTURE', transform=path.suffix.lower() == '.txt')


def collect_diagnostics(bundle, evidence_root):
    for path in walk_files(evidence_root / 'provenance/replay-runtime-original'):
        if path.suffix.lower() in {'.py', '.yaml', '.yml', '.lock'}:
            bundle.add(path, 'evidence/' + path.relative_to(evidence_root).as_posix(),
                       kind='ORIGINAL_EXECUTION_SOURCE_PROVENANCE')
    for path in walk_files(evidence_root / 'independent-primary-audit', prune=EVIDENCE_PRUNED):
        allowed = (re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', path.name)
                   or path.name in {'summary.json', 'report.md', 'script.py', 'console.log'})
        if allowed:
            if path.suffix == '.json' and contains_raw(safe_json(path)):
                raise ValueError(f'independent audit unexpectedly contains raw feed: {path}')
            name = 'evidence/' + path.relative_to(evidence_root).as_posix()
            if name not in bundle.entries:
                bundle.add(path, name, kind='INDEPENDENT_AUDIT_ARTIFACT', transform=path.suffix == '.log')
    for scope in ('tracking-temporal-audit', 'production-memory-probe'):
        collect_audit_directory(bundle, evidence_root / scope, 'audit-details/' + scope)


def collect_audit_directory(bundle, root, prefix):
    for path in walk_files(root, prune=EVIDENCE_PRUNED):
        if private_name(path):
            continue
        if path.suffix.lower() not in {'.py', '.md', '.json', '.xml', '.txt', '.yaml', '.yml', '.png', '.response'} and path.name not in {'stdout.log', 'stderr.log', 'console.log', '.gitattributes'}:
            continue
        if path.suffix.lower() == '.json' and contains_raw(safe_json(path)):
            raise ValueError(f'audit directory contains raw feed: {path}')
        bundle.add(path, prefix + '/' + path.relative_to(root).as_posix(),
                   kind='EXPLICIT_AUDIT_DETAIL', transform=path.suffix in {'.log', '.json', '.txt', '.xml'})


def collect_evidence(bundle, evidence_root):
    runs, junit, summaries, linked_logs, invalid_xml = [], [], [], set(), []
    files = list(walk_files(evidence_root, prune=EVIDENCE_PRUNED))
    for path in (p for p in files if p.name in EXECUTION_NAMES):
        relative = path.relative_to(evidence_root).as_posix()
        try:
            record = safe_json(path)
            if not isinstance(record, dict):
                raise ValueError('execution record must be an object')
            start = record.get('started_at', record.get('start'))
            end = record.get('ended_at', record.get('finished_at', record.get('end')))
            code, command = record.get('exit_code'), record.get('command', record.get('args'))
            observed = bool(command and start and end and isinstance(code, int) and not isinstance(code, bool))
            state = ('EXIT_ZERO' if code == 0 else 'EXIT_NONZERO') if observed else 'INCOMPLETE_EXECUTION_RECORD'
            if command and start and end and record.get('execution_state') in {'TIMED_OUT', 'ENVIRONMENT_BLOCKED'}:
                state = record['execution_state']
            run = {'record_path': str(path), 'record_sha256': sha256(path),
                'dimension': dimension(relative, command), 'observed_state': state,
                'SOURCE_RUN': {k: v for k, v in record.items() if k in RUN_FIELDS},
                'started_at': start, 'ended_at': end, 'exit_code': code, 'logs': []}
            declared = {}
            for key in ('logs', 'files', 'artifacts'):
                if isinstance(record.get(key), dict):
                    declared.update(record[key])
            logs = {path.parent / name for name in ('stdout.log', 'stderr.log')}
            if isinstance(record.get('console_log'), str):
                candidate = (path.parent / record['console_log']).resolve()
                if candidate.suffix == '.log' and candidate.is_relative_to(evidence_root.resolve()):
                    logs.add(candidate)
            for name in declared:
                candidate = path.parent / name
                if candidate.suffix == '.log' and candidate.resolve().is_relative_to(path.parent.resolve()):
                    logs.add(candidate)
            for log in sorted(logs):
                if not log.is_file() or log.is_symlink():
                    continue
                expected = declared.get(log.name)
                if isinstance(expected, dict):
                    expected = expected.get('sha256')
                actual = sha256(log)
                item = {'path': str(log), 'sha256': actual, 'declared_sha256': expected,
                    'declared_hash_matches': actual == expected if expected else None}
                if log.stat().st_size <= bundle.max_file_bytes:
                    if log not in linked_logs:
                        bundle.add(log, 'evidence/' + log.relative_to(evidence_root).as_posix(),
                                   kind='REDACTED_EXECUTION_LOG', transform=True)
                        linked_logs.add(log)
                    item['archive_path'] = 'evidence/' + log.relative_to(evidence_root).as_posix()
                else:
                    item['not_included_reason'] = 'OVERSIZE_LOG_REFERENCE_ONLY'
                run['logs'].append(item)
            run['log_integrity'] = ('MISMATCH' if any(x['declared_hash_matches'] is False for x in run['logs'])
                else 'MATCH' if run['logs'] and all(x['declared_hash_matches'] is True for x in run['logs'])
                else 'HASHED_WITHOUT_COMPLETE_DECLARATIONS')
            bundle.add_bytes('evidence/' + relative,
                bundle.redactor.text(json_bytes(run).decode('utf-8')).encode('utf-8'),
                kind='EXECUTION_METADATA_PROJECTION', source_path=str(path),
                source_sha256=sha256(path), transformed=True)
            runs.append(run)
        except (ValueError, OSError) as exc:
            runs.append({'record_path': str(path), 'observed_state': 'INVALID_RECORD',
                         'error': str(exc), 'record_sha256': sha256(path)})
    for path in files:
        relative = path.relative_to(evidence_root).as_posix()
        if path.suffix.lower() == '.xml':
            try:
                counts = junit_counts(path)
            except (ValueError, ET.ParseError) as exc:
                invalid_xml.append({'path': str(path), 'sha256': sha256(path),
                                    'classification': 'UNPARSEABLE_OR_NON_JUNIT_XML', 'error': str(exc)})
                continue
            bundle.add(path, 'evidence/' + relative, kind='JUNIT', transform=True)
            junit.append({'path': str(path), 'sha256': sha256(path), 'counts': counts,
                'linked_source_runs': [r['record_path'] for r in runs if run_references_path(r, path)],
                'scope': 'ENGINEERING_CASE_COUNTS_NOT_INVESTMENT_VALIDATION'})
        elif path.name in SUMMARY_NAMES:
            if contains_raw(safe_json(path)):
                bundle.exclusions.append({'path': str(path), 'sha256': sha256(path), 'reason': 'RAW_INPUT_IN_SUMMARY'})
            else:
                bundle.add(path, 'evidence/' + relative, kind='DERIVED_SUMMARY', transform=True)
                summaries.append({'path': str(path), 'sha256': sha256(path),
                    'classification': 'DERIVED_CLAIM_REQUIRES_LINKED_SOURCE_RUN'})
    return {'source_runs': sorted(runs, key=lambda r: (r.get('started_at') or '', r['record_path'])),
        'junit': junit, 'unparsed_xml': invalid_xml, 'derived_summaries': summaries,
        'execution_counts_by_state': dict(Counter(r['observed_state'] for r in runs)),
        'execution_counts_by_dimension': dict(Counter(r.get('dimension', 'UNKNOWN') for r in runs)),
        'aggregate_junit_case_counts': {k: sum(x['counts'][k] for x in junit)
            for k in ('tests', 'passed', 'failures', 'errors', 'skipped')},
        'junit_aggregation_note': 'Repeated executions are counted repeatedly; these are not unique test counts.'}


def collect_replay(bundle, root, archive_prefix='replay'):
    included = []
    if root is None:
        return included
    for path in walk_files(root, prune=EVIDENCE_PRUNED):
        if path.name in REPLAY_NAMES or (path.suffix.lower() == '.md' and path.parent == root):
            if path.suffix.lower() == '.json' and contains_raw(safe_json(path, bundle.max_file_bytes)):
                raise ValueError(f'replay allowlist contains raw market payload: {path}')
            relative = path.relative_to(root).as_posix()
            bundle.add(path, archive_prefix + '/' + relative, kind='DERIVED_REPLAY_ARTIFACT')
            included.append(relative)
    return included


def private_references(roots, acquisition):
    rows = []
    for root in roots:
        for path in walk_files(root, prune=PRUNED - {'checkpoints'}):
            if not private_name(path) and path.suffix != '.log':
                rows.append({'path': str(path.resolve()), 'bytes': path.stat().st_size,
                    'sha256': sha256(path), 'included': False,
                    'reason': 'PRIVATE_OR_FULL_MARKET_INPUT_REFERENCE_ONLY',
                    'acquisition_method': acquisition})
    return rows


def verify_zip(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('ZIP contains duplicate paths')
        expected = {r['archive_path']: r for r in json.loads(archive.read('manifest.json'))['files']}
        if set(names) != set(expected) | {'manifest.json'}:
            raise ValueError('ZIP entries differ from manifest')
        for name, row in expected.items():
            digest, size = hashlib.sha256(), 0
            with archive.open(name) as member:
                for chunk in iter(lambda: member.read(1024 * 1024), b''):
                    size += len(chunk)
                    digest.update(chunk)
            if size != row['bytes'] or digest.hexdigest() != row['sha256']:
                raise ValueError(f'ZIP byte/hash mismatch: {name}')
        if archive.testzip():
            raise ValueError('ZIP CRC failed')
        return {'zip_path': str(Path(path).resolve()), 'zip_sha256': sha256(path),
            'manifest_sha256': digest_bytes(archive.read('manifest.json')),
            'verified_file_count': len(expected), 'verified_at': datetime.now(timezone.utc).isoformat(),
            'verification': 'ALL_ENTRIES_BYTE_LENGTH_SHA256_AND_CRC_VERIFIED'}


def build_bundle(args):
    output = Path(args.output).resolve()
    if output.exists() or output.with_suffix('.verification.json').exists():
        raise FileExistsError('use a fresh output filename; historical bundles are never overwritten')
    dev_root, project_root = Path(args.dev_root).resolve(), Path(args.project_root).resolve()
    evidence_root, attachment_root = Path(args.evidence_root).resolve(), Path(args.attachment_root).resolve()
    if not dev_root.is_dir() or not evidence_root.is_dir():
        raise ValueError('source and evidence roots must exist')
    bundle = Bundle(args.max_file_mib * 1024 * 1024)
    collect_sources(bundle, dev_root, project_root, attachment_root)
    collect_public_context(bundle, evidence_root)
    evidence = collect_evidence(bundle, evidence_root)
    collect_diagnostics(bundle, evidence_root)
    prior_outcome = evidence_root / 'outcome-attempt-01-before-context-reuse'
    prior_replay = collect_replay(bundle, prior_outcome,
                                  'historical-replay-attempts/outcome-attempt-01-before-context-reuse')
    replay = collect_replay(bundle, Path(args.replay_root).resolve() if args.replay_root else None)
    for idx, raw_path in enumerate(args.evidence_file + args.workspace_record):
        path = Path(raw_path).resolve()
        if path.is_dir():
            collect_audit_directory(bundle, path, f'additional-evidence/{idx:03d}-{path.name}')
            continue
        if private_name(path) or path.suffix.lower() not in {'.json', '.md', '.yaml', '.yml', '.csv', '.txt', '.png', '.pdf'}:
            raise ValueError(f'explicit evidence is not an allowed report: {path}')
        if path.suffix.lower() == '.json' and contains_raw(safe_json(path)):
            raise ValueError(f'explicit evidence contains raw input: {path}')
        bundle.add(path, f'additional-evidence/{idx:03d}-{path.name}',
            kind='EXPLICIT_REPORT_OR_WORKSPACE_RECORD', transform=path.suffix.lower() not in {'.png', '.pdf'})
    roots = [Path(p).resolve() for p in args.private_artifact_root]
    if not roots and (evidence_root / 'supply').exists():
        roots = [evidence_root / 'supply']
    references = private_references(roots, args.acquisition_method)
    sources = [r for r in bundle.manifest if r['kind'] in
               {'CURRENT_SOURCE', 'PROJECT_DEPLOYMENT_SOURCE', 'ORIGINAL_REVIEW_CONTEXT'}]
    bundle.add_bytes('source_manifest.json', json_bytes({'files': sources,
        'tree_sha256': digest_bytes(json_bytes(sources)),
        'scope': 'Current bytes read during packaging; not a Git revision or prior test source identity.'}), kind='GENERATED_INDEX')
    if args.publish_root:
        root = Path(args.publish_root).resolve()
        rows = [{'path': p.relative_to(root).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha256(p)}
            for p in walk_files(root, root_exclusions=ROOT_EXCLUSIONS) if source_allowed(p, p.relative_to(root))]
        bundle.add_bytes('publish_source_manifest.json', json_bytes({'root': str(root), 'files': rows}), kind='GENERATED_INDEX')
    index = {'format': 'R05_RECTIFICATION_OBSERVED_EVIDENCE_V2',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'inputs': {'dev_root': str(dev_root), 'evidence_root': str(evidence_root),
                  'replay_root': args.replay_root, 'attachment_root': str(attachment_root)},
        'evidence': evidence, 'derived_replay_files': replay,
        'preserved_prior_outcome_attempt_files': prior_replay,
        'self_contained_for_real_market_replay': False,
        'limitations': ['Raw market inputs/private provider caches are excluded; exact references are in private-input-references.json.',
            'Exit-zero is an execution fact, not evidence of market edge or full task completion.',
            'All discovered executions, including historical failures, remain in the index.',
            'Current source bytes differ from historical SOURCE_RUN source identity when code changed.',
            'Optional MiroFish/BettaFish trees, generated/private directories and non-allowlisted binary data are excluded.'],
        'source_root_exclusions': sorted(ROOT_EXCLUSIONS),
        'source_path_exclusions': ['/'.join(p) for p in sorted(SOURCE_PATH_EXCLUSIONS)],
        'source_extensions': sorted(SOURCE_EXTENSIONS),
        'exclusions': bundle.exclusions}
    bundle.add_bytes('evidence-index.json', bundle.redactor.text(json_bytes(index).decode('utf-8')).encode('utf-8'), kind='GENERATED_INDEX')
    bundle.add_bytes('private-input-references.json', json_bytes({'files': references,
        'acquisition_method': args.acquisition_method, 'not_self_contained': True}), kind='REFERENCE_ONLY_INDEX')
    readme = ('# R05 observed evidence bundle\n\n'
        f"Generated: {index['generated_at']}\n\n"
        f"Execution states: {json.dumps(evidence['execution_counts_by_state'], ensure_ascii=False)}.\n\n"
        f"Derived replay artifacts: {len(replay)}; reference-only inputs: {len(references)}.\n\n"
        'evidence-index.json separates engineering, Docker, provider and replay evidence and retains historical failures. '
        'JUnit totals include repeated runs and do not establish investment validity. '
        'Real-market replay is not self-contained: private-input-references.json contains exact input paths/hashes and acquisition instructions. '
        'Source and original probes retain exact bytes; execution logs are credential-redacted. '
        'manifest.json covers every archive entry except itself. The adjacent verification JSON records the actual ZIP hash '
        'and byte/hash/CRC check. No Git or network operation is performed.\n')
    bundle.add_bytes('README.md', readme.encode('utf-8'), kind='GENERATED_REPORT')
    manifest = {'format': 'R05_BYTE_MANIFEST_V2', 'files': sorted(bundle.manifest, key=lambda r: r['archive_path'])}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.zip.incomplete', delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with ZipFile(temporary, 'w', compression=ZIP_DEFLATED) as archive:
            for name, value in sorted(bundle.entries.items()):
                if isinstance(value, Path):
                    archive.write(value, name)
                else:
                    archive.writestr(name, value)
            archive.writestr('manifest.json', json_bytes(manifest))
        verified = verify_zip(temporary)
        temporary.rename(output)
        verified['zip_path'] = str(output)
        output.with_suffix('.verification.json').write_bytes(json_bytes(verified))
    finally:
        temporary.unlink(missing_ok=True)
    return verified


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--dev-root', default=str(ROOT / 'stock-fish'))
    result.add_argument('--project-root', default=str(ROOT))
    result.add_argument('--evidence-root', default=str(ROOT / 'review/r05-rectification'))
    result.add_argument('--attachment-root', default=str(ROOT / 'work/r05-rectification-attachment'))
    result.add_argument('--replay-root')
    result.add_argument('--publish-root', help='Optional read-only independent published-tree byte manifest.')
    result.add_argument('--output', required=True, help='New ZIP path; existing files are never overwritten.')
    result.add_argument('--evidence-file', action='append', default=[], help='Explicit public report/image/PDF or narrow audit directory; never a raw feed.')
    result.add_argument('--workspace-record', action='append', default=[], help='Existing workspace/Git record JSON; no Git is invoked.')
    result.add_argument('--private-artifact-root', action='append', default=[], help='Hash/path only; defaults to evidence-root/supply.')
    result.add_argument('--acquisition-method', default='Use source/stock-fish/scripts/r05_capture_replay_inputs.py with recorded SOURCE_RUN commands and dates. Free provider availability and historical revisions may differ; exact replay requires local input bytes matching these SHA-256 values.')
    result.add_argument('--max-file-mib', type=int, default=256)
    return result


if __name__ == '__main__':
    print(json.dumps(build_bundle(parser().parse_args()), ensure_ascii=False, indent=2))
