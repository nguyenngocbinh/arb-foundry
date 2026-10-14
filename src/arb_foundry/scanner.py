"""Depth-aware cross-exchange spot arbitrage opportunity scanner.

This module evaluates public order-book snapshots only. It does not place orders,
reserve balances, or guarantee that a displayed opportunity can be executed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from arb_foundry.errors import DataValidationError, StaleMarketDataError
from arb_foundry.market_data import normalize_symbol, validate_freshness
from arb_foundry.models import MarketType, Opportunity, OrderBook
from arb_foundry.time_utils import ensure_utc, utc_now

_BPS = Decimal("10000")


def _decimal(name: str, value: Decimal | int | float | str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DataValidationError(f"{name} must be a valid decimal number") from exc
    if not parsed.is_finite():
        raise DataValidationError(f"{name} must be finite")
    return parsed


@dataclass(frozen=True, slots=True)
class ScanRejection:
    """Why an input book or a candidate exchange pair was not considered."""

    reason: str
    symbol: str | None = None
    buy_exchange: str | None = None
    sell_exchange: str | None = None


@dataclass(frozen=True, slots=True)
class ScanReport:
    """Immutable results and diagnostics for one scan pass."""

    opportunities: tuple[Opportunity, ...]
    rejections: tuple[ScanRejection, ...]
    accepted_books: int


def _vwap(levels, target_quantity: Decimal) -> Decimal | None:
    """Return exact-size VWAP, or None if visible depth cannot fill the full size."""
    remaining = target_quantity
    notional = Decimal("0")
    for level in levels:
        filled = min(remaining, level.quantity)
        notional += filled * level.price
        remaining -= filled
        if remaining == 0:
            return notional / target_quantity
    return None


class SpotArbitrageScanner:
    """Scan spot order books for fee-adjusted cross-exchange opportunities.

    Fee rates are fractions of traded notional, not percentages or basis points:
    for example, Decimal("0.001") means a 0.1% fee on that exchange. The scanner
    assumes fees are charged on quote notional for both buy and sell legs.
    """

    def __init__(
        self,
        *,
        max_data_age_seconds: int = 5,
        min_net_spread_bps: Decimal | int | float | str = Decimal("0"),
    ) -> None:
        if max_data_age_seconds <= 0:
            raise DataValidationError("max_data_age_seconds must be greater than zero")
        threshold = _decimal("min_net_spread_bps", min_net_spread_bps)
        self.max_data_age_seconds = max_data_age_seconds
        self.min_net_spread_bps = threshold

    def scan(
        self,
        order_books: Iterable[OrderBook],
        *,
        fee_rates: Mapping[str, Decimal | int | float | str],
        target_quantity: Decimal | int | float | str,
        now: datetime | None = None,
    ) -> ScanReport:
        """Return profitable candidates and explain rejected books/pairs.

        A candidate is only produced if both venues have fresh spot books for
        the same normalized symbol and enough visible depth to fill the entire
        target base-asset quantity on both legs.
        """
        quantity = _decimal("target_quantity", target_quantity)
        if quantity <= 0:
            raise DataValidationError("target_quantity must be greater than zero")
        current = ensure_utc(now) if now is not None else utc_now()

        normalized_fees: dict[str, Decimal] = {}
        for exchange, raw_rate in fee_rates.items():
            name = exchange.strip().lower()
            if not name:
                raise DataValidationError("fee-rate exchange name must not be empty")
            rate = _decimal(f"fee rate for {name}", raw_rate)
            if rate < 0 or rate >= 1:
                raise DataValidationError(f"fee rate for {name} must be in [0, 1)")
            normalized_fees[name] = rate

        rejections: list[ScanRejection] = []
        grouped: dict[str, list[OrderBook]] = defaultdict(list)

        for book in order_books:
            try:
                symbol = normalize_symbol(book.symbol)
            except DataValidationError as exc:
                rejections.append(
                    ScanRejection(f"invalid symbol: {exc}", book.symbol, None, book.exchange)
                )
                continue
            if book.market_type is not MarketType.SPOT:
                rejections.append(
                    ScanRejection(
                        "only spot order books are supported by this scanner",
                        symbol,
                        None,
                        book.exchange,
                    )
                )
                continue
            try:
                validate_freshness(book.timestamp, self.max_data_age_seconds, now=current)
            except (DataValidationError, StaleMarketDataError) as exc:
                rejections.append(
                    ScanRejection(f"unusable market data: {exc}", symbol, None, book.exchange)
                )
                continue
            grouped[symbol].append(book)

        # Do not silently pick one of multiple snapshots for the same venue/symbol.
        unique_by_symbol: dict[str, list[OrderBook]] = {}
        for symbol, books in grouped.items():
            by_exchange: dict[str, list[OrderBook]] = defaultdict(list)
            for book in books:
                by_exchange[book.exchange.lower()].append(book)
            unique: list[OrderBook] = []
            for exchange, exchange_books in by_exchange.items():
                if len(exchange_books) != 1:
                    rejections.append(
                        ScanRejection(
                            "duplicate order books for the same exchange and symbol",
                            symbol,
                            None,
                            exchange,
                        )
                    )
                else:
                    unique.append(exchange_books[0])
            unique_by_symbol[symbol] = unique

        exchanges_in_scope = {
            book.exchange.lower()
            for books in unique_by_symbol.values()
            for book in books
        }
        missing_fees = sorted(exchanges_in_scope - normalized_fees.keys())
        if missing_fees:
            raise DataValidationError(
                f"missing fee rate for exchange(s): {', '.join(missing_fees)}"
            )

        opportunities: list[Opportunity] = []
        for symbol, books in unique_by_symbol.items():
            if len(books) < 2:
                continue
            for buy_book in books:
                for sell_book in books:
                    buy_exchange = buy_book.exchange.lower()
                    sell_exchange = sell_book.exchange.lower()
                    if buy_exchange == sell_exchange:
                        continue

                    buy_vwap = _vwap(buy_book.asks, quantity)
                    sell_vwap = _vwap(sell_book.bids, quantity)
                    if buy_vwap is None or sell_vwap is None:
                        missing_side = []
                        if buy_vwap is None:
                            missing_side.append(f"{buy_exchange} ask depth")
                        if sell_vwap is None:
                            missing_side.append(f"{sell_exchange} bid depth")
                        rejections.append(
                            ScanRejection(
                                f"insufficient depth for target quantity {quantity}: "
                                + ", ".join(missing_side),
                                symbol,
                                buy_exchange,
                                sell_exchange,
                            )
                        )
                        continue

                    buy_notional = buy_vwap * quantity
                    sell_notional = sell_vwap * quantity
                    gross_profit = sell_notional - buy_notional
                    fees = (
                        buy_notional * normalized_fees[buy_exchange]
                        + sell_notional * normalized_fees[sell_exchange]
                    )
                    net_profit = gross_profit - fees
                    gross_bps = (gross_profit / buy_notional) * _BPS
                    net_bps = (net_profit / buy_notional) * _BPS

                    if net_bps < self.min_net_spread_bps:
                        rejections.append(
                            ScanRejection(
                                f"net spread {net_bps:.4f} bps below threshold "
                                f"{self.min_net_spread_bps} bps",
                                symbol,
                                buy_exchange,
                                sell_exchange,
                            )
                        )
                        continue

                    opportunities.append(
                        Opportunity(
                            symbol=symbol,
                            buy_exchange=buy_exchange,
                            sell_exchange=sell_exchange,
                            buy_price=buy_vwap,
                            sell_price=sell_vwap,
                            quantity=quantity,
                            gross_spread_bps=gross_bps,
                            net_spread_bps=net_bps,
                            detected_at=current,
                            strategy="spot_spot",
                            market_type=MarketType.SPOT,
                        )
                    )

        opportunities.sort(key=lambda item: item.net_spread_bps, reverse=True)
        return ScanReport(
            opportunities=tuple(opportunities),
            rejections=tuple(rejections),
            accepted_books=sum(len(books) for books in unique_by_symbol.values()),
        )
