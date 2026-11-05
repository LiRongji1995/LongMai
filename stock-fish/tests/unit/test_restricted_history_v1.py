"""TEST_FIXTURE: bytes-only history never grants a runtime/source capability."""
import gzip
import hashlib
import json

import pytest

from scripts import restricted_history_v1 as history


def test_old_dates_and_unknowns_remain_and_runtime_is_not_loaded(tmp_path):
    document=dict(run_id="TEST_FIXTURE_run",observation_date="2026-10-15",target_session="2026-10-16",
                  last_authenticated_at="2026-10-24T10:00:00Z",missing_return=None,status="UNKNOWN")
    path=tmp_path/"TEST_FIXTURE_run.json.gz";path.write_bytes(gzip.compress(json.dumps(document).encode()))
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    result=history.read_history(path,sha,root=tmp_path,include_document=True)
    assert result["saved_document"]==document
    assert result["saved_fields"]["observation_date"]=="2026-10-15"
    assert not any(result[k] for k in ["runtime_registration_checked","execution_authorized",
                                      "source_authority_restored","historical_pit_restored"])
    assert result["currentness"]=="NOT_REEVALUATED"


def test_same_byte_copy_allowed_but_tampering_and_alias_not_allowed(tmp_path):
    path=tmp_path/"TEST_FIXTURE.json";path.write_text('{"run_id":"TEST_FIXTURE"}')
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    copy=tmp_path/"TEST_FIXTURE_copy.json";copy.write_bytes(path.read_bytes())
    assert history.read_history(copy,sha,root=tmp_path)["status"]=="SAVED_BYTES_READ_ONLY"
    alias=tmp_path/"alias.json";alias.symlink_to(path)
    with pytest.raises(ValueError):
        history.read_history(alias,sha,root=tmp_path)
    copy.write_text('{}')
    with pytest.raises(ValueError,match="SHA_MISMATCH"):
        history.read_history(copy,sha,root=tmp_path)


def test_decompression_limit_duplicate_key_and_nonfinite_rejected(tmp_path):
    for name,raw,limit,reason in [
        ("large.gz",gzip.compress(b'"'+b'x'*1000+b'"'),100,"DECODED_BYTE_LIMIT"),
        ("duplicate.json",b'{"a":1,"a":2}',100,"DUPLICATE_JSON_KEY"),
        ("nan.json",b'{"a":NaN}',100,"NONFINITE_JSON")]:
        path=tmp_path/name;path.write_bytes(raw)
        with pytest.raises(ValueError,match=reason):
            history.read_history(path,hashlib.sha256(raw).hexdigest(),root=tmp_path,max_decoded_bytes=limit)
