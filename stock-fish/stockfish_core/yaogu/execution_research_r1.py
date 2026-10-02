"""EXECUTION_RESEARCH_R1: explicit, evidence-bound hypothetical open execution.

No broker, provider, database or production imports. The real-data tape adapter
is deliberately unavailable until dated rule/corporate-action evidence is supplied.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, time
from decimal import Decimal, ROUND_FLOOR
from statistics import median
from zoneinfo import ZoneInfo

from .contracts import digest
from .research_r1_contracts import quote_usable

D = lambda x: Decimal(str(x))
SH = ZoneInfo('Asia/Shanghai')


def stamp(value):
    value = datetime.fromisoformat(str(value))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return value


def deadline(session):
    return datetime.combine(datetime.fromisoformat(session).date(), time(9, 15), SH)


def visible(record, cutoff):
    fields = ('source_published_at', 'first_seen_at', 'ingested_at')
    try:
        return bool(record.get('source_refs')) and all(stamp(record[k]) <= cutoff for k in fields)
    except (KeyError, ValueError, TypeError):
        return False


def dated_rule(record, session, cutoff):
    return (record.get('verified') is True and record.get('effective_from', '9999') <= session
            <= record.get('effective_to', '0000') and visible(record, cutoff))


def fees(notional, side, scenario):
    required = ('commission_rate', 'minimum_commission', 'transfer_rate', 'sell_tax_rate')
    if any(k not in scenario or D(scenario[k]) < 0 for k in required):
        raise ValueError('EXPLICIT_NONNEGATIVE_COST_SCENARIO_REQUIRED')
    if scenario.get('commission_includes_transfer_or_tax') is not False:
        raise ValueError('COST_COMPONENT_OVERLAP_UNRESOLVED')
    return max(D(scenario['minimum_commission']), notional * D(scenario['commission_rate'])) + notional * (
        D(scenario['transfer_rate']) + (D(scenario['sell_tax_rate']) if side == 'SELL' else D(0)))


def allocate(candidates, nav, cash, holdings, *, spec=None):
    """Simultaneous proportional reduction; every known theme receives exposure."""
    from .research_r1 import config
    from .research_r1_contracts import effective_spec
    spec = spec or effective_spec(config())
    candidates = {c['instrument_id']: c for c in candidates if c['instrument_id'] not in holdings}
    ids = sorted(candidates)
    if not ids or nav <= 0 or cash <= 0:
        return {}, 'COMPLETE'
    before = min(cash / len(ids), nav * D(spec['single_name_cap']))
    existing = Counter()
    coverage = 'COMPLETE'
    for position in holdings.values():
        if position.get('themes') is None:
            coverage = 'THEME_COVERAGE_INCOMPLETE'
        for theme in set(position.get('themes') or []):
            existing[theme] += position['value']
    requested = Counter()
    for c in candidates.values():
        if c.get('themes') is None:
            coverage = 'THEME_COVERAGE_INCOMPLETE'
        for theme in set(c.get('themes') or []):
            requested[theme] += before
    scale = min([D(1), cash / (before * len(ids))] + [max(D(0), nav * D(spec['theme_cap']) - D(existing[t])) / total for t, total in requested.items()])
    return {iid: dict(before=before, after=before * scale, scale=scale) for iid in ids}, coverage


def quantity(budget, order, rule, cost):
    """Size from the pre-cutoff submitted maximum price, never realized open."""
    price, lot = D(order['limit_price']), int(rule['lot_size'])
    if price <= 0 or lot <= 0 or D(rule['tick_size']) <= 0:
        raise ValueError('INVALID_ORDER_RULE')
    if price % D(rule['tick_size']):
        raise ValueError('ORDER_PRICE_OFF_TICK')
    lots = int((budget / (price * lot)).to_integral_value(rounding=ROUND_FLOOR))
    while lots and price * lots * lot + fees(price * lots * lot, 'BUY', cost) > budget:
        lots -= 1
    return lots * lot


def fill(bar, side, rule, session, as_of, slippage, *, limit=None):
    if not bar or not visible(bar, as_of):
        return 'UNAVAILABLE', None
    bar=quote_usable(bar,'OPEN',session=session)
    if bar is None:return 'INVALID_OPEN_EVIDENCE', None
    if not dated_rule(rule, session, deadline(session)):
        return 'RULE_UNAVAILABLE', None
    if bar.get('suspended') is True:
        return 'SUSPENDED', None
    if bar.get('suspended') is not False or bar.get('limit_verified') is not True:
        return 'STATE_UNKNOWN', None
    if bar.get('open') is None:
        return 'NO_OPEN_PRICE', None
    opened = D(bar['open'])
    if opened <= 0 or bar.get('limit_up') is None or bar.get('limit_down') is None:
        return 'LIMIT_UNKNOWN', None
    up, down = D(bar['limit_up']), D(bar['limit_down'])
    if side == 'BUY' and opened >= up:
        return 'BUY_OPEN_LIMIT_UP', None
    if side == 'SELL' and opened <= down:
        return 'SELL_OPEN_LIMIT_DOWN', None
    price = opened * (D(1) + slippage if side == 'BUY' else D(1) - slippage)
    tick = D(rule['tick_size'])
    # Conservative rounding to a valid tick, with no clipping to force a fill.
    from decimal import ROUND_CEILING
    price = (price / tick).to_integral_value(rounding=ROUND_CEILING if side == 'BUY' else ROUND_FLOOR) * tick
    if not down <= price <= up or (side == 'BUY' and limit is not None and price > limit):
        return 'PRICE_OUTSIDE_ORDER_OR_LIMIT', None
    return 'ASSUMED_OPEN_FILL', price


def simulate(signals, tape, *, slippage_bps=10, spec=None, evaluation_window=None):
    """One account per group/scenario; preserve every signal, blocked entry and exit.

    Required tape records use explicit timestamps/source references. At present
    only TEST_FIXTURE can exercise the account adapter; genuine inputs fail closed.
    """
    from .research_r1 import config
    from .research_r1_contracts import effective_spec
    spec = effective_spec(spec or config(), tape.get('cost_scenario'))
    if slippage_bps not in spec['slippage_bps_per_side']:
        raise ValueError('UNREGISTERED_SLIPPAGE')
    from .research_r1_adapter import ResolvedTape
    if tape.get('coverage_type') != 'TEST_FIXTURE' and not (isinstance(tape, ResolvedTape) and tape.intact()):
        return dict(contract='EXECUTION_RESEARCH_R1', status='UNAVAILABLE',
                    reason='REAL_DATED_RULE_CORPORATE_ACTION_TAPE_ADAPTER_NOT_VERIFIED',
                    signal_count=len(signals), records=[dict(s, status='UNAVAILABLE') for s in signals])
    sessions = tape['sessions']
    if sessions != sorted(set(sessions)):
        raise ValueError('ORDERED_UNIQUE_EXCHANGE_SESSIONS_REQUIRED')
    as_of = stamp(tape['as_of'])
    cost = tape['cost_scenario']
    fees(D(1), 'BUY', cost)
    cash = D(spec['initial_nav_cny'])
    holdings, records, curve = {}, [], []
    reserved_uncertain = D(0)
    indexed = {}
    for signal in signals:
        record = dict(signal, trade_id=digest(signal), status='UNAVAILABLE', entry_session=None, planned_exit_session=None)
        records.append(record)
        if signal['signal_session'] not in sessions:
            record['reason'] = 'SIGNAL_CALENDAR_MISSING'
            continue
        i = sessions.index(signal['signal_session'])
        if i+spec['exit_offset'] >= len(sessions):
            record['reason'] = 'TARGET_SESSION_CALENDAR_MISSING'
            continue
        entry, planned = sessions[i+spec['entry_offset']], sessions[i+spec['exit_offset']]
        record.update(entry_session=entry, planned_exit_session=planned)
        cutoff = deadline(entry)
        try:
            timely = stamp(signal['published_at']) < cutoff and stamp(signal['evidence_available_at']) < cutoff
        except (KeyError, ValueError, TypeError):
            record['reason'] = 'PUBLICATION_EVIDENCE_MISSING'
            continue
        if not timely:
            record['status'] = 'LATE_SIGNAL'
            continue
        indexed.setdefault(entry, []).append(record)
    slip = D(slippage_bps) / D(10000)
    for session in sessions:
        if evaluation_window:
            if session<evaluation_window[0]:continue
            if evaluation_window[1] is None or session>evaluation_window[1]:break
        if datetime.combine(datetime.fromisoformat(session).date(), time(9, 30), SH) > as_of:
            break
        cutoff = deadline(session)
        start_cash = max(D(0),cash-reserved_uncertain)
        valuation_ok = reserved_uncertain == 0
        values = {}
        for iid, pos in holdings.items():
            quote = quote_usable(tape.get('valuations', {}).get(session, {}).get(iid),'BUDGET',instrument_id=iid,basis=pos['basis'])
            if not quote or (pos['basis']=='RAW_CNY' and quote.get('no_corporate_action_since_signal') is not True) or not visible(quote, cutoff) or quote.get('session') != sessions[sessions.index(session)-1] or quote.get('basis') != pos['basis']:
                valuation_ok = False
            else:
                values[iid] = dict(value=pos['quantity'] * D(quote['price']), themes=pos['themes'])
        nav = start_cash + sum((p['value'] for p in values.values()), D(0)) if valuation_ok else None
        daily = indexed.get(session, [])
        unique = {}
        for rec in sorted(daily, key=lambda x: (x['instrument_id'], x['signal_session'])):
            iid = rec['instrument_id']
            if iid in holdings or iid in unique:
                rec['status'] = 'ALREADY_HELD_OR_DUPLICATE_NO_EXTENSION'
            else:
                unique[iid] = rec
        budgets, coverage = allocate(list(unique.values()), nav, start_cash, values, spec=spec) if nav is not None else ({}, 'VALUATION_UNAVAILABLE')
        # Orders/budgets committed before exits: same-open proceeds cannot fund buys.
        prepared = []
        for iid, rec in unique.items():
            if iid not in budgets:
                rec.update(status='UNAVAILABLE', reason='PRETRADE_VALUATION_OR_BUDGET_UNAVAILABLE')
                continue
            budget = budgets[iid]
            rec['budget'] = {k: str(v) for k, v in budget.items()}
            rec['theme_coverage'] = coverage
            order = tape.get('orders', {}).get(session, {}).get(iid, {})
            rule = tape.get('rules', {}).get(session, {}).get(iid, {})
            if not visible(order, cutoff) or not dated_rule(rule, session, cutoff) or not dated_rule(cost, session, cutoff):
                rec.update(status='UNAVAILABLE', reason='ORDER_RULE_OR_COST_NOT_AVAILABLE_AT_CUTOFF')
                continue
            if rule.get('settlement')!='T_PLUS_1':raise ValueError('UNSUPPORTED_SETTLEMENT_RULE')
            qty = quantity(budget['after'], order, rule, cost)
            rec.update(quantity=qty, submitted_limit=order['limit_price'], capital_commitment_cny=str(budget['after']))
            if not qty:
                rec['status'] = 'NO_WHOLE_LOT_BUDGET'
                continue
            prepared.append((iid, rec, qty, order, rule, budget['after']))
        for iid, pos in list(holdings.items()):
            rec = pos['record']
            if session < rec['planned_exit_session']:
                continue
            bar = tape.get('bars', {}).get(session, {}).get(iid)
            bar = quote_usable(bar,'OPEN',instrument_id=iid,session=session)
            rule = tape.get('rules', {}).get(session, {}).get(iid, {})
            state, price = fill(bar, 'SELL', rule, session, as_of, slip)
            if not dated_rule(cost, session, cutoff):
                state, price = 'COST_UNAVAILABLE', None
            if price is not None and (bar.get('basis') != pos['basis'] or bar.get('corporate_actions_complete') is not True or bar.get('no_corporate_action_since_signal') is not True):
                state, price = 'CORPORATE_ACTION_RETURN_UNAVAILABLE', None
            rec.setdefault('exit_attempts', []).append(dict(session=session, status=state))
            if price is None:
                rec['status'] = 'EXIT_PENDING'
                continue
            proceeds = price * pos['quantity']
            sell_fees = fees(proceeds, 'SELL', cost)
            cash += proceeds - sell_fees
            pnl = proceeds - sell_fees - pos['entry_cost']
            rec.update(status='CLOSED', actual_exit_session=session, exit_price=str(price), sell_fees=str(sell_fees),
                exit_delay_sessions=sessions.index(session)-sessions.index(rec['planned_exit_session']),
                net_return=float(pnl / pos['entry_cost']), pnl_cny=str(pnl))
            del holdings[iid]
        for iid, rec, qty, order, rule, budget in prepared:
            bar = tape.get('bars', {}).get(session, {}).get(iid)
            bar = quote_usable(bar,'OPEN',instrument_id=iid,session=session)
            state, price = fill(bar, 'BUY', rule, session, as_of, slip, limit=D(order['limit_price']))
            if price is not None and (bar.get('basis') is None or bar.get('corporate_actions_complete') is not True or bar.get('no_corporate_action_since_signal') is not True):
                state, price = 'CORPORATE_ACTION_RETURN_UNAVAILABLE', None
            rec['entry_evidence'] = state
            if price is None:
                rec['status'] = 'ENTRY_NOT_FILLED' if state in ('BUY_OPEN_LIMIT_UP', 'SUSPENDED', 'PRICE_OUTSIDE_ORDER_OR_LIMIT') else 'ENTRY_UNCERTAIN'
                if rec['status']=='ENTRY_UNCERTAIN':
                    reserved_uncertain+=budget
                    rec.update(uncertain_capital_reservation_cny=str(budget),reservation_policy='MAX_COMMITMENT_NOT_CONFIRMED_FILL')
                continue
            buy_fees = fees(price * qty, 'BUY', cost)
            debit = price * qty + buy_fees
            if debit > min(budget, start_cash, cash):
                rec['status'] = 'CASH_CONSTRAINT_NO_FILL'
                continue
            cash -= debit
            start_cash -= debit
            rec.update(status='OPEN', entry_price=str(price), buy_fees=str(buy_fees), evidence_level='ASSUMED_OPEN_FILL')
            holdings[iid] = dict(record=rec, quantity=qty, basis=bar['basis'], entry_cost=debit, themes=rec.get('themes'))
        # Only verified, current-session closing marks; unavailable NAV is never imputed.
        marks = []
        for iid, pos in holdings.items():
            bar = quote_usable(tape.get('bars', {}).get(session, {}).get(iid),'CLOSE',instrument_id=iid,session=session,basis=pos['basis'])
            if (not bar or not visible(bar, as_of) or bar.get('close') is None or bar.get('basis') != pos['basis']
                    or bar.get('no_corporate_action_since_signal') is not True):
                marks = None
                break
            marks.append(pos['quantity'] * D(bar['close']))
        curve.append(dict(session=session, cash=str(cash), nav=str(cash+sum(marks, D(0))) if marks is not None and reserved_uncertain==0 else None,
                          open_positions=len(holdings), reserved_uncertain_cny=str(reserved_uncertain), theme_coverage=coverage))
    for pos in holdings.values():
        pos['record'].update(censored=True, capital_occupied_cny=str(pos['entry_cost']),pending_exit_delay_sessions=max(0,sessions.index(curve[-1]['session'])-sessions.index(pos['record']['planned_exit_session'])) if curve else 0)
    closed = [r['net_return'] for r in records if r['status'] == 'CLOSED']
    complete = all(rec['status'] in ('CLOSED', 'ENTRY_NOT_FILLED', 'NO_WHOLE_LOT_BUDGET', 'ALREADY_HELD_OR_DUPLICATE_NO_EXTENSION') for rec in records)
    all_returns = len(closed) == len(records)
    navs = [D(point['nav']) for point in curve if point['nav'] is not None]
    full_nav = bool(curve) and len(navs) == len(curve) and (not evaluation_window or curve[-1]['session']==evaluation_window[1])
    peak = D(spec['initial_nav_cny'])
    drawdown = D(0)
    for nav_value in navs:
        peak = max(peak, nav_value)
        drawdown = min(drawdown, nav_value / peak - 1)

    return dict(contract='EXECUTION_RESEARCH_R1', status='COMPLETE' if complete else 'INCOMPLETE',
        coverage_type=tape['coverage_type'], account_type='HYPOTHETICAL', initial_nav_cny=spec['initial_nav_cny'],
        effective_spec=spec, spec_hash=digest(spec), slippage_bps_per_side=slippage_bps, scenario_hash=digest(dict(spec=spec,slippage_bps=slippage_bps)),
        account_start_session=curve[0]['session'] if curve else None,evaluation_window=evaluation_window, evidence_as_of=tape['as_of'],tape_hash=digest(tape), signal_count=len(records), records=records, account_curve=curve,
        status_counts=dict(Counter(r['status'] for r in records)), open_positions=len(holdings),
        account_return=float(navs[-1]/D(spec['initial_nav_cny'])-1) if navs and full_nav and complete else None,
        max_drawdown=float(drawdown) if navs and full_nav and complete else None,
        mean_net_return=sum(closed)/len(closed) if closed and all_returns else None,
        median_net_return=median(closed) if closed and all_returns else None,
        worst_net_return=min(closed) if closed and all_returns else None,
        market_validation_status='INCONCLUSIVE')
