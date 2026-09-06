"""Offline reconstructed artifact authority stays separate from live publication."""
import json
from datetime import timedelta
from statistics import mean
from scripts.yaogu_history_study import execute
from stockfish_core.yaogu.contracts import CandidateRun,MarketEmotionSnapshot,digest
from stockfish_core.yaogu.engine import run
from tests.yaogu.fixtures import make_bundle


def test_history_script_reads_exact_three_same_scope_artifacts(tmp_path):
    b,c=make_bundle();source=tmp_path/'TEST_FIXTURE_market.json'
    source.write_text(b.model_dump_json(),encoding='utf-8')
    output=tmp_path/'TEST_FIXTURE_reconstructed_study'
    summary=execute([source],output,allow_test_fixture=True)
    assert summary['exception_count']==0
    runs=[CandidateRun.model_validate(r) for r in json.loads((output/'candidate-runs.json').read_text(encoding='utf-8'))]
    artifacts=json.loads((output/'reconstructed-emotion-artifacts.json').read_text(encoding='utf-8'))
    assert len(runs)==len(artifacts)==25
    assert all(r.emotion.readings['d'] is None for r in runs[:3])
    assert runs[3].emotion.readings['d']==runs[3].emotion.score-mean(r.emotion.score for r in runs[:3])
    assert runs[3].emotion.history_selection['authorization_mode']=='RECONSTRUCTED_EVALUATION_ARTIFACT'
    assert all(a['artifact_hash']==digest(a['artifact']) for a in artifacts)
    assert all(a['artifact']['publication_authority']['not_a_prospective_publication'] for a in artifacts)
    assert len({a['scope_id'] for a in artifacts})==1
    selected=runs[3].emotion.history_selection['selected']
    assert [r['document_hash'] for r in selected]==[a['artifact_hash'] for a in artifacts[:3]]
    assert all(r['selection_reason']=='RECONSTRUCTED_SAME_SCOPE_ARTIFACT_NOT_PROSPECTIVE_PUBLICATION' for r in selected)


def test_production_engine_does_not_accept_reconstructed_history_authority(tmp_path):
    from stockfish_core.yaogu.emotion_history import reconstructed_artifact
    b,c=make_bundle();past=[]
    for day in b.sessions[-4:-1]:
        result=run(b.model_copy(update={'observation_date':day}),cutoff=c.replace(year=day.year,month=day.month,day=day.day),history_mode='RECONSTRUCTED_EVALUATION_ARTIFACT')
        past.append(reconstructed_artifact(result,scope_id='TEST_FIXTURE_SCOPE',generated_at=c+timedelta(hours=1)))
    injected=b.model_copy(update={'previous_emotions':tuple(past)})
    production=run(injected,cutoff=c)
    research=run(injected,cutoff=c,history_mode='RECONSTRUCTED_EVALUATION_ARTIFACT')
    assert production.emotion.readings['d'] is None and production.emotion.history_selection['selected']==[]
    assert research.emotion.readings['d'] is not None
    assert production.effective_input_hash!=research.effective_input_hash
