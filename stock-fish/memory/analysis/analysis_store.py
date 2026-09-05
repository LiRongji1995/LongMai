"""Immutable complete legacy analysis archives; never a recommendation authority."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from stockfish_core.common import utc_now
from stockfish_core.storage import SQLiteStore
from memory.atomic_io import safe_component


class AnalysisStore:
    namespace = "research_archives"

    def __init__(self, base_dir: Path | None = None, *, store: SQLiteStore | None = None):
        self.store = store or SQLiteStore(Path(base_dir) / "archives.sqlite3" if base_dir else None)

    def save(self, *, symbol: str, state: dict, timestamp: str, employee_reports=None,
             cio_decision=None, prediction=None) -> str:
        safe_component(symbol)
        archive_id = "archive_" + uuid4().hex
        payload = {"archive_id": archive_id, "symbol": symbol, "state": state,
                   "legacy_timestamp": timestamp, "archived_at": utc_now().isoformat(),
                   "employee_reports": employee_reports, "cio_decision": cio_decision,
                   "prediction": prediction, "purpose": "LEGACY_RESEARCH_DIAGNOSTIC"}
        return self.store.put_document(self.namespace, archive_id, payload)

    def get(self, archive_id: str) -> dict | None:
        return self.store.get_document(self.namespace, archive_id)

    def list_history(self, symbol: str | None = None, limit: int = 100) -> list[dict]:
        # Iterate pages so querying one symbol never silently truncates after
        # unrelated archives. The compatibility store is low-volume.
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        result, offset = [], 0
        while True:
            page = self.store.list_documents(self.namespace, limit=1000, offset=offset)
            result.extend(row["payload"] for row in page if symbol is None or row["payload"]["symbol"] == symbol)
            if len(page) < 1000:
                break
            offset += len(page)
        return list(reversed(result))[:limit]

    def list_all_symbols(self) -> list[str]:
        symbols, offset = set(), 0
        while True:
            page = self.store.list_documents(self.namespace, limit=1000, offset=offset)
            symbols.update(row["payload"]["symbol"] for row in page)
            if len(page) < 1000:
                return sorted(symbols)
            offset += len(page)
