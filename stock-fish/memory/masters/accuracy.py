"""
准确率计算工具函数

提供验证脚本和报表使用。
"""
from datetime import datetime, timedelta
import math
from typing import Dict, List, Optional

from loguru import logger

from memory.masters.master_track import MasterTrackDB
from memory.stocks.stock_library import StockLibrary


def calculate_accuracy(master_key: str) -> Dict:
    """计算某位大师的准确率"""
    db = MasterTrackDB()
    return db.get_accuracy(master_key)


def accuracy_by_master() -> Dict[str, Dict]:
    """计算所有大师的准确率"""
    MASTERS = ["graham", "buffett", "fisher", "lynch", "templeton", "soros", "dalio"]
    result = {}
    for master_key in MASTERS:
        result[master_key] = calculate_accuracy(master_key)
    return result


def accuracy_by_symbol(master_key: str) -> Dict[str, Dict]:
    """按股票统计某位大师的准确率"""
    db = MasterTrackDB()
    records = db._load_records(master_key)

    symbol_stats: Dict[str, Dict] = {}
    for r in records:
        sym = r.get("symbol", "")
        if not sym:
            continue
        if sym not in symbol_stats:
            symbol_stats[sym] = {"total": 0, "correct": 0, "wrong": 0}
        if r.get("was_correct_short") is not None:
            symbol_stats[sym]["total"] += 1
            if r["was_correct_short"]:
                symbol_stats[sym]["correct"] += 1
            else:
                symbol_stats[sym]["wrong"] += 1

    result = {}
    for sym, stats in symbol_stats.items():
        total = stats["total"]
        result[sym] = {
            "total": total,
            "correct": stats["correct"],
            "wrong": stats["wrong"],
            "accuracy": round(stats["correct"] / total * 100, 1) if total > 0 else None,
        }
    return result


def _get_price_at_date(symbol: str, target_date: str) -> Optional[float]:
    """Read an explicitly final raw daily close; intraday/QFQ fallbacks are unsafe.

    Legacy reference prices do not establish executable entry or corporate-action
    accounting. These diagnostics must never be presented as strategy returns.
    """
    return StockLibrary().get_price_at_date(symbol, target_date)


def verify_predictions(symbol: Optional[str] = None, *, db=None, now=None,
                       price_loader=None, calendar=None) -> List[str]:
    """Update each independent legacy horizon (14/90/365 calendar days).

    A supplied shared TradingCalendar rolls a non-session due date forward.
    Missing calendars use only the exact due date, never guessed weekdays.
    Canonical 7/30/90 outcomes belong to RecommendationTracker, not this legacy
    diagnostic. A zero prediction is neutral; absent predictions are unscored.
    """
    db = db or MasterTrackDB()
    current = now or datetime.now()
    load_price = price_loader or _get_price_at_date
    updated = []
    for record in db.get_unverified_records():
        rec_symbol = record.get("symbol", "")
        if symbol and rec_symbol != symbol:
            continue
        timestamp = record.get("analysis_timestamp", "")
        try:
            analysis_day = datetime.strptime(timestamp[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        price_at = record.get("price_at_analysis")
        if not isinstance(price_at, (int, float)) or isinstance(price_at, bool) or not math.isfinite(price_at) or price_at <= 0:
            continue
        for field, days in (("short", 14), ("mid", 90), ("long", 365)):
            if record.get(f"was_correct_{field}") is not None:
                continue
            prediction = (record.get(f"{field}_term_pred") or {}).get("change_pct")
            if not isinstance(prediction, (int, float)) or isinstance(prediction, bool) or not math.isfinite(prediction):
                continue
            due = analysis_day + timedelta(days=days)
            if due > current.date():
                continue
            if calendar is not None:
                try:
                    due = calendar.next_session(due, inclusive=True)
                except (ValueError, RuntimeError):
                    continue
            if due > current.date():
                continue
            actual_price = load_price(rec_symbol, due.isoformat())
            if not isinstance(actual_price, (int, float)) or not math.isfinite(actual_price) or actual_price <= 0:
                continue
            actual_change = (actual_price / price_at - 1) * 100
            sign = lambda value: (value > 0) - (value < 0)
            db.update_outcome(record["id"], field, round(actual_change, 2), sign(actual_change) == sign(prediction))
            updated.append(record["id"])
    return list(dict.fromkeys(updated))
