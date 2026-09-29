"""SQLite persistence for poll history.

Every poll is stored as one row in ``polls`` (success or failure), with its
meters in ``readings``. Keeping failed polls lets the dashboard show *why* a
provider is stale, while still displaying the last successful values.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .models import PollResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    provider   TEXT    NOT NULL,
    polled_at  TEXT    NOT NULL,
    ok         INTEGER NOT NULL,
    error      TEXT
);
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id     INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    meter       TEXT    NOT NULL,
    label       TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL,
    limit_value REAL,
    resets_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_polls_provider ON polls(provider, id);
CREATE INDEX IF NOT EXISTS idx_readings_poll ON readings(poll_id);
"""


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


class Storage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        # WAL mode allows a reader (the future dashboard) to query while the
        # poller writes, without "database is locked" errors.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def save(self, result: PollResult) -> int:
        """Persist one poll result atomically and return its poll id."""
        with self.conn:  # Commits on success, rolls back on exception.
            cursor = self.conn.execute(
                "INSERT INTO polls (provider, polled_at, ok, error) VALUES (?, ?, ?, ?)",
                (result.provider, _iso(result.polled_at), int(result.ok), result.error),
            )
            poll_id = cursor.lastrowid
            self.conn.executemany(
                "INSERT INTO readings (poll_id, meter, label, kind, value, unit, limit_value, resets_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (poll_id, m.name, m.display_label, m.kind, m.value, m.unit, m.limit, _iso(m.resets_at))
                    for m in result.meters
                ],
            )
        return poll_id

    def latest_status(self) -> list[dict[str, Any]]:
        """Return, per provider, the newest poll outcome and the newest successful meters."""
        providers = [
            row["provider"]
            for row in self.conn.execute("SELECT DISTINCT provider FROM polls ORDER BY provider")
        ]
        status = []
        for provider in providers:
            last = self.conn.execute(
                "SELECT * FROM polls WHERE provider = ? ORDER BY id DESC LIMIT 1", (provider,)
            ).fetchone()
            last_ok = self.conn.execute(
                "SELECT * FROM polls WHERE provider = ? AND ok = 1 ORDER BY id DESC LIMIT 1",
                (provider,),
            ).fetchone()
            meters = []
            if last_ok is not None:
                meters = [
                    dict(row)
                    for row in self.conn.execute(
                        "SELECT meter, label, kind, value, unit, limit_value, resets_at "
                        "FROM readings WHERE poll_id = ? ORDER BY meter",
                        (last_ok["id"],),
                    )
                ]
            status.append(
                {
                    "provider": provider,
                    "last_polled_at": last["polled_at"],
                    "ok": bool(last["ok"]),
                    "error": last["error"],
                    "last_ok_at": last_ok["polled_at"] if last_ok else None,
                    "meters": meters,
                }
            )
        return status

    def close(self) -> None:
        self.conn.close()
