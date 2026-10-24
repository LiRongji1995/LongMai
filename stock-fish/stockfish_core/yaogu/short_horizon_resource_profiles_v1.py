"""Explicit verification resources; default archive behavior is unchanged.

This is an execution resource contract, not a strategy, freshness, or evidence
policy. Increasing time never disables physical/source/content verification.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


VERSION = 'SHORT_HORIZON_VERIFICATION_RESOURCE_PROFILE_V1'
FULL_ARCHIVE_900S_V1 = 'FULL_ARCHIVE_900S_V1'
VERIFICATION_RESOURCE_PROFILES = (FULL_ARCHIVE_900S_V1,)


def resolve_verification_profile(profile=None):
    """Return an independent immutable-by-convention description, or legacy None."""
    if profile is None:
        return None
    if type(profile) is not str or profile not in VERIFICATION_RESOURCE_PROFILES:
        raise ValueError('SHORT_HORIZON_VERIFICATION_PROFILE_INVALID')
    return {
        'schema_version': VERSION,
        'profile': FULL_ARCHIVE_900S_V1,
        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'limits': {
            'max_seconds': 900,
            'max_rss_bytes': 2 * 1024**3,
            'max_total_read_bytes': 8 * 1024**3,
            'max_unique_bytes': 4 * 1024**3,
            'max_files': 4096,
        },
    }


def session_options(profile=None):
    """Exact public ArchiveVerificationSession kwargs; legacy calls stay empty."""
    value = resolve_verification_profile(profile)
    return {} if value is None else dict(value['limits'])
