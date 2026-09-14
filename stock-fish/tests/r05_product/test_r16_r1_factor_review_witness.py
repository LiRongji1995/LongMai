"""TEST_FIXTURE: real normalizer/history/consumers, wholly isolated temp DB."""
from datetime import timedelta
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'stock-fish/stockfish_core').is_dir())
sys.path.insert(0, str(ROOT / 'stock-fish'))
sys.path.insert(0, str(ROOT / 'stock-fish/scripts'))
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
from tests.r03_sources.test_provider_boundaries import NOW, DAY, result, raw, base, master
from r16_r1_evidence_assets import register_isolated_input
from r16_r1_build_corrected_history import preserve_t_envelope_with_normalized_history

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / 'r16_r1_factor_review_witness.py'
if not SCRIPT.is_file(): SCRIPT = ROOT / 'stock-fish/scripts/r16_r1_factor_review_witness.py'
spec = importlib.util.spec_from_file_location('h4_witness', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_extracted_source_does_not_inherit_parent_repository_identity(tmp_path, monkeypatch):
    project = tmp_path / 'extracted' / 'stock-fish'
    project.mkdir(parents=True)
    calls = []
    def parent_git(command, **kwargs):
        calls.append(command)
        return str(tmp_path)  # An unrelated ancestor Git repository.
    monkeypatch.setattr(module.subprocess, 'check_output', parent_git)
    identity = module.git_source_identity(project)
    assert identity == {'commit': None, 'tree': None, 'tracked_status': None,
                        'git_root_matches': False, 'status': 'UNKNOWN_NO_MATCHING_GIT_ROOT'}
    assert calls == [['git', 'rev-parse', '--show-toplevel']]


def test_extracted_source_without_git_retains_explicit_unknown(tmp_path, monkeypatch):
    def no_git(command, **kwargs):
        raise module.subprocess.CalledProcessError(128, command)
    monkeypatch.setattr(module.subprocess, 'check_output', no_git)
    identity = module.git_source_identity(tmp_path / 'stock-fish')
    assert identity['commit'] is None and identity['tree'] is None
    assert identity['tracked_status'] is None and identity['git_root_matches'] is False


def test_actual_witness_entry_with_frozen_test_fixture(tmp_path):
    store = SQLiteStore(tmp_path / 'TEST_FIXTURE.sqlite3')
    owner = HistoryArtifactStore(store)
    sessions = tuple(sorted(DAY - timedelta(days=offset) for offset in range(100) if (DAY - timedelta(days=offset)).weekday() < 5))
    old = result('adjustment_factors', [{'code':'sh.600000','dividOperateDate':'2000-01-01','backAdjustFactor':'2'}],
                 {'codes':['sh.600000'],'start_date':'1990-01-01','end_date':str(DAY)})
    sources = base([raw(session=session, code=code) for session in sessions for code in ('sh.600000', 'sh.600001')])
    sources[0] = result('mainboard_sse', [master(), master('600001')])
    original, _ = normalize_capture_with_report(sources + [old], DAY, NOW, sessions,
                                               history_store=owner, coverage_type='TEST_FIXTURE')
    new = result('adjustment_factors', [*old['rows'], {'code':'sh.600000','dividOperateDate':str(DAY),'backAdjustFactor':'2.5'}],
                 old['parameters'], stamp=NOW + timedelta(seconds=10))
    common = NOW + timedelta(days=3)
    normalized, report_t = normalize_capture_with_report(sources[:2] + [old, new], DAY, common, sessions,
         history_store=owner, history_base_ref=original.history_artifact_ref, coverage_type='TEST_FIXTURE',
         review_factor_conflicts=True, factor_review_base=original)
    corrected = preserve_t_envelope_with_normalized_history(original, normalized, report_t['factor_revision_review'])
    next_day = DAY + timedelta(days=3)
    fresh = result('market_daily', [raw(session=next_day)], {'date':str(next_day)}, stamp=common)
    next_bundle, report_t1 = normalize_capture_with_report(sources[:2] + [old, new, fresh], next_day, common,
         sessions + (next_day,), history_store=owner, history_base_ref=corrected.history_artifact_ref,
         coverage_type='TEST_FIXTURE', review_factor_conflicts=True, factor_review_base=corrected)
    build = {'target_db': str(store.path), 'coverage_type': 'TEST_FIXTURE', 'common_information_cutoff': common.isoformat(),
             'input_t_original': register_isolated_input(store, original),
             'input_t_corrected': register_isolated_input(store, corrected),
             'input_t_plus_1_corrected': register_isolated_input(store, next_bundle)}
    build_path = tmp_path / 'build-result.json'
    module.write_json(build_path, build)
    module.write_json(tmp_path / 'normalization-t-report.json', report_t)
    module.write_json(tmp_path / 'normalization-report.json', report_t1)
    with store.transaction(write=False) as con:
        before = tuple(con.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                       for table in ('documents', 'history_evidence_rows', 'history_partition_members'))
    witness = module.Witness(build_path, tmp_path / 'witness', budget_seconds=60, max_rows=1000)
    report = witness.run()
    assert report['status'] == 'PASS', [row for row in report['checks'] if row['status'] != 'PASS']
    assert report['data_kind'] == 'TEST_FIXTURE'
    assert report['new_review_rows_t'] > 60 and report['new_review_rows_t1'] == 0
    assert report['rows_loaded_unique'] < 1000
    with store.transaction(write=False) as con:
        after = tuple(con.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                       for table in ('documents', 'history_evidence_rows', 'history_partition_members'))
    assert before == after
    # A self-consistent report hash is not source authority. Change only the
    # alleged old revision clock and update its hash; the actual DB candidate
    # binding must still reject it without any source database mutation.
    from stockfish_core.yaogu.contracts import DailyBar, digest
    from stockfish_core.yaogu.visibility import stamp
    bad_report = deepcopy(report_t)
    record = bad_report['factor_revision_review']['reviews'][0]
    record['old_row']['source_published_at'] = (stamp(record['old_row']['source_published_at']) + timedelta(seconds=1)).isoformat()
    record['old_row_hash'] = digest(DailyBar.model_validate(record['old_row']))
    bad_dir = tmp_path / 'forged-report'
    bad_dir.mkdir()
    module.write_json(bad_dir / 'build-result.json', build)
    module.write_json(bad_dir / 'normalization-t-report.json', bad_report)
    module.write_json(bad_dir / 'normalization-report.json', report_t1)
    bad = module.Witness(bad_dir / 'build-result.json', tmp_path / 'forged-witness').run()
    assert bad['status'] == 'FAIL'
    assert any(row['check_id'] == 'REVIEW_ROW_SELECTED_IN_BOTH_INPUTS' and row['status'] == 'FAIL' for row in bad['checks'])
