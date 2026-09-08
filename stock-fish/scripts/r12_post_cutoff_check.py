"""Read-only verification of the real scheduled primary, including failures."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import os
import hashlib
import sqlite3
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[2]
SH = ZoneInfo('Asia/Shanghai')


def probe(base, path):
    headers = {}
    key = os.environ.get('STOCKFISH_API_KEY')
    if key:
        headers['Authorization'] = 'Bearer ' + key
    with urlopen(Request(base.rstrip('/') + path, headers=headers), timeout=30) as response:
        return json.load(response)


def _session(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(SH).date().isoformat() if parsed.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def _local_input_evidence(base, db_path, input_id):
    """Hash existing SQLite bytes in small blocks; never load a full MarketInput."""
    if urlparse(base).hostname not in {'127.0.0.1', 'localhost', '::1'} or not db_path or not input_id:
        return {'status': 'NOT_VERIFIED', 'reason': 'LOCAL_DATABASE_OR_INPUT_ID_UNAVAILABLE'}
    path = Path(db_path).resolve()
    if not path.is_file():
        return {'status': 'NOT_VERIFIED', 'reason': 'LOCAL_DATABASE_UNAVAILABLE'}
    try:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5) as connection:
            row = connection.execute("SELECT rowid,content_hash,length(CAST(payload AS BLOB)) FROM documents WHERE namespace='candidate_inputs' AND document_id=?", (input_id,)).fetchone()
            if row is None:
                return {'status': 'NOT_VERIFIED', 'reason': 'SAVED_MARKET_INPUT_MISSING', 'input_id': input_id}
            hasher = hashlib.sha256()
            with connection.blobopen('documents', 'payload', row[0], readonly=True) as blob:
                while chunk := blob.read(65536):
                    hasher.update(chunk)
            actual = hasher.hexdigest()
            return {'status': 'VERIFIED' if actual == row[1] and input_id == 'market_' + actual else 'HASH_MISMATCH',
                    'input_id': input_id, 'payload_bytes': row[2], 'stored_content_hash': row[1],
                    'actual_sha256': actual, 'verification_policy': 'READ_ONLY_SQLITE_BLOB_64K_SHA256'}
    except (sqlite3.Error, OSError) as exc:
        return {'status': 'NOT_VERIFIED', 'reason': type(exc).__name__, 'input_id': input_id}


def collect_report(base, now, *, fetch=probe):
    errors = []

    def read(path):
        try:
            return fetch(base, path)
        except Exception as exc:
            # Record a failed probe without leaking request headers or tokens.
            errors.append({'path': path, 'error_type': type(exc).__name__, 'http_status': getattr(exc, 'code', None)})
            return {}

    schedule = read('/api/candidate-schedule')
    health = read('/api/health')
    score = read('/api/recommendation-scores/latest')
    state = schedule.get('prospective_validation') or {}
    primary_plan = state.get('primary_schedule') or {}
    validation = state.get('validation_state') or {}
    epoch = validation.get('validation_epoch_id') or state.get('validation_epoch_id')
    today = now.astimezone(SH).date().isoformat()
    latest_primary = state.get('latest_primary_capture')
    primary = next((row for row in (latest_primary, state.get('prospective_primary_run'))
                    if epoch and row and row.get('signal_observation_date') == today and row.get('validation_epoch_id') == epoch), None)
    candidates = list(schedule.get('events') or [])
    if state.get('scheduler_event_state'):
        candidates.append(state['scheduler_event_state'])
    events = [row for row in candidates if row.get('market_session') == 'EVENING'
              and _session(row.get('scheduled_at')) == today
              and (row.get('validation_epoch_id') == epoch
                   or not row.get('validation_epoch_id') and row.get('event_id') == primary_plan.get('job_id'))]
    today_event = max(events, key=lambda row: (row.get('observed_at') or '', row.get('event_id') or ''), default=None)
    job_id = (today_event or {}).get('job_id')
    if not job_id and primary_plan.get('signal_session') == today and state.get('scheduler_job_state'):
        job_id = state['scheduler_job_state'].get('job_id') or primary_plan.get('job_id')
    job = read('/api/jobs/' + quote(job_id, safe='')) if job_id else None
    run = read('/api/strategy-experiments/' + quote(primary['run_id'], safe='')
               + '/summary?strategy_id=STAGE_AWARE_MOMENTUM_V2&page_size=1') if primary else None
    expected_run = ((job or {}).get('result') or {}).get('stage_aware_primary_run_id')
    identity_checks = {
        'run_id_matches_primary': (run or {}).get('run_id') == primary.get('run_id') if primary and run else None,
        'epoch_matches_primary': (run or {}).get('validation_epoch_id') == epoch if primary and run else None,
        'input_id_matches_primary': (run or {}).get('input_id') == primary.get('input_id') if primary and run else None,
        'job_matches_primary': expected_run == primary.get('run_id') if primary and expected_run else None,
    }
    service_identity = state.get('service_identity') or {}
    input_evidence = (_local_input_evidence(base, service_identity.get('db_path'), primary.get('input_id'))
                      if primary else {'status': 'NOT_VERIFIED', 'reason': 'TODAY_PRIMARY_NOT_PERSISTED'})
    cutoff_passed = now.astimezone(SH).time().replace(tzinfo=None) >= datetime.strptime('20:30', '%H:%M').time()
    check_status = 'BEFORE_CUTOFF_NO_PRIMARY_CLAIM'
    if cutoff_passed:
        if errors:
            check_status = 'POST_CUTOFF_PROBE_INCOMPLETE'
        elif False in identity_checks.values() or input_evidence.get('status') == 'HASH_MISMATCH':
            check_status = 'POST_CUTOFF_IDENTITY_MISMATCH'
        elif (job or {}).get('state') in {'FAILED', 'CANCELLED', 'INTERRUPTED'}:
            check_status = 'POST_CUTOFF_CAPTURE_FAILED'
        elif primary and (None in identity_checks.values() or input_evidence.get('status') != 'VERIFIED'):
            check_status = 'POST_CUTOFF_PROBE_INCOMPLETE'
        elif not today_event and not primary:
            check_status = 'POST_CUTOFF_NO_TODAY_EVENT_OR_PRIMARY'
        else:
            check_status = 'POST_CUTOFF_ACTUAL_EVIDENCE'
    return {'observed_at': now.isoformat(), 'base_url': base,
        'cutoff_passed_today': cutoff_passed, 'check_status': check_status,
        'evening_event': today_event, 'evening_job': job,
        'service_identity': service_identity,
        'scheduler_thread_alive': state.get('scheduler_thread_alive'),
        'next_real_primary_capture': state.get('next_real_primary_capture'),
        'primary_schedule': primary_plan, 'validation': validation,
        'latest_actual_primary': latest_primary, 'today_actual_primary': primary,
        'today_strategy_run_summary': run, 'primary_identity_checks': identity_checks,
        'market_input_verification': input_evidence, 'probe_errors': errors,
        'latest_score': score,
        'health': {key: health.get(key) for key in ('status','production_ready','candidate_research')},
        'market_edge': 'NOT_ESTABLISHED'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    config_file = ROOT / 'work' / 'stockfish-service.json'
    config = json.loads(config_file.read_text(encoding='utf-8-sig')) if config_file.exists() else {}
    base = args.base_url or 'http://127.0.0.1:' + str(config.get('Port', 8000))
    now = datetime.now(SH)
    report = collect_report(base, now)
    output = args.output or ROOT / 'reports' / 'codex' / now.strftime('%Y-%m-%d') / ('r12-post-cutoff-' + now.strftime('%H%M%S') + '.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output.resolve()), 'check_status': report['check_status'],
        'scheduler_thread_alive': report['scheduler_thread_alive'],
        'validation_epoch_id': (report['validation'] or {}).get('validation_epoch_id'),
        'latest_primary_run_id': (report['latest_actual_primary'] or {}).get('run_id'),
        'today_primary_run_id': (report['today_actual_primary'] or {}).get('run_id')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
