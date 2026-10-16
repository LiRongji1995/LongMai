"""Explicit TEST_FIXTURE native archive builder; no vendor/production data."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from stockfish_core.yaogu import premarket_native_core_v1 as native
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from tests.yaogu.test_premarket_native_core_v1 import captures, FixtureBudget


def _dated_captures(root, observation_date, seen_at=None, *, calendar_version='TEST_FIXTURE_CALENDAR_V1'):
    from stockfish_core.observed_data import exchange_calendar_2026
    observed = date.fromisoformat(str(observation_date))
    calendar = exchange_calendar_2026()
    sessions = [day for day in calendar.sessions if day <= observed][-67:]
    assert len(sessions) == 67 and sessions[-1] == observed, 'TEST_FIXTURE_OFFICIAL_2026_SESSION_REQUIRED'
    target = next(day for day in calendar.sessions if day > observed)
    seen = seen_at or datetime(observed.year, observed.month, observed.day, 9, tzinfo=timezone.utc)
    if isinstance(seen, str):
        seen = datetime.fromisoformat(seen)
    cutoff = seen + timedelta(minutes=1)
    items = []
    def add(endpoint, rows, parameters, version='BAOSTOCK_00.9.30_R03_V1'):
        raw = dict(status='COMPLETE', pagination_complete=True, field_version=version,
            first_seen_at=seen.isoformat(), rows=rows, coverage={}, gaps=[])
        raw_bytes = json.dumps(raw, ensure_ascii=False).encode()
        physical = dict(provider='baostock', dataset=endpoint, parameters=parameters,
            content_hash=hashlib.sha256(raw_bytes).hexdigest(), first_seen_at=seen.isoformat(),
            ingested_at=seen.isoformat(), status='COMPLETE', pagination_complete=True,
            row_count=len(rows), field_version=version, source_url='https://TEST_FIXTURE.invalid/original',
            integrity_scope='LOCAL_SHA256_NOT_SECURITY_AUTHENTICATION')
        rid = hashlib.sha256(json.dumps(physical, sort_keys=True).encode()).hexdigest()
        physical = dict(receipt_id=rid, **physical)
        result = dict(**raw, provider='baostock', endpoint=endpoint, parameters=parameters,
            request_receipt_id=rid, receipts=[physical], started_at=seen.isoformat(), completed_at=seen.isoformat())
        documents = dict(raw=(root / 'cache/raw' / (physical['content_hash'] + '.raw.json'), raw_bytes),
            receipt=(root / 'cache/raw' / (rid + '.receipt.json'), json.dumps(physical).encode()),
            result=(root / 'results' / (rid + '.json'), json.dumps(result).encode()))
        item = dict(archive_root=str(root))
        for role, (path, data) in documents.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            item[role] = workflow._ref(path)
        items.append(item)
    start = sessions[0]
    all_sessions = set(calendar.sessions)
    add('calendar', [dict(calendar_date=str(start + timedelta(days=n)),
        is_trading_day='1' if start + timedelta(days=n) in all_sessions else '0')
        for n in range((target-start).days + 1)], {'start_date': str(start), 'end_date': str(target)},
        calendar_version)
    add('mainboard_sse', [{'证券代码': '600001', '证券简称': 'TEST_FIXTURE_SH', '上市日期': '2010-01-01'}], {})
    add('master_szse', [dict(A股代码='000001', A股简称='TEST_FIXTURE_SZ', A股上市日期='2010-01-01', 板块='主板')], {})
    add('delisting_sse', [], {})
    codes = ('sh.600001', 'sz.000001')
    for day in sessions:
        close = Decimal('10') + Decimal(calendar.sessions.index(day)) / 100
        add('master_all', [dict(code=code, tradeStatus='1', code_name='TEST_FIXTURE') for code in codes], {'date': str(day)})
        add('market_daily', [dict(code=code, date=str(day), adjustflag='3', open=str(close - Decimal('.05')),
            high=str(close + Decimal('.1')), low=str(close - Decimal('.1')), close=str(close),
            preclose=str(close - Decimal('.01')), amount='500000000' if day == observed else '100000000', volume='1000',
            turn='6', tradestatus='1', isST='0') for code in codes], {'date': str(day)})
    add('adjustment_factors', [dict(code=code, dividOperateDate='2010-01-01',
        backAdjustFactor='1', foreAdjustFactor='1', adjustFactor='1') for code in codes],
        {'codes': list(codes), 'start_date': '1990-01-01', 'end_date': str(observed)})
    for item in items:
        for role in ('raw', 'receipt', 'result'):
            item[role] = workflow._ref(Path(item[role]['path']))
    return items, observed, target, cutoff


def fixture_archive(tmp_path, *, history=False, observation_date=None, seen_at=None, generated_at=None,
                    coverage_type='TEST_FIXTURE'):
    root = Path(tmp_path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    job = root / 'TEST_FIXTURE-job'
    job.mkdir()
    (job / 'TEST_FIXTURE_ONLY').write_bytes(b'TEST_FIXTURE\n')
    if observation_date is None and coverage_type == 'TEST_FIXTURE':
        items, observed, target, cutoff = captures(root / 'TEST_FIXTURE-originals', history=history)
    else:
        items, observed, target, cutoff = _dated_captures(root / 'TEST_FIXTURE-originals', observation_date or '2026-09-30', seen_at,
            calendar_version='TEST_FIXTURE_CALENDAR_V1' if coverage_type == 'TEST_FIXTURE' else 'BAOSTOCK_00.9.30_R03_V1')
    current = Path(native.__file__).resolve().parents[2]
    if coverage_type != 'TEST_FIXTURE':
        # Synthetic bytes, with genuine full original producer-file proof for
        # testing the non-fixture coverage contract. No vendor data is used.
        producers = [workflow._ref(current / 'stockfish_core/yaogu' / name) for name in
            ('provider_requests.py', 'provider_requests_worker.py', 'provider_budget.py')]
        for item in items:
            path = Path(item['result']['path'])
            document = json.loads(path.read_bytes())
            document['implementation_source_hashes'] = {Path(ref['path']).name: ref['sha256'] for ref in producers}
            path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
            item['result'] = workflow._ref(path)
            item['producer_files'] = producers
    generated = generated_at or datetime.now(timezone.utc)
    if isinstance(generated, str):
        generated = datetime.fromisoformat(generated)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return generated.astimezone(tz or timezone.utc)
    with patch.object(native, 'datetime', Clock):
        owner = native.run_core(captures=items, observation_date=observed, target_session=target,
            cutoff=cutoff, output_dir=job / 'core', budget=FixtureBudget(), coverage_type=coverage_type)
    source = root / 'TEST_FIXTURE-original-source'
    registry = []
    for ref in workflow._code_refs():
        name = Path(ref['path']).relative_to(current)
        copied = source / name
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ref['path'], copied)
        registry.append(workflow._ref(copied))
    for name in owner.run['source_file_hashes']:
        target_path = source / name
        if not target_path.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(current / name, target_path)
    validation_refs = owner.validation_references
    if coverage_type != 'TEST_FIXTURE':
        old_producers = {ref['path']: workflow._ref(source / Path(ref['path']).relative_to(current)) for ref in producers}
        validation_refs = [old_producers.get(ref['path'], ref) for ref in validation_refs]
        for item in items:
            item['producer_files'] = [old_producers[ref['path']] for ref in item['producer_files']]
    def save(name, value):
        path = job / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return workflow._ref(path)
    config = save('configuration.json', {'schema_version': workflow.VERSION, 'fixture': 'TEST_FIXTURE'})
    code = save('code-registry.json', registry)
    save('capture-summary.json', {'schema_version': 'TEST_FIXTURE_LOCATOR_ONLY', 'core_records': items})
    completion_value = dict(schema_version=workflow.VERSION, status='COMPLETED_WITH_UNKNOWNS',
        strategy_id=native.STRATEGY, core_verified=True, business_database_written=False,
        source_capability_serialized=False, completed_at=datetime.now(timezone.utc).isoformat(),
        run_id=owner.run['run_id'], observation_date=str(observed), target_session=str(target),
        validation_references=validation_refs, core_artifacts=owner.artifacts,
        phases={'core': 'COMPLETE'})
    completion = save('completion.json', completion_value)
    final = dict(schema_version=workflow.VERSION, status='COMPLETED_WITH_UNKNOWNS',
        core_verified=True, external_worker_exit_code=2, external_worker_reaped=True,
        implementation_epoch_verified=True, source_terminal_binding_verified=True,
        source_capability_restored=False, final_sealed_at=datetime.now(timezone.utc).isoformat(),
        job=str(job), configuration=config, code_registry=code, completion=completion,
        source_reference_count=len(validation_refs))
    (job / 'final-control.json').write_bytes(b' ' * 16384)
    workflow._seal(job / 'final-control.json', final)
    return dict(job=job, final_sha256=hashlib.sha256((job / 'final-control.json').read_bytes()).hexdigest(),
        run=owner.run, artifacts=owner.artifacts, items=items, source=source,
        final=final, completion=completion_value, registry=registry)


def reseal(fixture, *, completion=None, manifest=None, final=None):
    """Fixture mutation helper; explicit new local seal, never genuine authority."""
    job = fixture['job']
    if manifest is not None:
        path = Path(fixture['artifacts']['manifest.json']['path'])
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        fixture['artifacts']['manifest.json'] = workflow._ref(path)
    if completion is not None:
        fixture['completion'] = completion
    fixture['completion']['core_artifacts'] = fixture['artifacts']
    path = Path(fixture['final']['completion']['path'])
    path.write_text(json.dumps(fixture['completion'], ensure_ascii=False), encoding='utf-8')
    fixture['final']['completion'] = workflow._ref(path)
    if final is not None:
        fixture['final'].update(final)
    workflow._seal(job / 'final-control.json', fixture['final'])
    fixture['final_sha256'] = hashlib.sha256((job / 'final-control.json').read_bytes()).hexdigest()
    return fixture
