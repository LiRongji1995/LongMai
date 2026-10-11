"""Independent explanation math and original-byte guards; tmp TEST_FIXTURE only."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction as F
import json

import pytest

from stockfish_core.yaogu import market_explanation as explanation
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.theme_evolution import encoded
from stockfish_core.yaogu.visibility import stamp, CLOCKS
from tests.yaogu.test_market_descriptive_context import fixture, run as issue
from tests.yaogu.test_short_horizon_strength_r2 import frozen_parent, fixture_bundle


def prepared(tmp_path, **kwargs):
    bundle, cutoff, stream, paths = fixture(tmp_path, **kwargs)
    cap = issue(tmp_path, bundle, cutoff, stream)
    parent = frozen_parent(bundle, cutoff)  # Genuine native selector, never mocked.
    saved = tmp_path / 'TEST_FIXTURE_original_native_run.json'
    saved.write_text(json.dumps(parent, ensure_ascii=False), encoding='utf-8')
    return bundle, cutoff, cap, json.loads(saved.read_text()), paths


def build(bundle, cutoff, cap, parent):
    return explanation.build_market_explanation(bundle, cap, cutoff=cutoff, run=parent)


def q(value):
    return F(value['numerator'], value['denominator']) if value is not None else None


def rehash(attachment):
    attachment['attachment_content_hash'] = digest_json_stream(
        {k: v for k, v in attachment.items() if k != 'attachment_content_hash'})


def test_native_issuer_exact_135_changes_OLS_identity_and_full_parent_immutability(tmp_path):
    def alter(index, day, master, market):
        for offset, row in enumerate(market):
            row['amount'] = str(100 * (index + 1) ** 2 * (offset + 1))
            row['close'] = str(Decimal('10') + Decimal(offset - 5) / 10 + Decimal(index - 20) / 100)
            row['turn'] = str(Decimal(offset) + Decimal(index) / 10)
    bundle, cutoff, cap, parent, _ = prepared(tmp_path, alter=alter)
    before = deepcopy(parent); input_before = bundle.model_dump(mode='json')
    attachment = build(bundle, cutoff, cap, parent)
    assert parent == before and bundle.model_dump(mode='json') == input_before
    binding = attachment['frozen_run_binding']
    assert binding['run_content_hash'] == digest_json_stream(before)
    assert binding['declared_run_content_hash'] == parent.get('content_hash')
    assert binding['input_hash'] == parent['input_hash']
    assert binding['run_id'] == parent['run_id']
    assert binding['decision_cutoff'] == binding['original_decision_cutoff'] == parent['decision_cutoff']
    assert binding['binding_role'] == 'FROZEN_RUN_CONTENT_BOUND_NOT_SELECTOR_REAUTHENTICATED'
    assert attachment['selector_reauthenticated'] is False and attachment['selection_gate'] is False
    assert attachment['official_pit_validation_count'] == attachment['authenticated_emotion_cycle_count'] == 0
    assert attachment['source_identity'] == explanation.source_identity()
    assert attachment['source_identity']['native_dependency_source_hash'] == cap.source_identity['source_hash']
    for window, h in zip(attachment['windows'], (1, 3, 5)):
        assert window['sessions'] == [str(d) for d in bundle.sessions[-h-1:]]
        assert window['expected_session_count'] == h + 1 and window['population_changed'] is False
        axes = window['axes']
        assert q(axes['market_amount_cny']['change']) == F(26, 26-h)**2 - 1
        # Quadratic levels y=7800*k^2: exact OLS slope at the interval midpoint.
        assert q(axes['market_amount_cny']['level_ols_slope_per_exchange_session']) == 7800 * (52-h)
        assert q(axes['median_return_1d']['change']) == F(h, 1000)
        assert q(axes['median_return_1d']['level_ols_slope_per_exchange_session']) == F(1, 1000)
        assert q(axes['median_turn_percent']['change']) == F(h, 10)
        assert q(axes['median_turn_percent']['level_ols_slope_per_exchange_session']) == F(1, 10)
        assert q(axes['top10_trading_amount_share']['change']) == 0
        assert q(axes['return_iqr']['level_ols_slope_per_exchange_session']) == 0
    assert attachment['generated_at'] == attachment['first_created_at']
    for key in CLOCKS:
        assert stamp(attachment['source_clock_maxima'][key]) == max(
            stamp(c[key]) for c in attachment['consumed_source_four_clocks'].values())
    assert explanation.render_market_explanation(attachment,
        as_of=attachment['attachment_visible_at']).startswith('市场解释附件')


def test_missing_middle_original_keeps_complete_calendar_and_UNKNOWN_not_zero(tmp_path):
    bundle, _ = fixture_bundle(); missing = str(bundle.sessions[-3])
    bundle, cutoff, cap, parent, _ = prepared(tmp_path, omit={(missing, 'market_daily')})
    attachment = build(bundle, cutoff, cap, parent)
    assert attachment['windows'][0]['axes']['market_amount_cny']['status'] == 'AVAILABLE'
    for window in attachment['windows'][1:]:
        assert missing in window['sessions']
        for axis in window['axes'].values():
            assert axis['status'] == 'UNKNOWN' and axis['change'] is None
            assert axis['level_ols_slope_per_exchange_session'] is None
        assert window['population_changed'] is None
        assert all(r['status'] == 'UNKNOWN' for r in window['counterevidence'])
    text = explanation.render_market_explanation(attachment, as_of=attachment['attachment_visible_at'])
    amount_line = next(line for line in text.splitlines() if line.startswith('| 成交额 |'))
    assert amount_line.endswith('| UNKNOWN | UNKNOWN |')
    assert '可用 19/20；成交额中位数 UNKNOWN' in text
    assert '较窗口起点放量但上涨广度下降：UNKNOWN' in text
    assert '3交易日窗（' in text and '）：UNKNOWN。' in text


def test_missing_intermediate_turn_is_axis_local_UNKNOWN(tmp_path):
    def alter(index, day, master, market):
        if index == 23: market[0]['turn'] = ''
    bundle, cutoff, cap, parent, _ = prepared(tmp_path, alter=alter)
    attachment = build(bundle, cutoff, cap, parent)
    assert attachment['windows'][0]['axes']['median_turn_percent']['status'] == 'AVAILABLE'
    for w in attachment['windows'][1:]:
        assert w['axes']['median_turn_percent']['status'] == 'UNKNOWN'
        assert w['axes']['market_amount_cny']['status'] == 'AVAILABLE'
    text = explanation.render_market_explanation(attachment, as_of=attachment['attachment_visible_at'])
    turn_line = next(line for line in text.splitlines() if line.startswith('| 流通股换手中位数 |'))
    assert turn_line.endswith('| UNKNOWN | UNKNOWN |')


@pytest.mark.parametrize('kind', ['dict', 'lookalike'])
def test_saved_JSON_or_lookalike_cannot_restore_issued_capability(tmp_path, kind):
    bundle, cutoff, cap, parent, _ = prepared(tmp_path)
    forged = cap.context if kind == 'dict' else type('Lookalike', (), {'context': cap.context})()
    with pytest.raises(ValueError, match='NATIVE_OUTPUT_CAPABILITY_REQUIRED'):
        build(bundle, cutoff, forged, parent)


@pytest.mark.parametrize('kind', ['input_hash', 'observation_date', 'cutoff', 'run_id', 'duplicate'])
def test_wrong_frozen_content_binding_is_rejected_without_reclassifying(tmp_path, kind):
    bundle, cutoff, cap, parent, _ = prepared(tmp_path)
    bad = deepcopy(parent)
    if kind == 'input_hash': bad['input_hash'] = '0' * 64
    if kind == 'observation_date': bad['observation_date'] = str(bundle.sessions[-2])
    if kind == 'cutoff': bad['decision_cutoff'] = (cutoff + timedelta(seconds=1)).isoformat()
    if kind == 'run_id': bad['run_id'] = ''
    if kind == 'duplicate':
        items = next(iter(bad['results'].values()))['items']; items.append(deepcopy(items[0]))
    before = deepcopy(bad)
    with pytest.raises(ValueError, match='MARKET_EXPLANATION_FROZEN_RUN'):
        build(bundle, cutoff, cap, bad)
    assert bad == before


def test_wrong_capability_cutoff_or_changed_raw_is_rejected(tmp_path):
    bundle, cutoff, cap, parent, paths = prepared(tmp_path)
    with pytest.raises(ValueError, match='OUTPUT_BINDING_MISMATCH'):
        build(bundle, cutoff + timedelta(seconds=1), cap, parent)
    path = next(p for rid, p in paths.items() if str(bundle.observation_date) in rid and 'market_daily' in rid)
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='ORIGINAL_SOURCE_SHA_MISMATCH'):
        build(bundle, cutoff, cap, parent)


@pytest.mark.parametrize('kind', ['raw', 'registry', 'run'])
def test_terminal_revalidation_detects_during_build_mutation(tmp_path, monkeypatch, kind):
    bundle, cutoff, cap, parent, paths = prepared(tmp_path)
    original = explanation._window_axis; mutated = False
    def changing(*args):
        nonlocal mutated
        result = original(*args)
        if not mutated:
            mutated = True
            if kind == 'raw':
                p = next(p for rid, p in paths.items() if str(bundle.observation_date) in rid and 'market_daily' in rid)
                p.write_bytes(p.read_bytes() + b' ')
            elif kind == 'registry': cap._sources.clear()
            else: parent['results'][next(iter(parent['results']))]['items'][0]['TEST_FIXTURE_CHANGED'] = True
        return result
    monkeypatch.setattr(explanation, '_window_axis', changing)
    with pytest.raises(ValueError, match='ORIGINAL_SOURCE_SHA_MISMATCH|CONSUMED_SOURCE_REGISTRY_CHANGED|BINDING_CHANGED_DURING_BUILD'):
        build(bundle, cutoff, cap, parent)


@pytest.mark.parametrize('kind', ['amount_breadth_concentration', 'price_amount'])
def test_descriptive_counterevidence_from_genuine_native_facts(tmp_path, kind):
    def alter(index, day, master, market):
        if index != 25:return
        for offset, row in enumerate(market):
            row['close'] = '9.5' if kind == 'amount_breadth_concentration' else '11'
            row['amount'] = ('4000000' if offset == 0 else '100') if kind == 'amount_breadth_concentration' else '1'
    bundle, cutoff, cap, parent, _ = prepared(tmp_path, alter=alter)
    attachment = build(bundle, cutoff, cap, parent)
    for w in attachment['windows']:
        states = {r['code']: r['status'] for r in w['counterevidence']}
        if kind == 'amount_breadth_concentration':
            assert states['AMOUNT_UP_BREADTH_DOWN'] == states['CONCENTRATION_UP_BREADTH_DOWN'] == 'PRESENT'
        else: assert states['MEDIAN_DAILY_RETURN_IMPROVING_AMOUNT_DOWN'] == 'PRESENT'


def test_changing_population_is_explicit_not_fixed_member_trend(tmp_path):
    def alter(index, day, master, market):
        if index == 25:
            master[0]['tradeStatus'] = market[0]['tradestatus'] = '0'
            market[0]['amount'] = market[0]['volume'] = '0'
    bundle, cutoff, cap, parent, _ = prepared(tmp_path, alter=alter)
    assert all(w['population_changed'] is True for w in build(bundle, cutoff, cap, parent)['windows'])


def test_amount_zero_initial_denominator_unknown_change_but_exact_level_slope(tmp_path):
    def alter(index, day, master, market):
        if index == 24:
            for row in market: row['amount'] = '0'
    bundle, cutoff, cap, parent, _ = prepared(tmp_path, alter=alter)
    attachment = build(bundle, cutoff, cap, parent)
    axis = attachment['windows'][0]['axes']['market_amount_cny']
    assert axis['status'] == 'PARTIAL' and axis['change_status'] == 'UNKNOWN' and axis['change'] is None
    assert q(axis['level_ols_slope_per_exchange_session']) == 78000 * 26
    text = explanation.render_market_explanation(attachment, as_of=attachment['attachment_visible_at'])
    assert '| UNKNOWN；斜率 +' in next(line for line in text.splitlines() if line.startswith('| 成交额 |'))


def test_renderer_creation_source_cutoff_and_asof_visibility_without_native_authority(tmp_path):
    bundle, cutoff, cap, parent, _ = prepared(tmp_path)
    attachment = build(bundle, cutoff, cap, parent)
    saved = json.loads(json.dumps(attachment)); before = deepcopy(saved)
    created = stamp(saved['first_created_at'])
    early = explanation.render_market_explanation(saved, as_of=created - timedelta(microseconds=1))
    assert 'NOT_YET_VISIBLE' in early and '| 指标 |' not in early and '交易家数：' not in early
    assert '前20日（' not in early and '描述反证：' not in early and '亿元' not in early
    text = explanation.render_market_explanation(saved, as_of=saved['attachment_visible_at'])
    assert 'MARKET_EDGE=NOT_ESTABLISHED' in text and '本次不恢复原生来源能力' in text
    assert saved == before
    # A late context cutoff independently hides values even after creation.
    late = deepcopy(saved); late_cutoff = created + timedelta(days=1)
    late['context_cutoff'] = late['attachment_visible_at'] = late_cutoff.isoformat(); rehash(late)
    assert 'NOT_YET_VISIBLE' in explanation.render_market_explanation(late, as_of=created)
    # Even a self-rehashed archived record must preserve internal four-clock maxima.
    bad = deepcopy(saved); bad['source_clock_maxima']['first_seen_at'] = created.isoformat(); rehash(bad)
    with pytest.raises(ValueError, match='SAVED_CLOCK_MAXIMUM_MISMATCH'):
        explanation.render_market_explanation(bad, as_of=created)
    with pytest.raises(ValueError, match='EXPLICIT_TIMEZONE_REQUIRED'):
        explanation.render_market_explanation(saved, as_of=created.replace(tzinfo=None))


def test_renderer_rejects_modified_saved_facts_and_optional_declared_hash_is_preserved(tmp_path):
    bundle, cutoff, cap, parent, _ = prepared(tmp_path)
    parent['content_hash'] = 'TEST_FIXTURE_DECLARED_ORIGINAL_HASH'
    before = deepcopy(parent); attachment = build(bundle, cutoff, cap, parent)
    assert attachment['frozen_run_binding']['declared_run_content_hash'] == parent['content_hash']
    assert attachment['frozen_run_binding']['run_content_hash'] == digest_json_stream(parent)
    assert parent == before
    attachment['observation_levels']['trading_count'] = 999
    with pytest.raises(ValueError, match='SAVED_ATTACHMENT_HASH_MISMATCH'):
        explanation.render_market_explanation(attachment, as_of=attachment['attachment_visible_at'])


def test_chinese_six_axis_display_units_counts_prior20_and_exact_saved_numbers(tmp_path):
    def alter(index, day, master, market):
        for offset, row in enumerate(market):
            row['amount'] = str(100000000 * (index + 1) * (offset + 1))
            row['close'] = str(Decimal('10') + Decimal(offset - 5) / 10 + Decimal(index - 20) / 100)
            row['turn'] = str(Decimal(offset) + Decimal(index) / 10)
    bundle, cutoff, cap, parent, _ = prepared(tmp_path, alter=alter)
    attachment = build(bundle, cutoff, cap, parent); before = deepcopy(attachment)
    text = explanation.render_market_explanation(attachment, as_of=attachment['attachment_visible_at'])
    assert '| 指标 | 当前水平 | 1日变化 | 3日变化 | 5日变化 |' in text
    assert '| 成交额 | 2028.00亿元 | +4.00%；斜率 +78.00亿元/交易日 | +13.04%' in text
    assert '| 当日收益中位数 | 1.00% | +0.10pp；斜率 +0.10pp/交易日' in text
    assert '| 流通股换手中位数 | 8.00% | +0.10pp；斜率 +0.10pp/交易日' in text
    assert '| 上涨广度 | 58.33%' in text and '| Top10成交集中度 | 96.15%' in text
    assert '| 收益分布IQR | 5.50%' in text and '800.00%' not in text
    assert '交易家数：12；上涨：7；下跌：5；平盘：0' in text
    assert '换手完整性：完整；有效 12/12；缺失 0；换手≥5% 9家' in text
    assert '可用 20/20；成交额中位数 1209.00亿元；当前成交额/前20日中位数 1.68倍' in text
    assert '交易总体未变化' in text and '当日收益中位数改善但较窗口起点缩量：本窗口未出现' in text
    assert '原件四时钟最大值：' in text and all(stamp(attachment['source_clock_maxima'][k]).isoformat() in text for k in CLOCKS)
    assert '附件首次创建：' + attachment['first_created_at'] in text
    assert 'numerator' not in text and 'reason_codes' not in text and '{' not in text
    assert attachment == before
    assert q(attachment['windows'][1]['axes']['market_amount_cny']['change']) == F(3, 23)


@pytest.mark.parametrize('value,scale,expected', [
    (F(1, 3), F(100), '33.33%'), (F(-1, 8), F(100), '-12.50%'),
    (F(1, 200), F(1), '0.00%'), (F(3, 200), F(1), '0.02%'),
    (F(10**5000 + 1, 10**5000), F(100), '100.00%'),
    (F(201 * 10**5000 + 1, 200 * 10**5000), F(1), '1.01%'),
    (F(201 * 10**5000, 200 * 10**5000), F(1), '1.00%'),
    (F(10**5000), F(1), '数值超出显示范围（精确值见JSON）'),
])
def test_display_decimal_exact_rounding_large_integer_safety_without_float(value, scale, expected):
    original = encoded(value); before = deepcopy(original)
    assert explanation._display_decimal(original, scale=scale, unit='%') == expected
    assert original == before


def test_incomplete_current_turn_and_unknown_axis_show_UNKNOWN_not_zero(tmp_path):
    def alter(index, day, master, market):
        if index == 25: market[0]['turn'] = ''
    bundle, cutoff, cap, parent, _ = prepared(tmp_path, alter=alter)
    attachment = build(bundle, cutoff, cap, parent)
    text = explanation.render_market_explanation(attachment, as_of=attachment['attachment_visible_at'])
    assert '| 流通股换手中位数 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |' in text
    assert '换手完整性：不完整；有效 11/12；缺失 1；换手≥5% UNKNOWN家' in text
    assert '交易家数：12' in text
    # A saved record does not acquire an available display from a missing axis status.
    unknown = deepcopy(attachment); unknown['observation_axis_status'].pop('rising_fraction'); rehash(unknown)
    assert '| 上涨广度 | UNKNOWN |' in explanation.render_market_explanation(unknown, as_of=unknown['attachment_visible_at'])


def test_absent_consumed_four_clocks_masks_all_research_values_in_saved_display(tmp_path):
    bundle, cutoff, cap, parent, _ = prepared(tmp_path)
    attachment = build(bundle, cutoff, cap, parent)
    saved = deepcopy(attachment)
    saved['consumed_source_four_clocks'] = {}
    saved['source_clock_maxima'] = {key: None for key in CLOCKS}
    saved['actual_source_visible_at'] = None
    saved['attachment_visible_at'] = max(stamp(saved['generated_at']), stamp(saved['context_cutoff'])).isoformat()
    rehash(saved); before = deepcopy(saved)
    text = explanation.render_market_explanation(saved, as_of=saved['attachment_visible_at'])
    assert '没有可用消费来源四时钟' in text
    assert '| 成交额 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |' in text
    assert '| 流通股换手中位数 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |' in text
    assert '交易家数：UNKNOWN；上涨：UNKNOWN；下跌：UNKNOWN；平盘：UNKNOWN' in text
    assert '成交额中位数 UNKNOWN；当前成交额/前20日中位数 UNKNOWN' in text
    assert '当日收益中位数改善但较窗口起点缩量：UNKNOWN' in text
    assert saved == before
