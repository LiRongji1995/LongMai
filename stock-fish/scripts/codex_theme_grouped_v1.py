"""Explicit, source-pinned theme candidate grouping; original V2 math stays intact.

This adapter only replaces the candidate comprehension's complete snapshot scan
with an invocation-local, order-preserving theme-id index. Every original
predicate, resolver call, journal operation and terminal validation is retained.
The generated function has a separate source identity and cannot be used as a
cross-request source capability. No imported module's globals are modified.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat
from types import FunctionType

from stockfish_core.yaogu import premarket_theme_context_v1 as native
from stockfish_core.yaogu import theme_evolution_streaming_v2 as original
from stockfish_core.yaogu.contracts import ThemeSnapshot
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.premarket_runtime_v1 import Budget

VERSION = 'PREMARKET_THEME_GROUPED_EXECUTION_V1'
ROOT = original.base.ROOT
REFERENCE_SOURCE_SHA256 = 'fcf84eaeba8bf7b3e745f30c3b0ad527cdfd231d884696bc06e82176c68586d8'
_FP = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_flags')
_CANDIDATE = """[row for row in themes if row.theme_id == theme_id
    and row.theme_type in base.CONFIG['allowed_theme_types']
    and row.effective_from <= date.fromisoformat(day)
    and (row.effective_to is None or date.fromisoformat(day) < row.effective_to)
    and resolver.resolve(row, session=day, require_complete=True).allowed]"""


def _need(condition, reason):
    if not condition:
        raise ValueError(VERSION + '_' + reason)


def _clone(function, replacements):
    namespace = {**function.__globals__, **replacements}
    result = FunctionType(function.__code__, namespace, function.__name__,
                          function.__defaults__, function.__closure__)
    result.__kwdefaults__ = function.__kwdefaults__
    result.__annotations__ = function.__annotations__
    return result


def source_identity():
    prior = original.source_identity()
    files = dict(prior['source_file_hashes'])
    for path in (Path(__file__).resolve(), Path(native.__file__).resolve()):
        files[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {**prior, 'implementation_version': VERSION,
            'feature_version': 'THEME_GROUPED_EXACT_CONTEXT_V1',
            'spec_revision': 'THEME_GROUPED_V1_INLINE_CONTRACT',
            'source_file_hashes': files, 'source_hash': digest_json_stream(files),
            'reference_execution_source_identity': prior,
            'reference_execution_sha256': REFERENCE_SOURCE_SHA256}


def _fp(info):
    return {key: getattr(info, key, 0) for key in _FP}


def _read(path, budget=None, expected=None):
    """Use the caller's original meter, or the same full read for fixtures."""
    if budget is not None:
        return budget.read(path, expected=expected)
    path = Path(path).absolute()
    before = path.lstat()
    _need(stat.S_ISREG(before.st_mode), 'SOURCE_MUST_BE_REGULAR')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        _need(_fp(os.fstat(stream.fileno())) == _fp(before), 'SOURCE_OPEN_IDENTITY_CHANGED')
        raw = stream.read()
        _need(_fp(os.fstat(stream.fileno())) == _fp(before), 'SOURCE_READ_IDENTITY_CHANGED')
    _need(_fp(path.lstat()) == _fp(before), 'SOURCE_PATH_IDENTITY_CHANGED')
    ref = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
           'bytes': len(raw), 'fp': _fp(before)}
    if expected is not None:
        _need(ref == expected, 'SOURCE_TERMINAL_IDENTITY_CHANGED')
    return raw, ref


def _pin(identity, budget):
    references, reference_source = [], None
    original_path = Path(original.__file__).resolve()
    for relative, expected in identity['source_file_hashes'].items():
        path = ROOT / relative
        raw, ref = _read(path, budget)
        _need(ref['sha256'] == expected, 'SOURCE_IDENTITY_CHANGED')
        references.append(ref)
        if path.resolve() == original_path:
            _need(ref['sha256'] == REFERENCE_SOURCE_SHA256, 'UNSUPPORTED_REFERENCE_EXECUTION')
            reference_source = raw
    _need(reference_source is not None, 'REFERENCE_EXECUTION_NOT_PINNED')
    return references, reference_source


def _terminal(references, identity, budget):
    for reference in references:
        _read(reference['path'], budget, reference)
    _need(source_identity() == identity, 'IMPLEMENTATION_CHANGED')


class _ThemeGroups:
    """Structural index only; never a cached evidence resolution."""
    def __init__(self, themes):
        self.themes = themes
        self.keys = tuple(row.theme_id for row in themes)
        self.groups = {}
        for row in themes:
            _need(type(row) is ThemeSnapshot and type(row.theme_id) is str,
                  'EXACT_FROZEN_THEME_REQUIRED')
            self.groups.setdefault(row.theme_id, []).append(row)
        self.groups = {key: tuple(rows) for key, rows in self.groups.items()}
        self.lookups = self.rows_visited = 0

    def check(self):
        # The field is frozen, but reject a caller bypassing Pydantic via
        # __dict__ or object.__setattr__. The original complete input/descriptor
        # hashes still check every value, including nested mutable fields.
        _need(tuple(row.theme_id for row in self.themes) == self.keys,
              'THEME_INDEX_KEYS_CHANGED')

    def for_id(self, theme_id):
        rows = self.groups.get(theme_id, ())
        _need(all(row.theme_id == theme_id for row in rows), 'THEME_INDEX_KEYS_CHANGED')
        self.lookups += 1
        self.rows_visited += len(rows)
        return rows

    def metrics(self):
        return {'scope': 'INVOCATION_LOCAL_STRUCTURAL_INDEX_NOT_SOURCE_AUTHORITY',
                'snapshot_count': len(self.themes), 'group_count': len(self.groups),
                'candidate_group_lookups': self.lookups, 'candidate_rows_visited': self.rows_visited,
                'original_predicates_and_resolver_order_preserved': True}


