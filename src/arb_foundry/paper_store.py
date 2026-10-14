"""Append-only SQLite audit store for paper-trading outcomes and rejections.

This store never interacts with an exchange or stores credentials. It is an
append-only application interface, not a tamper-proof audit system.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from arb_foundry.errors import DataValidationError
from arb_foundry.paper import PaperTradeResult
from arb_foundry.time_utils import ensure_utc, utc_now


def _json_payload(result: PaperTradeResult) -> str:
    payload = {
        "status": result.status.value,
        "symbol": result.symbol,
        "buy_exchange": result.buy_exchange,
        "sell_exchange": result.sell_exchange,
        "requested_quantity": str(result.requested_quantity),
        "filled_quantity": str(result.filled_quantity),
        "remaining_quantity": str(result.remaining_quantity),
        "buy_vwap": str(result.buy_vwap) if result.buy_vwap is not None else None,
        "sell_vwap": str(result.sell_vwap) if result.sell_vwap is not None else None,
        "buy_notional": str(result.buy_notional),
        "sell_notional": str(result.sell_notional),
        "buy_fee": str(result.buy_fee),
        "sell_fee": str(result.sell_fee),
        "gross_pnl": str(result.gross_pnl),
        "net_pnl": str(result.net_pnl),
        "slippage_cost": str(result.slippage_cost),
        "rejection_reason": result.rejection_reason,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class PaperTradeStore:
    """SQLite-backed append/query interface for paper-trade audit events."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trade_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                buy_exchange TEXT NOT NULL,
                sell_exchange TEXT NOT NULL,
                buy_snapshot_at TEXT NOT NULL,
                sell_snapshot_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paper_events_symbol_time
            ON paper_trade_events(symbol, recorded_at)
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_rejection_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                buy_exchange TEXT NOT NULL,
                sell_exchange TEXT NOT NULL,
                buy_snapshot_at TEXT NOT NULL,
                sell_snapshot_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                context_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paper_rejections_symbol_time
            ON paper_rejection_events(symbol, recorded_at)
            """
        )
        self._connection.commit()

    def record(
        self,
        result: PaperTradeResult,
        *,
        buy_snapshot_at: datetime,
        sell_snapshot_at: datetime,
        recorded_at: datetime | None = None,
    ) -> int:
        """Persist one simulator result and return its event ID."""
        if not isinstance(result, PaperTradeResult):
            raise DataValidationError("result must be a PaperTradeResult")
        buy_time = ensure_utc(buy_snapshot_at)
        sell_time = ensure_utc(sell_snapshot_at)
        recorded_time = ensure_utc(recorded_at) if recorded_at is not None else utc_now()
        cursor = self._connection.execute(
            """
            INSERT INTO paper_trade_events (
                recorded_at, symbol, status, buy_exchange, sell_exchange,
                buy_snapshot_at, sell_snapshot_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recorded_time.isoformat(),
                result.symbol,
                result.status.value,
                result.buy_exchange,
                result.sell_exchange,
                buy_time.isoformat(),
                sell_time.isoformat(),
                _json_payload(result),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def record_rejection(
        self,
        *,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        buy_snapshot_at: datetime,
        sell_snapshot_at: datetime,
        reason: str,
        context: Mapping[str, object] | None = None,
        recorded_at: datetime | None = None,
    ) -> int:
        """Persist a pre-trade exception or risk rejection without a fill result."""
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise DataValidationError("rejection reason must not be empty")
        normalized_symbol = symbol.strip().upper()
        normalized_buy = buy_exchange.strip().lower()
        normalized_sell = sell_exchange.strip().lower()
        if not normalized_symbol or "/" not in normalized_symbol:
            raise DataValidationError("symbol must use BASE/QUOTE format")
        if not normalized_buy or not normalized_sell:
            raise DataValidationError("buy and sell exchange names must not be empty")
        buy_time = ensure_utc(buy_snapshot_at)
        sell_time = ensure_utc(sell_snapshot_at)
        recorded_time = ensure_utc(recorded_at) if recorded_at is not None else utc_now()
        context_json = json.dumps(
            dict(context or {}), sort_keys=True, separators=(",", ":"), default=str
        )
        cursor = self._connection.execute(
            """
            INSERT INTO paper_rejection_events (
                recorded_at, symbol, buy_exchange, sell_exchange, buy_snapshot_at,
                sell_snapshot_at, reason, context_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recorded_time.isoformat(),
                normalized_symbol,
                normalized_buy,
                normalized_sell,
                buy_time.isoformat(),
                sell_time.isoformat(),
                normalized_reason,
                context_json,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def get(self, event_id: int) -> dict[str, object] | None:
        """Return one simulation event, including its parsed payload, or None."""
        row = self._connection.execute(
            "SELECT * FROM paper_trade_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._to_trade_dict(row) if row is not None else None

    def list_events(self, *, limit: int = 100) -> list[dict[str, object]]:
        """Return newest simulation events first; limit must be between 1 and 10000."""
        if not 1 <= limit <= 10_000:
            raise DataValidationError("limit must be between 1 and 10000")
        rows = self._connection.execute(
            "SELECT * FROM paper_trade_events ORDER BY event_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._to_trade_dict(row) for row in rows]

    def list_rejections(self, *, limit: int = 100) -> list[dict[str, object]]:
        """Return newest rejected pre-trade attempts first."""
        if not 1 <= limit <= 10_000:
            raise DataValidationError("limit must be between 1 and 10000")
        rows = self._connection.execute(
            "SELECT * FROM paper_rejection_events ORDER BY event_id DESC LIMIT ?", (limit,)
        ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["context"] = json.loads(event.pop("context_json"))
            events.append(event)
        return events

    @staticmethod
    def _to_trade_dict(row: sqlite3.Row) -> dict[str, object]:
        event = dict(row)
        event["payload"] = json.loads(event.pop("payload_json"))
        return event

    def close(self) -> None:
        """Close the SQLite connection."""
        self._connection.close()

    def __enter__(self) -> PaperTradeStore:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
