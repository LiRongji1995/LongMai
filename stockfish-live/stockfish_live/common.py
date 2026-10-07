"""Small independent primitives, compatible with Research data semantics."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

SHANGHAI = ZoneInfo("Asia/Shanghai")


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class LiveError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return value.astimezone(SHANGHAI)


def canonical_instrument(value: str, exchange: str | None = None) -> str:
    code = value.strip().upper()
    if re.fullmatch(r"(?:SH|SZ|BJ)[.]?\d{6}", code):
        code = f"{code[-6:]}.{code[:2]}"
    if re.fullmatch(r"\d{6}", code) and exchange:
        code = f"{code}.{exchange.upper()}"
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
        raise ValueError("explicit exchange required")
    if exchange and code[-2:] != exchange.upper():
        raise ValueError("instrument/exchange mismatch")
    return code


def canonical_json(value: Any) -> str:
    def encode(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, datetime):
            return aware(item).isoformat()
        raise TypeError(f"unsupported JSON type: {type(item).__name__}")
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False, default=encode)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
