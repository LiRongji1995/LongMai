"""Explicit first-computation profiles with original C/128 encoding and V2 reads.

Both reference and optimized profiles have new execution identities. Original
encoders, budget, native owner, source terminals and supervisor remain intact.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_first_core as legacy
from scripts import codex_compact_core_v2 as core
from scripts import codex_verification_v2 as verification
from scripts import codex_core as original

VERSION = "CODEX_FIRST_NATIVE_CORE_VERIFY_V2"
REFERENCE = "FIRST_CORE_REFERENCE_VERIFY_V2"
OPTIMIZED = "FIRST_CORE_CANONICAL_128_VERIFY_V2"
PROFILES = (REFERENCE, OPTIMIZED)
need = original._need


def _provenance(runtime, profile, first_id):
    _options(profile, first_id)
    snapshot = core._snapshot(runtime.profile)
    return dict(schema_version=VERSION, execution_profile=profile, first_run_id=first_id,
        coordinator=original._reference(Path(__file__).resolve()),
        original_encoder_adapter=original._reference(Path(legacy.__file__).resolve()),
        native_implementation_digest=original._digest(snapshot),
        canonical_encoding="ORIGINAL" if profile == REFERENCE else "TYPED_ROWS_128_V1",
        hash_policy="FULL_ORIGINAL_CANONICAL_CONTENT_EVERY_CALL_NO_MEMOIZATION")


@contextmanager
def encoding_scope(profile):
    _options(profile, None)
    selected = legacy.REFERENCE if profile == REFERENCE else legacy.OPTIMIZED
    with legacy.encoding_scope(selected) as metrics:
        yield metrics


class _RuntimeFacade(legacy._RuntimeFacade):
    def verify(self):
        need(_provenance(self.runtime, self.provenance["execution_profile"],
                         self.provenance["first_run_id"]) == self.provenance,
             "FIRST_CORE_IMPLEMENTATION_CHANGED")

    def run_once(self, identity, action, validate):
        enriched = dict(schema_version=VERSION, execution=self.provenance, native_identity=identity)
        def act():
            self.verify()
            receipt = action()
            self.verify()
            receipt["first_core_execution"] = deepcopy(self.provenance)
            return receipt
        def valid(receipt):
            self.verify()
            need(receipt.get("first_core_execution") == self.provenance,
                 "FIRST_CORE_EXECUTION_RECEIPT_CHANGED")
            result = validate(receipt)
            self.verify()
            return result
        return self.runtime.run_once(enriched, act, valid)


_api = verification.bind_functions(legacy,
    ("_options", "execute_child", "_monitor_owned_job", "supervised_command", "main"),
    dict(VERSION=VERSION, REFERENCE=REFERENCE, OPTIMIZED=OPTIMIZED, PROFILES=PROFILES,
         core=core, _provenance=_provenance, _RuntimeFacade=_RuntimeFacade,
         subprocess=verification.isolated_subprocess(legacy.subprocess, __file__),
         encoding_scope=encoding_scope, __file__=__file__))
globals().update(_api)

if __name__ == "__main__":
    raise SystemExit(main())
