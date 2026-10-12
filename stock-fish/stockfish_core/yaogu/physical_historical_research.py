"""Fresh, limited historical R1 research from authenticated physical originals.

This is a new source version, not a replacement capture wrapper or an archived
capability decoder. The old replay, normalizer and selectors remain unchanged.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import os
from pathlib import Path
import signal
import threading
import time as walltime
import weakref

from .contracts import DailyBar, MarketInput, SecurityRecord, UniverseSnapshot
from .history_artifacts import digest_json_stream, digest_market_input
from . import historical_replay as frozen
from .historical_replay_turnover import _turn, TURNOVER_BASIS
from .provider_comparability import comparable_rows, factor_series
from .saved_r2_sources_v2 import AuthenticatedSourceArtifactsV2, issue_saved_r2_source_reader
from .saved_r2_delivery import _json, _Registry
from .short_horizon_participation import _market_code, _market_observation, _source_parent
from .visibility import EvidenceResolver, SH, stamp
from . import strategy_experiments as se

VERSION = 'PHYSICAL_ORIGINAL_HISTORICAL_R1_RESEARCH_V1'
FEATURE_VERSION = 'PHYSICAL_DATED_IDENTITY_AND_EXACT_PRICE_V1'
SCHEMA = 'PHYSICAL_HISTORICAL_R1_RESEARCH_ARCHIVE_V1'
_TOKEN = object()
_USED_JOBS = set()
_INPUT_BINDINGS = {}
_FACTORY_JOBS = set()
FIELD_VERSION = 'BAOSTOCK_00.9.30_R03_V1'


def _require(value, reason):
    if not value:
        raise ValueError('PHYSICAL_HISTORICAL_' + reason)


def source_identity():
    root = Path(__file__).resolve().parents[2]
    names = ('physical_historical_research.py', 'saved_r2_sources_v2.py',
        'saved_r2_delivery.py', 'research_context_artifacts.py', 'historical_replay.py',
        'historical_replay_turnover.py', 'provider_comparability.py',
        'provider_normalization.py', 'contracts.py', 'visibility.py',
        'history_artifacts.py', 'short_horizon_participation.py',
        'short_horizon_strength.py', 'short_horizon_profiles.py',
        'strategy_experiments.py')
    paths = [Path(__file__).with_name(name) for name in names]
    paths.append(root / 'docs/PHYSICAL_HISTORICAL_RESEARCH_V1.md')
    files = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in paths}
    body = dict(implementation_version=VERSION, feature_version=FEATURE_VERSION,
        parameter_status='UNVALIDATED', market_edge='NOT_ESTABLISHED', source_file_hashes=files)
    return dict(body, source_hash=digest_json_stream(body))


class _Deadline:
    """The registered absolute deadline also bounds the unchanged native call."""
    def __init__(self, value):
        self.end = stamp(value)

    def check(self):
        if datetime.now(timezone.utc) >= self.end:
            raise TimeoutError('PHYSICAL_HISTORICAL_REGISTERED_DEADLINE_EXCEEDED')

    def __enter__(self):
        self.check()
        _require(threading.current_thread() is threading.main_thread(), 'MAIN_THREAD_DEADLINE_REQUIRED')
        self.started = walltime.monotonic()
        self.handler = signal.getsignal(signal.SIGALRM)
        self.timer = signal.getitimer(signal.ITIMER_REAL)
        # An earlier caller handler may return. Keeping it armed instead of our
        # own absolute deadline would then leave the native call unbounded.
        # Refuse that conflict without changing the caller's handler or timer.
        _require(not self.timer[0] and not self.timer[1], 'ACTIVE_CALLER_TIMER_CONFLICT')
        remaining = (self.end-datetime.now(timezone.utc)).total_seconds()
        self.owned = True
        if self.owned:
            signal.signal(signal.SIGALRM, self._expired)
            signal.setitimer(signal.ITIMER_REAL, remaining)
        return self

    def _expired(self, *_):
        raise TimeoutError('PHYSICAL_HISTORICAL_REGISTERED_DEADLINE_EXCEEDED')

    def __exit__(self, *_):
        if self.owned:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.handler)
            if self.timer[0]:
                signal.setitimer(signal.ITIMER_REAL,
                    max(.000001, self.timer[0]-(walltime.monotonic()-self.started)), self.timer[1])


def _payload(receipt, reader):
    body = _json(reader(receipt.receipt_id))
    _require(isinstance(body, dict) and isinstance(body.get('rows'), list)
        and len(body['rows']) == receipt.row_count
        and body.get('status') == receipt.status
        and body.get('pagination_complete') == receipt.pagination_complete
        and body.get('field_version') == receipt.field_version, 'ORIGINAL_PAYLOAD_ENVELOPE_CHANGED')
    if 'first_seen_at' in body:
        _require(stamp(body['first_seen_at']) == receipt.first_seen_at, 'ORIGINAL_FIRST_SEEN_CHANGED')
    return body


def _calendar(receipts, reader):
    candidates = [r for r in receipts if r.dataset == 'calendar' and r.provider == 'baostock'
        and r.status == 'COMPLETE' and r.pagination_complete and r.field_version == FIELD_VERSION]
    _require(bool(candidates), 'AUTHENTICATED_CALENDAR_REQUIRED')
    calendars = []
    for parent in candidates:
        start, end = (date.fromisoformat(str(parent.parameters[key])) for key in ('start_date', 'end_date'))
        rows = _payload(parent, reader)['rows']
        days = {}
        for row in rows:
            day = date.fromisoformat(str(row.get('calendar_date')))
            _require(day not in days and row.get('is_trading_day') in ('0', '1'), 'CALENDAR_ROW_CONFLICT')
            days[day] = row['is_trading_day']
        _require(set(days) == {start+timedelta(days=i) for i in range((end-start).days+1)}, 'CALENDAR_FULL_SCOPE_REQUIRED')
        calendars.append(tuple(sorted(d for d, trading in days.items() if trading == '1')))
    _require(all(axis == calendars[0] for axis in calendars), 'CALENDAR_REVISION_CONFLICT')
    return calendars[0]


def _factor_sources(receipts, reader, audits, deadline):
    """Retain all attempts; a COMPLETE retry needs genuine inherited fragments."""
    captures = []
    for parent in receipts:
        if parent.dataset != 'adjustment_factors':
            continue
        deadline.check()
        _require(parent.provider == 'baostock' and parent.field_version == FIELD_VERSION,
            'FACTOR_SCHEMA_UNRECOGNIZED')
        body = _payload(parent, reader)
        codes = parent.parameters.get('codes')
        if isinstance(codes, str):
            codes = ast.literal_eval(codes)
        _require(isinstance(codes, list) and len(codes) == len(set(codes))
            and all(frozen._iid(code) for code in codes), 'FACTOR_REQUEST_SCOPE_INVALID')
        start, end = (date.fromisoformat(str(parent.parameters[key])) for key in ('start_date', 'end_date'))
        _require(start <= end and all(row.get('code') in codes for row in body['rows']), 'FACTOR_ROW_OUTSIDE_REQUEST_SCOPE')
        dated = set()
        for row in body['rows']:
            effective = frozen._day(row.get('dividOperateDate'))
            _require(effective is not None and str(effective) == row.get('dividOperateDate')
                and start <= effective <= end, 'FACTOR_EFFECTIVE_DATE_OUTSIDE_REQUEST_SCOPE')
            key = row['code'], effective
            _require(key not in dated, 'FACTOR_DUPLICATE_EFFECTIVE_DATE')
            dated.add(key)
            # Bound later exact-rational operations on untrusted decimal text.
            text = str(row.get('backAdjustFactor'))
            factor = _number(text, positive=True)
            _require(len(text) <= 128 and factor is not None
                and abs(factor.as_tuple().exponent) <= 128, 'FACTOR_NUMERIC_REPRESENTATION_UNSUPPORTED')
        statuses = body.get('coverage', {}).get('security_statuses', [])
        _require(isinstance(statuses, list), 'FACTOR_SECURITY_COVERAGE_INVALID')
        by_code = {}
        for record in statuses:
            code = record.get('code')
            _require(code in codes and code not in by_code, 'FACTOR_SECURITY_SCOPE_CONFLICT')
            by_code[code] = record
        fragments = {}
        for code in codes:
            rows = [row for row in body['rows'] if row.get('code') == code]
            record = by_code.get(code)
            if record and record.get('status') == 'COMPLETE' and record.get('cursor_exhausted') is True:
                _require(type(record.get('row_count')) is int and record['row_count'] == len(rows), 'FACTOR_FRAGMENT_COUNT_CONFLICT')
                captured = stamp(record['captured_at'])
                _require(captured <= parent.first_seen_at, 'FACTOR_FRAGMENT_CAPTURE_AFTER_PARENT')
                fragments[code] = (rows, record)
        if parent.status == 'COMPLETE' and parent.pagination_complete:
            _require(set(fragments) == set(codes) and body.get('coverage', {}).get('cursor_exhausted') is True,
                'COMPLETE_FACTOR_FULL_REQUEST_NOT_PROVEN')
        captures.append(dict(parent=parent, start=start, end=end, fragments=fragments,
            raw_first_seen_present='first_seen_at' in body))
    audit_by = {row['receipt_id']: row for row in audits}
    inherited = []
    for capture in captures:
        parent = capture['parent']
        started = audit_by[parent.receipt_id].get('started_at')
        _require(started is not None, 'FACTOR_WRAPPER_START_REQUIRED_FOR_LINEAGE')
        for code, (rows, record) in capture['fragments'].items():
            if stamp(record['captured_at']) >= stamp(started):
                continue
            signature = Counter(digest_json_stream(row) for row in rows)
            ancestors = [other for other in captures if other is not capture
                and other['parent'].first_seen_at <= parent.first_seen_at
                and other['start'] == capture['start'] and other['end'] == capture['end']
                and code in other['fragments']
                and other['fragments'][code][1] == record
                and Counter(digest_json_stream(row) for row in other['fragments'][code][0]) == signature
                and (audit_by[other['parent'].receipt_id].get('started_at') is not None
                     and stamp(audit_by[other['parent'].receipt_id]['started_at']) <= stamp(record['captured_at'])
                     and audit_by[other['parent'].receipt_id].get('completed_at') is not None
                     and stamp(audit_by[other['parent'].receipt_id]['completed_at']) <= stamp(started))]
            _require(bool(ancestors), 'INHERITED_FACTOR_AUTHENTICATED_ANCESTOR_MISSING')
            inherited.append(dict(receipt_id=parent.receipt_id, code=code,
                ancestor_receipt_ids=sorted(other['parent'].receipt_id for other in ancestors),
                row_multiset_hash=digest_json_stream(sorted(signature.items()))))
    return captures, inherited


def _number(value, *, positive=False):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and (number > 0 if positive else number >= 0) else None
    except (ValueError, ArithmeticError):
        return None


def _build(issued, policy, day, history_sessions, derived, deadline):
    _require(type(issued) is AuthenticatedSourceArtifactsV2, 'GENUINE_PHYSICAL_SOURCE_ISSUER_REQUIRED')
    issued.validate_binding()
    reader, receipts = issued.reader, issued.receipts
    calendar = _calendar(receipts, reader)
    _require(day in calendar, 'OBSERVATION_NOT_VERIFIED_TRADING_SESSION')
    window = tuple(d for d in calendar if d <= day)[-history_sessions:]
    _require(len(window) == history_sessions, 'ACTUAL_CALENDAR_WINDOW_INCOMPLETE')
    resolver = EvidenceResolver(receipts, cutoff=derived, coverage_type='RECONSTRUCTED_HISTORICAL',
        sessions=tuple(map(str, calendar)), observation_date=str(day), record_rows=False)
    gaps, raw_by, masters = [], defaultdict(list), {}
    for session in window:
        deadline.check()
        try:
            _market_observation(resolver, str(session), reader)
        except ValueError as error:
            if not str(error).startswith(('SHR2_MARKET_MARKET_DAILY_SOURCE_UNAVAILABLE',
                                          'SHR2_MARKET_MASTER_ALL_SOURCE_UNAVAILABLE')):
                raise
            gaps.append(dict(session=str(session), field='DAILY_RAW_AND_MASTER', reason=str(error)))
            # A missing price parent cannot erase an independently complete
            # dated directory into an apparent empty qualification pool.
            try:
                master = _source_parent(resolver, str(session), 'master_all')
            except ValueError as missing_master:
                if not str(missing_master).startswith('SHR2_MARKET_MASTER_ALL_SOURCE_UNAVAILABLE'):
                    raise
            else:
                master_rows = _payload(master, reader)['rows']
                master_codes = [row.get('code') for row in master_rows]
                _require(len(master_codes) == len(set(master_codes))
                    and all(frozen._iid(code) for code in master_codes)
                    and all(str(row.get('tradeStatus')) in ('0','1') for row in master_rows),
                    'DATED_MASTER_IDENTITY_OR_STATUS_CONFLICT')
                _require(any(_market_code(code) for code in master_codes), 'DATED_MASTER_A_SHARE_SCOPE_EMPTY')
                masters[session] = (master, master_rows)
            continue
        market = _source_parent(resolver, str(session), 'market_daily')
        master = _source_parent(resolver, str(session), 'master_all')
        masters[session] = (master, _payload(master, reader)['rows'])
        for row in _payload(market, reader)['rows']:
            code = row['code']
            if code.startswith('sz.') and '000001' <= code[3:] <= '004999':
                raw_by[code].append((session, (row, market.receipt_id)))
    factors, inheritance = _factor_sources(receipts, reader, issued.audits, deadline)
    bars, numeric = [], []
    for code, rows in sorted(raw_by.items()):
        deadline.check()
        iid = frozen._iid(code)
        all_fragments, authority = [], []
        for capture in factors:
            if code not in capture['fragments']:
                continue
            parent = capture['parent']
            fragment = capture['fragments'][code][0]
            scoped = [row for row in fragment if frozen._day(row.get('dividOperateDate'))
                      and frozen._day(row['dividOperateDate']) <= day]
            value = (scoped, parent.receipt_id, str(min(capture['end'], day)), str(capture['start']))
            all_fragments.append(value)
            if (parent.status == 'COMPLETE' and parent.pagination_complete
                    and capture['raw_first_seen_present']):
                authority.append(value)
        # Audit fragments in chronological order so insertion/omission revisions
        # cannot be hidden by choosing the newest successful batch alone.
        order = {r.receipt_id: (r.first_seen_at, r.ingested_at, r.receipt_id) for r in receipts}
        all_fragments.sort(key=lambda c: order[c[1]])
        authority.sort(key=lambda c: order[c[1]])
        oracle, oracle_quality = comparable_rows(iid, rows, {}, all_fragments)
        _, _, conflict = factor_series(all_fragments)
        values, quality = comparable_rows(iid, rows, {}, authority)
        authority_series, _, _ = factor_series(authority)
        if not authority and any(code in item['fragments'] and item['parent'].status == 'COMPLETE'
                and not item['raw_first_seen_present'] for item in factors):
            quality = 'COMPLETE_FACTOR_RAW_CAPTURE_CLOCK_UNKNOWN'
        if conflict or oracle_quality == 'FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW':
            values, quality = {}, 'ALL_ATTEMPT_FACTOR_REVISION_CONFLICT'
        for session, (raw, raw_ref) in rows:
            comparable, refs = values.get(session, ({}, (raw_ref,)))
            if comparable and (session not in oracle or oracle[session][0] != comparable):
                comparable, refs, quality = {}, (raw_ref,), 'ALL_ATTEMPT_AUTHORITY_EXACT_LINK_UNPROVEN'
            if comparable:
                factor = next((value for effective,value in reversed(authority_series)
                               if effective <= str(session)), None)
                if factor is None or any(Fraction(comparable['comparable_'+key]) !=
                        Fraction(Decimal(str(raw[key]))) * Fraction(factor)
                        for key in ('open','high','low','close')):
                    comparable, refs, quality = {}, (raw_ref,), 'SHARED_DECIMAL_EXACT_PRODUCT_UNPROVEN'
            values_raw = {field: _number(raw.get(source), positive=field not in ('volume_shares', 'amount_cny'))
                for field, source in (('open', 'open'), ('high', 'high'), ('low', 'low'),
                    ('close', 'close'), ('reference_prev_close', 'preclose'),
                    ('volume_shares', 'volume'), ('amount_cny', 'amount'))}
            if (any(values_raw[key] is None for key in ('open','high','low','close','reference_prev_close'))
                or not values_raw['low'] <= min(values_raw['open'], values_raw['close'])
                       <= max(values_raw['open'], values_raw['close']) <= values_raw['high']):
                gaps.append(dict(instrument_id=iid, session=str(session), field='RAW_PRICE', reason='INVALID_OR_NONTRADING_RAW'))
                continue
            turnover, exact_turn, turn_reason = _turn(raw.get('turn'))
            clocks = dict(event_time=datetime.combine(session, time(15), SH),
                source_published_at=max(resolver.receipts[ref].source_published_at for ref in refs),
                first_seen_at=derived, ingested_at=derived)
            bars.append(DailyBar(instrument_id=iid, session=session, **values_raw, **comparable,
                turnover_fraction=turnover, turnover_basis=TURNOVER_BASIS if turnover is not None else None,
                source_refs=refs, **clocks))
            numeric.append(dict(instrument_id=iid, session=str(session), row_hash=digest_json_stream(raw),
                raw_source_ref=raw_ref, comparable_status=quality, source_refs=list(refs),
                turnover_exact=dict(numerator=exact_turn.numerator,denominator=exact_turn.denominator)
                    if exact_turn is not None else None, turnover_reason=turn_reason))
    basic, conflicts = {}, set()
    for parent in receipts:
        if parent.dataset not in ('stock_basic_all', 'stock_basic') or parent.provider != 'baostock':
            continue
        if parent.status != 'COMPLETE' or not parent.pagination_complete:
            continue
        _require(parent.field_version == FIELD_VERSION, 'STOCK_BASIC_SCHEMA_UNRECOGNIZED')
        for row in _payload(parent, reader)['rows']:
            code = row.get('code')
            _require(frozen._iid(code) is not None, 'STOCK_BASIC_IDENTITY_INVALID')
            fact = (row.get('ipoDate'), row.get('type'))
            if code in basic and basic[code][0] != fact:
                conflicts.add(code)
            basic[code] = (fact, parent.receipt_id)
    _require(day in masters, 'CURRENT_DATED_MASTER_UNAVAILABLE_DENOMINATOR_UNKNOWN')
    securities, current_master_ref, expected = [], (), None
    if day in masters:
        master, master_rows = masters[day]
        current_master_ref, expected = (master.receipt_id,), len(master_rows)
        current_raw = {code: (raw, rid) for code, rows in raw_by.items() for session, (raw,rid) in rows if session == day}
        for row in master_rows:
            code, iid = row['code'], frozen._iid(row['code'])
            fact, basic_ref = basic.get(code, ((None,None), None))
            ipo = frozen._day(fact[0])
            if ipo is None or code in conflicts or ipo > day:
                gaps.append(dict(instrument_id=iid, session=str(day), field='HISTORICAL_IDENTITY', reason='IPO_OR_TYPE_UNKNOWN_OR_CONFLICT'))
                continue
            raw, raw_ref = current_raw.get(code, ({}, None))
            main = code.startswith('sz.') and '000001' <= code[3:] <= '004999' and day >= date(2021,4,6) and fact[1] == '1' and bool(raw)
            a, b = str(raw.get('tradestatus')), str(row.get('tradeStatus'))
            name = str(row.get('code_name') or '')
            refs = (master.receipt_id, basic_ref, *((raw_ref,) if raw_ref else ()))
            securities.append(SecurityRecord(instrument_id=iid,
                exchange={'sz':'SZSE','sh':'SSE','bj':'BSE'}[code[:2]], board='MAIN' if main else 'UNKNOWN',
                security_type='A_COMMON' if main else 'UNKNOWN', listed_on=ipo,
                effective_from=day, effective_to=day+timedelta(days=1),
                st=raw.get('isST') == '1' if raw.get('isST') in ('0','1') else None,
                suspended=a == '0' if a in ('0','1') and a == b else None,
                delisting_period=name.endswith('退') if name and main else None,
                name=name, source_refs=refs, risk_flags=('CURRENT_VENDOR_HISTORICAL_REVISION_RISK',),
                event_time=datetime.combine(day,time(15),SH),
                source_published_at=max(resolver.receipts[ref].source_published_at for ref in refs),
                first_seen_at=derived, ingested_at=derived))
    universe = UniverseSnapshot(snapshot_id='physical_historical_'+digest_json_stream([s.model_dump(mode='json') for s in securities]),
        securities=tuple(securities), source_refs=current_master_ref, complete=False, expected_count=expected,
        event_time=datetime.combine(day,time(15),SH), source_published_at=derived,
        first_seen_at=derived, ingested_at=derived)
    bundle = MarketInput(observation_date=day, sessions=calendar, universe=universe, bars=tuple(bars),
        receipts=receipts, coverage_type='RECONSTRUCTED_HISTORICAL')
    # The whole newly constructed view is first recorded only after construction;
    # the source receipts keep their original clocks without alteration.
    completed = datetime.now(timezone.utc)
    securities = tuple(s.model_copy(update={'first_seen_at':completed, 'ingested_at':completed})
                       for s in securities)
    universe = universe.model_copy(update={'securities':securities,
        'source_published_at':completed, 'first_seen_at':completed, 'ingested_at':completed})
    bundle = bundle.model_copy(update={'universe':universe, 'bars':tuple(
        b.model_copy(update={'first_seen_at':completed, 'ingested_at':completed}) for b in bars)})
    issued.validate_binding()
    return bundle, dict(source_version=VERSION, feature_version=FEATURE_VERSION,
        observation_date=str(day), sessions_26=list(map(str,window)),
        derivation_started_at=derived.isoformat(), actual_derived_at=completed.isoformat(),
        derived_clock_role='FIRST_RECORD_AFTER_VIEW_CONSTRUCTION_NOT_SOURCE_CAPTURE_TIME',
        policy=policy, original_source_audits=[dict(row,
            original_result_wrapper_status='UNKNOWN_NOT_REQUIRED_BY_PHYSICAL_SOURCE_MODE'
                if row.get('authentication_mode') == 'WHOLE_RAW_AND_PHYSICAL_RECEIPT'
                else 'AUTHENTICATED_ORIGINAL_TRIO') for row in issued.audits], factor_inheritance=inheritance,
        numeric_observations=numeric, gaps=gaps, raw_price_scope='DATED_SZSE_000001_004999',
        scope='LIMITED_SZSE_OTHER_HISTORICAL_IDENTITY_UNKNOWN',
        original_result_wrappers_reconstructed=False, original_source_clocks_modified=False,
        historical_PIT_count=0, complete_independent_cycles=0, market_edge='NOT_ESTABLISHED')


class _InputBinding:
    def __new__(cls, *_args, **_kwargs):
        raise ValueError('PHYSICAL_HISTORICAL_GENUINE_INPUT_FACTORY_REQUIRED')

    def __copy__(self):
        raise ValueError('PHYSICAL_HISTORICAL_LIVE_INPUT_NOT_COPYABLE')

    def __deepcopy__(self, memo):
        raise ValueError('PHYSICAL_HISTORICAL_LIVE_INPUT_NOT_COPYABLE')

    def validate(self):
        held = _INPUT_BINDINGS.get(id(self))
        _require(type(self) is _InputBinding and held is not None and held['owner']() is self
            and self._token is _TOKEN and held['pid'] == os.getpid()
            and held['issued_id'] == id(self.issued) and held['bundle_id'] == id(self.bundle)
            and held['policy_registry_id'] == id(self.policy_registry)
            and held['job'] == self.job and held['cutoff'] == self.cutoff
            and held['input_hash'] == self.input_hash
            and held['policy_registry_hash'] == self._policy_registry_hash
            and held['state'] == self.state
            and digest_json_stream(self.identity) == held['identity_hash'],
            'GENUINE_INPUT_FACTORY_BINDING_CHANGED')
        _require(source_identity() == self.identity, 'IMPLEMENTATION_CHANGED')
        _require(digest_market_input(self.bundle) == self.input_hash, 'DERIVED_INPUT_CHANGED')
        self.issued.validate_binding()
        _require(digest_json_stream(self.policy_registry.files) == self._policy_registry_hash,
            'POLICY_REGISTRY_CHANGED')
        self.policy_registry.terminal()

    def consume(self, cutoff):
        self.validate()
        _require(stamp(cutoff) == self.cutoff, 'NATIVE_EXACT_CUTOFF_CHANGED')
        _require(self.state == 'READY', 'NATIVE_R1_ALREADY_CONSUMED')
        held = _INPUT_BINDINGS[id(self)]
        held['state'] = self.state = 'CONSUMING'
        try:
            result = se.run_short_horizon_strategy(self.bundle, cutoff=self.cutoff,
                input_id='physical_historical_'+self.input_hash, strategy_id='SHORT_HORIZON_STRENGTH_R1')
            self.validate()
        except BaseException:
            held['state'] = self.state = 'FAILED'
            raise
        held['state'] = self.state = 'COMPLETE'
        return result


def _new_input_binding(issued, bundle, identity, policy_registry, job, cutoff):
    _require(job in _USED_JOBS and job not in _FACTORY_JOBS, 'REGISTERED_FACTORY_ALREADY_ISSUED')
    _FACTORY_JOBS.add(job)
    owner = object.__new__(_InputBinding)
    owner._token, owner.issued, owner.bundle = _TOKEN, issued, bundle
    owner.identity, owner.policy_registry = identity, policy_registry
    owner._policy_registry_hash = digest_json_stream(policy_registry.files)
    owner.input_hash, owner.state = digest_market_input(bundle), 'READY'
    owner.job, owner.cutoff = job, stamp(cutoff)
    key = id(owner)
    _INPUT_BINDINGS[key] = dict(owner=weakref.ref(owner, lambda _: _INPUT_BINDINGS.pop(key, None)),
        pid=os.getpid(), issued_id=id(issued), bundle_id=id(bundle),
        policy_registry_id=id(policy_registry), job=job, cutoff=owner.cutoff, input_hash=owner.input_hash,
        policy_registry_hash=owner._policy_registry_hash,
        identity_hash=digest_json_stream(identity), state='READY')
    return owner


def run_physical_historical_r1(sources_manifest_path, source_policy, observation_date,
        registered_worker_deadline, history_sessions=26):
    """One fresh source issuance, new inline input and unchanged native R1.

    ``source_policy`` is the directory of the actual eight official documents,
    not a saved policy JSON or a caller-supplied validator/capability.
    Returned documents are archive data and never authorize another execution.
    """
    _require(isinstance(sources_manifest_path, (str, Path)) and isinstance(source_policy, (str, Path)),
        'REAL_SOURCE_AND_POLICY_PATHS_REQUIRED')
    day = date.fromisoformat(str(observation_date))
    _require(type(history_sessions) is int and history_sessions == 26, 'REGISTERED_26_SESSION_WINDOW_REQUIRED')
    deadline = _Deadline(registered_worker_deadline)
    path = Path(sources_manifest_path).resolve(strict=True)
    job = (str(path), str(day), deadline.end.isoformat())
    _require(job not in _USED_JOBS, 'REGISTERED_JOB_ALREADY_ATTEMPTED')
    _USED_JOBS.add(job)
    with deadline:
        identity = source_identity()
        policy = frozen.source_policy(source_policy)
        _require(policy['board_authority']['SZSE']['range'] == ['000001','004999']
            and policy['board_authority']['SZSE']['effective_from'] == '2021-04-06', 'SZSE_POLICY_SCOPE_CHANGED')
        policy_registry = _Registry()
        for registration in policy['source_documents'].values():
            policy_registry.read(registration['path'], registration['sha256'])
        issued_at = datetime.now(timezone.utc)
        issued = issue_saved_r2_source_reader(sources_manifest_path=path, cutoff=issued_at)
        _require(type(issued) is AuthenticatedSourceArtifactsV2, 'GENUINE_PHYSICAL_SOURCE_ISSUER_REQUIRED')
        started_at = datetime.now(timezone.utc)
        bundle, audit = _build(issued, policy, day, history_sessions, started_at, deadline)
        derived_at = stamp(audit['actual_derived_at'])
        deadline.check()
        cutoff = datetime.now(timezone.utc)
        _require(cutoff >= derived_at >= started_at >= issued_at, 'ACTUAL_CLOCK_ORDER_INVALID')
        binding = _new_input_binding(issued, bundle, identity, policy_registry, job, cutoff)
        native = binding.consume(cutoff)
        deadline.check()
        _require(native['input_hash'] == binding.input_hash and native['observation_date'] == str(day)
            and set(native['results']) == {'SHORT_HORIZON_STRENGTH_R1'}, 'NATIVE_RESULT_BINDING_CHANGED')
        binding.validate()
        terminal_at = datetime.now(timezone.utc)
        deadline.check()
        return dict(schema_version=SCHEMA, source_identity=identity,
            native_strategy_identity={key:native[key] for key in ('implementation_version','feature_version',
                'source_hash','config_hash','spec_hash','spec_revision')},
            issued_at=issued_at.isoformat(), derived_at=derived_at.isoformat(),
            decision_cutoff=cutoff.isoformat(), terminal_verified_at=terminal_at.isoformat(),
            registered_worker_deadline=deadline.end.isoformat(), native_R1_attempts=1,
            selector_rule_changed=False, normalization_view_role='NEW_DERIVED_INPUT_NOT_OLD_CAPTURE_RESULT',
            input_hash=binding.input_hash, input=bundle.model_dump(mode='json'), native_run=native,
            source_registry=issued.registry.files, policy_registry=policy_registry.files,
            audit=audit, archive_role='ARCHIVE_ONLY_NOT_RESTORABLE_SOURCE_CAPABILITY')
