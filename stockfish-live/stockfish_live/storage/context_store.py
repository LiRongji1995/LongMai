"""Content-addressed external context with captured raw parent evidence."""
from pathlib import Path

from ..common import LiveError, digest
from ..contracts.context import ContextBundle
from .parquet_store import (ParquetStore, SOURCE_VERSION, _checked_file, _json_bytes,
                            _load_json, _publish, _sha, _writer_lock)


class ContextStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().absolute()

    def _directory(self):
        folder = self.root / "context"
        if any(path.is_symlink() for path in (self.root, folder, self.root / "sources")):
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        return folder

    def save(self, raw_bundle, raw_by_source):
        bundle = ContextBundle.model_validate(raw_bundle.model_dump(mode="json"))
        folder = self._directory()
        for parent in bundle.receipts:
            raw = raw_by_source.get(parent.source_id)
            if not isinstance(raw, bytes) or _sha(raw) != parent.source_id:
                raise LiveError("SOURCE_HASH_MISMATCH")
        with _writer_lock(self.root):
            folder.mkdir(parents=True, exist_ok=True)
            sources = self.root / "sources"
            sources.mkdir(exist_ok=True)
            for parent in bundle.receipts:
                receipt_bytes = _json_bytes(parent)
                manifest = _json_bytes({"format_version": SOURCE_VERSION, "source_id": parent.source_id,
                    "raw_sha256": parent.source_id, "receipt_sha256": _sha(receipt_bytes)})
                for suffix, content in ((".raw", raw_by_source[parent.source_id]),
                                        (".receipt.json", receipt_bytes), (".manifest.json", manifest)):
                    _publish(sources / (parent.source_id + suffix), content, accept_identical=True)
            content = _json_bytes(bundle)
            target = folder / f"bundle-{digest(bundle)}.json"
            _publish(target.with_suffix(".manifest.json"), _json_bytes({"schema_version": "LIVE_CONTEXT_ARCHIVE_V1",
                "file": target.name, "content_hash": _sha(content)}), accept_identical=True)
            _publish(target, content, accept_identical=True)
        return target

    def load(self):
        folder = self._directory()
        paths = sorted(folder.glob("bundle-*.json")) if folder.exists() else []
        paths = [path for path in paths if not path.name.endswith(".manifest.json")]
        if not paths:
            return ContextBundle()
        bundles = []
        for path in paths:
            content = _checked_file(path)
            manifest = _load_json(path.with_suffix(".manifest.json"))
            if manifest != {"schema_version": "LIVE_CONTEXT_ARCHIVE_V1", "file": path.name, "content_hash": _sha(content)}:
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            bundle = ContextBundle.model_validate_json(content)
            if path.name != f"bundle-{digest(bundle)}.json":
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            for parent in bundle.receipts:
                if ParquetStore(self.root)._source(parent.source_id) != parent:
                    raise LiveError("STORAGE_INTEGRITY_ERROR")
            bundles.append(bundle)
        values = {}
        for name in ContextBundle.model_fields:
            if name == "schema_version":
                continue
            unique = {digest(item): item for bundle in bundles for item in getattr(bundle, name)}
            values[name] = tuple(unique[key] for key in sorted(unique))
        return ContextBundle(**values)
