"""A dumb, local SQLite cache for API responses.

The point isn't performance -- it's being a decent citizen of two
free, public, no-API-key APIs while you iterate on plots, and not
re-fetching the same day's data every time you tweak a chart. Same
pattern as glasshouse's ingestion/ SQLite storage.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Cache:
    def __init__(self, path: str | Path = "wattstack_ingestion_cache.sqlite"):
        self.path = Path(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            "key TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        self._conn.commit()

    def get(self, key: str):
        row = self._conn.execute("SELECT payload FROM cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key: str, payload) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, fetched_at, payload) VALUES (?, ?, ?)",
            (key, datetime.now(timezone.utc).isoformat(), json.dumps(payload)),
        )
        self._conn.commit()

    def clear(self, prefix: str | None = None) -> int:
        if prefix:
            cur = self._conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"{prefix}%",))
        else:
            cur = self._conn.execute("DELETE FROM cache")
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()
