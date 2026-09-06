"""Local evidence registration. HTTP never accepts paths or provenance assertions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stockfish_core.storage import SQLiteStore
from stockfish_core.jobs import JobService
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu.contracts import MarketInput
from stockfish_core.yaogu.auction import AuctionSnapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True, help='Explicit SQLite destination; preserve existing user state.')
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--allow-test-fixture', action='store_true', help='Only use a separate fixture database/state.')
    sub = parser.add_subparsers(dest='command', required=True)
    market = sub.add_parser('register-input')
    market.add_argument('file', type=Path)
    market.add_argument('--activate', action='store_true', help='Requires complete DAILY_V1 consumer validation.')
    auction = sub.add_parser('register-auction')
    auction.add_argument('file', type=Path, help='JSON object containing quotes and coverage_type.')
    evidence = sub.add_parser('register-public-evidence')
    evidence.add_argument('file', type=Path, help='Provider-normalized features and original receipts JSON.')
    evidence.add_argument('--coverage-type', required=True,
                          choices=['PROSPECTIVE_CAPTURED','RECONSTRUCTED_HISTORICAL','UNVERIFIABLE_AS_OF','TEST_FIXTURE'])
    aliases = sub.add_parser('register-seat-aliases')
    aliases.add_argument('file', type=Path, help='Array of typed SeatAliasEvidence with previously registered source receipts.')
    args = parser.parse_args()
    if args.allow_test_fixture:
        production = Path(__file__).resolve().parents[1] / 'data'
        if args.db.resolve().is_relative_to(production) or args.state.resolve().is_relative_to(production):
            parser.error('TEST_FIXTURE cannot use the normal application data directory')
    jobs = JobService(SQLiteStore(args.db), max_workers=1, max_pending=5)
    try:
        service = CandidateService(jobs.store, jobs, args.state / 'yaogu', allow_test_fixture=args.allow_test_fixture)
        if args.command == 'register-input':
            bundle = MarketInput.model_validate_json(args.file.read_bytes())
            key = service.register_input(bundle, activate=args.activate)
            value = {'input_id': key, 'activated': args.activate, 'coverage_type': bundle.coverage_type,
                     'observation_date': str(bundle.observation_date), 'bar_rows': len(bundle.bars)}
        elif args.command == 'register-auction':
            raw = json.loads(args.file.read_text(encoding='utf-8'))
            if set(raw) != {'quotes', 'coverage_type'}:
                raise ValueError('auction envelope requires only quotes and coverage_type')
            key = service.register_auction(tuple(AuctionSnapshot.model_validate(q) for q in raw['quotes']),
                                           coverage_type=raw['coverage_type'])
            value = {'snapshot_id': key, 'coverage_type': raw['coverage_type']}
        elif args.command == 'register-public-evidence':
            raw = json.loads(args.file.read_text(encoding='utf-8'))
            value = service.public_evidence.register_public_records(raw['features'], raw['receipts'], coverage_type=args.coverage_type)
        else:
            from stockfish_core.yaogu.evidence_service import SeatAliasEvidence
            raw = json.loads(args.file.read_text(encoding='utf-8'))
            value = service.public_evidence.register_aliases(tuple(SeatAliasEvidence.model_validate(v) for v in raw))
        print(json.dumps(value, ensure_ascii=False, indent=2))
    finally:
        jobs.shutdown(wait=True)


if __name__ == '__main__':
    main()
