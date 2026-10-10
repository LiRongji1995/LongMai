"""Authenticated public capture capabilities for the explicit R2 context layer.

Paths belong to the caller's private capture manifest, never to a second
business database. Original MarketInput and historical receipt clocks are
unchanged. The adapter refuses caller-provided prices or membership overrides.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from .contracts import DailyBar, SourceReceipt, ThemeSnapshot
from .history_artifacts import digest_market_input
from .visibility import EvidenceResolver, SH, stamp

RETURN_BASIS = 'BAOSTOCK_REFERENCE_RETURN_CHAIN_V1'
MARKET_SCOPE = 'SSE_SZSE_A_COMMON_ALL_BOARDS'


def _iid(code):
    exchange, number = code.split('.')
    return number + '.' + exchange.upper()


def _decimal(value):
    return Decimal(str(value)) if value not in (None, '') else None


class SourceArtifactReader:
    """An explicit receipt-to-original-bytes capability, including SHA checks."""

    def __init__(self, receipts, paths):
        by_id = {}
        for item in receipts:
            row = SourceReceipt.model_validate(item)
            prior = by_id.get(row.receipt_id)
            if prior is not None and prior != row:
                raise ValueError('CONTEXT_RECEIPT_ID_CONFLICT')
            by_id[row.receipt_id] = row
        self.receipts = tuple(by_id[key] for key in sorted(by_id))
        self._receipts = by_id
        self._paths = {key: Path(value['raw_path'] if isinstance(value, dict) else value)
                       for key, value in paths.items()}

    def __call__(self, receipt_id):
        if receipt_id not in self._receipts or receipt_id not in self._paths:
            raise ValueError('CONTEXT_SOURCE_CAPABILITY_UNAVAILABLE')
        raw = self._paths[receipt_id].read_bytes()
        if hashlib.sha256(raw).hexdigest() != self._receipts[receipt_id].content_hash:
            raise ValueError('CONTEXT_ORIGINAL_SOURCE_SHA_MISMATCH')
        return raw

    def _resolver(self, bundle, cutoff):
        resolver = EvidenceResolver(tuple(bundle.receipts) + self.receipts,
            cutoff=stamp(cutoff), coverage_type=bundle.coverage_type,
            sessions=[str(day) for day in bundle.sessions],
            observation_date=str(bundle.observation_date), record_rows=False)
        if resolver.conflicts:
            raise ValueError('CONTEXT_SOURCE_RECEIPT_CONFLICT')
        return resolver

    def target_calendar_evidence(self, *, observation_date, target_session, cutoff, coverage_type=None):
        """Verify a supplied next session without extrapolating weekdays.

        A calendar received later than a replay cutoff is unavailable at that
        cutoff. Unknown evidence is retained separately from price selection.
        """
        from . import short_horizon_participation as r2
        observed, target = date.fromisoformat(str(observation_date)), date.fromisoformat(str(target_session))
        decision = stamp(cutoff)
        candidates = [row for row in self.receipts if row.dataset == 'calendar'
            and row.provider.lower() == 'baostock' and row.status == 'COMPLETE'
            and row.pagination_complete and max(row.source_published_at,
                row.first_seen_at, row.ingested_at) <= decision
            and str(row.parameters.get('start_date', '')) <= str(observed)
            and str(row.parameters.get('end_date', '')) > str(observed)]
        if not candidates:
            return {'schema_version': 'TARGET_SESSION_CALENDAR_CONTEXT_V1',
                'status': 'UNKNOWN', 'observation_date': str(observed),
                'requested_target_session': str(target), 'verified_next_session': None,
                'source_refs': [], 'reason_codes': ['NEXT_SESSION_CALENDAR_NOT_AVAILABLE_ASOF_CUTOFF']}
        def revision(row):
            return row.source_published_at, row.ingested_at, row.first_seen_at
        latest = max(revision(row) for row in candidates)
        tied = [row for row in candidates if revision(row) == latest]
        identities = {(row.content_hash, row.field_version,
            json.dumps(row.parameters, sort_keys=True)) for row in tied}
        if len(identities) != 1:
            raise ValueError('CONTEXT_CALENDAR_SAME_CLOCK_SOURCE_CONFLICT')
        parent = min(tied, key=lambda row: row.receipt_id)
        if (parent.field_version != 'BAOSTOCK_00.9.30_R03_V1' and not
                (coverage_type == 'TEST_FIXTURE' and parent.field_version == 'TEST_FIXTURE_CALENDAR_V1')):
            raise ValueError('CONTEXT_CALENDAR_FIELD_VERSION_UNRECOGNIZED')
        payload = r2._source_payload(parent, self)
        start = date.fromisoformat(str(parent.parameters['start_date']))
        end = date.fromisoformat(str(parent.parameters['end_date']))
        from datetime import datetime, time
        if parent.field_version == 'BAOSTOCK_00.9.30_R03_V1' and parent.event_time != datetime.combine(end, time(15), SH):
            raise ValueError('CONTEXT_CALENDAR_COVERAGE_END_EVENT_SEMANTICS_UNVERIFIED')
        days = {}
        for row in payload['rows']:
            day = date.fromisoformat(str(row.get('calendar_date')))
            flag = row.get('is_trading_day')
            if flag not in ('0', '1') or day in days:
                raise ValueError('CONTEXT_CALENDAR_DATED_ROWS_INVALID')
            days[day] = flag
        expected = {start + timedelta(days=index) for index in range((end-start).days+1)}
        if set(days) != expected or days.get(observed) != '1':
            raise ValueError('CONTEXT_CALENDAR_FULL_DATE_SCOPE_UNVERIFIED')
        next_day = min((day for day, flag in days.items() if day > observed and flag == '1'), default=None)
        if (next_day is not None and next_day != target) or (target in days and days[target] != '1'):
            raise ValueError('CONTEXT_TARGET_IS_NOT_VERIFIED_NEXT_TRADING_SESSION')
        return {'schema_version': 'TARGET_SESSION_CALENDAR_CONTEXT_V1',
            'status': 'AVAILABLE' if next_day else 'UNKNOWN', 'observation_date': str(observed),
            'requested_target_session': str(target), 'verified_next_session': str(next_day) if next_day else None,
            'source_refs': [parent.receipt_id], 'source_content_hash': parent.content_hash,
            'field_version': parent.field_version,
            'first_seen_at': parent.first_seen_at.isoformat(), 'ingested_at': parent.ingested_at.isoformat(),
            'original_event_time': parent.event_time.isoformat(),
            'original_source_published_at': parent.source_published_at.isoformat(),
            'reason_codes': [] if next_day else ['NEXT_SESSION_OUTSIDE_CAPTURED_CALENDAR'],
            'role': 'TARGET_DATE_CALENDAR_METADATA_NOT_FUTURE_PRICE_INPUT',
            'visibility_policy': 'CALENDAR_PREPUBLISHED_SCHEDULE_CAPTURE_CLOCKS_V1',
            'event_time_role': 'ORIGINAL_TYPED_CALENDAR_COVERAGE_END_NOT_PRICE_EVENT',
            'publication_clock_role': 'CAPTURE_VISIBILITY_ALIAS_NOT_INDEPENDENT_RELEASE_PROOF'}

    def validate_target_calendar_context(self, context, *, bundle, cutoff):
        expected = self.target_calendar_evidence(observation_date=bundle.observation_date,
            target_session=context['requested_target_session'], cutoff=cutoff,
            coverage_type=bundle.coverage_type)
        if context != expected:
            raise ValueError('CONTEXT_TARGET_CALENDAR_ORIGINAL_SOURCE_MISMATCH')

    def _session(self, resolver, day, return_basis=None):
        from . import short_horizon_participation as r2
        observation = r2._market_observation(resolver, day, self)
        market = r2._source_parent(resolver, day, 'market_daily')
        master = r2._source_parent(resolver, day, 'master_all')
        raw = r2._source_payload(market, self)['rows']
        rows, nontrading = [], []
        clocks = {key: stamp(value) for key, value in observation['clocks'].items()}
        for item in raw:
            iid = _iid(item['code'])
            if str(item['tradestatus']) == '0':
                nontrading.append(iid)
                continue
            # No adjusted/comparable prices are invented. The context's
            # explicit source reference-return basis uses close/preclose.
            bar = DailyBar(instrument_id=iid, session=date.fromisoformat(day),
                open=_decimal(item.get('open')), high=_decimal(item.get('high')),
                low=_decimal(item.get('low')), close=_decimal(item.get('close')),
                reference_prev_close=_decimal(item.get('preclose')),
                volume_shares=_decimal(item.get('volume')),
                amount_cny=_decimal(item.get('amount')),
                source_refs=(market.receipt_id, master.receipt_id), **clocks)
            if (any(getattr(bar, field) is None for field in
                    ('open', 'high', 'low', 'close', 'reference_prev_close'))
                    or not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high):
                raise ValueError('CONTEXT_RAW_PRICE_INTEGRITY_UNVERIFIED')
            rows.append(bar.model_dump(mode='json'))
        result = {'session': day, 'scope': MARKET_SCOPE,
            'expected_instrument_ids': sorted(_iid(item['code']) for item in raw),
            'rows': sorted(rows, key=lambda row: row['instrument_id']),
            'source_refs': observation['source_refs'], 'clocks': observation['clocks'],
            'status': 'COMPLETE', 'pagination_complete': True,
            'nontrading_instrument_ids': sorted(nontrading),
            'nontrading_policy': 'NONTRADING_EXCLUDED'}
        if return_basis is not None:
            if return_basis != RETURN_BASIS:
                raise ValueError('CONTEXT_RETURN_BASIS_UNRECOGNIZED')
            result['return_basis'] = return_basis
        return result

    def validate_theme_market_pack(self, pack, *, bundle, cutoff):
        """Reconstruct each panel from bytes; never trust its verified flags."""
        from .theme_evolution import ThemeMarketPack, ThemeMarketSession
        parsed = ThemeMarketPack.model_validate(pack)
        if (parsed.source_input_hash != digest_market_input(bundle)
                or parsed.observation_date != bundle.observation_date
                or stamp(parsed.decision_cutoff) != stamp(cutoff)
                or parsed.coverage_type != bundle.coverage_type):
            raise ValueError('CONTEXT_PACK_INPUT_BINDING_MISMATCH')
        for receipt in parsed.receipts:
            if self._receipts.get(receipt.receipt_id) != receipt:
                raise ValueError('CONTEXT_PACK_RECEIPT_NOT_AUTHORIZED')
        resolver = self._resolver(bundle, cutoff)
        calendar = {str(day) for day in bundle.sessions if day <= bundle.observation_date}
        refs = set()
        for panel in parsed.sessions:
            day = str(panel.session)
            if day not in calendar:
                raise ValueError('CONTEXT_PACK_SESSION_OUTSIDE_OBSERVED_CALENDAR')
            expected = ThemeMarketSession.model_validate(self._session(resolver, day,
                getattr(panel, 'return_basis', None))).model_dump(mode='json')
            actual = panel.model_dump(mode='json')
            # amount_cny is a display-only field; real total is recomputed.
            actual.pop('amount_cny', None)
            expected.pop('amount_cny', None)
            if actual != expected:
                raise ValueError('CONTEXT_PACK_PANEL_ORIGINAL_SOURCE_MISMATCH')
            refs.update(expected['source_refs'])
        available = {row.receipt_id for row in parsed.receipts}
        if not refs.issubset(available):
            raise ValueError('CONTEXT_PACK_PARENT_RECEIPT_MISSING')
        for theme in parsed.themes:
            self._validate_theme(theme, cutoff)

    def _validate_theme(self, theme, cutoff):
        from . import short_horizon_participation as r2
        parents = [self._receipts.get(ref) for ref in theme.source_refs]
        member_parents = [row for row in parents if row is not None and row.dataset in
            ('concept_members', 'industry_members', 'concept_members_sina')]
        if len(member_parents) != 1 or any(row is None for row in parents):
            raise ValueError('CONTEXT_THEME_MEMBERS_SOURCE_UNAVAILABLE')
        receipt = member_parents[0]
        for parent in parents:
            if max(parent.event_time, parent.source_published_at, parent.first_seen_at,
                   parent.ingested_at) > stamp(cutoff):
                raise ValueError('CONTEXT_THEME_CAPTURE_AFTER_CUTOFF')
        payload = r2._source_payload(receipt, self)
        capture_day = receipt.first_seen_at.astimezone(SH).date()
        if theme.effective_from != capture_day or theme.effective_to is not None:
            raise ValueError('CONTEXT_CURRENT_MEMBERS_CANNOT_BE_BACKDATED')
        code_rows = []
        for row in payload['rows']:
            value = row.get('代码', row.get('code'))
            if not isinstance(value, str) or len(value) != 6 or not value.isdigit():
                raise ValueError('CONTEXT_THEME_MEMBER_IDENTITY_UNVERIFIED')
            exchange = 'SH' if value.startswith(('60', '68')) else 'SZ' if value.startswith(('00', '30')) else None
            if exchange is None:
                raise ValueError('CONTEXT_THEME_MEMBER_OUTSIDE_SUPPORTED_MARKET')
            if receipt.dataset == 'concept_members_sina' and row.get('symbol') != exchange.lower() + value:
                raise ValueError('CONTEXT_THEME_MEMBER_SOURCE_EXCHANGE_CONFLICT')
            code_rows.append(value + '.' + exchange)
        if len(set(code_rows)) != len(code_rows) or set(theme.member_ids) != set(code_rows):
            raise ValueError('CONTEXT_THEME_ORIGINAL_MEMBERS_MISMATCH')
        for key in ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at'):
            if getattr(theme, key) != max(getattr(parent, key) for parent in parents):
                raise ValueError('CONTEXT_THEME_PARENT_CLOCK_MISMATCH')
        symbol = str(receipt.parameters.get('symbol') or '')
        if receipt.dataset == 'concept_members_sina':
            from .provider_normalization import _theme_type
            directories = [row for row in parents if row.dataset == 'concept_list_sina']
            if len(directories) != 1 or len(parents) != 2:
                raise ValueError('CONTEXT_THEME_ORIGINAL_DIRECTORY_PARENT_REQUIRED')
            directory = r2._source_payload(directories[0], self)
            named = [row for row in directory['rows'] if row.get('label') == symbol]
            if len(named) != 1 or named[0].get('板块') != theme.name:
                raise ValueError('CONTEXT_THEME_DIRECTORY_NAME_MISMATCH')
            if (theme.theme_id != 'concept_list_sina:' + symbol
                    or theme.theme_type != _theme_type(theme.name, 'concept')
                    or theme.definition_version not in
                    ('CURRENT_SINA_DIRECTORY_COUNT_AND_ORIGINAL_MEMBERS_V1',
                     'PROVIDER_CONCEPT_TAXONOMY_EXCLUDE_QUALIFICATIONS_R03_V1')):
                raise ValueError('CONTEXT_THEME_TAXONOMY_IDENTITY_MISMATCH')
            if int(named[0]['公司家数']) != len(code_rows):
                raise ValueError('CONTEXT_THEME_DIRECTORY_MEMBER_COUNT_MISMATCH')
        elif not symbol or theme.name != symbol or len(parents) != 1:
            raise ValueError('CONTEXT_THEME_SOURCE_NAME_MISMATCH')

    def build_market_pack(self, bundle, *, cutoff, sessions, themes=(), return_basis=None):
        resolver = self._resolver(bundle, cutoff)
        panels = [self._session(resolver, str(day), return_basis) for day in sessions]
        refs = {ref for panel in panels for ref in panel['source_refs']}
        parsed_themes = [ThemeSnapshot.model_validate(row) for row in themes]
        for theme in parsed_themes:
            self._validate_theme(theme, cutoff)
            refs.update(theme.source_refs)
        pack = {'schema_version': 'THEME_MARKET_PACK_V1',
            'source_input_hash': digest_market_input(bundle),
            'observation_date': str(bundle.observation_date),
            'decision_cutoff': stamp(cutoff).isoformat(), 'coverage_type': bundle.coverage_type,
            'receipts': [self._receipts[ref].model_dump(mode='json') for ref in sorted(refs)],
            'sessions': panels, 'themes': [row.model_dump(mode='json') for row in parsed_themes]}
        self.validate_theme_market_pack(pack, bundle=bundle, cutoff=cutoff)
        return pack

    def build_current_theme_context(self, *, cutoff):
        """Current Sina cohorts with original directory/count proof and gaps.

        Only the membership fields are consumed; live/undated quote values in
        the directory or constituent response never become historical prices.
        """
        from . import short_horizon_participation as r2
        from .provider_normalization import _theme_type
        decision = stamp(cutoff)
        def visible(row):
            return (row.status == 'COMPLETE' and row.pagination_complete and
                max(row.event_time, row.source_published_at, row.first_seen_at, row.ingested_at) <= decision)
        directories = [row for row in self.receipts if row.dataset == 'concept_list_sina' and visible(row)]
        if not directories:
            return {'themes': [], 'status': 'UNKNOWN', 'expected_concept_count': None,
                'unknown': [{'reason': 'CURRENT_CONCEPT_DIRECTORY_UNAVAILABLE'}]}
        parent = max(directories, key=lambda row: (row.ingested_at, row.first_seen_at, row.receipt_id))
        directory = r2._source_payload(parent, self)
        themes, unknown, excluded = [], [], []
        for entry in directory['rows']:
            symbol, name = entry.get('label'), entry.get('板块')
            candidates = [row for row in self.receipts if row.dataset == 'concept_members_sina'
                and row.parameters.get('symbol') == symbol and visible(row)]
            if not candidates:
                unknown.append({'symbol': symbol, 'name': name, 'reason': 'CURRENT_COMPLETE_MEMBERS_UNAVAILABLE'})
                continue
            member = max(candidates, key=lambda row: (row.ingested_at, row.first_seen_at, row.receipt_id))
            try:
                payload = r2._source_payload(member, self)
                members = []
                for row in payload['rows']:
                    code = row.get('code')
                    market = str(row.get('symbol') or '')[:2]
                    if market not in ('sh', 'sz') or not isinstance(code, str) or len(code) != 6 or not code.isdigit():
                        raise ValueError('CURRENT_THEME_MEMBER_IDENTITY_UNVERIFIED')
                    members.append(code + '.' + market.upper())
                clocks = {key: max(getattr(member, key), getattr(parent, key)) for key in
                          ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')}
                theme = ThemeSnapshot(theme_id='concept_list_sina:' + str(symbol), name=name,
                    theme_type=_theme_type(name, 'concept'), member_ids=tuple(sorted(members)),
                    effective_from=member.first_seen_at.astimezone(SH).date(),
                    definition_version='CURRENT_SINA_DIRECTORY_COUNT_AND_ORIGINAL_MEMBERS_V1',
                    source_refs=(member.receipt_id, parent.receipt_id), **clocks)
                self._validate_theme(theme, decision)
                if theme.theme_type == 'QUALIFICATION':
                    excluded.append({'symbol': symbol, 'name': name, 'reason': 'QUALIFICATION_CATEGORY_NOT_THEME'})
                else:
                    themes.append(theme.model_dump(mode='json'))
            except (ValueError, TypeError, KeyError) as error:
                unknown.append({'symbol': symbol, 'name': name, 'reason': str(error)})
        return {'themes': themes, 'status': 'PARTIAL' if unknown else 'AVAILABLE',
            'expected_concept_count': len(directory['rows']), 'verified_theme_count': len(themes),
            'qualification_categories': excluded, 'unknown': unknown,
            'data_role': 'CURRENT_COHORT_BACKVIEW_ASOF_DECISION',
            'historical_membership_verified': False, 'directory_source_ref': parent.receipt_id}


def run_saved_context(*, input_path, base_run_path, provenance_path,
                      source_receipts_path, source_map_path, output, cutoff,
                      target_session, theme_market_pack_path=None,
                      membership_mode='DATED_ONLY'):
    """Call the native strategy and exporters over an immutable saved R1 run.

    This is a source adapter, not a second ranker or business database. It
    preserves the full official producer document, verifies its bytes before
    deriving the new context run, and saves a new private artifact directory.
    """
    from datetime import datetime, time, timezone
    from .candidate_service import CandidateService
    from .contracts import MarketInput
    from .history_artifacts import digest_json_stream
    from .strategy_experiments import run_short_horizon_strategy

    def read_json(path):
        raw = Path(path).read_bytes()
        if len(raw) > 256 * 1024 * 1024:
            raise ValueError('CONTEXT_ARTIFACT_EXCEEDS_EXISTING_READ_LIMIT')
        return raw, json.loads(raw)

    destination = Path(output)
    if destination.exists():
        raise ValueError('CONTEXT_OUTPUT_MUST_BE_NEW_PRIVATE_DIRECTORY')
    _, document = read_json(input_path)
    bundle = MarketInput.model_validate(document.get('input', document))
    target = date.fromisoformat(str(target_session))
    decision = stamp(cutoff)
    if target <= bundle.observation_date or decision >= datetime.combine(target, time(9, 25), SH):
        raise ValueError('CONTEXT_PREMARKET_DATE_OR_CUTOFF_INVALID')
    if decision > datetime.now(timezone.utc):
        raise ValueError('CONTEXT_FUTURE_DECISION_CUTOFF_NOT_ALLOWED')
    raw, base = read_json(base_run_path)
    _, proof = read_json(provenance_path)
    if hashlib.sha256(raw).hexdigest() != proof.get('base_run_sha256'):
        raise ValueError('CONTEXT_BASE_ORIGINAL_FILE_SHA_MISMATCH')
    if digest_json_stream(base) != proof.get('base_run_content_hash'):
        raise ValueError('CONTEXT_BASE_CANONICAL_CONTENT_MISMATCH')
    _, receipt_document = read_json(source_receipts_path)
    _, map_document = read_json(source_map_path)
    reader = SourceArtifactReader(receipt_document.get('source_receipts', receipt_document),
                                  map_document.get('mapping', map_document))
    target_calendar = reader.target_calendar_evidence(observation_date=bundle.observation_date,
        target_session=target_session, cutoff=cutoff, coverage_type=bundle.coverage_type)
    pack = read_json(theme_market_pack_path)[1] if theme_market_pack_path else None
    if pack is not None and pack.get('schema_version') == 'THEME_MARKET_PACK_REQUEST_V1':
        if set(pack) - {'schema_version', 'sessions', 'themes', 'return_basis'}:
            raise ValueError('CONTEXT_PACK_REQUEST_UNRECOGNIZED_FIELD')
        pack = reader.build_market_pack(bundle, cutoff=cutoff, sessions=pack['sessions'],
            themes=pack.get('themes', ()), return_basis=pack.get('return_basis'))
    result = run_short_horizon_strategy(bundle, cutoff=cutoff, input_id=proof['input_id'],
        strategy_id='SHORT_HORIZON_STRENGTH_R2', _frozen_base_run=base,
        _base_provenance=proof, _source_payload_reader=reader,
        _target_calendar_context=target_calendar,
        _target_calendar_validator=reader.validate_target_calendar_context,
        _theme_market_pack=pack, _theme_market_pack_validator=reader.validate_theme_market_pack,
        _theme_membership_mode=membership_mode)
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    result['target_session'] = str(target_session)
    result['target_calendar_evidence'] = target_calendar
    result['prospective_validation']['target_session'] = str(target_session)
    result['artifact_role'] = 'NATIVE_READ_ONLY_RESEARCH_ARTIFACT_NOT_BUSINESS_DB_REGISTRATION'
    result['source_database_written'] = False
    result['content_hash'] = digest_json_stream(result)

    class ArchivedReader(CandidateService):
        def strategy_experiment(self, run_id):
            if run_id != result['run_id']:
                raise ValueError('CONTEXT_SAVED_RUN_ID_MISMATCH')
            return result

    service = object.__new__(ArchivedReader)
    destination.mkdir(parents=True, exist_ok=False)
    files = {}
    def save(name, data):
        encoded = data.encode('utf-8')
        (destination / name).write_bytes(encoded)
        files[name] = {'sha256': hashlib.sha256(encoded).hexdigest(), 'bytes': len(encoded)}

    save('official-result.json', json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    for selection in ('observation', 'all', 'focus', 'unselected'):
        save(selection + '.csv', service.export_strategy_experiment_csv(result['run_id'],
             strategy_id='SHORT_HORIZON_STRENGTH_R2', selection=selection))
    save('observation.json', service.export_strategy_experiment_json(result['run_id'],
         strategy_id='SHORT_HORIZON_STRENGTH_R2', selection='observation'))
    summary = {'run_id': result['run_id'], 'observation_date': result['observation_date'],
        'target_session': result['target_session'], 'decision_cutoff': result['decision_cutoff'],
        'counts': result['results']['SHORT_HORIZON_STRENGTH_R2']['counts'],
        'input_hash': result['input_hash'], 'source_hash': result['source_hash'],
        'research_context_hash': result['research_context_hash'],
        'target_calendar_evidence': target_calendar,
        'market_turnover_context_status': result['results']['SHORT_HORIZON_STRENGTH_R2']['market_turnover_context']['status'],
        'files': files, 'source_database_written': False,
        'original_history_database_reverified': False, 'output_directory': str(destination.resolve())}
    (destination / 'manifest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary
