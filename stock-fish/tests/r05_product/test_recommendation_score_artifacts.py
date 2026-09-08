from __future__ import annotations

from datetime import datetime
import json
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from stockfish_core.yaogu.recommendation_score_artifacts import SVG_NAMES, write_score_artifacts
from stockfish_core.yaogu.recommendation_scoring import (
    OFFICIAL_PROSPECTIVE,
    RECONSTRUCTED_HISTORICAL,
    build_score_revision,
    summarize_score_history,
)
from stockfish_core.yaogu.stage_aware_momentum import STAGE_AWARE_ID
from tests.r05_product.test_recommendation_scoring_v1 import _label, _row, _run

SH = ZoneInfo("Asia/Shanghai")


def _doc(rows, labels, **kwargs):
    return build_score_revision(_run(rows, **kwargs), labels, datetime(2026, 9, 15, 20, 30, tzinfo=SH))


def _write(tmp_path, latest, docs):
    return write_score_artifacts(
        tmp_path,
        latest_document=latest,
        official_history=summarize_score_history(docs, evidence_type=OFFICIAL_PROSPECTIVE),
        reconstructed_history=summarize_score_history(docs, evidence_type=RECONSTRUCTED_HISTORICAL),
    )


def test_score_svg_files_are_valid_and_match_saved_json(tmp_path):
    doc = _doc([_row("A", "WATCH", rank=1), _row("B", "FOCUS", rank=2), _row("C", "WATCH", rank=3)],
               [_label("A", 1, 0.00), _label("B", 1, 0.05), _label("C", 1, 0.10)])
    _write(tmp_path, doc, [doc])

    for name in SVG_NAMES:
        ET.parse(tmp_path / name)
    summary = json.loads((tmp_path / "score-summary.json").read_text(encoding="utf-8"))
    scorecard = (tmp_path / "DAILY_RECOMMENDATION_SCORECARD.svg").read_text(encoding="utf-8")

    assert summary["latest"]["headline_score"] == doc["headline_score"]
    assert str(round(doc["headline_score"])) in scorecard
    assert "/100" in scorecard
    assert "MARKET_EDGE=NOT_ESTABLISHED" in scorecard


def test_na_and_pending_scorecards_do_not_show_zero_as_score(tmp_path):
    empty = _doc([_row("A", "WATCH")], [_label("A", 1, 0.10)])
    _write(tmp_path / "empty", empty, [empty])
    empty_scorecard = (tmp_path / "empty" / "DAILY_RECOMMENDATION_SCORECARD.svg").read_text(encoding="utf-8")

    pending = _doc([_row("A", "FOCUS")], [_label("A", 1, None, status="NOT_MATURED")])
    _write(tmp_path / "pending", pending, [pending])
    pending_scorecard = (tmp_path / "pending" / "DAILY_RECOMMENDATION_SCORECARD.svg").read_text(encoding="utf-8")

    assert "N/A" in empty_scorecard
    assert "NO FOCUS SIGNAL" in empty_scorecard
    assert "/100" not in empty_scorecard
    assert "Pending" in pending_scorecard
    assert "/100" not in pending_scorecard


def test_focus_watch_and_reconstructed_prospective_labels_are_not_swapped(tmp_path):
    official = _doc([_row("A", "FOCUS", rank=1), _row("B", "WATCH", rank=2)],
                    [_label("A", 1, 0.02), _label("B", 1, 0.03)])
    reconstructed = _doc([_row("A", "FOCUS")], [_label("A", 1, 0.10)],
                         coverage_type="RECONSTRUCTED_HISTORICAL", official=False, validation_role="MANUAL_OR_REPLAY")
    _write(tmp_path, reconstructed, [official, reconstructed])

    focus_watch = (tmp_path / "FOCUS_VS_WATCH.svg").read_text(encoding="utf-8")
    scorecard = (tmp_path / "DAILY_RECOMMENDATION_SCORECARD.svg").read_text(encoding="utf-8")
    trend = (tmp_path / "DAILY_SCORE_TREND.svg").read_text(encoding="utf-8")

    assert "FOCUS Score" in focus_watch
    assert "WATCH Research Score" in focus_watch
    assert "RECONSTRUCTED_HISTORICAL" in scorecard
    assert "OFFICIAL_PROSPECTIVE only" in trend


def test_trend_and_coverage_name_missingness_and_rolling_policy(tmp_path):
    history = {
        "official_signal_days": 2,
        "focus_nonempty_days": 1,
        "focus_signal_coverage": 0.5,
        "mean_M": 1,
        "median_M": 1,
        "rolling": {"20D": {"mean_score": 50, "valid_score_days": 1}, "60D": {"mean_score": 50, "valid_score_days": 1}},
        "daily": [
            {"signal_observation_date": "2026-09-08", "focus_score": None, "focus_status": "N_A_NO_SELECTION", "focus_M": 0},
            {"signal_observation_date": "2026-09-09", "focus_score": 50.0, "focus_status": "COMPLETE", "focus_M": 1, "benchmark_coverage": 1.0},
        ],
    }
    write_score_artifacts(tmp_path, latest_document=None, official_history=history)

    trend = (tmp_path / "DAILY_SCORE_TREND.svg").read_text(encoding="utf-8")
    coverage = (tmp_path / "SCORE_COVERAGE.svg").read_text(encoding="utf-8")

    assert "missing days are not filled as zero" in trend
    assert "20D rolling" in trend
    assert "60D rolling" in trend
    assert "official signal days: 2" in coverage
    assert "focus signal coverage: 50.00%" in coverage
