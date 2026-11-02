"""Meter the bounded bundle entry's control reads without changing its code.

Load this module by its explicitly bound absolute path outside the frozen
bundle. The bundle's scripts package and original execution budgets retain
their identities. This helper neither collects sources nor issues authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
import math
from pathlib import Path
import sys
from types import FunctionType, SimpleNamespace

VERSION = 'CODEX_METERED_BUNDLE_CONTROL_V1'
CONTROL_READ_RESERVE_BYTES = 8 * 1024**2
SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES = 16384


def need(condition, reason):
    if not condition:
        raise ValueError('METERED_BUNDLE_' + reason)


@contextmanager
def read_scope(control_budget):
    """Join an existing original meter; otherwise own one control scope."""
    from stockfish_core.yaogu import premarket_runtime_v1 as native_runtime
    need(isinstance(control_budget, native_runtime.Budget), 'ORIGINAL_CONTROL_BUDGET_REQUIRED')
    active = native_runtime._ACTIVE_METER
    if active is not None:
        need(isinstance(active,native_runtime.Budget) and active._meter_active is True,
             'INVALID_ACTIVE_ORIGINAL_METER')
        # The active original phase accounts for these same returned bytes.
        # Never borrow its allocation by charging the control allowance again.
        yield
    else:
        with control_budget.phase('capture'), control_budget.meter_files():
            yield


def _metered(function, budget):
    @wraps(function)
    def call(*args, **kwargs):
        with read_scope(budget):
            return function(*args, **kwargs)
    return call


def _namespace(original, **replacements):
    return SimpleNamespace(**{**vars(original), **replacements})


def _clone(function, replacements):
    value = FunctionType(function.__code__, {**function.__globals__, **replacements},
        function.__name__, function.__defaults__, function.__closure__)
    value.__kwdefaults__ = function.__kwdefaults__
    value.__annotations__ = dict(function.__annotations__)
    return value


def _shared_budget_factory(original):
    """One original allowance for every budget() call in one frozen run.

    The frozen coordinator calls its factory for the admission calendar gate,
    then for the action (or archived validation). They are the same execution,
    so their returned reads and elapsed time must accumulate in one ledger.
    """
    budget, expected = None, None

    def construct(limits):
        nonlocal budget, expected
        need(type(limits) is dict and all(type(key) is str and
            type(value) in (int,float) and math.isfinite(value) and value > 0
            for key,value in limits.items()), 'INVALID_INVOCATION_BUDGET_LIMITS')
        if budget is None:
            expected = deepcopy(limits)
            budget = original(limits)
        else:
            need(set(limits) == set(expected) and all(
                type(limits[key]) is type(expected[key]) and limits[key] == expected[key]
                for key in expected), 'INVOCATION_BUDGET_LIMITS_CHANGED')
        return budget

    construct.current = lambda: budget
    return construct


class _LiveSupervisorReads:
    """Check the live combined ledger without charging supervisor bytes twice."""
    def __init__(self, parent):
        from stockfish_core.yaogu import premarket_runtime_v1 as native_runtime
        need(isinstance(parent,native_runtime.Budget), 'ORIGINAL_PARENT_BUDGET_REQUIRED')
        need(parent.phase_name == 'capture', 'PARENT_CAPTURE_PHASE_REQUIRED')
        self.parent, self.returned, self.reserved_returned = parent, 0, 0
        # The frozen latest-job reference intentionally excludes this one
        # fixed-size final control from its reported S. Reserve it explicitly.
        parent.charge_read(SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES)

    def check(self):
        self.parent.check('live_inner_supervisor')
        used = self.parent.metrics['returned_read_bytes']['capture']
        if used + self.returned > self.parent.limits['capture_read_bytes']:
            self.parent._fail('COMBINED_PARENT_SUPERVISOR_READ_LIMIT_EXCEEDED')

    def wrap(self, callback):
        need(callable(callback), 'SUPERVISOR_READ_CALLBACK_REQUIRED')
        marker = getattr(callback,'_metered_bundle_live_gate',None)
        if marker is self:
            return callback
        need(marker is None, 'SUPERVISOR_READ_CALLBACK_FOREIGN_LEDGER')
        def charge(count):
            need(type(count) is int and count >= 0, 'INVALID_SUPERVISOR_READ_CHARGE')
            self.returned += count
            callback(count)  # Preserve exactly one original S charge.
            self.check()
        charge._metered_bundle_live_gate = self
        return charge

    def reserved(self, count):
        need(type(count) is int and count >= 0, 'INVALID_SUPERVISOR_RESERVED_READ')
        self.reserved_returned += count
        if self.reserved_returned > SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES:
            self.parent._fail('SUPERVISOR_FINAL_REFERENCE_RESERVE_EXCEEDED')
        self.check()


def _live_supervise(shared, parent, config, **kwargs):
    """Privately bind every original supervisor charge to the same allowance.

    Config bytes and original S accounting remain unchanged. The coordinator
    charges S once after this call; until then this gate tests P + S live.
    """
    live = _LiveSupervisorReads(parent)
    legacy = shared.legacy
    need(legacy.SEAL_BYTES == SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES,
         'SUPERVISOR_FINAL_REFERENCE_SIZE_CHANGED')

    class Guard(shared._SupervisorGuard):
        def reference(self, path, read_meter=None):
            if read_meter is None:
                need(self.job is not None and Path(path) == self.job/'final-control.json',
                     'UNRESERVED_SUPERVISOR_REFERENCE')
                callback = live.reserved
            else:
                callback = live.wrap(read_meter)
            return super().reference(path,callback)

        def verify(self, reference, read_meter=None):
            return super().verify(reference,live.wrap(read_meter))

        def tree_sample(self, pid, read_meter=None):
            return super().tree_sample(pid,live.wrap(read_meter))

    def code_refs(read_meter=None):
        return shared._code_refs(live.wrap(read_meter))

    def read_control(path, read_meter=None):
        return legacy._read_control(path,live.wrap(read_meter))

    def owned_group_members(pid, read_meter=None):
        return legacy._owned_group_members(pid,live.wrap(read_meter))

    inner = _clone(legacy.supervise,dict(_read_control=read_control,
        _owned_group_members=owned_group_members))
    proxy = _namespace(legacy,supervise=inner)
    invoke = _clone(shared.supervise,dict(legacy=proxy,_SupervisorGuard=Guard,
        _code_refs=code_refs))
    code, final = invoke(config,**kwargs)
    need(type(final.get('supervisor_returned_read_bytes')) is int and
         final['supervisor_returned_read_bytes'] == live.returned,
         'SUPERVISOR_RETURNED_READ_LEDGER_MISMATCH')
    return code,final


@contextmanager
def _metered_context(manager, budget):
    """Count only entry/exit, preserving the original context's suppression.

    A meter can fail immediately after __enter__ returned (for example its RSS
    terminal check). The already entered original lock must still be exited.
    """
    entered = False
    try:
        with read_scope(budget):
            value = manager.__enter__()
            entered = True
    except BaseException:
        if entered:
            exception = sys.exc_info()
            try:
                _exit_once(manager,budget,exception)
            except BaseException:
                pass  # Cleanup must not replace the original failed admission.
        raise
    try:
        yield value
    except BaseException:
        exception = sys.exc_info()
        try:
            suppress = _exit_once(manager,budget,exception)
        except BaseException:
            raise exception[1].with_traceback(exception[2])
        if not suppress:
            raise
    else:
        _exit_once(manager,budget,(None,None,None))


def _exit_once(manager,budget,exception):
    """An exhausted meter cannot prevent release of an already owned lock.

    The fallback is failed cleanup only. It cannot turn the enclosing call
    into success or create another allowance. Never call __exit__ twice when
    it was already entered before an exception or a meter terminal failure.
    """
    called = False
    try:
        with read_scope(budget):
            called = True
            return manager.__exit__(*exception)
    except BaseException:
        if not called:
            try:
                manager.__exit__(*exception)
            except BaseException:
                pass
        raise


class _Guard:
    def __init__(self, original, budget):
        self._original, self._budget = original, budget

    def __getattr__(self, name):
        return getattr(self._original, name)

    def check(self):
        with read_scope(self._budget):
            return self._original.check()


def _derived_only_reduces(original, derived):
    need({key:value for key,value in original.items() if key!='limits'} ==
         {key:value for key,value in derived.items() if key!='limits'}, 'DERIVED_CONFIGURATION_SEMANTICS_CHANGED')
    need(set(derived['limits']) == set(original['limits']) and all(
        type(value) in (int,float) and math.isfinite(value) and 0 < value <= original['limits'][name]
        for name,value in derived['limits'].items()), 'DERIVED_LIMITS_MUST_ONLY_DECREASE')


def bind(registered_runtime, guard, derived_config_path, control_budget):
    """Return the real facade and a privately metered copy of the frozen run.

    The fixed 8 MiB charge is a conservative reserve, not measured source IO.
    workspace.run_once reads at most one 1 MiB operation control, then writes
    at most two bounded controls. Its ancestor/operation scans, validation,
    physical guards and capacity inspection are separately metered below.
    """
    # Resolve these imports from the package already selected by the owning
    # fresh worker. Do not insert another source root or alter scripts.__path__.
    from scripts import codex_factor_checkpoint_premarket_v1 as gateway
    from stockfish_core.yaogu import premarket_runtime_v1 as native_runtime
    need(isinstance(control_budget,native_runtime.Budget), 'ORIGINAL_CONTROL_BUDGET_REQUIRED')
    need(isinstance(registered_runtime,gateway.recovery.RecoveryRuntime), 'ORIGINAL_RECOVERY_RUNTIME_REQUIRED')
    path = Path(derived_config_path).absolute()
    with read_scope(control_budget):
        need(path.resolve(strict=True) == path, 'DERIVED_CONFIGURATION_ALIAS')
        original = gateway.bounded.configuration(registered_runtime,gateway.bounded.PROFILE)
        derived = gateway.bounded.frozen.load_config(str(path))
        _derived_only_reduces(original,derived)
        guard.check()
    control_budget.charge_read(CONTROL_READ_RESERVE_BYTES,'capture')
    proxy = _Guard(guard,control_budget)

    class MeteredRuntime(gateway.BundleRuntime):
        def guard_ancestor_request(self, identity):
            with read_scope(control_budget):
                return super().guard_ancestor_request(identity)

        def preflight(self, **kwargs):
            with read_scope(control_budget):
                return super().preflight(**kwargs)

        @contextmanager
        def exclusive(self):
            # In particular, the action is not inside the control meter: the
            # unchanged original action opens its own exclusive Budget meter.
            with _metered_context(super().exclusive(),control_budget) as value:
                yield value

    facade = MeteredRuntime(registered_runtime,proxy)
    facade.profile['config'] = str(path)
    base = gateway.bound_functions(proxy)
    old = base.__globals__
    daily = _namespace(old['daily'],
        _snapshot=_metered(old['daily']._snapshot,control_budget),
        _prior_request=_metered(old['daily']._prior_request,control_budget))
    original = _namespace(old['original'],
        _prepared=_metered(old['original']._prepared,control_budget))
    recovery = _namespace(old['recovery'],
        reference=_metered(old['recovery'].reference,control_budget))
    need(old['Budget'] is native_runtime.Budget, 'ORIGINAL_RUN_BUDGET_REQUIRED')
    template = _clone(base,dict(daily=daily,original=original,recovery=recovery,
        configuration=_metered(old['configuration'],control_budget)))

    @wraps(template)
    def run(*args, **kwargs):
        # Both the globals and factory belong only to this invocation. A later
        # request cannot inherit a successful or failed previous allowance.
        factory = _shared_budget_factory(native_runtime.Budget)
        def supervise(config, **options):
            return _live_supervise(old['workflow'],factory.current(),config,**options)
        invoked = _clone(template,dict(Budget=factory,
            workflow=_namespace(old['workflow'],supervise=supervise)))
        return invoked(*args, **kwargs)

    facade.control_read_metering = dict(schema_version=VERSION,
        fixed_run_once_control_read_reserve_bytes=CONTROL_READ_RESERVE_BYTES,
        reserve_is_not_measured_io=True, active_original_meter_is_reused=True,
        single_original_parent_budget_per_invocation=True,
        live_parent_and_inner_supervisor_read_gate=True,
        supervisor_final_reference_read_reserve_bytes=SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES,
        control_scope_excludes_action_body=True, source_capability_issued=False)
    return facade,run
