"""Tiny TTL caches for provider responses (database-backed in the app, in-memory in tests)."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Optional

from ..db import HttpCache, utcnow


def cache_key(kind: str, **params: Any) -> str:
    raw = kind + "|" + json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, Any]] = {}

    def get(self, key: str, ttl_s: float) -> Optional[Any]:
        item = self._data.get(key)
        if not item:
            return None
        value, stamp = item
        return value if utcnow() - stamp <= timedelta(seconds=ttl_s) else None

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (value, utcnow())


class DbCache:
    def __init__(self, session_factory) -> None:  # noqa: ANN001
        self.sf = session_factory

    def get(self, key: str, ttl_s: float) -> Optional[Any]:
        with self.sf() as db:
            row = db.get(HttpCache, key)
            if not row:
                return None
            stamp = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=utcnow().tzinfo)
            if utcnow() - stamp > timedelta(seconds=ttl_s):
                return None
            return json.loads(row.body)

    def set(self, key: str, value: Any) -> None:
        with self.sf() as db:
            row = db.get(HttpCache, key)
            body = json.dumps(value)
            if row:
                row.body, row.fetched_at = body, utcnow()
            else:
                db.add(HttpCache(key=key, body=body, fetched_at=utcnow()))
            db.commit()
