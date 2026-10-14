"""Exchange-specific symbol constraints for research and paper-order validation.

Rules must come from a trusted exchange market-metadata source or explicit
operator configuration. This module does not fetch or guess exchange rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal, InvalidOperation

from arb_foundry.errors import DataValidationError
from arb_foundry.market_data import normalize_symbol
from arb_foundry.models import TradeSide


def _decimal(name: str, value: Decimal | int | float | str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DataValidationError(f"{name} must be a valid decimal number") from exc
    if not parsed.is_finite():
        raise DataValidationError(f"{name} must be finite")
    return parsed


def _multiple(value: Decimal, increment: Decimal) -> bool:
    return value / increment == (value / increment).to_integral_value()


@dataclass(frozen=True, slots=True)
class SymbolConstraints:
    """Trading filters for one exchange and one normalized market symbol."""

    exchange: str
    symbol: str
    price_tick: Decimal
    quantity_step: Decimal
    min_quantity: Decimal = Decimal("0")
    min_notional: Decimal = Decimal("0")
    max_quantity: Decimal | None = None

    def __post_init__(self) -> None:
        exchange = self.exchange.strip().lower()
        if not exchange:
            raise DataValidationError("exchange must not be empty")
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        for name in ("price_tick", "quantity_step"):
            value = _decimal(name, getattr(self, name))
            if value <= 0:
                raise DataValidationError(f"{name} must be greater than zero")
            object.__setattr__(self, name, value)
        for name in ("min_quantity", "min_notional"):
            value = _decimal(name, getattr(self, name))
            if value < 0:
                raise DataValidationError(f"{name} must not be negative")
            object.__setattr__(self, name, value)
        if self.max_quantity is not None:
            maximum = _decimal("max_quantity", self.max_quantity)
            if maximum <= 0:
                raise DataValidationError("max_quantity must be greater than zero")
            if maximum < self.min_quantity:
                raise DataValidationError("max_quantity must be >= min_quantity")
            object.__setattr__(self, "max_quantity", maximum)

    def round_quantity_down(self, quantity: Decimal | int | float | str) -> Decimal:
        """Round base quantity down to a valid multiple of quantity_step."""
        value = _decimal("quantity", quantity)
        if value < 0:
            raise DataValidationError("quantity must not be negative")
        units = (value / self.quantity_step).to_integral_value(rounding=ROUND_DOWN)
        return units * self.quantity_step

    def round_price(self, price: Decimal | int | float | str, side: TradeSide) -> Decimal:
        """Round buy prices upward and sell prices downward to the price tick."""
        value = _decimal("price", price)
        if value <= 0:
            raise DataValidationError("price must be greater than zero")
        if not isinstance(side, TradeSide):
            side = TradeSide(side)
        rounding = ROUND_CEILING if side is TradeSide.BUY else ROUND_FLOOR
        units = (value / self.price_tick).to_integral_value(rounding=rounding)
        return units * self.price_tick

    def validate_order(
        self,
        *,
        price: Decimal | int | float | str,
        quantity: Decimal | int | float | str,
    ) -> None:
        """Raise DataValidationError if price/quantity violates configured rules."""
        parsed_price = _decimal("price", price)
        parsed_quantity = _decimal("quantity", quantity)
        if parsed_price <= 0:
            raise DataValidationError("price must be greater than zero")
        if parsed_quantity <= 0:
            raise DataValidationError("quantity must be greater than zero")
        if not _multiple(parsed_price, self.price_tick):
            raise DataValidationError("price is not aligned to price_tick")
        if not _multiple(parsed_quantity, self.quantity_step):
            raise DataValidationError("quantity is not aligned to quantity_step")
        if parsed_quantity < self.min_quantity:
            raise DataValidationError("quantity is below min_quantity")
        if self.max_quantity is not None and parsed_quantity > self.max_quantity:
            raise DataValidationError("quantity exceeds max_quantity")
        if parsed_price * parsed_quantity < self.min_notional:
            raise DataValidationError("order notional is below min_notional")
