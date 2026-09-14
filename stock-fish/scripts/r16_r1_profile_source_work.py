"""Small controlled source/dependency profile; supplied old source is immutable."""
from __future__ import annotations
import argparse
import cProfile
import hashlib
import importlib.util
import json
from pathlib import Path
import pstats
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stockfish_core.yaogu.strategy_experiments import _prepare_context
from stockfish_core.yaogu import source_resolution, derived_feature_store
from tests.yaogu.fixtures import make_bundle
from r16_r1_supervise import write_json, sha256, source_metadata, now


def load_old(path, suffix):
    spec = importlib.util.spec_from_file_location('stockfish_core.yaogu._r16_r1_profile_' + suffix, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encode(mapping):
    return json.dumps([[list(key), value] for key, value in sorted(mapping.items())],
        sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str).encode()


def measure(source, dependency, output, label, count):
    bundle, cutoff = make_bundle(count)
    ctx = _prepare_context(bundle, cutoff)
    profiler = cProfile.Profile()
    started = now()
    profiler.enable()
    t0 = time.perf_counter()
    view = source.canonical_price_source_view(ctx, ctx.sessions, ctx.eligible_ids)
    t1 = time.perf_counter()
    plan = dependency.dependency_plan_for_context(ctx, ctx.sessions[-26:], ctx.eligible_ids, source_view=view)
    t2 = time.perf_counter()
    profiler.disable()
    profiler.dump_stats(str(output/(label+'.cprofile')))
    data = {'tokens': encode(view['tokens']), 'selected': encode(view['selected']), 'dependencies': encode(plan)}
    hashes = {key: hashlib.sha256(value).hexdigest() for key, value in data.items()}
    calls = [{'file': file, 'line': line, 'function': function, 'calls': values[1], 'self_seconds': values[2], 'cumulative_seconds': values[3]}
        for (file, line, function), values in pstats.Stats(profiler).stats.items()
        if function in {'_candidate','_provenance_for','_source_token_for_view','_source_summary_for_range','_source_session_range','_json'}]
    report = {'label': label, 'start': started, 'end': now(), 'source_seconds': t1-t0,
        'dependency_seconds': t2-t1, 'total_seconds': t2-t0, 'cutoff': cutoff.isoformat(),
        'instruments': count, 'source_sessions': len(ctx.sessions), 'target_sessions': 26,
        'token_count': len(view['tokens']), 'plan_rows': len(plan), 'byte_hashes': hashes,
        'source_work_counters': view.get('work_counters'), 'dependency_work_counters': view.get('dependency_work_counters'),
        'source_file_sha256': sha256(source.__file__), 'dependency_file_sha256': sha256(dependency.__file__),
        'calls': calls, 'profile_policy': 'SAME_CPROFILE_INSTRUMENTATION; FIXTURE_PREPARATION_EXCLUDED'}
    write_json(output/(label+'.json'), report)
    return data, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--before-source-directory', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--instruments', type=int, default=30)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = Path(args.before_source_directory)
    old_source = load_old(before/'source_resolution.py', 'source')
    old_dependency = load_old(before/'derived_feature_store.py', 'dependency')
    left, a = measure(old_source, old_dependency, output, 'before', args.instruments)
    right, b = measure(source_resolution, derived_feature_store, output, 'after', args.instruments)
    matched = left == right
    write_json(output/'comparison.json', {'source': source_metadata(ROOT.parent), 'command_argv': sys.argv,
        'input_role': 'TEST_FIXTURE', 'exact_token_selected_dependency_bytes_matched': matched,
        'before': a, 'after': b, 'total_ratio_after_over_before': b['total_seconds']/a['total_seconds'],
        'sample_policy': 'ONE_CONTROLLED_PAIR; NO_P95_CLAIM; FULL_MARKET_BENCHMARK_SEPARATE'})
    print(json.dumps({'matched': matched, 'before_seconds': a['total_seconds'], 'after_seconds': b['total_seconds']}, indent=2))
    raise SystemExit(0 if matched else 2)


if __name__ == '__main__':
    main()
