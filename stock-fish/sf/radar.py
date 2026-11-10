"""Theme radar and market high-flyers (信息栏, not part of any strategy rule).

- radar: boards ranked by 3-session limit-up concentration (z3, see mainline.concentration), so a
  theme that is still running but had a split day (few limit-ups today) is still visible. Boards
  whose 3-session limit-ups are mostly covered by a board already shown are skipped.
- high_flyers: the market's tallest boards (空间板) are always shown, whatever their theme.
- home_board: the most concentrated hot board a stock belongs to (theme attribution), so a stock
  sitting in several boards is attributed to the one that is actually abnormal.
Board data says what a stock belongs to, not why it rises: the reason for a high-flyer must come from
news / announcements (looked up by the person or the AI assistant, not by this module).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import mainline, strategy


def lu3_mask(ts: dict, t: int) -> pd.Series:
    return ts["limit_up"].iloc[max(0, t - 2):t + 1].fillna(False).astype(bool).any().reindex(ts["cols"]).fillna(False)


def radar(ts: dict, t: int, top: int = 8, min_lu3: int = 5, overlap_max: float = 0.6) -> list[dict]:
    day = ts["lu"].index[t]
    z = mainline.concentration(ts, t)
    frame = pd.DataFrame({"lu": ts["lu"].loc[day], "lu3": ts["lu3"].loc[day], "n": ts["n"].loc[day], "z": z}).dropna()
    frame = frame[(frame["lu3"] >= min_lu3) & (frame["n"] >= 5)].sort_values("z", ascending=False)
    hit = lu3_mask(ts, t).to_numpy(dtype=bool)
    covered = np.zeros(len(ts["cols"]), dtype=bool)
    out = []
    for board, r in frame.iterrows():
        members = (ts["M"][ts["names"].index(board)] > 0) & hit
        if not members.any() or (members & covered).sum() >= overlap_max * members.sum():
            continue
        covered |= members
        out.append(dict(board=board, lu=int(r["lu"]), lu3=int(r["lu3"]), members=int(r["n"]), z=round(float(r["z"]), 1),
                        codes=[ts["cols"][j] for j in np.flatnonzero(members)]))
        if len(out) >= top:
            break
    return out


def heights_all(aw: dict) -> pd.DataFrame:
    """Board height for every SSE/SZSE A share (incl. 20cm boards)."""
    return strategy._heights(aw["limit_up"].fillna(False).astype(bool))


def home_board(ts: dict, t: int, code: str, z: pd.Series | None = None, min_z: float = 2.0) -> str | None:
    if code not in ts["cols"]:
        return None
    j = list(ts["cols"]).index(code)
    boards = [ts["names"][i] for i in np.flatnonzero(ts["M"][:, j] > 0)]
    z = mainline.concentration(ts, t) if z is None else z
    scored = z.reindex(boards).dropna()
    scored = scored[scored >= min_z]
    return None if scored.empty else str(scored.idxmax())


def high_flyers(aw: dict, ts: dict, t: int, min_height: int = 4, top: int = 3) -> list[str]:
    h = heights_all(aw).iloc[t].fillna(0)
    h = h[h > 0].sort_values(ascending=False)
    if h.empty:
        return []
    # stocks at >= min_height boards; if none, the tallest level only (the market's 空间板)
    cut = min_height if int(h.iloc[0]) >= min_height else int(h.iloc[0])
    return h[h >= cut].index.tolist()[:top]
