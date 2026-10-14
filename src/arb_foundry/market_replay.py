"""Checksummed SQLite market-input snapshots for deterministic scanner replay.

This module persists public order-book inputs and the exact scanner parameters;
it never calls an exchange, stores credentials, or submits orders.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from arb_foundry.errors import DataValidationError
from arb_foundry.models import MarketType, OrderBook, OrderBookLevel
from arb_foundry.scanner import ScanReport, SpotArbitrageScanner
from arb_foundry.time_utils import ensure_utc, utc_now


def _canonical(payload: dict[str, object]) -> tuple[str, str]:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _decimal_text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise DataValidationError(f"{name} must be stored as a decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise DataValidationError(f"{name} must be a valid decimal string") from exc
    if not parsed.is_finite():
        raise DataValidationError(f"{name} must be finite")
    return str(parsed)


def _book_payload(book: OrderBook) -> dict[str, object]:
    return {
        "exchange": book.exchange,
        "symbol": book.symbol,
        "market_type": book.market_type.value,
        "timestamp": book.timestamp.isoformat(),
        "bids": [
            {"price": str(level.price), "quantity": str(level.quantity)}
            for level in book.bids
        ],
        "asks": [
            {"price": str(level.price), "quantity": str(level.quantity)}
            for level in book.asks
        ],
    }


def _restore_book(raw: object) -> OrderBook:
    if not isinstance(raw, dict):
        raise DataValidationError("stored order book must be an object")
    try:
        bids_raw, asks_raw = raw["bids"], raw["asks"]
        if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
            raise DataValidationError("stored order-book sides must be lists")

        def levels(items: list[object]) -> tuple[OrderBookLevel, ...]:
            output = []
            for item in items:
                if not isinstance(item, dict):
                    raise DataValidationError("stored order-book levels must be objects")
                output.append(
                    OrderBookLevel(
                        _decimal_text("level price", item.get("price")),
                        _decimal_text("level quantity", item.get("quantity")),
                    )
                )
            return tuple(output)

        return OrderBook(
            exchange=raw["exchange"],
            symbol=raw["symbol"],
            bids=levels(bids_raw),
            asks=levels(asks_raw),
            timestamp=datetime.fromisoformat(raw["timestamp"]),
            market_type=MarketType(raw["market_type"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, DataValidationError):
            raise
        raise DataValidationError("stored order book has invalid fields") from exc


class MarketReplayStore:
    """Append-only store for scanner inputs and reproducible scan replay."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_scan_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                sha256 TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def record_scan(
        self,
        order_books: Iterable[OrderBook],
        *,
        fee_rates: Mapping[str, Decimal | int | float | str],
        target_quantity: Decimal | int | float | str,
        scanner: SpotArbitrageScanner,
        now: datetime | None = None,
    ) -> int:
        """Validate a scan, persist every input/config value, and return its run ID."""
        if not isinstance(scanner, SpotArbitrageScanner):
            raise DataValidationError("scanner must be a SpotArbitrageScanner")
        books = tuple(order_books)
        if any(not isinstance(book, OrderBook) for book in books):
            raise DataValidationError("order_books must contain only OrderBook instances")
        scan_time = ensure_utc(now) if now is not None else utc_now()

        normalized_fees: dict[str, str] = {}
        for exchange, rate in fee_rates.items():
            if not isinstance(exchange, str) or not exchange.strip():
                raise DataValidationError("fee-rate exchange name must not be empty")
            name = exchange.strip().lower()
            if name in normalized_fees:
                raise DataValidationError(f"duplicate normalized fee-rate exchange: {name}")
            try:
                parsed = rate if isinstance(rate, Decimal) else Decimal(str(rate))
            except (InvalidOperation, ValueError) as exc:
                raise DataValidationError(f"invalid fee rate for {name}") from exc
            if not parsed.is_finite():
                raise DataValidationError(f"fee rate for {name} must be finite")
            normalized_fees[name] = str(parsed)

        # Run once before writing so invalid scanner inputs/config never create a replay row.
        scanner.scan(books, fee_rates=normalized_fees, target_quantity=target_quantity, now=scan_time)
        quantity = target_quantity if isinstance(target_quantity, Decimal) else Decimal(str(target_quantity))
        if not quantity.is_finite():
            raise DataValidationError("target_quantity must be finite")
        payload: dict[str, object] = {
            "schema_version": 1,
            "scan_now": scan_time.isoformat(),
            "scanner": {
                "max_data_age_seconds": scanner.max_data_age_seconds,
                "min_net_spread_bps": str(scanner.min_net_spread_bps),
            },
            "fee_rates": normalized_fees,
            "target_quantity": str(quantity),
            "order_books": [_book_payload(book) for book in books],
        }
        encoded, digest = _canonical(payload)
        cursor = self._connection.execute(
            """
            INSERT INTO market_scan_runs (recorded_at, schema_version, payload_json, sha256)
            VALUES (?, 1, ?, ?)
            """,
            (scan_time.isoformat(), encoded, digest),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def replay_scan(self, run_id: int) -> ScanReport:
        """Re-run the original scanner against its exact saved books/config/time."""
        if type(run_id) is not int or run_id < 1:
            raise DataValidationError("run_id must be a positive integer")
        row = self._connection.execute(
            "SELECT schema_version, payload_json, sha256 FROM market_scan_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise DataValidationError(f"market scan run not found: {run_id}")
        encoded = row["payload_json"]
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row["sha256"]:
            raise DataValidationError("market scan snapshot checksum mismatch")
        if row["schema_version"] != 1:
            raise DataValidationError("unsupported stored market scan schema_version")
        try:
            payload = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise DataValidationError("stored market scan payload is invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise DataValidationError("unsupported market scan payload schema_version")
        config = payload.get("scanner")
        fees = payload.get("fee_rates")
        books_raw = payload.get("order_books")
        if not isinstance(config, dict) or not isinstance(fees, dict) or not isinstance(books_raw, list):
            raise DataValidationError("stored market scan payload has invalid structure")
        try:
            scanner = SpotArbitrageScanner(
                max_data_age_seconds=config["max_data_age_seconds"],
                min_net_spread_bps=_decimal_text("min_net_spread_bps", config["min_net_spread_bps"]),
            )
            scan_time = datetime.fromisoformat(payload["scan_now"])
            quantity = _decimal_text("target_quantity", payload["target_quantity"])
            books = tuple(_restore_book(item) for item in books_raw)
            if not all(isinstance(key, str) for key in fees):
                raise DataValidationError("stored fee-rate keys must be strings")
            fee_rates = {
                key: _decimal_text(f"fee rate for {key}", value) for key, value in fees.items()
            }
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, DataValidationError):
                raise
            raise DataValidationError("stored market scan payload has invalid fields") from exc
        return scanner.scan(books, fee_rates=fee_rates, target_quantity=quantity, now=scan_time)

    def list_runs(self, *, limit: int = 100) -> list[dict[str, object]]:
        """List run metadata newest first without exposing full snapshot payloads."""
        if type(limit) is not int or not 1 <= limit <= 10_000:
            raise DataValidationError("limit must be between 1 and 10000")
        rows = self._connection.execute(
            """
            SELECT run_id, recorded_at, schema_version, sha256
            FROM market_scan_runs ORDER BY run_id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """Close the SQLite connection."""
        self._connection.close()

    def __enter__(self) -> MarketReplayStore:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
