"""Bounded, byte-only historical reading without a runnable runtime registration.

This deliberately cannot restore a RAW capability, source/PIT certification or
permission to execute. A caller must pin the expected carrier SHA independently.
The original observation dates are returned without freshness reinterpretation.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from scripts import business_authority_v1 as a
from scripts import codex_runtime_recovery_v1 as old

VERSION = "RESTRICTED_HISTORY_V1"


def _pairs(rows):
    result = {}
    for key, value in rows:
        a.need(key not in result, "HISTORY_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def read_history(path, expected_sha256, *, root, max_carrier_bytes=32*1024**2,
                 max_decoded_bytes=128*1024**2, include_document=False):
    root, path = Path(root).absolute(), Path(path).absolute()
    a.need(root.resolve(strict=True) == root and root.is_dir(), "HISTORY_ROOT_ALIAS_REJECTED")
    a.need(path.is_relative_to(root) and path != root, "HISTORY_PATH_OUTSIDE_ROOT")
    a.need(type(max_carrier_bytes) is int and type(max_decoded_bytes) is int and
           max_carrier_bytes > 0 and max_decoded_bytes > 0, "HISTORY_POSITIVE_LIMIT_REQUIRED")
    a.need(isinstance(expected_sha256,str) and len(expected_sha256)==64 and
           all(c in "0123456789abcdef" for c in expected_sha256), "HISTORY_PINNED_CARRIER_SHA_REQUIRED")
    before = old.reference(path, maximum=max_carrier_bytes)
    a.need(before["sha256"] == expected_sha256, "HISTORY_CARRIER_SHA_MISMATCH")
    raw = path.read_bytes()
    a.need(hashlib.sha256(raw).hexdigest() == expected_sha256, "HISTORY_CARRIER_CHANGED")
    if path.suffix == ".gz":
        with gzip.GzipFile(fileobj=io.BytesIO(raw),mode="rb") as stream:
            decoded=stream.read(max_decoded_bytes+1)
    else:
        decoded=raw
    a.need(len(decoded)<=max_decoded_bytes,"HISTORY_DECODED_BYTE_LIMIT")
    def reject_constant(value):
        raise a.AuthorityError("HISTORY_NONFINITE_JSON")
    document=json.loads(decoded,object_pairs_hook=_pairs,parse_constant=reject_constant)
    a.need(isinstance(document,dict),"HISTORY_OBJECT_REQUIRED")
    a.need(old.reference(path,maximum=max_carrier_bytes)==before,"HISTORY_CHANGED_DURING_READ")
    fields={key:document.get(key) for key in ("run_id","strategy_id","schema_version",
        "observation_date","target_session","as_of","data_cutoff","created_at",
        "last_full_authenticated_at","last_authenticated_at")}
    result=dict(schema=VERSION,status="SAVED_BYTES_READ_ONLY",carrier_sha256=expected_sha256,
        carrier_bytes=len(raw),decoded_bytes=len(decoded),saved_fields=fields,
        provenance="CALLER_PINNED_SAVED_CARRIER_NOT_CURRENT_SOURCE_CERTIFICATION",
        currentness="NOT_REEVALUATED",source_authority_restored=False,
        historical_pit_restored=False,runtime_registration_checked=False,
        execution_authorized=False,market_edge="NOT_ESTABLISHED")
    if include_document:
        result["saved_document"]=document
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument("--path",type=Path,required=True)
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--sha256",required=True)
    parser.add_argument("--max-carrier-bytes",type=int,default=32*1024**2)
    parser.add_argument("--max-decoded-bytes",type=int,default=128*1024**2)
    parser.add_argument("--include-document",action="store_true")
    args=parser.parse_args(argv)
    result=read_history(args.path,args.sha256,root=args.root,max_carrier_bytes=args.max_carrier_bytes,
                        max_decoded_bytes=args.max_decoded_bytes,include_document=args.include_document)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
