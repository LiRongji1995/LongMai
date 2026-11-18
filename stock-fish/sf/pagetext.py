"""Page text helpers: Chinese-only wording (stage, group, version and command names, Beijing time stamps) and
HTML escaping, shared by the page modules."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import pandas as pd

from . import store

GROUPS = ("主线龙头", "连板梯队", "观察", "高波动")
GROUP_CLASS = {"主线龙头": "g-lead", "连板梯队": "g-ladder", "观察": "g-watch", "高波动": "g-risk"}


ZH_WORDS = {
    "STRONG_CONTINUATION": "强势延续", "STRONG_START": "强势启动", "FIRST_PULLBACK": "首次回调", "NEUTRAL": "中性",
    "UNKNOWN": "未知", "leader": "主线龙头", "ladder": "连板梯队", "watch": "观察", "avoid": "高波动",
    "baseline": "对照", "description": "只描述", "confirm": "确认", "deny": "否定", "neutral": "中性",
    "stock_lu": "个股涨停", "stock_bigloss": "个股大面", "stock_out5": "个股走出来", "theme_fade": "题材退潮",
    "free": "其他", "ok": "正常", "partial": "部分缺失", "FAILED": "失败", "skipped": "跳过", "pending": "待定",
    "not captured": "未采集",
}
ZH_COMMANDS = {
    "intraday --backfill": "分时补抓", "intraday": "分时抓取", "auction": "竞价快照", "premarket": "盘前清单",
    "baserates": "历史同类统计", "themes": "板块成分更新", "note": "题材记录", "page": "页面生成", "review": "复盘",
    "evaluate": "检验", "update": "数据更新", "seats": "龙虎榜席位", "schedule": "定时任务",
}


def _stamp_zh(m: re.Match) -> str:
    when = datetime.strptime(m.group(0), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(store.SH)
    return when.strftime("%m-%d %H:%M")


def zh(text: str) -> str:
    """Page text in Chinese only: stage codes, group codes, version names, UTC run stamps (shown as Beijing time),
    pinyin in homophone groups, command and file names. Stock and board names (e.g. *ST, TOPCon) are kept."""
    t = re.sub(r"\d{8}T\d{6}Z", _stamp_zh, text)
    t = re.sub(r"SF_LONGMAI_V(\d+)", r"龙脉第\1版", t)
    t = re.sub(r"SF_[A-Z_]+?_V(\d+)", r"第\1版", t)
    t = re.sub(r"龙脉\s*V(\d+)", r"龙脉第\1版", t)
    t = re.sub(r"(?<![A-Za-z])V(\d+)(?![A-Za-z0-9])", r"第\1版", t)
    t = re.sub(r"音·[a-z]+\(([^)]*)\)", r"谐音「\1」", t)
    t = re.sub(r"谐音 [a-z]+（([^）]*)）", r"谐音「\1」", t)
    t = re.sub(r"名字谐音「[a-z]+\(([^)]*)\)」", r"谐音「\1」", t)
    t = re.sub(r"(?:bin/)?sf ([a-z]+(?: --[a-z\-]+)?)",
               lambda m: ZH_COMMANDS.get(m.group(1), ZH_COMMANDS.get(m.group(1).split()[0], "相应")) + "命令", t)
    t = re.sub(r"docs/[\w./\-]+", "说明文档", t)
    for en, cn in sorted(ZH_WORDS.items(), key=lambda kv: -len(kv[0])):
        t = re.sub(rf"(?<![A-Za-z_]){re.escape(en)}(?![A-Za-z_])", cn, t)
    return t


def _e(x) -> str:
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else html.escape(zh(str(x)))
