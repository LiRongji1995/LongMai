"""Request-local JSON reuse inside the original complete capture verifier.

Every original read, SHA/physical check, scope assertion and terminal remains.
Only the native verifier's second JSON decode may borrow an already strictly
decoded private value.  The value never survives this one _authenticate call;
there is no cache of files, hashes, successful authentication or source power.
"""
from __future__ import annotations

import io
import types

from stockfish_core.yaogu import premarket_capture_cache_v1 as original


VERSION = "CODEX_CAPTURE_DECODED_V1"
_ROLES = ("result", "raw", "receipt")
_NATIVE_VERIFY = original.historical_replay.verify_capture
_NATIVE_VERIFY_CODE = _NATIVE_VERIFY.__code__


def _bind(function, replacements):
    namespace = dict(function.__globals__)
    namespace.update(replacements)
    value = types.FunctionType(function.__code__, namespace, function.__name__,
                               function.__defaults__, function.__closure__)
    value.__kwdefaults__ = function.__kwdefaults__
    return value


class _Facade:
    def __init__(self, parent, **bindings):
        self._parent = parent
        self.__dict__.update(bindings)

    def __getattr__(self, name):
        return getattr(self._parent, name)


class _DecodeScope:
    """Unborrowed lexical values, fresh-read witnesses and no persistent state."""
    def __init__(self, cache):
        self.cache = cache
        self.entries = {}
        self.pending_outer = None
        self.pending_native = None
        self.witness = None
        self.active = True
        self.metrics = cache.decoded_metrics

    def _need(self, condition, reason):
        original._need(self.active and condition, "DECODE_SCOPE_" + reason)

    def outer_read(self, *args, **kwargs):
        raw, reference = self.cache._read(*args, **kwargs)
        if len(self.entries) < len(_ROLES):
            self._need(self.pending_outer is None, "OUTER_READ_ORDER")
            self.pending_outer = (raw, reference)
        return raw, reference

    def decode(self, raw):
        self._need(self.pending_outer is not None and self.pending_outer[0] is raw,
                   "STRICT_DECODE_READ_BINDING")
        self._need(len(self.entries) < len(_ROLES), "STRICT_DECODE_COUNT")
        value = original._json(raw)
        role = _ROLES[len(self.entries)]
        _, reference = self.pending_outer
        self.pending_outer = None
        # RESULT is never reused.  Its object identity binds the native call;
        # only RAW and receipt need their original bytes retained temporarily.
        self.entries[role] = (raw if role != "result" else None, value, reference)
        self.metrics["strict_decodes"] += 1
        retained = sum(len(entry[0]) for entry in self.entries.values() if entry[0] is not None)
        self.metrics["max_retained_source_bytes"] = max(
            self.metrics["max_retained_source_bytes"], retained)
        return value

    def native_read(self, *args, **kwargs):
        raw, reference = self.cache._read(*args, **kwargs)
        matches = [entry for role, entry in self.entries.items()
                   if role != "result" and entry[2]["path"] == reference["path"]]
        self._need(len(matches) == 1, "NATIVE_READ_PATH")
        entry = matches[0]
        self._need(type(raw) is bytes and reference == entry[2] and raw == entry[0],
                   "NATIVE_SOURCE_CHANGED")
        self.pending_native = entry
        self.witness = None
        self.metrics["fresh_native_reads"] += 1
        return raw, reference

    def text_wrapper(self, *args, **kwargs):
        # Preserve the exact original TextIOWrapper decoding, including UTF-8
        # errors and universal newline handling.  Object identity proves that
        # loads receives the text returned by this particular fresh read.
        wrapped = io.TextIOWrapper(*args, **kwargs)
        scope = self
        entry = self.pending_native
        eligible = kwargs.get("encoding") == "utf-8" and kwargs.get("errors") is None

        class Witness:
            def read(self, *read_args, **read_kwargs):
                value = wrapped.read(*read_args, **read_kwargs)
                scope.witness = (value, entry) if eligible and entry is not None else None
                return value

        return Witness()

    def native_loads(self, text, *args, **kwargs):
        witness, self.witness = self.witness, None
        self._need(self.active, "EXPIRED")
        # json.loads(bytes) accepts an UTF-8 BOM but json.loads(str) rejects it.
        # Other signatures/texts retain the native decoder's precise behavior.
        if (not args and not kwargs and type(text) is str and witness is not None
                and text is witness[0] and not text.startswith("\ufeff")):
            self.metrics["native_json_reuses"] += 1
            return witness[1][1]
        self.metrics["native_fallback_decodes"] += 1
        return original.historical_replay.json.loads(text, *args, **kwargs)

    def native_verify(self, archive_root, result, refs):
        self._need(tuple(self.entries) == _ROLES and self.pending_outer is None,
                   "INCOMPLETE_STRICT_TRIO")
        self._need(result is self.entries["result"][1], "RESULT_BINDING")
        self._need(tuple(refs) == tuple(self.entries[role][2] for role in ("raw", "receipt")),
                   "NATIVE_REFERENCES_CHANGED")
        native = original.historical_replay.verify_capture
        if (native is not _NATIVE_VERIFY or not isinstance(native, types.FunctionType)
                or native.__code__ is not _NATIVE_VERIFY_CODE):
            # A diagnostic/test collaborator must still run and may reject.
            # No parsed-object shortcut is granted to an unknown callable.
            return original.CaptureCache._native_verify(self.cache, archive_root, result, refs)
        verifier = _bind(native, {"json": _Facade(original.historical_replay.json,
                                                   loads=self.native_loads)})
        operation = _bind(original.CaptureCache._native_verify, {
            "historical_replay": _Facade(original.historical_replay, verify_capture=verifier),
            "io": _Facade(io, TextIOWrapper=self.text_wrapper),
        })
        reader = _Facade(self.cache, _read=self.native_read)
        return operation(reader, archive_root, result, refs)

    def close(self):
        self.active = False
        self.entries.clear()
        self.pending_outer = self.pending_native = self.witness = None


class CaptureCache(original.CaptureCache):
    """Original cache with a bounded, single-authentication decode lifetime."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.decoded_metrics = dict(authentication_calls=0, strict_decodes=0,
            fresh_native_reads=0, native_json_reuses=0, native_fallback_decodes=0,
            max_retained_source_bytes=0)

    def _authenticate(self, *args, **kwargs):
        self.decoded_metrics["authentication_calls"] += 1
        scope = _DecodeScope(self)
        proxy = _Facade(self, _read=scope.outer_read, _native_verify=scope.native_verify)
        authenticate = _bind(original.CaptureCache._authenticate, {"_json": scope.decode})
        try:
            return authenticate(proxy, *args, **kwargs)
        finally:
            scope.close()
