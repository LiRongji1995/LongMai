"""Explicit local smoke: registers the audited release as RESEARCH_ONLY.

No provider/LLM calls, no training, no existing deployment outputs overwritten.
"""
import json
import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stockfish_core.model_registry import ModelRegistry
from stockfish_core.quant import QlibQuantEngine, QuantRequest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--all', action='store_true', help='As-of CSI300 plus one historical nonmember rejection probe')
    parser.add_argument('--compare', help='Earlier result.json; compare all overlapping scores exactly')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    registry = ModelRegistry(root / 'data/core/model_registry')
    model = registry.import_legacy(root / 'qlib-zh/DATA/analysis_outputs/2026-06-21-csi300-alpha158',
        expected_checkpoint_hash='bc5480d90dbefc1d7611c3391b3c0d165f0b9bfdc99c7a3be4a066966c9268de')
    engine = QlibQuantEngine(registry, root / 'data/core/quant', root.parent / 'data/cn_data')
    state = engine.inspect_data()
    instruments = ('600519.SH', '600000.SH', '000001.SZ')
    if args.all:
        members, historical = set(), set()
        for line in (root.parent / 'data/cn_data/instruments/csi300.txt').read_text().splitlines():
            code, start, end = line.split()
            identity = code[2:] + '.' + code[:2]
            historical.add(identity)
            if start <= '2026-09-04' <= end:
                members.add(identity)
        instruments = tuple(sorted(members | {sorted(historical - members)[0]}))
    result = engine.rank(QuantRequest(model_id=model.model_id, model_version=model.model_version,
        as_of=datetime(2026, 9, 4, 15, 1, tzinfo=ZoneInfo('Asia/Shanghai')),
        data_version=state['data_version'], universe_version=state['universe_version'],
        policy_version='native-smoke-research-only-v1', instruments=instruments),
        allow_research=True)
    if args.compare:
        before = json.loads(Path(args.compare).read_text(encoding='utf-8'))
        old = {s['instrument_id']: s['raw_score'] for s in before['scores']}
        new = {s.instrument_id: s.raw_score for s in result.scores}
        assert all(new[key] == value for key, value in old.items()), 'Subset feature scores changed'
    output = result.model_dump(mode='json')
    if args.all:
        output['scores'] = {'count': len(result.scores), 'all_scores_saved': result.artifact_path}
        output['subset_comparison'] = 'PASS' if args.compare else 'NOT_REQUESTED'
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
