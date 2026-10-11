"""Explicit current Sina industry membership from authenticated source bytes.

The original SourceArtifactReader/default concept behavior is unchanged.
Only this opt-in subclass understands the two new industry datasets. Saved
context JSON is a display artifact, never a source capability or price source.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from .contracts import ThemeSnapshot
from .research_context_artifacts import SourceArtifactReader
from .visibility import SH

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / 'docs/INDUSTRY_SINA_CONTEXT_V1.md'
VERSION = 'CURRENT_SINA_INDUSTRY_CONTEXT_V1'
DEFINITION = 'CURRENT_SINA_INDUSTRY_DIRECTORY_COUNT_AND_ORIGINAL_MEMBERS_V1'
FIELD_VERSION = 'AKSHARE_1.18.94_SINA_INDUSTRY_ORIGINAL_COUNT_PAGES_V1'
FIXTURE_FIELD_VERSION = 'TEST_FIXTURE_SINA_INDUSTRY_V1'
DIRECTORY = 'industry_list_sina'
MEMBERS = 'industry_members_sina'
NODE = re.compile(r'hangye_[A-Za-z0-9_]+')
CLOCKS = ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')
DIRECTORY_URL = 'http://money.finance.sina.com.cn/q/view/newFLJK.php'
MEMBERS_URL = 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount'
MAX_RAW_BYTES = 8 * 1024**2


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    allow_nan=False).encode()).hexdigest()


def source_identity():
    names = ('industry_sina_context_v1.py', 'research_context_artifacts.py',
             'provider_requests.py', 'provider_requests_worker.py', 'provider_budget.py',
             'contracts.py', 'visibility.py', 'history_artifacts.py')
    files = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in (Path(__file__).with_name(name) for name in names)}
    files[str(SPEC.relative_to(ROOT))] = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    identity = dict(implementation_version=VERSION, feature_version=DEFINITION,
                    source_file_hashes=files, supported_field_version=FIELD_VERSION,
                    explicit_opt_in=True, default_source_reader_unchanged=True)
    return {**identity, 'source_hash': _digest(identity)}


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise ValueError('SINA_INDUSTRY_AWARE_CLOCK_REQUIRED')
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('SINA_INDUSTRY_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _count(value):
    if isinstance(value, bool):
        raise ValueError('SINA_INDUSTRY_COUNT_NOT_INTEGER')
    if isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]*', value):
        value = int(value)
    if not isinstance(value, int) or value < 0:
        raise ValueError('SINA_INDUSTRY_COUNT_NOT_INTEGER')
    return value


class IndustrySinaSourceArtifactReader(SourceArtifactReader):
    """Fresh filesystem source reader; registry weakening invalidates its scope.

    All supplied receipts/path registrations are bound, including sources not
    requested by a nodes subset. New industry original bytes are rechecked at
    both boundaries. Other theme datasets still use the base validator.
    """
    def __init__(self, receipts, paths):
        super().__init__(receipts, paths)
        self._issued_registry_hash = self._registry_hash()
        self._issued_identity = source_identity()

    def _registry_hash(self):
        return _digest(dict(receipts=[row.model_dump(mode='json') for row in self.receipts],
            lookup={key: row.model_dump(mode='json') for key, row in self._receipts.items()},
            paths={key: str(path) for key, path in self._paths.items()}))

    def _terminal_industry_sources(self):
        if self._registry_hash() != self._issued_registry_hash:
            raise ValueError('SINA_INDUSTRY_REGISTERED_SOURCE_SCOPE_CHANGED')
        if source_identity() != self._issued_identity:
            raise ValueError('SINA_INDUSTRY_ADAPTER_IDENTITY_CHANGED')
        for row in self.receipts:
            if row.dataset not in (DIRECTORY, MEMBERS):
                continue
            path = self._paths.get(row.receipt_id)
            if path is None or not path.is_file() or path.stat().st_size > MAX_RAW_BYTES:
                raise ValueError('SINA_INDUSTRY_ORIGINAL_SOURCE_UNAVAILABLE_OR_TOO_LARGE')
            self(row.receipt_id)  # The base capability checks complete raw SHA.

    def _payload(self, receipt, cutoff):
        if (receipt.status != 'COMPLETE' or not receipt.pagination_complete
                or max(getattr(receipt, key) for key in CLOCKS) > cutoff):
            raise ValueError('SINA_INDUSTRY_CAPTURE_PARTIAL_OR_AFTER_CUTOFF')
        fixture = receipt.provider == 'TEST_FIXTURE' and receipt.field_version == FIXTURE_FIELD_VERSION
        if not fixture and (receipt.provider.lower() != 'akshare' or receipt.field_version != FIELD_VERSION):
            raise ValueError('SINA_INDUSTRY_PROVIDER_SCHEMA_UNREGISTERED')
        expected_url = DIRECTORY_URL if receipt.dataset == DIRECTORY else MEMBERS_URL
        if not fixture and receipt.source_url != expected_url:
            raise ValueError('SINA_INDUSTRY_ORIGINAL_SOURCE_URL_MISMATCH')
        if (receipt.event_time != receipt.first_seen_at
                or receipt.source_published_at != receipt.first_seen_at):
            raise ValueError('SINA_INDUSTRY_CAPTURE_CLOCK_CANNOT_BE_BACKDATED')
        raw = self(receipt.receipt_id)
        try:
            payload = json.loads(raw, object_pairs_hook=_unique_object,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError('SINA_INDUSTRY_ORIGINAL_JSON_INVALID:' + str(error)) from error
        if (not isinstance(payload, dict) or payload.get('status') != 'COMPLETE'
                or payload.get('pagination_complete') is not True
                or payload.get('field_version') != receipt.field_version
                or not isinstance(payload.get('rows'), list)
                or len(payload['rows']) != receipt.row_count):
            raise ValueError('SINA_INDUSTRY_ORIGINAL_ENVELOPE_MISMATCH')
        if _aware(payload.get('first_seen_at')) != receipt.first_seen_at:
            raise ValueError('SINA_INDUSTRY_ORIGINAL_CAPTURE_CLOCK_MISMATCH')
        coverage = payload.get('coverage')
        proof = coverage.get('sina_industry') if isinstance(coverage, dict) else None
        if (not isinstance(proof, dict) or proof.get('contract') != 'SINA_INDUSTRY_ORIGINAL_COUNT_PAGES_V1'
                or proof.get('indicator') != '行业' or proof.get('complete') is not True
                or proof.get('reason_codes') != [] or type(proof.get('returned_rows')) is not int
                or proof.get('returned_rows') != len(payload['rows'])):
            raise ValueError('SINA_INDUSTRY_ORIGINAL_COUNT_PAGE_PROOF_MISSING')
        return payload, proof

    def _directory(self, receipt, cutoff):
        payload, proof = self._payload(receipt, cutoff)
        if (receipt.dataset != DIRECTORY or receipt.parameters != {}
                or proof.get('kind') != 'DIRECTORY_ONE_HTTP'
                or type(proof.get('directory_row_count')) is not int
                or proof.get('directory_row_count') != len(payload['rows'])):
            raise ValueError('SINA_INDUSTRY_DIRECTORY_REQUEST_OR_PROOF_MISMATCH')
        entries = {}
        for row in payload['rows']:
            if not isinstance(row, dict):
                raise ValueError('SINA_INDUSTRY_DIRECTORY_ROW_SCHEMA_INVALID')
            node, name = row.get('label'), row.get('板块')
            if not isinstance(node, str) or len(node) > 80 or not NODE.fullmatch(node):
                raise ValueError('SINA_INDUSTRY_DIRECTORY_NAMESPACE_INVALID')
            if node in entries:
                raise ValueError('SINA_INDUSTRY_DUPLICATE_DIRECTORY_NODE')
            if not isinstance(name, str) or not name.strip():
                raise ValueError('SINA_INDUSTRY_DIRECTORY_NAME_UNAVAILABLE')
            entries[node] = dict(name=name, count=_count(row.get('公司家数')))
        if not entries:
            raise ValueError('SINA_INDUSTRY_DIRECTORY_EMPTY_UNVERIFIED')
        return entries

    def _members(self, receipt, node, expected_count, cutoff):
        payload, proof = self._payload(receipt, cutoff)
        if (receipt.dataset != MEMBERS or receipt.parameters != {'symbol': node}
                or proof.get('kind') != 'NODE_COUNT_AND_ALL_80_ROW_PAGES'
                or proof.get('node') != node or type(proof.get('page_size')) is not int or proof.get('page_size') != 80
                or _count(proof.get('upstream_count')) != expected_count
                or len(payload['rows']) != expected_count):
            raise ValueError('SINA_INDUSTRY_DIRECTORY_MEMBER_COUNT_OR_NODE_MISMATCH')
        expected_pages = list(range(1, (expected_count + 79) // 80 + 1))
        expected_rows = [dict(page=page, rows=min(80, expected_count - (page-1)*80)) for page in expected_pages]
        for field in ('expected_pages', 'observed_pages'):
            if (not isinstance(proof.get(field), list)
                    or any(type(page) is not int for page in proof[field])):
                raise ValueError('SINA_INDUSTRY_MEMBER_PAGE_COVERAGE_UNVERIFIED')
        if (not isinstance(proof.get('page_row_counts'), list)
                or any(not isinstance(row, dict) or type(row.get('page')) is not int
                       or type(row.get('rows')) is not int for row in proof['page_row_counts'])):
            raise ValueError('SINA_INDUSTRY_MEMBER_PAGE_COVERAGE_UNVERIFIED')
        if (proof.get('expected_pages') != expected_pages or proof.get('observed_pages') != expected_pages
                or proof.get('page_row_counts') != expected_rows):
            raise ValueError('SINA_INDUSTRY_MEMBER_PAGE_COVERAGE_UNVERIFIED')
        ids = []
        for row in payload['rows']:
            if not isinstance(row, dict):
                raise ValueError('SINA_INDUSTRY_MEMBER_ROW_SCHEMA_INVALID')
            code, symbol = row.get('code'), row.get('symbol')
            if not isinstance(code, str) or re.fullmatch(r'(?:60|68|00|30)[0-9]{4}', code) is None:
                raise ValueError('SINA_INDUSTRY_MEMBER_IDENTITY_UNVERIFIED')
            exchange = 'sh' if code.startswith(('60', '68')) else 'sz'
            if symbol != exchange + code:
                raise ValueError('SINA_INDUSTRY_MEMBER_SOURCE_EXCHANGE_CONFLICT')
            ids.append(code + '.' + exchange.upper())
        if not ids or len(set(ids)) != len(ids):
            raise ValueError('SINA_INDUSTRY_MEMBERS_EMPTY_OR_DUPLICATE')
        return tuple(sorted(ids))

    @staticmethod
    def _latest(candidates):
        if not candidates:
            return None
        revision = lambda row: (row.ingested_at, row.first_seen_at)
        latest = max(revision(row) for row in candidates)
        tied = [row for row in candidates if revision(row) == latest]
        if len({(row.content_hash, row.field_version, _digest(row.parameters)) for row in tied}) != 1:
            raise ValueError('SINA_INDUSTRY_SAME_CLOCK_SOURCE_REVISION_CONFLICT')
        return min(tied, key=lambda row: row.receipt_id)

    def build_current_industry_context(self, *, cutoff, nodes):
        """Only requested nodes observed in the real current directory are exposed."""
        decision = _aware(cutoff)
        if isinstance(nodes, (str, bytes)):
            raise ValueError('SINA_INDUSTRY_EXPLICIT_NODE_SEQUENCE_REQUIRED')
        nodes = tuple(nodes)
        if (not nodes or len(nodes) > 32
                or any(not isinstance(node, str) or len(node) > 80 or not NODE.fullmatch(node) for node in nodes)
                or len(set(nodes)) != len(nodes)):
            raise ValueError('SINA_INDUSTRY_EXPLICIT_UNIQUE_INDUSTRY_NODES_REQUIRED')
        self._terminal_industry_sources()
        def visible(row):
            return row.status == 'COMPLETE' and row.pagination_complete and max(getattr(row, key) for key in CLOCKS) <= decision
        themes, unknown = [], []
        directory = self._latest([row for row in self.receipts if row.dataset == DIRECTORY
                                 and row.field_version != 'UPSTREAM_HTTP_BODY_V1' and visible(row)])
        entries = None
        if directory is not None:
            try:
                entries = self._directory(directory, decision)
            except (ValueError, TypeError, KeyError) as error:
                unknown.append(dict(node=None, reason=str(error)))
        else:
            unknown.append(dict(node=None, reason='CURRENT_COMPLETE_INDUSTRY_DIRECTORY_UNAVAILABLE'))
        for node in nodes:
            if entries is None or node not in entries:
                unknown.append(dict(node=node, reason='CURRENT_OBSERVED_INDUSTRY_NODE_UNAVAILABLE'))
                continue
            member = self._latest([row for row in self.receipts if row.dataset == MEMBERS
                and row.parameters.get('symbol') == node and row.field_version != 'UPSTREAM_HTTP_BODY_V1' and visible(row)])
            if member is None:
                unknown.append(dict(node=node, name=entries[node]['name'], reason='CURRENT_COMPLETE_INDUSTRY_MEMBERS_UNAVAILABLE'))
                continue
            try:
                ids = self._members(member, node, entries[node]['count'], decision)
                clocks = {key: max(getattr(directory, key), getattr(member, key)) for key in CLOCKS}
                theme = ThemeSnapshot(theme_id=DIRECTORY+':'+node, name=entries[node]['name'],
                    theme_type='INDUSTRY_PROXY', member_ids=ids,
                    effective_from=member.first_seen_at.astimezone(SH).date(),
                    definition_version=DEFINITION, source_refs=(member.receipt_id, directory.receipt_id), **clocks)
                self._validate_theme(theme, decision)
                themes.append(theme.model_dump(mode='json'))
            except (ValueError, TypeError, KeyError) as error:
                unknown.append(dict(node=node, name=entries[node]['name'], reason=str(error)))
        self._terminal_industry_sources()
        return dict(schema_version=VERSION, themes=themes, unknown=unknown,
            status='PARTIAL' if themes and unknown else 'AVAILABLE' if themes else 'UNKNOWN',
            requested_nodes=list(nodes), requested_industry_count=len(nodes),
            provider_directory_count=len(entries) if entries is not None else None,
            verified_requested_industry_count=len(themes), directory_source_ref=directory.receipt_id if directory else None,
            source_identity=self._issued_identity, registered_source_scope_hash=self._issued_registry_hash,
            source_coverage_type='TEST_FIXTURE' if any(row.provider == 'TEST_FIXTURE'
                for row in self.receipts if row.dataset in (DIRECTORY, MEMBERS)) else 'PROSPECTIVE_CAPTURED',
            data_role='CURRENT_PROVIDER_INDUSTRY_COHORT_NOT_PRICE_OR_MAINLINE_RANKING',
            historical_membership_verified=False, official_pit_validation_countable=False,
            economic_mainline_completeness='UNKNOWN', stock_selection_or_score_changed=False)

    def validate_theme_market_pack(self, pack, *, bundle, cutoff):
        # The guard only concerns the new test industry sources. Original
        # concept and market panel validation remains the base implementation.
        values = pack.model_dump(mode='json') if hasattr(pack, 'model_dump') else pack
        industry_refs = {ref for theme in values.get('themes', ()) for ref in theme.get('source_refs', ())
                         if ref in self._receipts and self._receipts[ref].dataset in (DIRECTORY, MEMBERS)}
        if (bundle.coverage_type != 'TEST_FIXTURE'
                and any(self._receipts[ref].provider == 'TEST_FIXTURE' for ref in industry_refs)):
            raise ValueError('SINA_INDUSTRY_TEST_FIXTURE_NOT_PRODUCTION_AUTHORITY')
        return super().validate_theme_market_pack(pack, bundle=bundle, cutoff=cutoff)

    def _validate_theme(self, theme, cutoff):
        parents = [self._receipts.get(ref) for ref in theme.source_refs]
        if not any(row is not None and row.dataset in (DIRECTORY, MEMBERS) for row in parents):
            return super()._validate_theme(theme, cutoff)
        decision = _aware(cutoff)
        self._terminal_industry_sources()
        directories = [row for row in parents if row is not None and row.dataset == DIRECTORY]
        members = [row for row in parents if row is not None and row.dataset == MEMBERS]
        if len(parents) != 2 or len(directories) != 1 or len(members) != 1 or any(row is None for row in parents):
            raise ValueError('SINA_INDUSTRY_EXACT_DIRECTORY_AND_MEMBER_PARENTS_REQUIRED')
        directory, member = directories[0], members[0]
        entries = self._directory(directory, decision)
        node = member.parameters.get('symbol')
        if node not in entries:
            raise ValueError('SINA_INDUSTRY_CURRENT_DIRECTORY_NODE_MISMATCH')
        ids = self._members(member, node, entries[node]['count'], decision)
        if (theme.theme_id != DIRECTORY+':'+node or theme.name != entries[node]['name']
                or theme.theme_type != 'INDUSTRY_PROXY' or theme.definition_version != DEFINITION
                or set(theme.member_ids) != set(ids) or len(theme.member_ids) != len(ids)):
            raise ValueError('SINA_INDUSTRY_THEME_NAME_NAMESPACE_OR_MEMBERS_MISMATCH')
        if theme.effective_from != member.first_seen_at.astimezone(SH).date() or theme.effective_to is not None:
            raise ValueError('SINA_INDUSTRY_CURRENT_MEMBERS_CANNOT_BE_BACKDATED')
        if any(getattr(theme, key) != max(getattr(row, key) for row in parents) for key in CLOCKS):
            raise ValueError('SINA_INDUSTRY_THEME_PARENT_FOUR_CLOCKS_MISMATCH')
        self._terminal_industry_sources()


def build_industry_context(reader, *, cutoff, nodes):
    if not isinstance(reader, IndustrySinaSourceArtifactReader):
        raise ValueError('SINA_INDUSTRY_FRESH_ORIGINAL_READER_REQUIRED_NOT_SAVED_JSON')
    return reader.build_current_industry_context(cutoff=cutoff, nodes=nodes)
