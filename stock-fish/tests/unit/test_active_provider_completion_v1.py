"""TEST_FIXTURE: generated parent/worker completion against real authority.

Only a local provider-library adapter is replaced in an exact-hashed fixture
base. The actual execute_request, worker.main, transport reservations, archive
receipts and generated subprocess bootstrap execute in real parent/child PIDs.
No market provider, network, real registration or production source is changed.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import active_runtime_v1 as builder
from scripts import business_authority_v1 as authority


@pytest.fixture(scope='module')
def provider_tree(tmp_path_factory):
    root=tmp_path_factory.mktemp('TEST_FIXTURE_provider_completion').resolve()
    base=root/'frozen_source';base.mkdir()
    manifest=json.loads(builder.BASE_MANIFEST.read_bytes())
    publication=builder.ROOT.parents[1]/'publish'/builder.ROOT.parent.name/'stock-fish'
    for item in manifest['files']:
        matches=[p/item['path'] for p in (builder.ROOT,publication)
            if (p/item['path']).is_file() and hashlib.sha256((p/item['path']).read_bytes()).hexdigest()==item['sha256']]
        assert matches,'exact published preimage missing: '+item['path']
        target=base/item['path'];target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(matches[0].read_bytes())
    relative='stockfish_core/yaogu/provider_requests_worker.py'
    path=base/relative;old=path.read_text()
    adapter='''def _TEST_FIXTURE_library(endpoint,params,partial_path=None):
    import os, socket
    from unittest.mock import patch
    assert endpoint=='history'
    assert params['codes'][0].startswith('sh.')
    with patch.object(socket.socket,'connect',side_effect=AssertionError('TEST_FIXTURE_NETWORK_FORBIDDEN')):
        # Simulated library login/query, admitted through the actual generated
        # reserve_attempt. Both operations precede the typed library result.
        reserve_attempt('SDK')
        reserve_attempt('SDK')
        mode=os.environ['TEST_FIXTURE_PROVIDER_MODE']
        if mode=='EXCEPTION':
            raise RuntimeError('TEST_FIXTURE_LIBRARY_EXCEPTION')
        result=dict(status='PARTIAL' if mode=='PARTIAL' else 'COMPLETE',
            rows=[{'code':params['codes'][0],'fixture':'TEST_FIXTURE'}],
            field_version='TEST_FIXTURE_LOCAL_LIBRARY_V1',
            diagnostics={'fixture':'TEST_FIXTURE','worker_pid':os.getpid(),'parameters':params})
        if mode!='MISSING_PAGINATION':
            result['pagination_complete']=mode!='PARTIAL'
        return result


'''
    original_call="result=baostock_request(request['endpoint'],parameters,request.get('partial_path'))"
    assert old.count(original_call)==1 and old.count('def main():')==1
    changed=old.replace('def main():',adapter+'def main():').replace(original_call,
        "result=_TEST_FIXTURE_library(request['endpoint'],parameters,request.get('partial_path'))")
    # Keep all original worker control and admission bodies. The one library
    # call target is the only change to main, before production AST hooks.
    before,after=ast.parse(old),ast.parse(changed)
    main=lambda tree: next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    before_main=main(before)
    replaced=0
    for node in ast.walk(before_main):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='baostock_request':
            node.func.id='_TEST_FIXTURE_library';replaced+=1
    assert replaced==1 and ast.dump(before_main,include_attributes=False)==ast.dump(main(after),include_attributes=False)
    path.write_text(changed)
    for item in manifest['files']:
        if item['path']==relative:
            raw=path.read_bytes();item.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    manifest['published_commit']='TEST_FIXTURE_LOCAL_LIBRARY_REAL_PARENT_CHILD'
    fixture_manifest=root/'TEST_FIXTURE_BASE_MANIFEST.json'
    fixture_manifest.write_text(json.dumps(manifest,indent=2))
    state=root/'state';state.mkdir()
    owner=authority.Authority.initialize(root/'authority.sqlite3','TEST_FIXTURE_PROVIDER_COMPLETION',
        onboarding={'mode':'EMPTY_NEW_WORKSPACE','state_roots':[str(state)]})
    source=root/'active'
    result=builder.build(base,source,base_manifest=fixture_manifest,
        authority_binding={'database':str(owner.path),'workspace_id':owner.workspace_id})
    from scripts.codex_runtime_recovery_v2 import reference
    registry=root/'registry';registry.mkdir();(registry/'resources').mkdir()
    plan={'profile':{'source_root':str(source)},'inventory':{'registry_roots':[str(registry)]},
        'active_source_manifest':reference(source/'ACTIVE_RUNTIME_MANIFEST.json')}
    with owner._connect() as db:
        db.execute('INSERT INTO epochs VALUES (?,?,?,?,?)',('TEST_FIXTURE_epoch','[]',json.dumps(plan),'ACTIVE',authority.stamp()))
    return dict(root=root,source=source,base=base,owner=owner,manifest=result)


def _claim(tree,code,start='2026-11-01',end='2026-11-04'):
    owner=tree['owner']
    claim=owner.claim({'operation':'CAPTURE','provider':'baostock','dataset':'history',
        'window_start':start,'window_end':end,'codes':['sh.'+code]},epoch_id='TEST_FIXTURE_epoch',
        limits={'provider_requests':4,'http_attempts':4,'sdk_calls':4})
    owner.start(claim)
    token=owner.derive_capability(claim,['provider','network','worker','storage','capture'])
    return claim,token


def _request(tree,claim,token,code,mode,*,end='2026-11-04'):
    env=dict(os.environ)
    for name in ('STOCKFISH_WORKER_CAPABILITY','STOCKFISH_BUSINESS_ID','STOCKFISH_AUTHORITY_DATABASE','STOCKFISH_AUTHORITY_WORKSPACE'):
        env.pop(name,None)
    owner=tree['owner']
    env.update(STOCKFISH_AUTHORITY_DATABASE=str(owner.path),STOCKFISH_AUTHORITY_WORKSPACE=owner.workspace_id,
        STOCKFISH_WORKER_CAPABILITY=token,STOCKFISH_BUSINESS_ID=claim['business_id'],TEST_FIXTURE_PROVIDER_MODE=mode)
    output=tree['root']/('TEST_FIXTURE_result_'+code+'_'+end)
    program="""import sys,json,os,socket
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from stockfish_core.yaogu import provider_requests as provider
params={'codes':[sys.argv[3]+'.SH'],'start_date':'2026-11-01','end_date':sys.argv[4]}
try:
 with patch.object(socket.socket,'connect',side_effect=AssertionError('TEST_FIXTURE_NETWORK_FORBIDDEN')):
  if os.environ['TEST_FIXTURE_PROVIDER_MODE']=='ARCHIVE_FAILURE':
   with patch.object(provider,'archive_receipt',side_effect=RuntimeError('TEST_FIXTURE_PARENT_ARCHIVE_FAILURE')):
    answer=provider.execute_request('history',params,sys.argv[2],timeout=45)
  else:
   answer=provider.execute_request('history',params,sys.argv[2],timeout=45)
 result={'returned':True,'answer':answer,'parent_pid':os.getpid()}
