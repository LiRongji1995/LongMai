"""SVG and Markdown artifacts for Daily Recommendation Score V1."""
from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any

from .recommendation_scoring import FOCUS_SCORE, WATCH_RESEARCH_SCORE


SVG_NAMES = (
    "DAILY_RECOMMENDATION_SCORECARD.svg",
    "DAILY_SCORE_TREND.svg",
    "FOCUS_VS_WATCH.svg",
    "SETUP_SCORE_HEATMAP.svg",
    "SCORE_COVERAGE.svg",
)


def _fmt(value: Any, *, pct: bool = False) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value * 100:.2f}%" if pct else f"{value:.2f}"
    if isinstance(value, int):
        return f"{value * 100:.2f}%" if pct else str(value)
    return str(value)


def _score_band(score: float | None, status: str | None) -> str:
    if score is None:
        if status == "N_A_SIGNAL_DATA_UNAVAILABLE":
            return "Signal data unavailable"
        if status == "N_A_NO_SELECTION":
            return "NO FOCUS SIGNAL"
        if status == "NOT_MATURED":
            return "Pending"
        if status and "COVERAGE" in status:
            return "Partial / Insufficient Coverage"
        return "Not available yet"
    if score >= 80:
        return "Top 20% random-basket percentile"
    if score >= 60:
        return "Above median random basket"
    if score >= 40:
        return "Near random median"
    if score >= 20:
        return "Below median"
    return "Bottom 20%"


def _record(document: dict[str, Any] | None, tier: str, horizon: int) -> dict[str, Any] | None:
    return next((row for row in (document or {}).get("records", [])
                 if row.get("selection_tier") == tier and int(row.get("horizon", 0)) == horizon), None)


def _svg(width: int, height: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">\n'
        '<rect width="100%" height="100%" fill="#fbfcf8"/>\n'
        f"{body}\n</svg>\n"
    )


def _line(text: str, x: int, y: int, *, size: int = 14, weight: int = 400, color: str = "#24372f",
          anchor: str = "start") -> str:
    return f'<text x="{x}" y="{y}" font-family="Inter, Segoe UI, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{escape(text)}</text>'


def _daily_scorecard(document: dict[str, Any] | None) -> str:
    headline = (document or {}).get("headline") or {}
    score = headline.get("score")
    status = headline.get("score_status") or (document or {}).get("headline_status")
    label = "N/A" if score is None else str(round(float(score)))
    band = _score_band(score, status)
    circumference = 2 * 3.14159 * 70
    dash = 0 if score is None else max(0, min(100, float(score))) / 100 * circumference
    focus = _record(document, FOCUS_SCORE, 1) or headline
    body = [
        _line("DAILY_RECOMMENDATION_SCORE_V1", 32, 42, size=14, weight=700, color="#164f42"),
        _line((document or {}).get("evidence_type", "NO_SCORE_REVISION"), 32, 68, size=12, color="#64716f"),
        '<circle cx="132" cy="156" r="70" fill="none" stroke="#e7ece3" stroke-width="16"/>',
        f'<circle cx="132" cy="156" r="70" fill="none" stroke="#164f42" stroke-width="16" stroke-linecap="round" stroke-dasharray="{dash:.2f} {circumference:.2f}" transform="rotate(-90 132 156)"/>',
        _line(label, 132, 153, size=40, weight=800, anchor="middle", color="#164f42"),
        _line("" if score is None else "/100", 132, 179, size=14, anchor="middle", color="#64716f"),
        _line(band, 238, 123, size=20, weight=700),
        _line(f"Signal day: {(document or {}).get('signal_observation_date', 'N/A')}", 238, 154, size=13, color="#64716f"),
        _line(f"Status: {status or 'N/A'}", 238, 178, size=13, color="#64716f"),
        _line(f"FOCUS M: {_fmt(focus.get('M'))}  valid: {_fmt(focus.get('valid_M'))}", 238, 218, size=14),
        _line(f"D+1 Selection Return: {_fmt(focus.get('equal_weight_return'), pct=True)}", 238, 245, size=14),
        _line(f"Excess Return: {_fmt(focus.get('excess_return'), pct=True)}", 238, 272, size=14),
        _line(f"Hit Rate: {_fmt(focus.get('hit_rate'), pct=True)}", 520, 245, size=14),
        _line(f"Outperform Rate: {_fmt(focus.get('outperform_market_rate'), pct=True)}", 520, 272, size=14),
        _line("Selection Score is a percentile, not a trading win rate.", 32, 330, size=12, color="#64716f"),
        _line("MARKET_EDGE=NOT_ESTABLISHED", 32, 354, size=12, weight=700, color="#64716f"),
        _line(f"Validation Epoch: {(document or {}).get('validation_epoch_id', 'N/A')}", 32, 384, size=10, color="#64716f"),
        _line(f"Actual Signal Cutoff: {(document or {}).get('actual_decision_cutoff') or (document or {}).get('decision_cutoff') or 'N/A'}", 32, 407, size=12, color="#64716f"),
        _line(f"Data Quality Status: {(document or {}).get('data_quality_status', 'N/A')}", 32, 430, size=12, color="#64716f"),
        _line(f"Implementation: {(document or {}).get('signal_implementation_version', 'N/A')} / {(document or {}).get('signal_spec_revision', 'N/A')}", 32, 453, size=10, color="#64716f"),
    ]
    return _svg(760, 480, "\n".join(body))


