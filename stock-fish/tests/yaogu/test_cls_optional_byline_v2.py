"""Isolated TEST_FIXTURE originals; no network, DB, or native selector calls."""
from copy import copy
from datetime import date, datetime, timedelta
import hashlib
import json
import socket
import sqlite3

import pytest

from stockfish_core.yaogu import cls_optional_byline_v2 as cls
from stockfish_core.yaogu import theme_event_evidence as old
from stockfish_core.yaogu.contracts import SourceReceipt
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader
from stockfish_core.yaogu.visibility import SH

DAY = date(2026, 5, 13)
CUTOFF = datetime(2026, 5, 13, 20, 30, tzinfo=SH)
PUBLICATION = CUTOFF.replace(hour=16, minute=0)
BODY = '【5月13日TEST_FIXTURE日报】电力走强，TEST_FIXTURE甲6连板，TEST_FIXTURE乙2连板。'
ABSENT = object()


@pytest.fixture(autouse=True)
def no_network_or_db(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_OR_DB_FORBIDDEN')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


def raw_article(*, author=ABSENT, body=BODY, **changes):
    article = {'id': 456, 'title': '5月13日TEST_FIXTURE报道', 'content': body,
               'ctime': int(PUBLICATION.timestamp())}
    if author is not ABSENT:
        article['author'] = author
    article.update(changes)
    data = json.dumps({'props': {'pageProps': {'articleDetail': article}}}, ensure_ascii=False).replace('</', '<\\/')
    return ('<script id="__NEXT_DATA__" type="application/json">' + data + '</script>').encode()


def original(tmp_path, *, raw=None, received=None, **receipt_changes):
    raw = raw_article() if raw is None else raw
    path = tmp_path / 'TEST_FIXTURE-cls-original.html'
    path.write_bytes(raw)
    seen = received or CUTOFF - timedelta(minutes=5)
    receipt = SourceReceipt(receipt_id='TEST_FIXTURE_CLS_ORIGINAL', provider='TEST_FIXTURE',
        dataset='TEST_FIXTURE_REPORTED_NEWS', content_hash=hashlib.sha256(raw).hexdigest(),
        field_version=cls.FEATURE_VERSION, row_count=1, pagination_complete=True, status='COMPLETE',
        source_url='https://www.cls.cn/detail/456', event_time=PUBLICATION,
        source_published_at=PUBLICATION, first_seen_at=seen, ingested_at=seen + timedelta(seconds=1))
    if receipt_changes:
        receipt = SourceReceipt.model_validate({**receipt.model_dump(mode='json'), **receipt_changes})
    artifact = old.ArticleArtifact(receipt_id=receipt.receipt_id, parser=cls.VERSION,
        request_started_at=seen - timedelta(seconds=1), actual_received_at=seen,
        ingested_at=receipt.ingested_at)
    reader = SourceArtifactReader([receipt], {receipt.receipt_id: str(path)})
    return reader, artifact, path


def authenticate(source, *, cutoff=CUTOFF, coverage='TEST_FIXTURE'):
    reader, artifact, _ = source
    return cls.authenticate_cls_original_v2(artifact_reader=reader, artifact=artifact,
        cutoff=cutoff, coverage_type=coverage)


def claim(**changes):
    arguments = dict(witness_id='TEST_FIXTURE_NAMED_WITNESS', receipt_id='TEST_FIXTURE_CLS_ORIGINAL',
        reported_business_date=DAY, reported_name='TEST_FIXTURE甲', evidence_text='TEST_FIXTURE甲6连板',
        date_context_text=BODY)
    arguments.update(changes)
    return cls.CLSNamedWitnessClaim(**arguments)


@pytest.mark.parametrize('author', [ABSENT, None, {}, {'name': ''}, {'name': '  '}])
def test_missing_author_retains_sha_bound_body_but_old_v1_still_fails(tmp_path, author):
    source = original(tmp_path, raw=raw_article(author=author))
    evidence = authenticate(source)
    assert evidence['status'] == 'AVAILABLE_REPORTED_SOURCE'
    article = evidence['article']
    assert article['body'] == BODY and article['title'] == '5月13日TEST_FIXTURE报道'
    assert article['author'] is None and article['author_status'] == 'UNKNOWN'
    assert article['publisher_family'] == article['origin_family'] == 'CLS'
    assert article['independent_information_source_verified'] is False
    witness = cls.build_cls_named_witness_v2(evidence, claim())
    assert witness['status'] == 'AUTHENTICATED_DATED_NAMED_REPORTED_WITNESS'
    assert witness['dated_reported_name_countable'] is True
    assert witness['security_identity_status'] == witness['complete_theme_membership_status'] == 'UNKNOWN'
    assert witness['official_pit_validation_countable'] is False
    if author is ABSENT or author is None or author == {}:
        with pytest.raises((KeyError, TypeError)):
            old._parse_article(source[2].read_bytes(), 'CLS_NEXT_DATA_V1', source[0].receipts[0], 'TEST_FIXTURE')


def test_named_author_preserves_one_cls_family(tmp_path):
    evidence = authenticate(original(tmp_path, raw=raw_article(author={'name': 'TEST_FIXTURE记者'})))
    assert evidence['article']['author'] == 'TEST_FIXTURE记者'
    assert evidence['article']['author_status'] == 'AVAILABLE'
    assert evidence['article']['origin_family'] == 'CLS'
    assert evidence['article']['upstream_origin_status'] == 'UNKNOWN'


@pytest.mark.parametrize('author', ['', [], 0, True, {'name': None}, {'name': []}, {'name': 1}])
def test_unexpected_author_shapes_are_hard_rejected(author):
    with pytest.raises(ValueError, match='CLS_V2_AUTHOR_'):
        cls.parse_cls_article_v2(raw_article(author=author), source_url='https://www.cls.cn/detail/456')


@pytest.mark.parametrize('url', ['https://cls.cn.evil.test/detail/456', 'http://www.cls.cn/detail/456',
    'https://user@www.cls.cn/detail/456', 'https://www.cls.cn:444/detail/456',
    'https://www.cls.cn/detail/456?other=1', 'https://www.cls.cn/detail/456#fragment',
    'https://www.cls.cn/not-detail/456', 'https://www.cls.cn/detail/457',
    'https://www.cls.cn/detail/0456'])
def test_wrong_domain_path_or_original_id_is_rejected(url):
    with pytest.raises(ValueError, match='CLS_V2_ORIGINAL_'):
        cls.parse_cls_article_v2(raw_article(), source_url=url)


@pytest.mark.parametrize('changes', [{'id': True}, {'id': '456'}, {'ctime': True}, {'ctime': 0},
    {'ctime': '1778659202'}, {'ctime': 10**30}, {'title': None}, {'title': ''},
    {'content': []}, {'content': '<script>invented witness</script>'}])
def test_identity_publication_title_and_body_types_are_strict(changes):
    with pytest.raises(ValueError, match='CLS_V2_ORIGINAL_'):
        cls.parse_cls_article_v2(raw_article(**changes), source_url='https://www.cls.cn/detail/456')


@pytest.mark.parametrize('mutation', ['two_scripts', 'missing_close', 'duplicate_json',
    'wrong_script_type', 'duplicate_id_attr', 'wrong_path_shape', 'nonfinite'])
def test_duplicate_or_wrong_fixed_structure_is_rejected(mutation):
    raw = raw_article()
    if mutation == 'two_scripts': raw += raw
    elif mutation == 'missing_close': raw = raw.replace(b'</script>', b'')
    elif mutation == 'duplicate_json': raw = raw.replace(b'"id": 456', b'"id": 456, "id": 456')
    elif mutation == 'wrong_script_type': raw = raw.replace(b'application/json', b'text/javascript')
    elif mutation == 'duplicate_id_attr': raw = raw.replace(b'id="__NEXT_DATA__"', b'id="__NEXT_DATA__" id="other"')
    elif mutation == 'wrong_path_shape': raw = raw.replace(b'"articleDetail": {', b'"articleDetail": [').replace(b'}}}</script>', b']}}</script>')
    elif mutation == 'nonfinite': raw = raw.replace(b'"id": 456', b'"id": 456, "ignored": NaN')
    with pytest.raises(ValueError, match='CLS_V2_'):
        cls.parse_cls_article_v2(raw, source_url='https://www.cls.cn/detail/456')


def test_missing_and_oversize_original_rejected(tmp_path):
    source = original(tmp_path)
    source[2].unlink()
    with pytest.raises(ValueError, match='FILE_SIZE_OR_PATH_INVALID'): authenticate(source)
    source[2].write_bytes(b'x' * (cls.MAX_ARTICLE_BYTES + 1))
    with pytest.raises(ValueError, match='FILE_SIZE_OR_PATH_INVALID'): authenticate(source)


def test_original_and_witness_terminal_sha_guards(tmp_path):
    source = original(tmp_path)
    evidence = authenticate(source)
    witness = cls.build_cls_named_witness_v2(evidence, claim())
    source[2].write_bytes(source[2].read_bytes() + b' ')
    with pytest.raises(ValueError, match='SOURCE_SHA_MISMATCH'): cls.validate_cls_evidence_v2(evidence)
    with pytest.raises(ValueError, match='SOURCE_SHA_MISMATCH'): cls.validate_cls_evidence_v2(witness)


def test_tamper_during_first_authentication_refused_at_terminal(tmp_path, monkeypatch):
    source = original(tmp_path)
    parse = cls.parse_cls_article_v2
    def change_parent(*args, **kwargs):
        result = parse(*args, **kwargs)
        source[2].write_bytes(source[2].read_bytes() + b' ')
        return result
    monkeypatch.setattr(cls, 'parse_cls_article_v2', change_parent)
    with pytest.raises(ValueError, match='SOURCE_SHA_MISMATCH'): authenticate(source)


def test_capture_clock_and_receipt_identity_cannot_change(tmp_path):
    source = original(tmp_path)
    wrong = source[1].model_copy(update={'actual_received_at': source[1].actual_received_at + timedelta(seconds=1)})
    with pytest.raises(ValueError, match='CAPTURE_RECEIPT_CLOCK_MISMATCH'):
        cls.authenticate_cls_original_v2(artifact_reader=source[0], artifact=wrong, cutoff=CUTOFF, coverage_type='TEST_FIXTURE')
    evidence = authenticate(source)
    receipt = source[0].receipts[0]
    source[0].receipts = (SourceReceipt.model_validate({**receipt.model_dump(mode='json'), 'provider': 'TEST_FIXTURE_CHANGED'}),)
    with pytest.raises(ValueError, match='READER_RECEIPT_BINDING_MISMATCH'): cls.validate_cls_evidence_v2(evidence)


@pytest.mark.parametrize('coverage', ['TEST_FIXTURE', 'RECONSTRUCTED_HISTORICAL'])
def test_late_actual_capture_never_visible_in_old_cutoff(tmp_path, coverage):
    evidence = authenticate(original(tmp_path, received=CUTOFF + timedelta(minutes=1)), coverage=coverage)
    assert evidence['status'] == 'UNKNOWN'
    assert 'CLS_V2_ACTUAL_CAPTURE_OR_RECEIPT_CLOCK_AFTER_CUTOFF' in evidence['reason_codes']
    assert evidence['article']['body'] == BODY
    witness = cls.build_cls_named_witness_v2(evidence, claim())
    assert witness['status'] == 'UNKNOWN' and witness['body_locator'] is None
    assert witness['dated_reported_name_countable'] is False and witness['official_pit_validation_countable'] is False


def test_publication_after_cutoff_is_not_a_business_date_proof(tmp_path):
    evidence = authenticate(original(tmp_path, raw=raw_article(ctime=int((CUTOFF + timedelta(hours=1)).timestamp()))))
    assert evidence['status'] == 'UNKNOWN' and 'CLS_V2_PUBLICATION_AFTER_CUTOFF' in evidence['reason_codes']


@pytest.mark.parametrize('changes', [{'status': 'PARTIAL'}, {'pagination_complete': False}, {'row_count': 0}])
def test_incomplete_original_cannot_supply_witness(tmp_path, changes):
    evidence = authenticate(original(tmp_path, **changes))
    assert evidence['status'] == 'UNKNOWN' and evidence['article'] is None
    assert cls.build_cls_named_witness_v2(evidence, claim())['dated_reported_name_countable'] is False


def test_json_and_copy_are_not_issued_capabilities(tmp_path):
    evidence = authenticate(original(tmp_path))
    restored = json.loads(json.dumps(evidence))
    with pytest.raises(ValueError, match='ISSUED_ORIGINAL_CAPABILITY_REQUIRED'): cls.build_cls_named_witness_v2(restored, claim())
    with pytest.raises(ValueError, match='ISSUED_PROCESS_CAPABILITY_REQUIRED'): cls.validate_cls_evidence_v2(restored)
    with pytest.raises(ValueError, match='ISSUED_PROCESS_CAPABILITY_REQUIRED'): cls.validate_cls_evidence_v2(copy(evidence))
    evidence['article']['author'] = 'TEST_FIXTURE_INVENTED_AUTHOR'
    with pytest.raises(ValueError, match='CHANGED_AFTER_AUTHENTICATION'): cls.validate_cls_evidence_v2(evidence)


def test_missing_business_date_is_unknown_even_publication_matches(tmp_path):
    body = '今日TEST_FIXTURE甲6连板。'
    evidence = authenticate(original(tmp_path, raw=raw_article(body=body)))
    witness = cls.build_cls_named_witness_v2(evidence, claim(date_context_text=body))
    assert witness['status'] == 'UNKNOWN'
    assert 'CLS_V2_REPORTED_BUSINESS_DATE_NOT_IN_ORIGINAL_TEXT' in witness['reason_codes']
    assert witness['reported_business_date_status'] == 'UNKNOWN' and witness['dated_reported_name_countable'] is False


@pytest.mark.parametrize('body,day,reason', [
    ('2026年5月13日TEST_FIXTURE甲6连板；2026年5月12日另有消息。', DAY, 'AMBIGUOUS_OR_MISMATCH'),
    ('5月12日TEST_FIXTURE甲6连板。', DAY, 'AMBIGUOUS_OR_MISMATCH'),
    ('5月14日TEST_FIXTURE甲6连板。', date(2026, 5, 14), 'AFTER_PUBLICATION_OR_CUTOFF')])
def test_conflicting_or_future_business_date_not_assigned(tmp_path, body, day, reason):
    evidence = authenticate(original(tmp_path, raw=raw_article(body=body)))
    witness = cls.build_cls_named_witness_v2(evidence, claim(reported_business_date=day, date_context_text=body))
    assert witness['status'] == 'UNKNOWN' and witness['dated_reported_name_countable'] is False
    assert any(reason in r for r in witness['reason_codes'])


def test_context_cannot_borrow_an_unrelated_date_and_later_name_stays_unresolved(tmp_path):
    body = '5月13日旧消息。\nTEST_FIXTURE甲6连板。'
    evidence = authenticate(original(tmp_path, raw=raw_article(body=body)))
    with pytest.raises(ValueError, match='DATE_CONTEXT_DOES_NOT_CONTAIN_BOUND_WITNESS'):
        cls.build_cls_named_witness_v2(evidence, claim(date_context_text='5月13日旧消息。'))
    witness = cls.build_cls_named_witness_v2(evidence, claim(date_context_text=body, requested_instrument_id='TEST_FIXTURE_FUTURE_NAME_JOIN.SZ'))
    assert witness['requested_instrument_id'] == 'TEST_FIXTURE_FUTURE_NAME_JOIN.SZ'
    assert witness['resolved_instrument_id'] is None and witness['security_identity_status'] == 'UNKNOWN'
    assert witness['historical_qualification_status'] == 'UNKNOWN' and witness['selection_or_focus_effect'] == 'NONE'


def test_full_literal_date_can_differ_from_publication_day_with_explicit_proof(tmp_path):
    body = '2026-05-12电力报道：TEST_FIXTURE甲6连板。'
    evidence = authenticate(original(tmp_path, raw=raw_article(body=body)))
    witness = cls.build_cls_named_witness_v2(evidence, claim(reported_business_date=date(2026, 5, 12), date_context_text=body))
    assert witness['status'] == 'AUTHENTICATED_DATED_NAMED_REPORTED_WITNESS'
    assert witness['date_proof_basis'] == 'ORIGINAL_FULL_DATE_LITERAL'
    assert witness['reported_business_date'] == '2026-05-12'


@pytest.mark.parametrize('context', ['去年5月13日TEST_FIXTURE甲6连板。',
    '明年5月13日TEST_FIXTURE甲6连板。', '2025年的5月13日TEST_FIXTURE甲6连板。',
    '同年5月13日TEST_FIXTURE甲6连板。'])
def test_relative_or_unbound_year_does_not_become_publication_year(tmp_path, context):
    evidence = authenticate(original(tmp_path, raw=raw_article(body=context)))
    witness = cls.build_cls_named_witness_v2(evidence, claim(date_context_text=context))
    assert witness['status'] == 'UNKNOWN' and witness['dated_reported_name_countable'] is False
    assert witness['reported_business_date_status'] == 'UNKNOWN'
    assert 'CLS_V2_REPORTED_DATE_YEAR_CONTEXT_UNRESOLVED' in witness['reason_codes']
    assert witness['resolved_instrument_id'] is None


def test_wrong_receipt_name_or_nonbody_script_evidence_rejected(tmp_path):
    evidence = authenticate(original(tmp_path))
    with pytest.raises(ValueError, match='WITNESS_RECEIPT_BINDING_MISMATCH'):
        cls.build_cls_named_witness_v2(evidence, claim(receipt_id='TEST_FIXTURE_OTHER'))
    with pytest.raises(ValueError, match='REPORTED_NAME_NOT_IN_WITNESS_SPAN'):
        cls.build_cls_named_witness_v2(evidence, claim(reported_name='TEST_FIXTURE不在正文'))
    with pytest.raises(ValueError, match='WITNESS_SPAN_NOT_IN_ORIGINAL_BODY'):
        cls.build_cls_named_witness_v2(evidence, claim(evidence_text='TEST_FIXTURE_NOT_PRESENT'))
