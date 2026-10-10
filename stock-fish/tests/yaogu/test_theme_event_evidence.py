"""Isolated native TEST_FIXTURE parents; reported news is not investment proof."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from stockfish_core.yaogu import theme_event_evidence as events
from stockfish_core.yaogu.contracts import SourceReceipt
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader
from tests.yaogu.test_theme_observation_diagnostics import native_case


class NativeScope:
    def __init__(self, case):
        assert case.bundle.coverage_type == 'TEST_FIXTURE'
        self.case = case
        self.parent_hash = digest_json_stream(case.stock_parent)
        self.qualified = tuple(sorted(case.stock_parent['eligible_pool_ids']))

    def verify_theme(self, parent, *, bundle, cutoff):
        self.case.verify_context(parent, bundle=bundle, cutoff=cutoff)

    def verify_selection(self, parent, *, selected_ids, qualified_ids, bundle, cutoff):
        self.case.verify_selection(selected_ids, bundle=bundle, cutoff=cutoff)
        if (parent is not self.case.stock_parent or digest_json_stream(parent) != self.parent_hash
                or tuple(qualified_ids) != self.qualified):
            raise ValueError('TEST_FIXTURE_SELECTION_PARENT_OR_QUALIFICATION_NOT_BOUND')


@pytest.fixture(scope='module')
def native():
    case = native_case('overlap')
    return case, NativeScope(case)


def source(tmp_path, case, body, *, name='A', publication=None, parser='TEST_FIXTURE_JSON_V1',
           raw=None, url=None, received=None, status='COMPLETE', source_label='TEST_FIXTURE_MEDIA_A'):
    publication = publication or case.cutoff.strftime('%Y-%m-%d') + ' 19:00'
    if raw is None:
        raw = json.dumps({'title': 'TEST_FIXTURE ARTICLE', 'body': body, 'publisher': 'TEST_FIXTURE',
            'source': source_label, 'author': 'TEST_FIXTURE AUTHOR', 'publication': publication}, ensure_ascii=False).encode()
    received = received or case.cutoff - timedelta(minutes=5)
    path = tmp_path / (name + '.raw')
    path.write_bytes(raw)
    receipt = SourceReceipt(receipt_id='TEST_FIXTURE_ARTICLE_' + name, provider='TEST_FIXTURE',
        dataset='REPORTED_EVENT_ARTICLE', content_hash=hashlib.sha256(raw).hexdigest(), field_version=events.FEATURE_VERSION,
        row_count=1, pagination_complete=True, status=status,
        event_time=case.cutoff - timedelta(hours=1), source_published_at=case.cutoff - timedelta(hours=1),
        first_seen_at=received, ingested_at=received + timedelta(seconds=1),
        source_url=url or 'https://fixture.invalid/' + name)
    artifact = events.ArticleArtifact(receipt_id=receipt.receipt_id, parser=parser,
        request_started_at=received - timedelta(seconds=1), actual_received_at=received,
        ingested_at=receipt.ingested_at)
    return receipt, artifact, path


def claim(case, receipt, text, *, claim_id='C1', theme_ids=None, **kwargs):
    return events.EventClaim(claim_id=claim_id, receipt_id=receipt.receipt_id,
        kind='LADDER_REPORTED', theme_ids=theme_ids if theme_ids is not None else ('TEST_FIXTURE_ACTIVE_CONT',),
        event_date=case.bundle.observation_date, evidence_text=text, **kwargs)


def build(native, sources, claims=(), **kwargs):
    case, scope = native
    reader = SourceArtifactReader([row[0].model_dump(mode='json') for row in sources],
        {row[0].receipt_id: str(row[2]) for row in sources})
    arguments = dict(cutoff=case.cutoff, selected_ids=case.selected_ids, qualified_ids=scope.qualified,
        artifact_reader=reader, artifacts=[row[1] for row in sources], claims=claims,
        _theme_parent_validator=scope.verify_theme, _selection_parent_validator=scope.verify_selection)
    arguments.update(kwargs)
    return events.build_theme_event_context(case.bundle, case.context, case.stock_parent, **arguments)


def test_true_native_parents_original_bytes_and_unchanged_selection(tmp_path, native):
    case, scope = native
    before = digest_json_stream(case.stock_parent), digest_json_stream(case.context)
    s = source(tmp_path, case, '今日59股涨停。')
    result = build(native, [s], [claim(case, s[0], '今日59股涨停。', metric='LIMIT_UP_COUNT', snapshot='CLOSE')])
    assert result['items'][0]['status'] == 'AUTHENTICATED_REPORTED_CLAIM'
    assert result['items'][0]['reported_metric']['value'] == {'numerator': 59, 'denominator': 1}
    assert result['original_artifacts'][0]['raw_sha256'] == hashlib.sha256(s[2].read_bytes()).hexdigest()
    assert result['frozen_selected_ids'] == list(case.selected_ids)
    assert result['frozen_qualified_ids'] == list(scope.qualified)
    assert (digest_json_stream(case.stock_parent), digest_json_stream(case.context)) == before
    assert result['official_pit_validation_countable'] is False and result['formal_focus'] is False
    assert result['default_strategy_or_service_integration'] == 'NOT_CONNECTED'


def test_april13_arithmetic_conflict_is_not_a_repaired_market_value(tmp_path, native):
    case, _ = native
    line = '今日59股涨停，19股炸板，封板率为80%。'
    s = source(tmp_path, case, line)
    claims = [claim(case, s[0], line, claim_id=metric, metric=metric, declared_population='UNVERIFIED_SAME_POOL', snapshot='CLOSE')
              for metric in ('LIMIT_UP_COUNT', 'FAILED_SEAL_COUNT', 'SEALING_RATE_PERCENT')]
    result = build(native, [s], claims)
    arithmetic = result['conflicts'][0]
    assert arithmetic['type'] == 'DECLARED_SAME_POOL_SEAL_RATE_ARITHMETIC_CONFLICT'
    assert arithmetic['population_verified'] is False and arithmetic['resolved_market_value'] is None
    assert [row['reported_metric']['value']['numerator'] for row in result['items']] == [59, 19, 80]


@pytest.mark.parametrize('line,conflict', [('79股涨停，23股炸板，封板率为77%。', False),
                                         ('79股涨停，23股炸板，封板率为77.0%。', True)])
def test_sealing_rate_uses_actual_printed_decimal_precision(tmp_path, native, line, conflict):
    case, _ = native
    s = source(tmp_path, case, line)
    rows = [claim(case, s[0], line, claim_id=metric, metric=metric, declared_population='DECLARED')
            for metric in ('LIMIT_UP_COUNT', 'FAILED_SEAL_COUNT', 'SEALING_RATE_PERCENT')]
    assert bool(build(native, [s], rows)['conflicts']) is conflict


def test_april20_75_vs90_not_relabelled_as_touch_count(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '今日75股涨停；其中90只个股涨停。')
    rows = [claim(case, s[0], span, claim_id=str(i), metric='LIMIT_UP_COUNT', snapshot='CLOSE', comparison_key='APR20_COUNT')
            for i, span in enumerate(('75股涨停', '90只个股涨停'))]
    result = build(native, [s], rows)
    assert result['conflicts'][0]['type'] == 'REPORTED_VALUE_CONFLICT_OR_SCOPE_UNKNOWN'
    assert result['conflicts'][0]['resolved_value'] is None
    assert result['conflicts'][0]['averaging_or_scope_relabel_allowed'] is False


def test_midday_eight_and_close_seven_are_separate_paths(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '盘中连续第8日涨停。尾盘炸板止步7连板。')
    rows = [claim(case, s[0], '连续第8日涨停', claim_id='AM', metric='CONSECUTIVE_BOARD_COUNT', snapshot='INTRADAY', comparison_key='LADDER'),
            claim(case, s[0], '尾盘炸板止步7连板', claim_id='CLOSE', metric='CONSECUTIVE_BOARD_COUNT', snapshot='CLOSE', comparison_key='LADDER')]
    result = build(native, [s], rows)
    assert [row['type'] for row in result['conflicts']] == ['DIFFERENT_SNAPSHOT_PATH_REQUIRES_SEPARATE_CLOCKS']
    assert result['conflicts'][0]['same_clock_contradiction_proven'] is False
    assert all(row['strict_limit_event_verified'] is False for row in result['items'])


def test_continuous_vs_x_days_y_boards_not_same_metric(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '3连板；7天4板。')
    rows = [claim(case, s[0], '3连板', claim_id='ONE', metric='CONSECUTIVE_BOARD_COUNT', comparison_key='SAME_NAME'),
            claim(case, s[0], '7天4板', claim_id='TWO', metric='WINDOW_BOARD_COUNT', comparison_key='SAME_NAME')]
    result = build(native, [s], rows)
    assert result['conflicts'] == []
    assert result['items'][1]['reported_metric']['window_sessions_reported'] == 7


def test_token_money_unit_conflict_never_becomes_amount(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '日均Token调用量突破140万亿元。')
    result = build(native, [s], [claim(case, s[0], '日均Token调用量突破140万亿元', metric='TOKEN_CALLS')])
    row = result['items'][0]
    assert row['status'] == 'UNKNOWN' and 'EVENT_TOKEN_MONETARY_UNIT_CONFLICT' in row['reason_codes']
    assert row['reported_metric']['unit'] == 'CNY'
    assert row['reported_metric']['market_numeric_authority'] is False


def test_valid_token_number_is_exact_non_monetary(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '日均词元调用量突破140万亿。')
    row = build(native, [s], [claim(case, s[0], '日均词元调用量突破140万亿', metric='TOKEN_CALLS')])['items'][0]
    assert row['reported_metric']['value'] == {'numerator': 140000000000000, 'denominator': 1}
    assert row['reported_metric']['unit'] == 'TOKEN' and row['numeric_market_authority'] is False


def test_exact_price_range_conflict_not_averaged(tmp_path, native):
    case, _ = native
    a = source(tmp_path, case, '涨幅2%-7%。', name='A')
    b = source(tmp_path, case, '涨幅2%—5%。', name='B', source_label='TEST_FIXTURE_MEDIA_B')
    rows = [claim(case, s[0], span, claim_id=str(i), metric='PRICE_CHANGE_RANGE_PERCENT', comparison_key='NOTICE', snapshot='ANNOUNCEMENT')
            for i, (s, span) in enumerate(((a, '2%-7%'), (b, '2%—5%')))]
    result = build(native, [a, b], rows)
    assert result['conflicts'][0]['resolved_value'] is None
    assert result['reported_origin_family_count'] == 2
    assert result['origin_count_is_not_independent_catalyst_or_market_data_count'] is True


def stcn_raw(case, text, source_label='证券时报网'):
    clock = case.cutoff.strftime('%Y-%m-%d') + ' 19:00'
    return (f'<div class="detail-title">TEST_FIXTURE报道</div><div class="detail-info"><span>来源：{source_label}</span>'
            f'<span>作者：TEST_FIXTURE记者</span><span>{clock}</span></div>'
            f'<div class="detail-content"><p>{text}</p><script>99股涨停</script></div>').encode()


def test_stcn_reprint_cls_and_cls_original_share_one_origin(tmp_path, native):
    case, _ = native
    a = source(tmp_path, case, '', name='S', parser='STCN_HTML_V1', raw=stcn_raw(case, '59股涨停。', '财联社'),
               url='https://www.stcn.com/article/detail/123.html')
    article = {'id': 456, 'title': 'TEST_FIXTURE报道', 'content': '<p>59股涨停。</p>',
               'author': {'name': '财联社 TEST_FIXTURE记者'}, 'ctime': int((case.cutoff - timedelta(hours=1)).timestamp())}
    raw = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps({'props': {'pageProps': {'articleDetail': article}}}) + '</script>').encode()
    b = source(tmp_path, case, '', name='C', parser='CLS_NEXT_DATA_V1', raw=raw, url='https://www.cls.cn/detail/456')
    rows = [claim(case, s[0], '59股涨停。', claim_id=str(i), metric='LIMIT_UP_COUNT') for i, s in enumerate((a, b))]
    result = build(native, [a, b], rows)
    assert result['reported_origin_family_count'] == 1
    assert {r['origin_family'] for r in result['items']} == {'CLS'}
    assert result['original_artifacts'][1]['originality'] == 'REPRINT_ONE_UPSTREAM_FAMILY'


def test_scripts_are_not_article_evidence(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '', parser='STCN_HTML_V1', raw=stcn_raw(case, '59股涨停。'), url='https://www.stcn.com/article/detail/1.html')
    with pytest.raises(ValueError, match='SPAN_NOT_IN_AUTHENTICATED_BODY'):
        build(native, [s], [claim(case, s[0], '99股涨停', metric='LIMIT_UP_COUNT')])


def test_date_only_interval_is_not_midnight_publication(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '59股涨停。', publication=str(case.bundle.observation_date))
    result = build(native, [s], [claim(case, s[0], '59股涨停。', metric='LIMIT_UP_COUNT')])
    meta = result['original_artifacts'][0]
    assert meta['publication_precision'] == 'DAY_HALF_OPEN_INTERVAL'
    assert meta['publication_bounds_role'] == 'BOUNDS_NOT_FILLED_PRECISE_PUBLICATION_CLOCK'
    assert meta['status'] == 'UNKNOWN'
    assert 'EVENT_PUBLICATION_INTERVAL_NOT_FULLY_BEFORE_CUTOFF' in meta['reason_codes']
    assert result['source_refs'] == []


def test_date_only_upper_bound_equal_cutoff_is_conservatively_usable(tmp_path, native):
    case, _ = native
    day = str(case.bundle.observation_date)
    s = source(tmp_path, case, '报告。', publication=day)
    reader = SourceArtifactReader([s[0]], {s[0].receipt_id: str(s[2])})
    _, meta = events._original(s[1], reader, case.cutoff.replace(hour=0, minute=0) + timedelta(days=1), 'TEST_FIXTURE')
    assert meta['status'] == 'AVAILABLE_REPORTED_SOURCE'
    assert meta['publication_upper'].startswith(str(case.bundle.observation_date + timedelta(days=1)))


@pytest.mark.parametrize('coverage', ['TEST_FIXTURE', 'RECONSTRUCTED_HISTORICAL'])
def test_late_capture_never_reconstructed_into_old_cutoff(tmp_path, native, coverage):
    case, _ = native
    s = source(tmp_path, case, '', parser='STCN_HTML_V1', raw=stcn_raw(case, '59股涨停。'),
               url='https://www.stcn.com/article/detail/1.html', received=case.cutoff + timedelta(minutes=1))
    proof = dict(policy_id='TEST_FIXTURE_ONLY', source_url=s[0].source_url, evidence_hash='a' * 64,
        event_time_basis='SOURCE_DATED_EVENT_RECORD', publication_time_basis='SOURCE_PUBLICATION_TIMESTAMP',
        applies_from=case.bundle.observation_date, applies_to=case.bundle.observation_date,
        evidence_refs=['TEST_FIXTURE_NOT_REAL_PROOF'], verified_at=case.cutoff)
    r = SourceReceipt.model_validate({**s[0].model_dump(mode='json'), 'historical_time_proof': proof})
    reader = SourceArtifactReader([r], {r.receipt_id: str(s[2])})
    _, meta = events._original(s[1], reader, case.cutoff, coverage)
    assert 'EVENT_ACTUAL_CAPTURE_OR_RECEIPT_CLOCK_AFTER_CUTOFF' in meta['reason_codes']
    assert meta['status'] == 'UNKNOWN' and meta['official_pit_validation_countable'] is False


@pytest.mark.parametrize('status', ['PARTIAL', 'FETCH_FAILED', 'NOT_PUBLISHED'])
def test_incomplete_or_failed_original_cannot_supply_claim(tmp_path, native, status):
    case, _ = native
    s = source(tmp_path, case, '59股涨停。', status=status)
    row = build(native, [s], [claim(case, s[0], '59股涨停。', metric='LIMIT_UP_COUNT')])['items'][0]
    assert row['status'] == 'UNKNOWN' and row['reported_metric'] is None


def test_unknown_parser_is_visible_unknown_not_guessed_nbd_clock(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '20:45:33与21:54:37', parser='NBD_UNIMPLEMENTED')
    row = build(native, [s], [claim(case, s[0], '20:45:33与21:54:37')])['items'][0]
    assert 'EVENT_ARTICLE_PARSER_UNSUPPORTED' in row['reason_codes']
    assert row['locator'] is None and row['reported_metric'] is None


def test_raw_tamper_and_capture_clock_mismatch_are_hard_failures(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '59股涨停。')
    s[2].write_bytes(b'TEST_FIXTURE_TAMPER')
    with pytest.raises(ValueError, match='SOURCE_SHA_MISMATCH'):
        build(native, [s])
    t = source(tmp_path, case, '59股涨停。', name='B')
    bad = t[1].model_copy(update={'actual_received_at': t[1].actual_received_at - timedelta(seconds=1)})
    with pytest.raises(ValueError, match='CAPTURE_RECEIPT_CLOCK_MISMATCH'):
        build(native, [(t[0], bad, t[2])])


def test_same_receipt_different_hash_and_authenticated_boolean_rejected(tmp_path, native):
    case, _ = native
    a = source(tmp_path, case, '59股涨停。')
    b = a[0].model_copy(update={'content_hash': 'f' * 64})
    with pytest.raises(ValueError, match='RECEIPT_ID_CONFLICT'):
        SourceArtifactReader([a[0], b], {a[0].receipt_id: str(a[2])})
    with pytest.raises(ValueError, match='Extra inputs'):
        events.ArticleArtifact.model_validate({**a[1].model_dump(mode='json'), 'authenticated': True})


def test_fake_parent_hash_different_selected_or_qualified_and_lambda_rejected(tmp_path, native):
    case, scope = native
    s = source(tmp_path, case, '59股涨停。')
    with pytest.raises(ValueError, match='BOUND_PARENT_AND_SELECTION'):
        build(native, [s], _theme_parent_validator=lambda *a, **k: True)
    with pytest.raises(ValueError, match='SELECTION_OR_QUALIFICATION'):
        build(native, [s], selected_ids=scope.qualified)
    with pytest.raises(ValueError, match='SELECTION_OR_QUALIFICATION'):
        build(native, [s], qualified_ids=case.selected_ids)
    fake = deepcopy(case.context)
    fake['status'] = 'TEST_FIXTURE_FAKE'
    fake['context_content_hash'] = digest_json_stream({k: v for k, v in fake.items() if k != 'context_content_hash'})
    reader = SourceArtifactReader([s[0]], {s[0].receipt_id: str(s[2])})
    with pytest.raises(ValueError, match='NATIVE_THEME_PARENT_NOT_ORIGINAL'):
        events.build_theme_event_context(case.bundle, fake, case.stock_parent, cutoff=case.cutoff,
            selected_ids=case.selected_ids, qualified_ids=scope.qualified, artifact_reader=reader, artifacts=[s[1]], claims=[],
            _theme_parent_validator=scope.verify_theme, _selection_parent_validator=scope.verify_selection)


def test_bound_boolean_authority_is_not_native_parent_authentication(tmp_path, native):
    case, _ = native
    class UnboundAuthority:
        def verify(self, *args, **kwargs):
            return True
    s = source(tmp_path, case, '59股涨停。')
    with pytest.raises(ValueError, match='THEME_PARENT_AUTHENTICATION_FAILED'):
        build(native, [s], _theme_parent_validator=UnboundAuthority().verify)


def test_names_change_or_outside_selection_never_associated_from_memory(tmp_path, native):
    case, _ = native
    iid = case.selected_ids[0]
    name = next(s.name for s in case.bundle.universe.securities if s.instrument_id == iid)
    body = name + '涨停；TEST_FIXTURE旧名涨停。'
    s = source(tmp_path, case, body)
    c = claim(case, s[0], body, securities=[dict(reported_name=name, instrument_id=iid),
                                         dict(reported_name='TEST_FIXTURE旧名', instrument_id=iid)])
    row = build(native, [s], [c])['items'][0]
    assert row['resolved_selected_ids'] == [iid]
    assert row['unresolved_securities'][0]['identity_status'] == 'UNKNOWN'
    assert row['historical_qualification_verified'] is False


def test_shared_stock_in_two_topics_not_independent_synergy(tmp_path, native):
    case, _ = native
    iid = case.selected_ids[0]
    name = next(s.name for s in case.bundle.universe.securities if s.instrument_id == iid)
    s = source(tmp_path, case, name + '涨停。')
    rows = [claim(case, s[0], name + '涨停。', claim_id=str(i), theme_ids=(theme,),
                  securities=[dict(reported_name=name, instrument_id=iid)])
            for i, theme in enumerate(('TEST_FIXTURE_ACTIVE_CONT', 'TEST_FIXTURE_OVERLAP'))]
    result = build(native, [s], rows)
    overlap = result['cross_theme_shared_reported_names'][0]
    assert overlap['resolved_selected_ids'] == [iid] and overlap['independent_synergy_countable'] is False
    assert overlap['complete_dated_membership_status'] == 'UNKNOWN'


def test_missing_numeric_span_unknown_not_zero_and_no_news_not_no_catalyst(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '未提供涨停股数。')
    row = build(native, [s], [claim(case, s[0], '未提供涨停股数。', metric='LIMIT_UP_COUNT')])['items'][0]
    assert row['reported_metric'] is None and row['status'] == 'UNKNOWN'
    empty = build(native, [])
    assert empty['status'] == 'UNKNOWN'
    assert empty['reason_codes'] == ['EVENT_CHANNEL_HAS_NO_ARTICLES_NOT_NO_CATALYST']


def test_annotation_retains_native_groups_and_requires_producer_capability(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '59股涨停。')
    context = build(native, [s], [claim(case, s[0], '59股涨停。', metric='LIMIT_UP_COUNT')])
    diagnostic = case.report()
    before = digest_json_stream(diagnostic)
    result = events.annotate_theme_observation_diagnostics(diagnostic, context)
    assert result['native_diagnostics'] == diagnostic
    assert digest_json_stream(diagnostic) == before and result['news_created_mainlines'] is False
    assert result['display_claim_ids'] == ['C1']
    with pytest.raises(ValueError, match='NATIVE_PRODUCER_ATTACHMENT_CAPABILITY_REQUIRED'):
        events.annotate_theme_observation_diagnostics(diagnostic, dict(context))
    context['frozen_selected_ids'] = []
    with pytest.raises(ValueError, match='CHANGED_AFTER_AUTHENTICATION'):
        events.annotate_theme_observation_diagnostics(diagnostic, context)


def test_unsupported_theme_does_not_create_mainline_and_raw_tamper_after_build_fails(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '59股涨停。')
    context = build(native, [s], [claim(case, s[0], '59股涨停。', theme_ids=('TEST_FIXTURE_NOT_EXISTENT',))])
    result = events.annotate_theme_observation_diagnostics(case.report(), context)
    assert result['display_claim_ids'] == []
    assert context['items'][0]['status'] == 'UNKNOWN'
    s[2].write_bytes(b'TEST_FIXTURE_TAMPER_AFTER_BUILD')
    with pytest.raises(ValueError, match='SOURCE_SHA_MISMATCH'):
        events.annotate_theme_observation_diagnostics(case.report(), context)


def test_original_domain_fake_json_parser_real_coverage_and_naive_clocks_rejected(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '', parser='STCN_HTML_V1', raw=stcn_raw(case, '59股涨停。'), url='https://fixture.invalid/1')
    with pytest.raises(ValueError, match='PARSER_DOMAIN_MISMATCH'):
        build(native, [s])
    t = source(tmp_path, case, '59股涨停。', name='B')
    reader = SourceArtifactReader([t[0]], {t[0].receipt_id: str(t[2])})
    with pytest.raises(ValueError, match='FIXTURE_PARSER_FORBIDDEN'):
        events._original(t[1], reader, case.cutoff, 'PROSPECTIVE_CAPTURED')
    value = t[1].model_dump(mode='json')
    value['request_started_at'] = '2026-05-29T19:00:00'
    with pytest.raises(ValueError, match='timezone'):
        events.ArticleArtifact.model_validate(value)


def test_source_identity_and_all_manifest_inputs_bound(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '59股涨停。')
    result = build(native, [s], [claim(case, s[0], '59股涨停。', metric='LIMIT_UP_COUNT')])
    assert result['source_hash'] == events.source_identity()['source_hash']
    assert result['source_input_hash'] == digest_market_input(case.bundle)
    assert result['native_selection_parent_hash'] == digest_json_stream(case.stock_parent)
    assert result['artifact_manifest_hash'] and result['claim_manifest_hash']


def test_cnstock_paper_and_next_data_share_one_house_without_nav_evidence(tmp_path, native):
    case, _ = native
    day = str(case.bundle.observation_date - timedelta(days=1))
    paper = (f'<title>TEST_FIXTURE上证报|上海证券报</title><div class="time-from"><span>{day}</span>'
        '<span>来源：上海证券报</span></div><div class="content_article"><p>记者 TEST_FIXTURE记者</p>'
        '<p>59股涨停。</p></div><div>90只个股涨停</div>').encode()
    a = source(tmp_path, case, '', name='PAPER', raw=paper, parser='CNSTOCK_PAPER_V1',
        url='https://paper.cnstock.com/html/2026-05/28/content_123.htm')
    data = dict(contId='456', source='上海证券报·中国证券网', author='TEST_FIXTURE记者', original=1,
        title='TEST_FIXTURE上证报', pubTime=day + ' 19:00', textInfo={'content': '<p>59股涨停。</p><p>上海证券报微信公众号</p>'})
    news = ('<script id="__NEXT_DATA__">' + json.dumps({'props': {'pageProps': {'data': data}}}) + '</script>').encode()
    b = source(tmp_path, case, '', name='NEWS', raw=news, parser='CNSTOCK_NEXT_DATA_V1',
        url='https://news.cnstock.com/news,bwkx-202605-456.htm')
    rows = [events.EventClaim(claim_id=str(i), receipt_id=s[0].receipt_id, kind='LADDER_REPORTED',
        event_date=day, theme_ids=('TEST_FIXTURE_ACTIVE_CONT',), evidence_text='59股涨停。', metric='LIMIT_UP_COUNT')
        for i, s in enumerate((a, b))]
    result = build(native, [a, b], rows)
    assert result['reported_origin_family_count'] == 1
    assert {row['origin_family'] for row in result['items']} == {'CNSTOCK'}
    with pytest.raises(ValueError, match='SPAN_NOT_IN_AUTHENTICATED_BODY'):
        build(native, [a], [claim(case, a[0], '90只个股涨停', metric='LIMIT_UP_COUNT')])


def test_gov_press_source_fields_and_body_exclude_navigation(tmp_path, native):
    case, _ = native
    day = str(case.bundle.observation_date)
    raw = (f'<title>TEST_FIXTURE 国务院新闻办发布会</title><meta name="firstpublishedtime" content="{day}-19:00:00">'
        '<div class="pages-date">来源：国务院新闻办网站</div><div>99股涨停</div>'
        '<div class="pages_content"><p>日均词元调用量突破140万亿。</p></div>').encode()
    s = source(tmp_path, case, '', raw=raw, parser='GOV_PRESS_RELEASE_V1', url='https://www.gov.cn/lianbo/123.htm')
    result = build(native, [s], [claim(case, s[0], '日均词元调用量突破140万亿。', metric='TOKEN_CALLS')])
    assert result['original_artifacts'][0]['origin_family'] == 'SCIO'
    assert result['items'][0]['reported_metric']['unit'] == 'TOKEN'
    with pytest.raises(ValueError, match='SPAN_NOT_IN_AUTHENTICATED_BODY'):
        build(native, [s], [claim(case, s[0], '99股涨停', metric='LIMIT_UP_COUNT')])


@pytest.mark.parametrize('url', ['https://www.cnstock.com/commonDetail/4560',
    'https://news.cnstock.com/news,bwkx-202645-4560.htm',
    'https://www.cnstock.com/commonDetail/1456'])
def test_cnstock_article_id_requires_exact_url_identifier(tmp_path, native, url):
    case, _ = native
    data = dict(contId='456', source='上海证券报·中国证券网', author='TEST_FIXTURE记者', original=1,
        title='TEST_FIXTURE上证报', pubTime=str(case.bundle.observation_date) + ' 19:00',
        textInfo={'content': '<p>59股涨停。</p>'})
    raw = ('<script id="__NEXT_DATA__">' + json.dumps({'props': {'pageProps': {'data': data}}}) + '</script>').encode()
    s = source(tmp_path, case, '', raw=raw, parser='CNSTOCK_NEXT_DATA_V1', url=url)
    with pytest.raises(ValueError, match='ORIGINAL_ID_OR_FLAG_INVALID'):
        build(native, [s])


def test_duplicate_original_timestamp_or_body_field_rejected(tmp_path, native):
    case, _ = native
    raw = stcn_raw(case, '59股涨停。') + b'<div class="detail-content">90\xe5\x8f\xaa\xe4\xb8\xaa\xe8\x82\xa1\xe6\xb6\xa8\xe5\x81\x9c</div>'
    s = source(tmp_path, case, '', raw=raw, parser='STCN_HTML_V1', url='https://www.stcn.com/article/detail/1.html')
    with pytest.raises(ValueError, match='SINGLE_FIELD_REQUIRED'):
        build(native, [s])
    raw = stcn_raw(case, '59股涨停。').replace(b'</span></div>', b'</span><span>2026-05-29 21:54</span></div>', 1)
    t = source(tmp_path, case, '', name='B', raw=raw, parser='STCN_HTML_V1', url='https://www.stcn.com/article/detail/2.html')
    with pytest.raises(ValueError, match='ORIGINAL_FIELDS_UNAVAILABLE'):
        build(native, [t])


def test_whole_channel_unknown_does_not_claim_usable_context(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '未知报道。', parser='NBD_UNIMPLEMENTED')
    context = build(native, [s], [claim(case, s[0], '未知报道。')])
    assert context['status'] == 'UNKNOWN'
    assert context['counts'] == {'reported_claims': 1, 'authenticated_reported': 0, 'unknown': 1}
    assert context['report_banner_required'] is True


def test_size_guard_precedes_original_capability_full_read(tmp_path, native, monkeypatch):
    case, _ = native
    s = source(tmp_path, case, '59股涨停。')
    s[2].write_bytes(b'x' * (events.MAX_ARTICLE_BYTES + 1))
    with pytest.raises(ValueError, match='FILE_SIZE_LIMIT_EXCEEDED'):
        build(native, [s])


def test_unproven_old_event_date_unknown_and_manifest_changes_identity(tmp_path, native):
    case, _ = native
    s = source(tmp_path, case, '59股涨停。')
    c = claim(case, s[0], '59股涨停。', metric='LIMIT_UP_COUNT')
    a = build(native, [s], [c])
    changed = c.model_copy(update={'event_date': case.bundle.observation_date - timedelta(days=5)})
    b = build(native, [s], [changed])
    assert 'EVENT_REPORTED_DATE_NOT_ESTABLISHED_BY_SOURCE_SPAN' in b['items'][0]['reason_codes']
    assert a['claim_manifest_hash'] != b['claim_manifest_hash']
    assert a['event_context_content_hash'] != b['event_context_content_hash']
