"""Finite TEST_FIXTURE originals, no SDK/network/DB/native selector."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stockfish_core.yaogu import economic_direction_explanation_v1 as m
from stockfish_core.yaogu.history_artifacts import digest_json_stream

CLOCK = '2026-10-11T12:00:00+00:00'
CUTOFF = datetime(2026, 10, 11, 13, tzinfo=timezone.utc)


def file(path, value):
    body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
    return m.RegisteredFile(path, hashlib.sha256(body).hexdigest())


def exact(n, d=1): return {'numerator': n, 'denominator': d}


def parent(root, *, missing=False):
    rows = [dict(instrument_id='600001.SH', name='TEST_FIXTURE甲', decision_status='WATCH', primary_stage='STRONG_START',
                 exact_features=dict(amount=exact(10001, 100), returns={'1': exact(1, 100)})),
            dict(instrument_id='600002.SH', name='TEST_FIXTURE乙', decision_status='WATCH', primary_stage='FIRST_PULLBACK',
                 exact_features=dict(amount=exact(9002, 100), returns={'1': exact(-1, 100)})),
            dict(instrument_id='002244.SZ', name='TEST_FIXTURE丙', decision_status='WATCH', primary_stage='STRONG_CONTINUATION',
                 exact_features=dict(amount=exact(777, 100), returns={'1': exact(0)}))]
    if missing: rows[0]['exact_features']['amount'] = None
    ids = [r['instrument_id'] for r in rows]
    layer = dict(theme_id='TEST_FIXTURE_SOLID', name='TEST_FIXTURE固态', stage='DIVERGENCE', selected_ids=[ids[2]],
                 high_risk={'status': 'UNKNOWN'}, source_feature_axes={'theme_amount_cny': exact(999)})
    value = dict(schema='TEST_FIXTURE', created_at=CLOCK, new_context_cutoff=CLOCK, observation_date='2026-10-09',
        target_session='2026-10-12', stock_selection_changed=False, score_or_FOCUS_changed=False, primary_group_union_exact=True,
        frozen_run_binding=dict(run_id='TEST_FIXTURE_RUN', input_hash='a'*64, observation_date='2026-10-09',
          original_decision_cutoff=CLOCK, counts=dict(watch=3, focus=0)), frozen_selected_ids=ids,
        selected_original_items_hash=digest_json_stream(rows),
        groups=[dict(group_id='TEST_FIXTURE_SOLID', group_type='active_divergence_mainlines', name='TEST_FIXTURE固态', stage='DIVERGENCE',
                     primary_selected_ids=ids, items=rows, original_V4_observation=deepcopy(layer))],
        V4=dict(confirmed_mainlines=[], fermentation_watch=[], active_divergence_mainlines=[layer]), optional_limits_LHB_funds={'status':'UNKNOWN'})
    return file(root/'grouped.json', value), value


def business(root, iid='600001.SH', name='TEST_FIXTURE甲', direction='BATTERY', *, clock=CLOCK):
    original = file(root/(iid+'.pdf'), b'%PDF-TEST_FIXTURE_NOT_REAL_COMPANY_REPORT')
    text = file(root/(iid+'.txt'), (iid[:6]+' '+name+'\nTEST_FIXTURE业务关系\f').encode())
    transform = file(root/(iid+'.transform.json'), dict(exit_code=0, original_sha256=original.sha256, derived_sha256=text.sha256))
    receipt = file(root/(iid+'.receipt.json'), dict(original_sha256=original.sha256,
        status='CAPTURED_PDF_UNREVIEWED',http_status=200,
        url='https://static.cninfo.com.cn/finalpage/2026-01-01/TEST_FIXTURE.PDF',
        request_started_at=clock, response_header_seen_at=clock, body_saved_at=clock, request_completed_at=clock))
    return m.OfficialBusinessRegistration(iid, name, direction, 'TEST_FIXTURE_RELATION', 'TEST_FIXTURE摘要',
        'TEST_FIXTURE限制：当日资金归因未知', 'TEST_FIXTURE报告期', '2026-01-01', 1,
        ('TEST_FIXTURE业务关系',), original, receipt, text, transform)


def node(root, *, absent_page=False, normalize_name=False, count=84, upstream_count_override=None):
    endpoint='concept_members_sina'; symbol='gn_TEST_FIXTURE'
    rows=[dict(code=f'{600001+i:06}',symbol=f'sh{600001+i:06}',name='TEST_FIXTURE'+str(i)) for i in range(count-4)]
    rows += [dict(code=c,symbol=s,name='TEST_FIXTURE非A') for c,s in
             [('920030','bj920030'),('900923','sh900923'),('900943','sh900943'),('200026','sz200026')]]
    base=root/'capture'; originals=[]; receipts=[]
    def capture(body, params, url, folder, row_count, dataset=endpoint):
        raw_body=json.dumps(body,ensure_ascii=False).encode(); h=hashlib.sha256(raw_body).hexdigest()
        values=dict(provider='TEST_FIXTURE', dataset=dataset, content_hash=h, parameters=params,
                    source_url=url,status='COMPLETE', pagination_complete=True,row_count=row_count,
                    first_seen_at=CLOCK,ingested_at=CLOCK,field_version='TEST_FIXTURE_COUNTED_NODE_V2')
        rid=hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest();r=dict(receipt_id=rid,**values)
        rfile=file(folder/(rid+'.receipt.json'),r);rawfile=file(folder/(h+'.raw.json'),raw_body)
        originals.append((rfile,rawfile));receipts.append(r);return rfile,rawfile
    count_url='http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount'
    capture(str(count if upstream_count_override is None else upstream_count_override),{'node':symbol},count_url,base/'raw/TEST_FIXTURE/upstream',None)
    for page in (1,2):
        if absent_page and page==2:continue
        body=rows[(page-1)*80:page*80]
        capture(body,dict(_s_r_a='page',asc='1',node=symbol,num='80',page=str(page),sort='symbol',symbol=''),
                count_url.replace('StockCount','Data'),base/'raw/TEST_FIXTURE/upstream',len(body))
    normalized=deepcopy(rows)
    if normalize_name: normalized[-1]['name']='TEST_FIXTURE_CHANGED'
    payload=dict(rows=normalized,status='COMPLETE',pagination_complete=True,first_seen_at=CLOCK,coverage={},gaps=[],
                 field_version='TEST_FIXTURE_COUNTED_NODE_V2')
    mainreceipt,mainraw=capture(payload,{'symbol':symbol},count_url,base/'raw/TEST_FIXTURE/raw',None)
    result=file(root/'node-result.json',dict(request_receipt_id=receipts[-1]['receipt_id'],receipts=receipts,
        status='COMPLETE',pagination_complete=True,provider='TEST_FIXTURE',endpoint=endpoint,parameters={'symbol':symbol},
        field_version='TEST_FIXTURE_COUNTED_NODE_V2',rows=normalized,coverage={},gaps=[],started_at=CLOCK,completed_at=CLOCK))
    directory=dict(rows=[{'label':symbol,'板块':'TEST_FIXTURE锂电池','公司家数':100}],
                   status='COMPLETE',pagination_complete=True,first_seen_at=CLOCK,field_version='TEST_FIXTURE_COUNTED_NODE_V2')
    # Directory pair is independent of node's required original set.
    dr,raw=capture(directory,{},'https://example.invalid/TEST_FIXTURE',root/'directory',1,'concept_list_sina')
    return m.CountedNodeRegistration(symbol,'TEST_FIXTURE锂电池',endpoint,base,result,tuple(originals[:-1]),dr,raw)


class EconomicExplanationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def build(self, *, missing=False, business_rows=None):
        saved, old=parent(self.root,missing=missing)
        registrations=business_rows or (business(self.root), business(self.root,'600002.SH','TEST_FIXTURE乙','RETAIL_CONSUMPTION'))
        out=m.build_economic_direction_explanation(frozen_grouped=saved,business_sources=registrations,counted_nodes=(),cutoff=CUTOFF)
        return out,old,saved

    def test_full_union_items_model_and_native_divergence_unchanged(self):
        out,old,_=self.build()
        self.assertEqual(out['frozen_original_groups'],old['groups'])
        self.assertEqual(out['frozen_run_binding'],old['frozen_run_binding'])
        self.assertEqual(out['preserved_native_subtheme_layers'],old['V4'])
        rows=[r for g in out['primary_display_groups'] for r in g['original_items']]
        self.assertEqual({r['instrument_id']:r for r in rows},{r['instrument_id']:r for r in old['groups'][0]['items']})
        self.assertFalse(out['source_capability_issued']);self.assertFalse(out['score_or_FOCUS_changed'])
        self.assertEqual(out['complete_economic_mainline_coverage'],'UNKNOWN')

    def test_finite_literal_amount_and_breadth_are_not_child_theme_total(self):
        out,_,_=self.build();metric=out['economic_directions'][0]['selected_subset_metrics']
        self.assertEqual(metric['amount_cny'],exact(10001,100));self.assertEqual(metric['rising_fraction'],exact(1))
        self.assertIsNone(metric['broad_direction_amount_cny']);self.assertEqual(metric['broad_direction_strength'],'UNKNOWN')

    def test_missing_axis_is_unknown_not_partial_sum_or_zero(self):
        out,_,_=self.build(missing=True);metric=out['economic_directions'][0]['selected_subset_metrics']
        self.assertEqual(metric['amount_status'],'UNKNOWN');self.assertIsNone(metric['amount_cny'])
        self.assertEqual(metric['breadth_status'],'AVAILABLE')

    def test_renderer_asof_gate_hides_all_names_and_numeric_metrics(self):
        out,_,_=self.build();hidden=m.render_economic_direction_explanation(out,as_of='2020-01-01T00:00:00Z')
        self.assertTrue(hidden.startswith('NOT_YET_VISIBLE'));self.assertNotIn('亿元',hidden)
        shown=m.render_economic_direction_explanation(out,as_of='2029-01-01T00:00:00Z')
        self.assertIn('活跃子题材，出现分歧',shown);self.assertIn('整体方向金额/广度/排名未知',shown)
        self.assertIn('600001.SH',shown);self.assertIn('002244.SZ',shown)

    def test_future_official_source_is_rejected(self):
        saved,_=parent(self.root);b=business(self.root,clock='2026-10-12T00:00:00Z')
        with self.assertRaisesRegex(ValueError,'VISIBILITY'):
            m.build_economic_direction_explanation(frozen_grouped=saved,business_sources=(b,),counted_nodes=(),cutoff=CUTOFF)

    def test_failed_official_http_capture_cannot_support_business_relation(self):
        saved,_=parent(self.root);b=business(self.root)
        value=json.loads(b.receipt.path.read_bytes());value['http_status']=503
        failed=replace(b,receipt=file(b.receipt.path,value))
        with self.assertRaisesRegex(ValueError,'HTTP_CAPTURE_NOT_COMPLETE'):
            m.build_economic_direction_explanation(frozen_grouped=saved,business_sources=(failed,),counted_nodes=(),cutoff=CUTOFF)

    def test_exact_szse_disclosure_domain_registered_in_new_feature_version(self):
        saved,old=parent(self.root);b=business(self.root)
        value=json.loads(b.receipt.path.read_bytes())
        value['url']='https://disc.static.szse.cn/disc/disk03/finalpage/2026-01-01/TEST_FIXTURE.PDF'
        changed=replace(b,receipt=file(b.receipt.path,value))
        out=m.build_economic_direction_explanation(frozen_grouped=saved,business_sources=(changed,),counted_nodes=(),cutoff=CUTOFF)
        self.assertEqual(out['feature_version'],'FINITE_OFFICIAL_BUSINESS_RELATIONS_PRESERVED_NATIVE_SUBTHEMES_V2')
        self.assertEqual(out['economic_directions'][0]['business_relations'][0]['official_url'],value['url'])
        self.assertEqual(out['frozen_original_groups'],old['groups'])
        self.assertEqual(out['preserved_native_subtheme_layers'],old['V4'])

    def test_lookalike_szse_disclosure_domains_are_not_registered(self):
        saved,_=parent(self.root);b=business(self.root)
        for url in ('https://disc.static.szse.cn.evil.example/TEST_FIXTURE.PDF',
                    'https://evil.disc.static.szse.cn/TEST_FIXTURE.PDF',
                    'https://disc-static.szse.cn/TEST_FIXTURE.PDF',
                    'https://disc.static.szse.cn@evil.example/TEST_FIXTURE.PDF'):
            with self.subTest(url=url):
                value=json.loads(b.receipt.path.read_bytes());value['url']=url
                changed=replace(b,receipt=file(b.receipt.path,value))
                with self.assertRaisesRegex(ValueError,'DOCUMENT_URL_UNREGISTERED'):
                    m.build_economic_direction_explanation(frozen_grouped=saved,business_sources=(changed,),counted_nodes=(),cutoff=CUTOFF)

    def test_all_business_literals_must_be_on_the_single_registered_page(self):
        saved,_=parent(self.root);b=business(self.root)
        literals=('TEST_FIXTURE电解液','TEST_FIXTURE未经审计')
        for body,supported in ((('600001 TEST_FIXTURE甲\n'+literals[0]+' '+literals[1]+'\f').encode(),True),
                               (('600001 TEST_FIXTURE甲\n'+literals[0]+'\f'+literals[1]+'\f').encode(),False)):
            with self.subTest(supported=supported):
                text=file(b.derived_text.path,body)
                transform=file(b.transform.path,dict(exit_code=0,original_sha256=b.original.sha256,derived_sha256=text.sha256))
                changed=replace(b,literals=literals,derived_text=text,transform=transform)
                if supported:
                    out=m.build_economic_direction_explanation(frozen_grouped=saved,business_sources=(changed,),counted_nodes=(),cutoff=CUTOFF)
                    self.assertEqual(out['economic_directions'][0]['business_relations'][0]['literals_checked'],list(literals))
                else:
                    with self.assertRaisesRegex(ValueError,'IDENTITY_OR_BUSINESS_LITERAL_MISMATCH'):
                        m.build_economic_direction_explanation(frozen_grouped=saved,business_sources=(changed,),counted_nodes=(),cutoff=CUTOFF)

    def test_wrong_identity_business_literal_and_source_sha_rejected(self):
        saved,_=parent(self.root);b=business(self.root)
        for changed in (replace(b,instrument_id='600003.SH'), replace(b,literals=('不存在的业务',)),
                        replace(b,original=m.RegisteredFile(b.original.path,'0'*64))):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    m.build_economic_direction_explanation(frozen_grouped=saved,business_sources=(changed,),counted_nodes=(),cutoff=CUTOFF)

    def test_duplicate_json_key_and_saved_json_not_source_registration(self):
        bad=file(self.root/'bad.json',b'{"same":1,"same":2}')
        with self.assertRaisesRegex(ValueError,'DUPLICATE_JSON'):m._json(m._read(bad))
        with self.assertRaisesRegex(ValueError,'REGISTERED_ORIGINAL'):m._read({'path':str(bad.path),'sha256':bad.sha256})

    def test_source_mutation_during_build_is_caught(self):
        saved,_=parent(self.root);b=business(self.root);real=m._business
        def altered(*args):
            value=real(*args);b.original.path.write_bytes(b'CHANGED_TEST_FIXTURE');return value
        with patch.object(m,'_business',side_effect=altered):
            with self.assertRaisesRegex(ValueError,'SHA_MISMATCH'):
                m.build_economic_direction_explanation(frozen_grouped=saved,business_sources=(b,),counted_nodes=(),cutoff=CUTOFF)

    def test_whole_counted_node_real_frozen_verifier_retains_non_A_and_count_conflict(self):
        registration=node(self.root);value=m._node(registration,CUTOFF)
        self.assertEqual(value['whole_member_count'],84);self.assertEqual(value['directory_company_count'],100)
        self.assertEqual(value['directory_vs_technical_count'],'CONFLICT')
        self.assertEqual(value['pages'],[{'page':1,'rows':80},{'page':2,'rows':4}])
        self.assertEqual(sum(r['namespace_role']!='A_PRICE_NAMESPACE_ONLY_NOT_QUALIFICATION' for r in value['all_member_identities']),4)
        self.assertFalse(value['source_capability_issued']);self.assertEqual(value['economic_direction_complete_membership'],'UNKNOWN')

    def test_missing_whole_page_and_normalized_name_conflict_rejected(self):
        for index,kwargs in enumerate(({'absent_page':True},{'normalize_name':True})):
            with self.subTest(kwargs=kwargs):
                registration=node(self.root/str(index),**kwargs)
                with self.assertRaisesRegex(ValueError,'ALL_NODE_PAGES|IDENTITIES_DIFFER'):m._node(registration,CUTOFF)

    def test_gigantic_and_inconsistent_count_reject_before_range_expansion(self):
        for index,count in enumerate((10**100,85)):
            with self.subTest(count=count):
                registration=node(self.root/('huge'+str(index)),upstream_count_override=count)
                with self.assertRaisesRegex(ValueError,'COUNT_SOURCE_INVALID|NORMALIZED_LENGTH_MISMATCH'):
                    m._node(registration,CUTOFF)

    def test_node_source_registry_subset_and_exchange_conflict_rejected(self):
        registration=node(self.root)
        with self.assertRaisesRegex(ValueError,'WHOLE_NODE_ORIGINAL_SCOPE'):
            m._node(replace(registration,originals=registration.originals[1:]),CUTOFF)
        with self.assertRaisesRegex(ValueError,'EXCHANGE_CONFLICT'):
            m._coded_rows([dict(code='600001',symbol='sz600001',name='TEST_FIXTURE')])

    def test_attachment_tamper_does_not_pass_renderer_hash(self):
        out,_,_=self.build();out['primary_group_union_exact']=False
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):
            m.render_economic_direction_explanation(out,as_of='2029-01-01T00:00:00Z')


if __name__=='__main__':unittest.main()