def _compiled(source):
    """Accept only the exact reference source and one exact comprehension."""
    _need(hashlib.sha256(source).hexdigest() == REFERENCE_SOURCE_SHA256,
          'UNSUPPORTED_REFERENCE_EXECUTION')
    tree = ast.parse(source, filename=original.__file__)
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name == 'run_theme_evolution_stream']
    _need(len(matches) == 1, 'REFERENCE_FUNCTION_SHAPE')
    function = deepcopy(matches[0])
    expected = ast.dump(ast.parse(_CANDIDATE, mode='eval').body, include_attributes=False)
    candidates = [node for node in ast.walk(function) if isinstance(node, ast.ListComp)
                  and ast.dump(node, include_attributes=False) == expected]
    _need(len(candidates) == 1, 'REFERENCE_CANDIDATE_SHAPE')
    candidates[0].generators[0].iter = ast.parse('_theme_groups.for_id(theme_id)', mode='eval').body
    initialized = daily_checks = final_checks = metrics = 0
    for node in tuple(function.body):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'themes'
                                                for target in node.targets):
            position = function.body.index(node)
            function.body.insert(position + 1, ast.parse('_theme_groups = _ThemeGroups(themes)').body[0])
            initialized += 1
    for node in ast.walk(function):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Tuple)
                and [getattr(value, 'id', None) for value in node.target.elts] == ['index', 'day']):
            node.body.insert(0, ast.parse('_theme_groups.check()').body[0])
            daily_checks += 1
        if isinstance(node, ast.Try):
            for child in tuple(node.body):
                if (isinstance(child, ast.Assign) and isinstance(child.targets[0], ast.Tuple)
                        and getattr(child.targets[0].elts[0], 'id', None) == 'final_descriptor'):
                    node.body.insert(node.body.index(child), ast.parse('_theme_groups.check()').body[0])
                    final_checks += 1
    for node in tuple(function.body):
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].slice, ast.Constant)
                and node.targets[0].slice.value == 'context_content_hash'):
            function.body.insert(function.body.index(node), ast.parse(
                "result['execution_provenance']['theme_candidate_index'] = _theme_groups.metrics()").body[0])
            metrics += 1
    _need((initialized, daily_checks, final_checks, metrics) == (1, 1, 1, 1),
          'REFERENCE_EXECUTION_SHAPE')
    namespace = {**original.run_theme_evolution_stream.__globals__,
                 'source_identity': source_identity, '_ThemeGroups': _ThemeGroups,
                 '_terminal_binding': _clone(original._terminal_binding, {'source_identity': source_identity})}
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, str(Path(__file__).resolve()) + ':pinned_reference', 'exec'), namespace)
    return namespace['run_theme_evolution_stream']


def run_theme_evolution_stream(bundle, *, _budget=None, **kwargs):
    _need(type(_budget) is Budget or bundle.coverage_type == 'TEST_FIXTURE',
          'ORIGINAL_EXECUTION_BUDGET_REQUIRED')
    identity = source_identity()
    references, source = _pin(identity, _budget)
    execute = _compiled(source)
    result = execute(bundle, **kwargs)
    _terminal(references, identity, _budget)
    return result


def attach_theme_context_grouped(core_owner, *, budget, **kwargs):
    """Attach through the genuine original native issuer, journal and binder."""
    _need(type(budget) is Budget, 'ORIGINAL_EXECUTION_BUDGET_REQUIRED')
    native._owner_state(core_owner)
    identity = source_identity()
    references, source = _pin(identity, budget)
    stream_execute = _compiled(source)
    class Binding(native._FreshThemeBinding):
        __init__ = _clone(native._FreshThemeBinding.__init__, {'stream_identity': source_identity})
        __call__ = _clone(native._FreshThemeBinding.__call__, {'stream_identity': source_identity})
    execute = _clone(native.attach_theme_context, {
        'VERSION': VERSION, '_FreshThemeBinding': Binding,
        'run_theme_evolution_stream': stream_execute})
    result = execute(core_owner, budget=budget, **kwargs)
    _terminal(references, identity, budget)
    def validate():
        result.validate()
        _terminal(references, identity, budget)
        return True
    by_path = {}
    for reference in [*result.validation_references, *references]:
        previous = by_path.setdefault(reference['path'], reference)
        _need(previous == reference, 'VALIDATION_REFERENCE_CONFLICT')
    return native.ThemeContextResult(result.to_dict(), result.artifacts, validate, list(by_path.values()))
