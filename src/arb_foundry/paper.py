"""Deterministic paper-only fills against supplied order-book snapshots.

No network calls, exchange credentials, balance transfers, or live orders are
performed. The result is a simplified execution estimate, not a fill guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from arb_foundry.errors import DataValidationError
from arb_foundry.models import MarketType, OrderBook

_BPS = Decimal("10000")
_ZERO = Decimal("0")


def _decimal(name: str, value: Decimal | int | float | str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DataValidationError(f"{name} must be a valid decimal number") from exc
    if not parsed.is_finite():
        raise DataValidationError(f"{name} must be finite")
    return parsed


class PaperFillStatus(StrEnum):
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class PaperTradeResult:
    """Immutable result for a simulated buy leg and sell leg."""

    status: PaperFillStatus
    symbol: str
    buy_exchange: str
    sell_exchange: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    buy_vwap: Decimal | None
    sell_vwap: Decimal | None
    buy_notional: Decimal
    sell_notional: Decimal
    buy_fee: Decimal
    sell_fee: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    slippage_cost: Decimal
    rejection_reason: str | None = None


def _available(levels) -> Decimal:
    return sum((level.quantity for level in levels), _ZERO)


def _vwap(levels, quantity: Decimal) -> Decimal:
    remaining = quantity
    notional = _ZERO
    for level in levels:
        fill = min(remaining, level.quantity)
        notional += fill * level.price
        remaining -= fill
        if remaining == 0:
            return notional / quantity
    raise DataValidationError("requested quantity exceeds available book depth")


class PaperTradingSimulator:
    """Simulate both legs using visible depth, fee assumptions, and slippage."""

    def simulate(
        self,
        buy_book: OrderBook,
        sell_book: OrderBook,
        *,
        quantity: Decimal | int | float | str,
        fee_rates: dict[str, Decimal | int | float | str],
        slippage_bps: Decimal | int | float | str = Decimal("0"),
        allow_partial_fills: bool = False,
    ) -> PaperTradeResult:
        requested = _decimal("quantity", quantity)
        if requested <= 0:
            raise DataValidationError("quantity must be greater than zero")
        slippage = _decimal("slippage_bps", slippage_bps)
        if slippage < 0 or slippage >= _BPS:
            raise DataValidationError("slippage_bps must be in [0, 10000)")
        if (
            buy_book.market_type is not MarketType.SPOT
            or sell_book.market_type is not MarketType.SPOT
        ):
            raise DataValidationError("paper spot simulation requires spot order books")
        if buy_book.symbol != sell_book.symbol:
            raise DataValidationError("buy and sell books must have the same symbol")
        if buy_book.exchange == sell_book.exchange:
            raise DataValidationError("buy and sell exchanges must differ")

        normalized_fees: dict[str, Decimal] = {}
        for exchange, raw_rate in fee_rates.items():
            name = exchange.strip().lower()
            if not name:
                raise DataValidationError("fee-rate exchange name must not be empty")
            rate = _decimal(f"fee rate for {name}", raw_rate)
            if rate < 0 or rate >= 1:
                raise DataValidationError(f"fee rate for {name} must be in [0, 1)")
            normalized_fees[name] = rate
        buy_exchange = buy_book.exchange.lower()
        sell_exchange = sell_book.exchange.lower()
        missing = [name for name in (buy_exchange, sell_exchange) if name not in normalized_fees]
        if missing:
            raise DataValidationError(f"missing fee rate for exchange(s): {', '.join(missing)}")

        buy_available = _available(buy_book.asks)
        sell_available = _available(sell_book.bids)
        fill_quantity = min(requested, buy_available, sell_available)
        if fill_quantity < requested and not allow_partial_fills:
            return self._rejected(
                buy_book, sell_book, requested,
                "insufficient depth for full paired fill; partial fills are disabled",
            )
        if fill_quantity <= 0:
            return self._rejected(buy_book, sell_book, requested, "no paired liquidity available")

        raw_buy_vwap = _vwap(buy_book.asks, fill_quantity)
        raw_sell_vwap = _vwap(sell_book.bids, fill_quantity)
        slip_rate = slippage / _BPS
        executed_buy_vwap = raw_buy_vwap * (Decimal("1") + slip_rate)
        executed_sell_vwap = raw_sell_vwap * (Decimal("1") - slip_rate)
        buy_notional = executed_buy_vwap * fill_quantity
        sell_notional = executed_sell_vwap * fill_quantity
        buy_fee = buy_notional * normalized_fees[buy_exchange]
        sell_fee = sell_notional * normalized_fees[sell_exchange]
        gross_pnl = sell_notional - buy_notional
        net_pnl = gross_pnl - buy_fee - sell_fee
        raw_notional = (raw_buy_vwap + raw_sell_vwap) * fill_quantity
        slippage_cost = raw_notional * slip_rate
        status = PaperFillStatus.FILLED if fill_quantity == requested else PaperFillStatus.PARTIAL

        return PaperTradeResult(
            status=status,
            symbol=buy_book.symbol,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            requested_quantity=requested,
            filled_quantity=fill_quantity,
            remaining_quantity=requested - fill_quantity,
            buy_vwap=executed_buy_vwap,
            sell_vwap=executed_sell_vwap,
            buy_notional=buy_notional,
            sell_notional=sell_notional,
            buy_fee=buy_fee,
            sell_fee=sell_fee,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            slippage_cost=slippage_cost,
        )

    @staticmethod
    def _rejected(
        buy_book: OrderBook,
        sell_book: OrderBook,
        requested: Decimal,
        reason: str,
    ) -> PaperTradeResult:
        return PaperTradeResult(
            status=PaperFillStatus.REJECTED,
            symbol=buy_book.symbol,
            buy_exchange=buy_book.exchange.lower(),
            sell_exchange=sell_book.exchange.lower(),
            requested_quantity=requested,
            filled_quantity=_ZERO,
            remaining_quantity=requested,
            buy_vwap=None,
            sell_vwap=None,
            buy_notional=_ZERO,
            sell_notional=_ZERO,
            buy_fee=_ZERO,
            sell_fee=_ZERO,
            gross_pnl=_ZERO,
            net_pnl=_ZERO,
            slippage_cost=_ZERO,
            rejection_reason=reason,
        )
