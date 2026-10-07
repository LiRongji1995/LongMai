from typing import Literal

DataQuality = Literal["VALID", "PARTIAL", "STALE", "GAPPED", "INVALID"]
PITStatus = Literal["VERIFIED_PIT", "RECONSTRUCTED", "RESEARCH_ONLY", "TEST_FIXTURE"]
SourceTimeBasis = Literal["PUBLISHED", "RECEIVED", "UNKNOWN"]


def effective_pit_status(statuses) -> PITStatus:
    """Never upgrade a result beyond its weakest input provenance."""
    values = set(statuses)
    if "TEST_FIXTURE" in values:
        if len(values) != 1:
            from ..common import LiveError
            raise LiveError("DATA_PROVENANCE_MIXED")
        return "TEST_FIXTURE"
    if "RESEARCH_ONLY" in values or not values:
        return "RESEARCH_ONLY"
    if "RECONSTRUCTED" in values:
        return "RECONSTRUCTED"
    return "VERIFIED_PIT"
