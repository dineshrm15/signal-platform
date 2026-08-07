"""Snapshot storage for derived-signal services.

The platform contract (docs/00-platform-architecture.md): calc workers are the only
writers, MCP servers are read-only consumers. SQLite backs local/dev deployments;
a DynamoDB implementation can replace it behind the same protocol.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class StoredSnapshot:
    service: str
    kind: str
    as_of: datetime
    payload: dict[str, Any]


class SnapshotStore(Protocol):
    def put(self, service: str, kind: str, as_of: datetime, payload: dict[str, Any]) -> None: ...

    def put_many(
        self, items: Iterable[tuple[str, str, datetime, dict[str, Any]]]
    ) -> None: ...

    def get_latest(self, service: str, kind: str) -> StoredSnapshot | None: ...

    def get_since(
        self, service: str, kind: str, since: datetime, limit: int = 1000
    ) -> list[StoredSnapshot]: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    service TEXT NOT NULL,
    kind    TEXT NOT NULL,
    as_of   TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (service, kind, as_of)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_lookup ON snapshots (service, kind, as_of DESC);
"""


def _to_iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def _from_iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


class SqliteSnapshotStore:
    """SQLite-backed store. Upserts on (service, kind, as_of) so re-runs are idempotent."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def put(self, service: str, kind: str, as_of: datetime, payload: dict[str, Any]) -> None:
        self.put_many([(service, kind, as_of, payload)])

    def put_many(self, items: Iterable[tuple[str, str, datetime, dict[str, Any]]]) -> None:
        rows = [
            (service, kind, _to_iso(as_of), json.dumps(payload, default=str))
            for service, kind, as_of, payload in items
        ]
        with self._conn:
            self._conn.executemany(
                "INSERT INTO snapshots (service, kind, as_of, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (service, kind, as_of) DO UPDATE SET payload = excluded.payload",
                rows,
            )

    def get_latest(self, service: str, kind: str) -> StoredSnapshot | None:
        row = self._conn.execute(
            "SELECT as_of, payload FROM snapshots WHERE service = ? AND kind = ? "
            "ORDER BY as_of DESC LIMIT 1",
            (service, kind),
        ).fetchone()
        if row is None:
            return None
        return StoredSnapshot(service, kind, _from_iso(row[0]), json.loads(row[1]))

    def get_since(
        self, service: str, kind: str, since: datetime, limit: int = 1000
    ) -> list[StoredSnapshot]:
        rows = self._conn.execute(
            "SELECT as_of, payload FROM snapshots WHERE service = ? AND kind = ? AND as_of >= ? "
            "ORDER BY as_of ASC LIMIT ?",
            (service, kind, _to_iso(since), limit),
        ).fetchall()
        return [
            StoredSnapshot(service, kind, _from_iso(as_of), json.loads(payload))
            for as_of, payload in rows
        ]

    def close(self) -> None:
        self._conn.close()
