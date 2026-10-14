"""Validated, exchange-neutral domain models for market data and opportunities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from arb_foundry.errors import DataValidationError
from arb_foundry.time_utils import ensure_utc


class MarketType(StrEnum):
    SPOT = "spot"
    PERPETUAL = "perpetual"


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


def _decimal(name: str, value: Decimal | int | float | str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DataValidationError(f"{name} must be a valid decimal number") from exc
    if not parsed.is_finite():
        raise DataValidationError(f"{name} must be finite")
    return parsed


def _positive(name: str, value: Decimal | int | float | str) -> Decimal:
    parsed = _decimal(name, value)
    if parsed <= 0:
        raise DataValidationError(f"{name} must be greater than zero")
    return parsed


def _identity(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise DataValidationError(f"{name} must not be empty")
    if any(character.isspace() for character in normalized):
        raise DataValidationError(f"{name} must not contain whitespace")
    return normalized


@dataclass(frozen=True, slots=True)
class Ticker:
    exchange: str
    symbol: str
    bid: Decimal
    ask: Decimal
    bid_quantity: Decimal
    ask_quantity: Decimal
    timestamp: datetime
    market_type: MarketType = MarketType.SPOT

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange", _identity("exchange", self.exchange).lower())
        object.__setattr__(self, "symbol", _identity("symbol", self.symbol).upper())
        for name in ("bid", "ask", "bid_quantity", "ask_quantity"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if self.ask < self.bid:
            raise DataValidationError("ticker ask must be greater than or equal to bid")
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp))
        if not isinstance(self.market_type, MarketType):
            object.__setattr__(self, "market_type", MarketType(self.market_type))


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _positive("price", self.price))
        object.__setattr__(self, "quantity", _positive("quantity", self.quantity))


@dataclass(frozen=True, slots=True)
class OrderBook:
    exchange: str
    symbol: str
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    timestamp: datetime
    market_type: MarketType = MarketType.SPOT

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange", _identity("exchange", self.exchange).lower())
        object.__setattr__(self, "symbol", _identity("symbol", self.symbol).upper())
        object.__setattr__(self, "bids", tuple(self.bids))
        object.__setattr__(self, "asks", tuple(self.asks))
        if not self.bids or not self.asks:
            raise DataValidationError("order book must contain at least one bid and one ask")
        if any(not isinstance(level, OrderBookLevel) for level in (*self.bids, *self.asks)):
            raise DataValidationError("order book levels must be OrderBookLevel instances")
        if any(a.price < b.price for a, b in zip(self.bids, self.bids[1:], strict=False)):
            raise DataValidationError("order book bids must be sorted by descending price")
        if any(a.price > b.price for a, b in zip(self.asks, self.asks[1:], strict=False)):
            raise DataValidationError("order book asks must be sorted by ascending price")
        if self.bids[0].price >= self.asks[0].price:
            raise DataValidationError("order book best bid must be below best ask")
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp))
        if not isinstance(self.market_type, MarketType):
            object.__setattr__(self, "market_type", MarketType(self.market_type))


@dataclass(frozen=True, slots=True)
class Trade:
    exchange: str
    symbol: str
    price: Decimal
    quantity: Decimal
    timestamp: datetime
    side: TradeSide

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange", _identity("exchange", self.exchange).lower())
        object.__setattr__(self, "symbol", _identity("symbol", self.symbol).upper())
        object.__setattr__(self, "price", _positive("price", self.price))
        object.__setattr__(self, "quantity", _positive("quantity", self.quantity))
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp))
        if not isinstance(self.side, TradeSide):
            object.__setattr__(self, "side", TradeSide(self.side))


@dataclass(frozen=True, slots=True)
class Opportunity:
    """A measured candidate; it is not an instruction to place an order."""

    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: Decimal
    sell_price: Decimal
    quantity: Decimal
    gross_spread_bps: Decimal
    net_spread_bps: Decimal
    detected_at: datetime
    strategy: str = "spot_spot"
    market_type: MarketType = MarketType.SPOT

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _identity("symbol", self.symbol).upper())
        object.__setattr__(
            self, "buy_exchange", _identity("buy_exchange", self.buy_exchange).lower()
        )
        object.__setattr__(
            self, "sell_exchange", _identity("sell_exchange", self.sell_exchange).lower()
        )
        if self.buy_exchange == self.sell_exchange:
            raise DataValidationError("buy_exchange and sell_exchange must differ")
        for name in ("buy_price", "sell_price", "quantity"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        for name in ("gross_spread_bps", "net_spread_bps"):
            object.__setattr__(self, name, _decimal(name, getattr(self, name)))
        object.__setattr__(self, "detected_at", ensure_utc(self.detected_at))
        object.__setattr__(self, "strategy", _identity("strategy", self.strategy).lower())
        if not isinstance(self.market_type, MarketType):
            object.__setattr__(self, "market_type", MarketType(self.market_type))