def _rolling_series(daily: list[dict[str, Any]], length: int) -> list[float | None]:
    result: list[float | None] = []
    values: list[float] = []
    for row in daily:
        score = row.get("focus_score")
        if score is not None:
            values.append(float(score))
        recent = values[-length:]
        result.append(sum(recent) / len(recent) if recent else None)
    return result


def _points(values: list[float | None], *, width: int, height: int, x0: int, y0: int, y_scale: int) -> str:
    valid = [(index, value) for index, value in enumerate(values) if value is not None]
    if not valid:
        return ""
    step = width / max(1, len(values) - 1)
    pieces = []
    last = None
    for index, value in valid:
        x = x0 + index * step
        y = y0 + y_scale - (max(0, min(100, value)) / 100 * y_scale)
        pieces.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="#164f42"/>')
        if last is not None:
            pieces.append(f'<line x1="{last[0]:.1f}" y1="{last[1]:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="#164f42" stroke-width="2"/>')
        last = (x, y)
    return "\n".join(pieces)


def _daily_score_trend(history: dict[str, Any], title: str) -> str:
    daily = history.get("daily") or []
    scores = [row.get("focus_score") for row in daily]
    rolling20 = _rolling_series(daily, 20)
    rolling60 = _rolling_series(daily, 60)
    body = [
        _line(title, 32, 42, size=18, weight=700, color="#164f42"),
        _line("D+1 Score, 20D rolling, 60D rolling; missing days are not filled as zero.", 32, 68, size=12, color="#64716f"),
        '<line x1="56" y1="188" x2="720" y2="188" stroke="#dca54c" stroke-width="1.5" stroke-dasharray="5 5"/>',
        _line("50 percentile reference", 570, 182, size=11, color="#8b5c22"),
        '<rect x="56" y="88" width="664" height="200" fill="none" stroke="#dde1d9"/>',
        _points(scores, width=664, height=200, x0=56, y0=88, y_scale=200),
    ]
    for series, color, label, y in ((rolling20, "#376f8b", "20D rolling", 315), (rolling60, "#8b5c22", "60D rolling", 338)):
        valid = [(i, v) for i, v in enumerate(series) if v is not None]
        if valid:
            step = 664 / max(1, len(series) - 1)
            path = " ".join(("M" if n == 0 else "L") + f" {56 + i * step:.1f} {88 + 200 - max(0, min(100, v)) / 100 * 200:.1f}" for n, (i, v) in enumerate(valid))
            body.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>')
        body.append(_line(label, 56, y, size=12, color=color))
    if not daily:
        body.append(_line("Official prospective score: Not available yet", 230, 192, size=18, weight=700, color="#64716f"))
    return _svg(760, 370, "\n".join(body))


