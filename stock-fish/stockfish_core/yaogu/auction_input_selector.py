"""Resolve registered archives into one factual auction revision per frozen key.

New unrelated, late, 09:30, or source-invalid archives cannot hide an earlier
valid 09:25 observation. The source clocks remain unchanged in the merged input.
"""
from __future__ import annotations
from datetime import time
from .contracts import digest
from .auction import AuctionSnapshot,ThesisCondition,SH
from .visibility import EvidenceResolver,clock_visible


def select_auction_inputs(run,archives,cutoff):
    keys=set()
    for item in run.items:
        for value in item.thesis_conditions:
            frozen=ThesisCondition.model_validate(value)
            if frozen.expected_session is not None:
                keys.add((item.instrument_id,frozen.expected_session))
                keys.update((iid,frozen.expected_session) for iid in frozen.peer_ids)
    selected={};conflicts=set()
    for archive in archives:
        mode=archive.get('coverage_type')
        if mode not in ('PROSPECTIVE_CAPTURED','RECONSTRUCTED_HISTORICAL','TEST_FIXTURE'):
            continue
        if mode=='TEST_FIXTURE' and run.coverage_type!='TEST_FIXTURE':continue
        resolver=EvidenceResolver(archive.get('receipts',()),cutoff=cutoff,coverage_type=mode,sessions=archive.get('sessions',()))
        archive_id='auction_'+digest(archive)
        for value in archive.get('quotes',()):
            # Time and frozen identity are read before price/quality fields.
            if not clock_visible(value,cutoff,allow_late_capture=mode=='RECONSTRUCTED_HISTORICAL'):continue
            key=(value.get('instrument_id'),str(value.get('session')))
            if not any(iid==key[0] and str(day)==key[1] for iid,day in keys):continue
            quote=AuctionSnapshot.model_validate(value)
            if quote.event_time.astimezone(SH).time().replace(tzinfo=None)!=time(9,25) or quote.quality!='VALID':continue
            result=resolver.resolve(quote,instrument_id=quote.instrument_id,session=quote.session,
                source_refs=(quote.source_ref,),source_types={'AUCTION_0925'},require_complete=True,require_session=True)
            if not result.allowed:continue
            source=resolver.receipts[quote.source_ref]
            if quote.instrument_id not in source.instrument_ids or quote.session not in source.business_sessions:continue
            k=(quote.instrument_id,quote.session)
            previous=selected.get(k)
            if previous is None or quote.ingested_at>previous['quote'].ingested_at:
                selected[k]={'quote':quote,'source':source,'archive_id':archive_id,'coverage_type':mode}
                conflicts.discard(k)
            elif quote.ingested_at==previous['quote'].ingested_at:
                if quote!=previous['quote'] or source!=previous['source']:
                    conflicts.add(k)
                elif archive_id<previous['archive_id']:
                    previous['archive_id']=archive_id
    for key in conflicts:selected.pop(key,None)
    if not selected:return None
    sources={};source_conflicts=set()
    for value in selected.values():
        r=value['source']
        if r.receipt_id in sources and sources[r.receipt_id]!=r:source_conflicts.add(r.receipt_id)
        sources[r.receipt_id]=r
    selected={k:v for k,v in selected.items() if v['quote'].source_ref not in source_conflicts}
    if not selected:return None
    refs={v['quote'].source_ref for v in selected.values()}
    modes={v['coverage_type'] for v in selected.values()}
    # TEST_FIXTURE stays isolated; any historical reconstruction is labelled as
    # such even if some included observations were captured prospectively.
    mode='TEST_FIXTURE' if 'TEST_FIXTURE' in modes else 'RECONSTRUCTED_HISTORICAL' if 'RECONSTRUCTED_HISTORICAL' in modes else 'PROSPECTIVE_CAPTURED'
    ordered=[selected[k] for k in sorted(selected)]
    return {'quotes':tuple(v['quote'] for v in ordered),'receipts':tuple(sources[r] for r in sorted(refs)),
        'sessions':tuple(sorted({k[1] for k in selected})),'coverage_type':mode,
        'selection_evidence':{'policy_id':'R03_FROZEN_AUCTION_ARCHIVE_RESOLUTION_V1',
            'run_id':run.run_id,'cutoff':cutoff.isoformat(),
            'source_archive_ids':sorted({v['archive_id'] for v in ordered}),
            'selected_keys':[{'instrument_id':v['quote'].instrument_id,'session':str(v['quote'].session),
                              'source_ref':v['quote'].source_ref,'source_archive_id':v['archive_id']} for v in ordered]}}
