"""Persistent, fail-closed kill switch for paper-simulation workflows.

This module only gates local research simulations. It does not control an
exchange connection or provide a distributed lock for multiple processes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from arb_foundry.errors import DataValidationError
from arb_foundry.time_utils import ensure_utc, utc_now


class KillSwitchState(StrEnum):
    HALTED = "halted"
    ARMED = "armed"


@dataclass(frozen=True, slots=True)
class KillSwitchStatus:
    """Current persisted kill-switch state."""

    state: KillSwitchState
    reason: str
    changed_at: datetime
    event_id: int


def _reason(value: str) -> str:
    if not isinstance(value, str):
        raise DataValidationError("kill-switch reason must be a string")
    normalized = value.strip()
    if not normalized:
        raise DataValidationError("kill-switch reason must not be empty")
    if len(normalized) > 500:
        raise DataValidationError("kill-switch reason must be at most 500 characters")
    return normalized


class KillSwitchStore:
    """SQLite-backed state and append-only transition history.

    A new database starts HALTED. Only an explicit :meth:`arm` call permits
    simulations. Missing/invalid state and SQLite read errors never grant
    permission. The history is append-only through this API, not tamper-proof.
    """

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS kill_switch_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                state TEXT NOT NULL CHECK (state IN ('halted', 'armed')),
                reason TEXT NOT NULL,
                changed_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS kill_switch_current (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                event_id INTEGER NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('halted', 'armed')),
                reason TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                FOREIGN KEY(event_id) REFERENCES kill_switch_events(event_id)
            )
            """
        )
        self._connection.commit()
        # Missing current state is treated as a first boot or a damaged/missing
        # row. Initialize it to HALTED; never silently re-arm on restart.
        self._initialize_halted_if_missing()

    def _initialize_halted_if_missing(self) -> None:
        with self._connection:
            row = self._connection.execute(
                "SELECT state FROM kill_switch_current WHERE singleton_id = 1"
            ).fetchone()
            if row is not None:
                return
            timestamp = utc_now().isoformat()
            reason = "initialized fail-closed; explicit arm required"
            cursor = self._connection.execute(
                "INSERT INTO kill_switch_events(state, reason, changed_at) VALUES (?, ?, ?)",
                (KillSwitchState.HALTED.value, reason, timestamp),
            )
            self._connection.execute(
                """
                INSERT INTO kill_switch_current(singleton_id, event_id, state, reason, changed_at)
                VALUES (1, ?, ?, ?, ?)
                """,
                (int(cursor.lastrowid), KillSwitchState.HALTED.value, reason, timestamp),
            )

    def status(self) -> KillSwitchStatus:
        """Read the persisted state; database errors propagate and block callers."""
        row = self._connection.execute(
            """
            SELECT c.event_id, c.state, c.reason, c.changed_at, e.event_id AS event_exists
            FROM kill_switch_current AS c
            LEFT JOIN kill_switch_events AS e ON e.event_id = c.event_id
            WHERE c.singleton_id = 1
            """
        ).fetchone()
        if row is None:
            raise DataValidationError("kill-switch state missing; simulation must remain halted")
        try:
            state = KillSwitchState(row["state"])
        except ValueError as exc:
            raise DataValidationError("kill-switch state is invalid; simulation must remain halted") from exc
        if row["event_exists"] is None:
            raise DataValidationError("kill-switch audit event missing; simulation must remain halted")
        try:
            changed_at = ensure_utc(datetime.fromisoformat(row["changed_at"]))
        except (TypeError, ValueError) as exc:
            raise DataValidationError("kill-switch timestamp invalid; simulation must remain halted") from exc
        return KillSwitchStatus(
            state=state,
            reason=row["reason"],
            changed_at=changed_at,
            event_id=int(row["event_id"]),
        )

    def require_armed(self) -> KillSwitchStatus:
        """Return state only when explicitly armed; otherwise fail closed."""
        status = self.status()
        if status.state is not KillSwitchState.ARMED:
            raise DataValidationError(
                f"paper simulation blocked by kill switch: {status.state.value} ({status.reason})"
            )
        return status

    def arm(self, reason: str, *, changed_at: datetime | None = None) -> KillSwitchStatus:
        """Explicitly enable the paper-simulation gate and append a transition."""
        return self._transition(KillSwitchState.ARMED, reason, changed_at=changed_at)

    def halt(self, reason: str = "manual stop", *, changed_at: datetime | None = None) -> KillSwitchStatus:
        """Persist a stop immediately for subsequent gate checks."""
        return self._transition(KillSwitchState.HALTED, reason, changed_at=changed_at)

    def _transition(
        self,
        state: KillSwitchState,
        reason: str,
        *,
        changed_at: datetime | None,
    ) -> KillSwitchStatus:
        normalized_reason = _reason(reason)
        timestamp = ensure_utc(changed_at) if changed_at is not None else utc_now()
        timestamp_text = timestamp.isoformat()
        # BEGIN IMMEDIATE serializes state transitions within SQLite. Current
        # state and its audit event are committed in one transaction.
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                "INSERT INTO kill_switch_events(state, reason, changed_at) VALUES (?, ?, ?)",
                (state.value, normalized_reason, timestamp_text),
            )
            event_id = int(cursor.lastrowid)
            self._connection.execute(
                """
                INSERT INTO kill_switch_current(singleton_id, event_id, state, reason, changed_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    event_id = excluded.event_id,
                    state = excluded.state,
                    reason = excluded.reason,
                    changed_at = excluded.changed_at
                """,
                (event_id, state.value, normalized_reason, timestamp_text),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return KillSwitchStatus(state, normalized_reason, timestamp, event_id)

    def list_events(self, *, limit: int = 100) -> list[dict[str, object]]:
        """Return transition history newest first."""
        if type(limit) is not int or not 1 <= limit <= 10_000:
            raise DataValidationError("limit must be an integer between 1 and 10000")
        rows = self._connection.execute(
            """
            SELECT event_id, state, reason, changed_at
            FROM kill_switch_events ORDER BY event_id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """Close the SQLite connection."""
        self._connection.close()

    def __enter__(self) -> KillSwitchStore:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