def _focus_vs_watch(history: dict[str, Any], title: str) -> str:
    daily = history.get("daily") or []
    focus = [row.get("focus_score") for row in daily]
    watch = [row.get("watch_score") for row in daily]
    body = [
        _line(title, 32, 42, size=18, weight=700, color="#164f42"),
        _line("FOCUS Score and WATCH Research Score are separate cohorts.", 32, 68, size=12, color="#64716f"),
        '<rect x="56" y="88" width="664" height="200" fill="none" stroke="#dde1d9"/>',
        _points(focus, width=664, height=200, x0=56, y0=88, y_scale=200).replace("#164f42", "#164f42"),
        _points(watch, width=664, height=200, x0=56, y0=88, y_scale=200).replace("#164f42", "#8b5c22"),
        _line("FOCUS Score", 56, 320, size=12, color="#164f42"),
        _line("WATCH Research Score", 180, 320, size=12, color="#8b5c22"),
    ]
    if not daily:
        body.append(_line("Pending: no complete saved score history", 240, 192, size=18, weight=700, color="#64716f"))
    return _svg(760, 350, "\n".join(body))


def _setup_heatmap(document: dict[str, Any] | None) -> str:
    setups = ("START", "ACCELERATION", "HIGH_LEVEL_REPAIR", "TREND_PULLBACK")
    horizons = (1, 3, 5)
    body = [
        _line("SETUP_SCORE_HEATMAP", 32, 42, size=18, weight=700, color="#164f42"),
        _line("FOCUS setup scores stay separated by setup and horizon.", 32, 68, size=12, color="#64716f"),
    ]
    x0, y0, w, h = 210, 96, 150, 54
    for j, horizon in enumerate(horizons):
        body.append(_line(f"D+{horizon}", x0 + j * w + 48, y0 - 18, size=13, weight=700))
    for i, setup in enumerate(setups):
        body.append(_line(setup, 32, y0 + i * h + 34, size=13, weight=700))
        for j, horizon in enumerate(horizons):
            record = _record(document, f"FOCUS:{setup}", horizon)
            score = record.get("score") if record else None
            status = record.get("score_status") if record else "Pending"
            color = "#e7ece3" if score is None else "#164f42" if score >= 60 else "#dca54c" if score >= 40 else "#fff0df"
            missing_labels = {"N_A_SIGNAL_DATA_UNAVAILABLE": "Data unavailable", "N_A_NO_SELECTION": "N/A",
                              "NOT_MATURED": "Pending", "INSUFFICIENT_BENCHMARK_COVERAGE": "Low coverage"}
            text = missing_labels.get(status, status) if score is None else str(round(float(score)))
            body.append(f'<rect x="{x0 + j * w}" y="{y0 + i * h}" width="{w - 8}" height="{h - 8}" fill="{color}" stroke="#dde1d9"/>')
            body.append(_line(text, x0 + j * w + 70, y0 + i * h + 31, size=12, weight=700, anchor="middle", color="#24372f"))
    body.append(_line("WEAKENING_HIGH_ACTIVITY_OBSERVATION is not a formal setup score.", 32, 340, size=12, color="#64716f"))
    return _svg(760, 370, "\n".join(body))


def _coverage_chart(history: dict[str, Any], title: str) -> str:
    daily = history.get("daily") or []
    max_m = max([row.get("focus_M") or 0 for row in daily] or [1])
    body = [
        _line(title, 32, 42, size=18, weight=700, color="#164f42"),
        _line("Daily M, Focus signal coverage, and benchmark coverage travel with the score.", 32, 68, size=12, color="#64716f"),
        '<rect x="56" y="96" width="664" height="180" fill="none" stroke="#dde1d9"/>',
    ]
    if daily:
        step = 664 / max(1, len(daily))
        for i, row in enumerate(daily):
            m = row.get("focus_M") or 0
            bar_h = 0 if max_m == 0 else m / max_m * 150
            x = 58 + i * step
            body.append(f'<rect x="{x:.1f}" y="{276 - bar_h:.1f}" width="{max(3, step - 3):.1f}" height="{bar_h:.1f}" fill="#164f42"/>')
            coverage = row.get("benchmark_coverage")
            if coverage is not None:
                y = 276 - max(0, min(1, coverage)) * 150
                body.append(f'<circle cx="{x + max(3, step - 3) / 2:.1f}" cy="{y:.1f}" r="3" fill="#8b5c22"/>')
    else:
        body.append(_line("No official score coverage yet", 270, 186, size=18, weight=700, color="#64716f"))
    body.append(_line(f"official signal days: {history.get('official_signal_days', 0)}", 56, 315, size=12))
    body.append(_line(f"focus signal coverage: {_fmt(history.get('focus_signal_coverage'), pct=True)}", 250, 315, size=12))
    body.append(_line(f"mean M: {_fmt(history.get('mean_M'))}  median M: {_fmt(history.get('median_M'))}", 488, 315, size=12))
    return _svg(760, 350, "\n".join(body))