except Exception as error:
 result={'returned':False,'error_type':type(error).__name__,'error':str(error),'parent_pid':os.getpid()}
print(json.dumps(result))
"""
    command=[sys.executable,'-I','-B','-c',program,str(tree['source']),str(output),code,end]
    result=subprocess.run(command,cwd=tree['root'],env=env,capture_output=True,text=True,timeout=60)
    label=tree['root']/('TEST_FIXTURE_process_'+code+'_'+end)
    label.with_suffix('.stdout').write_text(result.stdout)
    label.with_suffix('.stderr').write_text(result.stderr)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def _rows(tree,claim):
    with tree['owner']._connect(readonly=True) as db:
        rows=[dict(row) for row in db.execute('SELECT s.*,e.state,e.receipt FROM source_scopes s JOIN effects e '
            'USING(business_id,effect_kind,stable_effect_id) WHERE s.business_id=?',(claim['business_id'],))]
        events=[json.loads(row['data']) for row in db.execute(
            "SELECT data FROM events WHERE business_id=? AND event='SOURCE_REQUEST_COMPLETED' ORDER BY sequence",(claim['business_id'],))]
    return rows,events


def _assert_scope(rows,code,end='2026-11-04'):
    assert len(rows)==3  # one parent provider + two actual worker SDK admissions
    semantic={'provider':'baostock','endpoint':'history',
        'parameters':{'codes':[code+'.SH'],'start_date':'2026-11-01','end_date':end},
        'source_window':{'start_date':'2026-11-01','end_date':end}}
    expected_hash=hashlib.sha256(json.dumps(semantic,sort_keys=True,ensure_ascii=False,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()
    assert {row['request_semantic_id'] for row in rows}=={expected_hash}
    for row in rows:
        assert row['provider']=='baostock' and row['endpoint']=='history'
        assert row['window_start']=='2026-11-01' and row['window_end']==end
        assert json.loads(row['codes_json'])==[code+'.SH']
    assert sum(row['stable_effect_id'].startswith('transport:') for row in rows)==2


def test_completion_hooks_are_single_and_at_real_boundaries(provider_tree):
    for relative,hook in [('stockfish_core/yaogu/provider_requests.py','provider_result'),
                          ('stockfish_core/yaogu/provider_requests_worker.py','transport_result')]:
        tree=ast.parse((provider_tree['source']/relative).read_bytes())
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
            and isinstance(n.func.value,ast.Name) and n.func.value.id=='_active' and n.func.attr==hook]
        assert len(calls)==1
        if hook=='provider_result':
            assert [ast.unparse(a) for a in calls[0].args]==['answer']
            assert any(isinstance(n,ast.Return) and n.value is calls[0] for n in ast.walk(tree))
        else:
            assert [ast.unparse(a) for a in calls[0].args]==['request','result']
            main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
            index=next(i for i,n in enumerate(main.body) if isinstance(n,ast.Expr) and n.value is calls[0])
            reset=main.body[index+1]
            assert isinstance(reset,ast.Assign) and ast.unparse(reset)=='_transport = {}'
    # The parent's reviewed execution algorithm is an exact frozen preimage.
    manifest=json.loads(builder.BASE_MANIFEST.read_bytes())
    item=next(x for x in manifest['files'] if x['path']=='stockfish_core/yaogu/provider_requests.py')
    assert hashlib.sha256((provider_tree['base']/item['path']).read_bytes()).hexdigest()==item['sha256']


def test_actual_parent_and_child_complete_same_scope_then_allow_expanded_next_day(provider_tree):
    tree=provider_tree;code='600101'
    claim,token=_claim(tree,code)
    result=_request(tree,claim,token,code,'COMPLETE')
    assert result['returned'] is True,result
    answer=result['answer']
    assert answer['status']=='COMPLETE' and answer['pagination_complete'] is True
    assert answer['parameters']=={'codes':['sh.'+code],'start_date':'2026-11-01','end_date':'2026-11-04'}
    assert answer['diagnostics']['parameters']==answer['parameters']
    assert answer['diagnostics']['worker_pid']!=result['parent_pid']
    assert answer['coverage']['sdk_operations']==2 and answer['coverage']['http_attempts']==0
    rows,events=_rows(tree,claim);_assert_scope(rows,code)
    assert {r['state'] for r in rows}=={'COMPLETED'}
    assert [event['completed_effects'] for event in events]==[3,0]
    assert all(json.loads(row['receipt'])['source_authority_restored'] is False for row in rows)
    tree['owner'].finish(claim,success=True,receipt={'fixture':'TEST_FIXTURE','status':answer['status']})
    expanded,new_token=_claim(tree,code,end='2026-11-05')
    again=_request(tree,expanded,new_token,code,'COMPLETE',end='2026-11-05')
    assert again['returned'] and again['answer']['pagination_complete'] is True,again
    rows,events=_rows(tree,expanded);_assert_scope(rows,code,end='2026-11-05')
    assert {r['state'] for r in rows}=={'COMPLETED'}
    tree['owner'].finish(expanded,success=True,receipt={'fixture':'TEST_FIXTURE','status':'COMPLETE'})


@pytest.mark.parametrize('mode,code,status',[('PARTIAL','600102','PARTIAL'),
    ('EXCEPTION','600103','FETCH_FAILED'),('MISSING_PAGINATION','600104','COMPLETE')])
def test_partial_exception_and_missing_pagination_leave_overlap_nonretryable(provider_tree,mode,code,status):
    tree=provider_tree;claim,token=_claim(tree,code)
    result=_request(tree,claim,token,code,mode)
    assert result['returned'] is True,result  # ordinary function return is insufficient
    assert result['answer']['status']==status
    assert result['answer']['pagination_complete'] is False
    rows,events=_rows(tree,claim);_assert_scope(rows,code)
    assert {row['state'] for row in rows}=={'STARTED'} and events==[]
    # Even a caller recording the returned request as successful must not turn
    # its unresolved source/transport effects into a completed source request.
    tree['owner'].finish(claim,success=True,receipt={'fixture':'TEST_FIXTURE','status':status})
    rows,_=_rows(tree,claim);assert {row['state'] for row in rows}=={'UNKNOWN'}
    expanded,next_token=_claim(tree,code,end='2026-11-05')
    blocked=_request(tree,expanded,next_token,code,'COMPLETE',end='2026-11-05')
    assert blocked['returned'] is False
    assert 'AUTHORITY_SOURCE_SCOPE_OVERLAPS_FAILED_OR_UNKNOWN_NO_RETRY' in blocked['error']
    assert _rows(tree,expanded)==([],[])
    tree['owner'].finish(expanded,success=False)


def test_parent_archive_exception_keeps_business_failure_despite_child_completion(provider_tree):
    tree=provider_tree;code='600105';claim,token=_claim(tree,code)
    result=_request(tree,claim,token,code,'ARCHIVE_FAILURE')
    assert result['returned'] is False and result['error']=='TEST_FIXTURE_PARENT_ARCHIVE_FAILURE'
    rows,events=_rows(tree,claim);_assert_scope(rows,code)
    assert {row['state'] for row in rows}=={'COMPLETED'}
    assert [event['completed_effects'] for event in events]==[3]
    tree['owner'].finish(claim,success=False)
    expanded,next_token=_claim(tree,code,end='2026-11-05')
    blocked=_request(tree,expanded,next_token,code,'COMPLETE',end='2026-11-05')
    assert blocked['returned'] is False
    assert 'AUTHORITY_SOURCE_SCOPE_OVERLAPS_FAILED_OR_UNKNOWN_NO_RETRY' in blocked['error']
    assert _rows(tree,expanded)==([],[])
    tree['owner'].finish(expanded,success=False)
