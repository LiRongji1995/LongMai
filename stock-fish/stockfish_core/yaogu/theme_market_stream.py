"""Authenticated, one-session source access for explicit annual research.

Original SHA, dated master scope, numeric and capture-clock checks use the
existing SourceArtifactReader. A stream descriptor is never a price authority;
panels are reconstructed from the original bytes again when consumed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

from .contracts import ThemeSnapshot
from .history_artifacts import digest_json_stream, digest_market_input
from .research_context_artifacts import SourceArtifactReader, RETURN_BASIS
from .theme_evolution import ThemeMarketSession
from .visibility import SH, stamp

VERSION = 'AUTHENTICATED_THEME_MARKET_SESSION_STREAM_V1'


def source_identity():
    root = Path(__file__).resolve().parents[2]
    names = ('theme_market_stream.py', 'research_context_artifacts.py',
             'short_horizon_participation.py', 'visibility.py', 'contracts.py')
    hashes = {str((Path(__file__).parent / name).relative_to(root)):
              hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
              for name in names}
    return {'implementation_version': VERSION, 'source_file_hashes': hashes,
            'source_hash': digest_json_stream(hashes)}


class SourceMarketStream:
    """A frozen source capability; at most one full panel is live internally."""
    def __init__(self, reader, bundle, *, cutoff, sessions, themes=(), return_basis=RETURN_BASIS):
        if not isinstance(reader, SourceArtifactReader):
            raise ValueError('THEME_STREAM_AUTHENTICATED_READER_REQUIRED')
        self._reader = reader
        self._cutoff = stamp(cutoff)
        if bundle.observation_date > self._cutoff.astimezone(SH).date():
            raise ValueError('THEME_STREAM_FUTURE_OBSERVATION')
        self._calendar = tuple(str(day) for day in bundle.sessions if day <= bundle.observation_date)
        self._requested = tuple(str(day) for day in sessions)
        if (len(set(self._requested)) != len(self._requested)
                or any(day not in self._calendar for day in self._requested)
                or self._requested != tuple(day for day in self._calendar if day in set(self._requested))):
            raise ValueError('THEME_STREAM_REQUEST_NOT_ORDERED_OBSERVED_CALENDAR')
        if return_basis not in (None, RETURN_BASIS):
            raise ValueError('CONTEXT_RETURN_BASIS_UNRECOGNIZED')
        self._return_basis = return_basis
        self._themes = tuple(ThemeSnapshot.model_validate(row) for row in themes)
        for theme in self._themes:
            reader._validate_theme(theme, self._cutoff)
        self._receipts = tuple(reader.receipts)
        self._receipt_identity = digest_json_stream([row.model_dump(mode='json') for row in self._receipts])
        self._resolver = reader._resolver(bundle, self._cutoff)
        panels, missing = {}, {}
        for day in self._requested:
            try:
                raw_panel = reader._session(self._resolver, day, return_basis)
            except ValueError as error:
                # Missing or not-yet-visible parents are UNKNOWN. A malformed
                # available source, wrong scope or conflicting revision fails.
                reason = str(error)
                prefix, _, details = reason.partition(':')
                unavailable = prefix in ('SHR2_MARKET_MARKET_DAILY_SOURCE_UNAVAILABLE',
                                         'SHR2_MARKET_MASTER_ALL_SOURCE_UNAVAILABLE')
                allowed = all(value == 'CAPTURE_AFTER_CUTOFF' or value == 'SOURCE_NOT_COMPLETE'
                              or value.startswith('SOURCE_STATUS_')
                              for value in details.split(',') if value)
                if not unavailable or not allowed:
                    raise
                missing[day] = {'status': 'UNKNOWN', 'reason_codes': [reason]}
                continue
            panel = ThemeMarketSession.model_validate(raw_panel)
            panels[day] = {'panel_hash': digest_json_stream(panel.model_dump(mode='json')),
                           'source_refs': list(panel.source_refs),
                           'source_content_hashes': {ref: reader._receipts[ref].content_hash
                                                     for ref in panel.source_refs},
                           'expected_instrument_count': len(panel.expected_instrument_ids),
                           'trading_row_count': len(panel.rows),
                           'nontrading_count': len(panel.nontrading_instrument_ids)}
            del raw_panel, panel
        self._panels = panels
        self._available = tuple(day for day in self._requested if day in panels)
        self._identity = source_identity()
        self._descriptor = {'schema_version': VERSION, 'source_input_hash': digest_market_input(bundle),
                            'observation_date': str(bundle.observation_date),
                            'decision_cutoff': self._cutoff.isoformat(),
                            'coverage_type': bundle.coverage_type,
                            'full_observed_calendar': list(self._calendar),
                            'requested_sessions': list(self._requested),
                            'available_sessions': list(self._available),
                            'missing_sessions': missing, 'panels': panels,
                            'source_receipt_identity_hash': self._receipt_identity,
                            'theme_snapshots': [row.model_dump(mode='json') for row in self._themes],
                            'return_basis': return_basis, 'source_identity': self._identity,
                            'data_role': 'CURRENT_SOURCE_CAPTURE_CONTEXT_NOT_HISTORICAL_FIRST_SEEN',
                            'official_pit_validation_countable': False}
        self._descriptor_hash = digest_json_stream(self._descriptor)

    @classmethod
    def from_reader(cls, reader, bundle, **kwargs):
        return cls(reader, bundle, **kwargs)

    @property
    def descriptor(self):
        return deepcopy(self._descriptor)

    @property
    def descriptor_hash(self):
        return self._descriptor_hash

    @property
    def calendar(self):
        return self._calendar

    @property
    def requested_sessions(self):
        return self._requested

    @property
    def available_sessions(self):
        return self._available

    @property
    def receipts(self):
        return self._receipts

    @property
    def themes(self):
        return self._themes

    @property
    def cutoff(self):
        return self._cutoff

    def _assert_integrity(self):
        if digest_json_stream(self._descriptor) != self._descriptor_hash:
            raise ValueError('THEME_STREAM_DESCRIPTOR_CHANGED')
        # Frozen models allow an object/value comparison here. Re-expanding
        # hundreds of full-market receipt scopes on every panel is unnecessary.
        mapped = tuple(self._reader._receipts[key] for key in sorted(self._reader._receipts))
        if self._reader.receipts != self._receipts or mapped != self._receipts:
            raise ValueError('THEME_STREAM_SOURCE_RECEIPT_CHANGED')
        if source_identity() != self._identity:
            raise ValueError('THEME_STREAM_SOURCE_IMPLEMENTATION_CHANGED')

    def validate_binding(self, bundle, cutoff):
        self._assert_integrity()
        if (digest_market_input(bundle) != self._descriptor['source_input_hash']
                or stamp(cutoff) != self._cutoff
                or bundle.coverage_type != self._descriptor['coverage_type']
                or str(bundle.observation_date) != self._descriptor['observation_date']
                or tuple(str(day) for day in bundle.sessions if day <= bundle.observation_date) != self._calendar):
            raise ValueError('THEME_STREAM_INPUT_BINDING_MISMATCH')
        for theme in self._themes:
            self._reader._validate_theme(theme, self._cutoff)

    def panel(self, day):
        self._assert_integrity()
        text = str(day)
        if text not in self._panels:
            return None
        panel = ThemeMarketSession.model_validate(self._reader._session(self._resolver, text, self._return_basis))
        expected = self._panels[text]
        if digest_json_stream(panel.model_dump(mode='json')) != expected['panel_hash']:
            raise ValueError('THEME_STREAM_PANEL_ORIGINAL_SOURCE_MISMATCH')
        return panel