def write_score_artifacts(output_dir: str | Path, *, latest_document: dict[str, Any] | None,
                          official_history: dict[str, Any], reconstructed_history: dict[str, Any] | None = None) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    reconstructed_history = reconstructed_history or {}
    display_document = latest_document or {"validation_epoch_id": official_history.get("validation_epoch_id") or "N/A"}
    files = {
        "DAILY_RECOMMENDATION_SCORECARD.svg": _daily_scorecard(display_document),
        "DAILY_SCORE_TREND.svg": _daily_score_trend(official_history, "DAILY_SCORE_TREND · OFFICIAL_PROSPECTIVE only"),
        "FOCUS_VS_WATCH.svg": _focus_vs_watch(official_history, "FOCUS_VS_WATCH · OFFICIAL_PROSPECTIVE only"),
        "SETUP_SCORE_HEATMAP.svg": _setup_heatmap(latest_document),
        "SCORE_COVERAGE.svg": _coverage_chart(official_history, "SCORE_COVERAGE · OFFICIAL_PROSPECTIVE only"),
    }
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    summary = {
        "score_spec_id": "DAILY_RECOMMENDATION_SCORE_V1",
        "latest": latest_document,
        "official_prospective_score": official_history,
        "reconstructed_historical_score": reconstructed_history,
        "svg_files": list(SVG_NAMES),
        "market_edge": "NOT_ESTABLISHED",
        "score_role": "SELECTION_QUALITY_NOT_EXECUTION_RETURN",
    }
    (root / "score-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    headline = (latest_document or {}).get("headline") or {}
    report = [
        "# Recommendation Score Report",
        "",
        f"- score_spec_id: `DAILY_RECOMMENDATION_SCORE_V1`",
        f"- latest_evidence_type: `{(latest_document or {}).get('evidence_type', 'NO_SCORE_REVISION')}`",
        f"- validation_epoch_id: `{(latest_document or {}).get('validation_epoch_id', official_history.get('validation_epoch_id', 'N/A'))}`",
        f"- signal_implementation_version: `{(latest_document or {}).get('signal_implementation_version', 'N/A')}`",
        f"- signal_spec_revision: `{(latest_document or {}).get('signal_spec_revision', 'N/A')}`",
        f"- actual_signal_cutoff: `{(latest_document or {}).get('actual_decision_cutoff') or (latest_document or {}).get('decision_cutoff') or 'N/A'}`",
        f"- data_quality_status: `{(latest_document or {}).get('data_quality_status', 'N/A')}`",
        f"- primary_capture_attempted_days: `{official_history.get('primary_capture_attempted_days', 0)}`",
        f"- evaluable_official_signal_days: `{official_history.get('evaluable_official_signal_days', 0)}`",
        f"- data_unavailable_primary_days: `{official_history.get('data_unavailable_primary_days', 0)}`",
        f"- valid_no_focus_days: `{official_history.get('valid_no_focus_days', 0)}`",
        f"- headline_status: `{headline.get('score_status', (latest_document or {}).get('headline_status', 'N/A'))}`",
        f"- headline_score: `{headline.get('score')}`",
        f"- official_signal_days: `{official_history.get('official_signal_days', 0)}`",
        f"- focus_signal_coverage: `{official_history.get('focus_signal_coverage')}`",
        f"- MARKET_EDGE: `NOT_ESTABLISHED`",
        "",
        "![Daily scorecard](DAILY_RECOMMENDATION_SCORECARD.svg)",
        "",
        "![Daily score trend](DAILY_SCORE_TREND.svg)",
        "",
        "![Focus vs Watch](FOCUS_VS_WATCH.svg)",
        "",
        "![Setup score heatmap](SETUP_SCORE_HEATMAP.svg)",
        "",
        "![Score coverage](SCORE_COVERAGE.svg)",
        "",
        "Selection Score uses close-to-close comparable returns. It is not a paper execution score, not a win-rate promise, and not an automatic tuning target.",
    ]
    (root / "score-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return {name: str((root / name).resolve()) for name in (*SVG_NAMES, "score-summary.json", "score-report.md")}
