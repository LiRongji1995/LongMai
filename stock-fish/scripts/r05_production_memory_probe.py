"""实际运行单日生产 R05 选择并测量整个进程的 Windows 峰值工作集。"""
from __future__ import annotations

import time

PROCESS_PROBE_STARTED = time.perf_counter()

import argparse
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import json
from pathlib import Path
import platform
import sys
import traceback
import xml.etree.ElementTree as ET

import psutil

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))
from stockfish_core.yaogu.contracts import MarketInput
from stockfish_core.yaogu.strategy_experiments import run_strategy_experiments


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def snapshot(process):
    memory = process.memory_info()
    return {'working_set_bytes': memory.rss, 'peak_working_set_bytes': getattr(memory, 'peak_wset', None),
            'private_bytes': getattr(memory, 'private', None), 'peak_pagefile_bytes': getattr(memory, 'peak_pagefile', None),
            'elapsed_since_first_probe_line_seconds': time.perf_counter() - PROCESS_PROBE_STARTED}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date-directory', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    process = psutil.Process()
    result = {'status': 'RUNNING', 'started_at': datetime.now(timezone.utc).isoformat(),
              'command_argv': sys.argv, 'working_directory': str(Path.cwd()), 'python': sys.version,
              'platform': platform.platform(), 'pid': process.pid, 'psutil_version': psutil.__version__,
              'script_sha256': sha256(__file__), 'measurement_method': 'WINDOWS_PROCESS_MEMORY_COUNTERS_EX_PEAK_WORKING_SET_VIA_PSUTIL',
              'peak_scope': 'ENTIRE_PROCESS_INCLUDING_IMPORTS_INPUT_LOADING_PRODUCTION_SELECTION_AND_FROZEN_RESULT_COMPARISON',
              'synthetic_fixture': False, 'source_modified': False, 'replaced_frozen_rank': False,
              'original_4599_snapshot_available': False, 'available_physical_memory_at_start_bytes': psutil.virtual_memory().available,
              'memory_snapshots': {'after_imports': snapshot(process)}}
    checks = []
    try:
        reference_path = args.date_directory / 'input_reference.json'
        rank_path = args.date_directory / 'rank.json'
        reference = json.loads(reference_path.read_text(encoding='utf-8'))
        result.update(input_reference_sha256=sha256(reference_path), frozen_rank_sha256=sha256(rank_path),
                      canonical_input_hash=reference['canonical_input_hash'], compressed_input_sha256=sha256(reference['path']))
        checks.append(('compressed_input_sha256', result['compressed_input_sha256'] == reference['sha256']))
        started = time.perf_counter()
        with gzip.open(reference['path'], 'rb') as stream:
            bundle = MarketInput.model_validate_json(stream.read())
        result['input_loading_seconds'] = time.perf_counter() - started
        result['memory_snapshots']['after_input_loading'] = snapshot(process)
        result.update(observation_date=str(bundle.observation_date), input_security_records=len(bundle.universe.securities),
                      input_price_records=len(bundle.bars), input_receipts=len(bundle.receipts))
        cutoff = datetime.fromisoformat(str(bundle.observation_date) + 'T20:00:00+08:00')
        print(json.dumps({'stage': 'INPUT_LOADED', 'seconds': result['input_loading_seconds'],
                          'security_records': result['input_security_records'], 'price_records': result['input_price_records'],
                          'memory': result['memory_snapshots']['after_input_loading']}), flush=True)
        started = time.perf_counter()
        computed = run_strategy_experiments(bundle, cutoff=cutoff)
        result['production_selection_seconds'] = time.perf_counter() - started
        result['memory_snapshots']['after_production_selection'] = snapshot(process)
        print(json.dumps({'stage': 'PRODUCTION_SELECTION_COMPLETE', 'seconds': result['production_selection_seconds'],
                          'memory': result['memory_snapshots']['after_production_selection']}), flush=True)
        del bundle
        gc.collect()
        frozen = json.loads(rank_path.read_text(encoding='utf-8'))
        pv, expected = computed['results']['PV_OBSERVATION_V1'], frozen['results']['PV_OBSERVATION_V1']
        focus, expected_focus = computed['selection_results']['PV_FOCUS_PREVIEW_V0.1'], frozen['selection_results']['PV_FOCUS_PREVIEW_V0.1']
        checks += [('canonical_input_hash_binding', computed['input_hash'] == reference['canonical_input_hash']),
                   ('source_identity_matches_frozen_run', computed['source_hash'] == frozen['source_hash']),
                   ('spec_identity_matches_frozen_run', computed['spec_hash'] == frozen['spec_hash']),
                   ('universe_identity_matches_frozen_run', computed['universe_hash'] == frozen['universe_hash']),
                   ('run_identity_matches_frozen_run', computed['run_id'] == frozen['run_id']),
                   ('full_pv_rank_ids', [r['instrument_id'] for r in pv['items']] == [r['instrument_id'] for r in expected['items']]),
                   ('all_pv_exact_factors_and_scores', [(r['instrument_id'], r['exact_factors'], r['S_PV'], r['rank_percentiles']) for r in pv['items']] ==
                    [(r['instrument_id'], r['exact_factors'], r['S_PV'], r['rank_percentiles']) for r in expected['items']]),
                   ('all_complete_features_exact_factors', [(r['instrument_id'], r['exact_factors'], r['exact_prices']) for r in pv['eligible_feature_items']] ==
                    [(r['instrument_id'], r['exact_factors'], r['exact_prices']) for r in expected['eligible_feature_items']]),
                   ('full_focus_ids', focus['selected_ids'] == expected_focus['selected_ids']),
                   ('unique_dated_qualification', computed['qualification'] == frozen['qualification']),
                   ('frozen_rank_unchanged', sha256(rank_path) == result['frozen_rank_sha256'])]
        for relative, expected_hash in computed['source_file_hashes'].items():
            checks.append(('source_file_unchanged:' + relative, sha256(SOURCE_ROOT / relative) == expected_hash))
        result.update(source_hash=computed['source_hash'], spec_hash=computed['spec_hash'], source_file_hashes=computed['source_file_hashes'],
                      spec_source_hashes=computed['spec_source_hashes'], pv_count=len(pv['items']), focus_count=len(focus['selected_ids']),
                      eligible_count=pv['counts']['eligible'], complete_feature_count=len(pv['eligible_feature_items']),
                      coverage_type=computed['coverage_type'], scope=pv['scope_label'])
        result['status'] = 'PASS' if all(ok for _, ok in checks) else 'FAIL'
    except Exception:
        result.update(status='ERROR', exception=traceback.format_exc())
    result['memory_snapshots']['final'] = snapshot(process)
    result['peak_working_set_bytes'] = result['memory_snapshots']['final']['peak_working_set_bytes']
    result['peak_working_set_mib'] = result['peak_working_set_bytes'] / 1024 ** 2 if result['peak_working_set_bytes'] is not None else None
    result['whole_probe_seconds'] = time.perf_counter() - PROCESS_PROBE_STARTED
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    result['checks'] = [{'name': name, 'status': 'PASS' if ok else 'FAIL'} for name, ok in checks]
    result['limitations'] = ['本次实际输入替代缺失的原4599快照，仅报告其自身耗时与峰值。',
                             '单次运行测量，不宣称缓存冷启动或多次运行性能分布。',
                             '包含加载与比较的进程峰值不等同于纯排序函数增量内存。',
                             '实测调用全部生产R05实验选择；冻结DAILY_V1独立计算及后续outcomes不在本次函数计时内。']
    (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    suite = ET.Element('testsuite', name='R05_actual_production_memory_and_identity_probe', tests=str(len(checks)),
                       failures=str(sum(not ok for _, ok in checks)), errors=str(int(result['status'] == 'ERROR')), time=str(result['whole_probe_seconds']))
    properties = ET.SubElement(suite, 'properties')
    for key in ('production_selection_seconds', 'input_loading_seconds', 'whole_probe_seconds', 'peak_working_set_bytes', 'script_sha256'):
        ET.SubElement(properties, 'property', name=key, value=str(result.get(key)))
    for name, ok in checks:
        case = ET.SubElement(suite, 'testcase', name=name)
        if not ok:
            ET.SubElement(case, 'failure', message='Actual frozen-result comparison failed')
    if result['status'] == 'ERROR':
        ET.SubElement(ET.SubElement(suite, 'testcase', name='runtime'), 'error').text = result['exception']
    ET.ElementTree(suite).write(args.output / 'junit.xml', encoding='utf-8', xml_declaration=True)
    print(json.dumps({key: result.get(key) for key in ('status', 'production_selection_seconds', 'whole_probe_seconds',
                                                     'peak_working_set_bytes', 'peak_working_set_mib', 'pv_count', 'focus_count')}), flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
