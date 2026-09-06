"""Export explicit synthetic engine evidence only; never opens a runtime database."""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import csv
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.yaogu.fixtures import make_bundle
from stockfish_core.yaogu.engine import run

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True)
    # Preserve already-delivered evidence generations; a source change must not
    # silently replace the old run/hash files under the same evidence names.
    if any(out.glob('TEST_FIXTURE_*')):
        out=out/('generation_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
        out.mkdir(exist_ok=False)
    for count in (6,31):
        bundle,cutoff=make_bundle(count)
        result=run(bundle,cutoff=cutoff,run_id=f'TEST_FIXTURE_{count}_V1')
        (out/f'TEST_FIXTURE_{count}_input.json').write_text(bundle.model_dump_json(indent=2),encoding='utf-8')
        (out/f'TEST_FIXTURE_{count}_candidate_run.json').write_text(result.model_dump_json(indent=2),encoding='utf-8')
        with (out/f'TEST_FIXTURE_{count}_all_candidates.csv').open('w',encoding='utf-8',newline='') as fp:
            writer=csv.DictWriter(fp,fieldnames=['coverage_type','instrument_id','family','secondary_tags','tier','rank','score','B','V','T','L','G','M','selected_display','selected_focus','reason_codes','missing'])
            writer.writeheader()
            for item in result.items:
                writer.writerow(dict(coverage_type=result.coverage_type,instrument_id=item.instrument_id,family=item.primary_family,secondary_tags='|'.join(item.secondary_tags),tier=item.tier,rank=item.family_rank,score=item.score,**item.components,selected_display=item.instrument_id in result.display_ids,selected_focus=item.instrument_id in result.focus_ids,reason_codes='|'.join(item.reason_codes),missing='|'.join(item.missing)))
        print(json.dumps({'coverage_type':'TEST_FIXTURE','evidence_directory':str(out.resolve()),'universe_count':count,'candidate_count':len(result.items),'display_count':len(result.display_ids),'status':result.status,'semantic_hash':result.semantic_hash},ensure_ascii=False))
if __name__=='__main__':main()
