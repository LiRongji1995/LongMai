"""Same-provider backward-adjustment arithmetic, with explicit revision checks.

BaoStock adjustment definition: https://www.baostock.com/helpdocs/pdf/
BaoStock%E5%A4%8D%E6%9D%83%E5%9B%A0%E5%AD%90%E7%AE%80%E4%BB%8B.pdf
The R03 captured 50 HFQ bars / 200 OHLC values equal RAW * backAdjustFactor
exactly. This is a price-reference convention, not total investment return.
"""
from bisect import bisect_right
from decimal import Decimal, InvalidOperation
from datetime import date
from .contracts import digest

DEFINITION = 'BAOSTOCK_HFQ_RAW_X_BACK_FACTOR_R03_V1'


def positive(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() and value > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def factor_series(captures):
    """Accept complete snapshots; a historical factor revision needs review.

    New corporate-action dates extend a backward-adjusted series without
    changing its IPO anchor. Conflicting values for an existing effective date
    are refused, so cached observations cannot silently mix revised factors.
    """
    values = {}; refs = set(); conflict = False; covered_to = None
    for capture in captures:
        rows, receipt_id = capture[:2]
        capture_end = capture[2] if len(capture)>2 else None
        capture_start = capture[3] if len(capture)>3 else None
        incoming_days={str(row.get('dividOperateDate','')) for row in rows}
        if capture_start is not None and capture_end is not None and any(capture_start<=day<=capture_end and day not in incoming_days for day in values):
            # An explicitly complete replacement omitting an old factor is
            # a revision. A failed request never enters this capture list.
            conflict = True
        refs.add(receipt_id)
        for row in rows:
            day = str(row.get('dividOperateDate', ''))
            factor = positive(row.get('backAdjustFactor'))
            try:valid_day=date.fromisoformat(day).isoformat()==day
            except ValueError:valid_day=False
            if not valid_day or factor is None:
                conflict = True
                continue
            if day in values and values[day] != factor:
                conflict = True
            if day not in values and covered_to is not None and day<=covered_to:
                # A newly reported action inside an already complete prior
                # capture window is a revision, not a future extension.
                conflict = True
            values[day] = factor
        if capture_end is not None:covered_to=max(covered_to or capture_end,capture_end)
    return sorted(values.items()), tuple(sorted(refs)), conflict


def comparable_rows(iid, raw_rows, adjusted_rows, captures):
    """Return session -> comparable OHLC/source refs, and a precise status.

    Exact Decimal agreement is required whenever an independently fetched HFQ
    row is available. No current float share count or forward factor is used.
    A partial factor window lacking its left anchor never acquires an invented
    factor of one. Direct HFQ fallback is one immutable capture basis only.
    """
    series, factor_refs, conflict = factor_series(captures)
    if conflict:
        return {}, 'FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW'
    if series:
        days = [d for d, _ in series]
        basis = DEFINITION + ':' + iid + ':' + digest([series[0][0], str(series[0][1])])[:20]
        converted = {}
        for session, (raw, raw_ref) in raw_rows:
            index = bisect_right(days, session.isoformat()) - 1
            if index < 0:
                continue
            factor = series[index][1]
            prices = {name: positive(raw.get(name)) for name in ('open', 'high', 'low', 'close')}
            if any(value is None for value in prices.values()):
                continue
            values = {name: value * factor for name, value in prices.items()}
            observed = adjusted_rows.get(session)
            if observed and str(observed[0].get('adjustflag')) == '1':
                if any(positive(observed[0].get(name)) != value for name, value in values.items()):
                    return {}, 'RAW_FACTOR_HFQ_CROSS_VALIDATION_FAILED'
            refs = [raw_ref, *factor_refs]
            if observed:
                refs.append(observed[1])
            converted[session] = ({**{'comparable_' + name: value for name, value in values.items()},
                                   'comparable_basis': basis}, tuple(dict.fromkeys(refs)))
        return converted, 'VERIFIED_PROVIDER_FACTOR_DEFINITION' if len(converted) == len(raw_rows) else 'FACTOR_LEFT_BOUNDARY_OR_RAW_GAP'
    # No factor source: a single capture is internally comparable. Different
    # captures are deliberately distinct until factor evidence links them.
    converted = {}
    for session, (row, rid) in adjusted_rows.items():
        if str(row.get('adjustflag')) != '1':
            continue
        values = {'comparable_' + name: positive(row.get(name)) for name in ('open', 'high', 'low', 'close')}
        if all(value is not None for value in values.values()):
            converted[session] = (values | {'comparable_basis': 'BAOSTOCK_HFQ_FLAG1_CAPTURE_' + rid}, (rid,))
    bases={value[0]['comparable_basis'] for value in converted.values()}
    status='HFQ_MULTIPLE_CAPTURE_BASES_REQUIRE_FACTOR_LINK' if len(bases)>1 else 'DIRECT_HFQ_CAPTURE_ONLY' if converted else 'NO_COMPARABLE_SOURCE'
    return converted, status
