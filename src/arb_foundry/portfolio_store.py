"""Durable SQLite snapshots for paper portfolio state.

Snapshots are append-only and checksummed to detect accidental corruption. A
checksum is not a tamper-proof signature and this store has no exchange access.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from arb_foundry.errors import DataValidationError
from arb_foundry.paper import PaperTradeResult
from arb_foundry.portfolio import PortfolioLedger
from arb_foundry.time_utils import ensure_utc, utc_now


def _portfolio_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError("portfolio_id must not be empty")
    normalized = value.strip()
    if any(character.isspace() for character in normalized):
        raise DataValidationError("portfolio_id must not contain whitespace")
    return normalized


def _canonical_snapshot(ledger: PortfolioLedger) -> tuple[str, str]:
    payload = json.dumps(
        ledger.to_snapshot(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return payload, digest


class PortfolioStateStore:
    """Append-only, recoverable portfolio snapshots stored in SQLite."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id TEXT NOT NULL,
                saved_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                sha256 TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_latest
            ON portfolio_snapshots(portfolio_id, snapshot_id DESC)
            """
        )
        self._connection.commit()

    def save(
        self,
        ledger: PortfolioLedger,
        *,
        portfolio_id: str = "default",
        saved_at: datetime | None = None,
    ) -> int:
        """Append one complete ledger snapshot and return its ID."""
        if not isinstance(ledger, PortfolioLedger):
            raise DataValidationError("ledger must be a PortfolioLedger")
        identity = _portfolio_id(portfolio_id)
        timestamp = ensure_utc(saved_at) if saved_at is not None else utc_now()
        payload, digest = _canonical_snapshot(ledger)
        cursor = self._connection.execute(
            """
            INSERT INTO portfolio_snapshots (
                portfolio_id, saved_at, schema_version, payload_json, sha256
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (identity, timestamp.isoformat(), 1, payload, digest),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def apply_trade_and_save(
        self,
        ledger: PortfolioLedger,
        result: PaperTradeResult,
        *,
        portfolio_id: str = "default",
        now: datetime | None = None,
        saved_at: datetime | None = None,
    ) -> int:
        """Commit candidate accounting state before publishing it to in-memory state."""
        if not isinstance(ledger, PortfolioLedger):
            raise DataValidationError("ledger must be a PortfolioLedger")
        if not isinstance(result, PaperTradeResult):
            raise DataValidationError("result must be a PaperTradeResult")
        candidate = PortfolioLedger.from_snapshot(ledger.to_snapshot())
        candidate.apply_trade(result, now=now)
        snapshot_id = self.save(candidate, portfolio_id=portfolio_id, saved_at=saved_at)
        ledger._replace_with(candidate)
        return snapshot_id

    def load_latest(self, *, portfolio_id: str = "default") -> PortfolioLedger | None:
        """Restore the latest snapshot, verifying its checksum and schema."""
        identity = _portfolio_id(portfolio_id)
        row = self._connection.execute(
            """
            SELECT payload_json, sha256, schema_version
            FROM portfolio_snapshots
            WHERE portfolio_id = ?
            ORDER BY snapshot_id DESC
            LIMIT 1
            """,
            (identity,),
        ).fetchone()
        if row is None:
            return None
        payload = row["payload_json"]
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if expected != row["sha256"]:
            raise DataValidationError("portfolio snapshot checksum mismatch")
        if row["schema_version"] != 1:
            raise DataValidationError("unsupported stored portfolio snapshot schema_version")
        try:
            snapshot = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise DataValidationError("stored portfolio snapshot is invalid JSON") from exc
        return PortfolioLedger.from_snapshot(snapshot)

    def list_snapshots(
        self, *, portfolio_id: str = "default", limit: int = 100
    ) -> list[dict[str, object]]:
        """List snapshot metadata newest first; payloads are excluded."""
        identity = _portfolio_id(portfolio_id)
        if not 1 <= limit <= 10_000:
            raise DataValidationError("limit must be between 1 and 10000")
        rows = self._connection.execute(
            """
            SELECT snapshot_id, portfolio_id, saved_at, schema_version, sha256
            FROM portfolio_snapshots
            WHERE portfolio_id = ?
            ORDER BY snapshot_id DESC
            LIMIT ?
            """,
            (identity, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """Close the SQLite connection."""
        self._connection.close()

    def __enter__(self) -> PortfolioStateStore:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
