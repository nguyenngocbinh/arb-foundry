from decimal import Decimal

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.market_constraints import SymbolConstraints
from arb_foundry.models import TradeSide


def rules(**overrides) -> SymbolConstraints:
    values = {
        "exchange": "Binance",
        "symbol": "btc-usdt",
        "price_tick": "0.1",
        "quantity_step": "0.001",
        "min_quantity": "0.002",
        "min_notional": "10",
        "max_quantity": "5",
    }
    values.update(overrides)
    return SymbolConstraints(**values)


def test_constraints_normalize_identity_and_round_quantity_down() -> None:
    constraints = rules()
    assert constraints.exchange == "binance"
    assert constraints.symbol == "BTC/USDT"
    assert constraints.round_quantity_down("1.2349") == Decimal("1.234")


def test_buy_price_rounds_up_and_sell_price_rounds_down() -> None:
    constraints = rules()
    assert constraints.round_price("100.01", TradeSide.BUY) == Decimal("100.1")
    assert constraints.round_price("100.09", TradeSide.SELL) == Decimal("100.0")


def test_valid_order_passes_all_filters() -> None:
    rules().validate_order(price="100.1", quantity="0.100")


@pytest.mark.parametrize(
    ("price", "quantity", "message"),
    [
        ("100.11", "0.100", "price_tick"),
        ("100.1", "0.1001", "quantity_step"),
        ("100.1", "0.001", "min_quantity"),
        ("1.0", "0.010", "min_notional"),
        ("100.1", "6.000", "max_quantity"),
    ],
)
def test_invalid_order_is_rejected(price: str, quantity: str, message: str) -> None:
    with pytest.raises(DataValidationError, match=message):
        rules().validate_order(price=price, quantity=quantity)


def test_invalid_constraint_values_are_rejected() -> None:
    with pytest.raises(DataValidationError, match="price_tick"):
        rules(price_tick="0")
    with pytest.raises(DataValidationError, match="max_quantity"):
        rules(min_quantity="6", max_quantity="5")
