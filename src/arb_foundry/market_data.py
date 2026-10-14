"""Common market-data adapter contract and freshness validation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from arb_foundry.errors import DataValidationError, StaleMarketDataError
from arb_foundry.models import OrderBook, Ticker


class MarketDataAdapter(Protocol):
    """Read-only interface implemented by each exchange adapter."""

    @property
    def exchange(self) -> str:
        """Canonical exchange identifier."""

    def get_ticker(self, symbol: str) -> Ticker:
        """Fetch a normalized best-bid/best-ask ticker."""

    def get_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        """Fetch a normalized order book."""

    def health_check(self) -> None:
        """Raise an exception if the exchange public API is unhealthy."""


def normalize_symbol(symbol: str) -> str:
    """Normalize BASE-QUOTE or BASE/QUOTE into BASE/QUOTE.

    Ambiguous concatenated symbols (for example BTCUSDT) are rejected because
    splitting them without exchange metadata can produce incorrect markets.
    """
    value = symbol.strip().upper().replace("-", "/")
    parts = [part.strip() for part in value.split("/")]
    if len(parts) != 2 or not all(parts):
        raise DataValidationError("symbol must use BASE/QUOTE or BASE-QUOTE format")
    if any(any(character.isspace() for character in part) for part in parts):
        raise DataValidationError("symbol components must not contain whitespace")
    return f"{parts[0]}/{parts[1]}"


def validate_freshness(
    timestamp: datetime,
    max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> None:
    """Reject market data older than the configured threshold or far in the future."""
    if max_age_seconds <= 0:
        raise DataValidationError("max_age_seconds must be greater than zero")
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise DataValidationError("now must be timezone-aware")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise DataValidationError("timestamp must be timezone-aware")
    age_seconds = (current.astimezone(UTC) - timestamp.astimezone(UTC)).total_seconds()
    if age_seconds < -5:
        raise DataValidationError("market data timestamp is more than 5 seconds in the future")
    if age_seconds > max_age_seconds:
        raise StaleMarketDataError(
            f"market data is {age_seconds:.3f}s old; maximum is {max_age_seconds}s"
        )
