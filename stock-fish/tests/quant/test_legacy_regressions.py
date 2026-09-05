"""Bounded tests of legacy corrections, never a declaration of legacy safety."""
import ast
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2] / 'qlib-zh'


def functions(path, names, **globals_):
    source = ast.parse((ROOT / path).read_text(encoding='utf-8-sig'))
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    nodes.extend(n for n in source.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names)
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    scope = dict(pd=pd, np=np, os=os, Path=Path, shutil=shutil, **globals_)
    exec(compile(module, str(ROOT / path), 'exec'), scope)
    return scope


def test_finetune_exports_actual_test_day_and_never_relabels_validation(tmp_path):
    scope = functions('scripts/finetune_alpha158.py', {'_export_scores'})
    artifact = tmp_path / 'artifact'
    artifact.mkdir()
    frame = pd.DataFrame({'score': [.1, .2]}, index=pd.MultiIndex.from_tuples([
        (pd.Timestamp('2025-01-02'), 'SH600000'), (pd.Timestamp('2025-01-03'), 'SH600000')],
        names=['datetime', 'instrument']))
    frame.to_pickle(artifact / 'test_pred_snapshot.pkl')
    with pytest.raises(ValueError, match='dates cannot be rewritten'):
        scope['_export_scores'](artifact, tmp_path / 'wrong', '2025-01-04')
    metrics = scope['_export_scores'](artifact, tmp_path / 'correct', '2025-01-03')
    saved = pd.read_csv(tmp_path / 'correct/scores.csv')
    assert metrics['total_stocks'] == 1
    assert saved['score'].tolist() == [.2]
    assert saved['date'].tolist() == ['2025-01-03']
    (artifact / 'test_pred_snapshot.pkl').rename(artifact / 'valid_pred_snapshot.pkl')
    with pytest.raises(RuntimeError, match='Test prediction artifact unavailable'):
        scope['_export_scores'](artifact, tmp_path / 'validation-fallback', '2025-01-03')


def test_practice_overlap_is_rejected_even_with_old_escape_flag():
    scope = functions('scripts/practice/gen_practice_yaml.py', {'validate_date_order'})
    dates = dict(train_start='2020-01-01', train_end='2023-01-01', valid_start='2023-01-02',
        valid_end='2024-01-01', test_start='2023-12-01', test_end='2024-01-02')
    with pytest.raises(ValueError, match='Invalid date order'):
        scope['validate_date_order'](dates, practice_mode=True)


def test_reverse_buckets_score_low_liability_higher():
    scope = functions('scripts/practice/stage4_risk_eval.py', {'_score_bucket'})
    thresholds = [(30, 100), (50, 80), (70, 60), (100, 40)]
    assert [scope['_score_bucket'](v, thresholds, reverse=True) for v in (20, 40, 60, 90)] == [100, 80, 60, 40]


def test_empty_screen_stays_empty_even_if_model_scores_exist(tmp_path, monkeypatch):
    scope = functions('scripts/practice/stage6_final_result.py',
        {'final_result', '_load_csv', 'import_env'}, EmptyDataError=pd.errors.EmptyDataError)
    monkeypatch.setenv('ALLOW_EXPERIMENTAL_LEGACY_QUANT', '1')
    pd.DataFrame(columns=['code', 'score']).to_csv(tmp_path / 'screen.csv', index=False)
    pd.DataFrame({'code': ['600000'], 'score': [99]}).to_csv(tmp_path / 'scores.csv', index=False)
    scope['final_result'](str(tmp_path / 'screen.csv'), str(tmp_path), str(tmp_path / 'out'))
    assert pd.read_csv(tmp_path / 'out/result_update.csv').empty
    monkeypatch.delenv('ALLOW_EXPERIMENTAL_LEGACY_QUANT')
    with pytest.raises(RuntimeError, match='EXPERIMENTAL_DISABLED'):
        scope['final_result'](str(tmp_path / 'screen.csv'), str(tmp_path), str(tmp_path / 'blocked'))


def test_legacy_backtest_does_not_present_prediction_quality_as_investment_return():
    scope = functions('scripts/finetune_alpha158.py', {'_run_backtest'})
    assert scope['_run_backtest']()['status'] == 'NOT_EVALUATED'
