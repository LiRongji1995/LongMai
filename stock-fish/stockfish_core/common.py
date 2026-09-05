"""Small shared primitives; no provider, Flask, or Qlib imports."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


class CoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class UnavailableError(RuntimeError):
    """A dependency or trusted input is unavailable, never a synthetic result."""

    def __init__(self, reason: str, component: str = "core"):
        super().__init__(reason)
        self.reason = reason
        self.component = component


class Quality(str, Enum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    MOCK = "MOCK"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime is not an auditable instant")
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
