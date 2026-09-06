"""Public SDK fund summaries for evidence display; strict scoring stays gated.

LHB summary rows do not independently establish seat identities or one/three
session disclosure windows. This adapter never fabricates those authorities.
"""
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

LHB_SUMMARY_FIELDS={
    '上榜日':'session','龙虎榜净买额':'disclosed_net_cny','龙虎榜买入额':'disclosed_buy_cny',
    '龙虎榜卖出额':'disclosed_sell_cny','上榜原因':'reason',
}


def _finite(value):
    try:
        number=Decimal(str(value))
        return float(number) if number.is_finite() else None
    except (InvalidOperation,ValueError,TypeError):return None


def summary_preview(row, instrument_id, source_refs, clocks):
    # Explicit source-field projection prevents all "上榜后N日" labels,
    # narrative 解读, or future fields from entering evidence/LLM input.
    record={'instrument_id':instrument_id,'provider':'EASTMONEY_LHB_VIA_AKSHARE',
            'window_status':'UNVERIFIED','seat_status':'NOT_IN_SUMMARY_ENDPOINT',
            'source_refs':tuple(source_refs),**clocks}
    for source,target in LHB_SUMMARY_FIELDS.items():
        value=row.get(source)
        if target.endswith('_cny'):value=_finite(value)
        elif target=='session':
            try:value=date.fromisoformat(str(value)[:10]).isoformat()
            except ValueError:value=None
        record[target]=value
    return record
