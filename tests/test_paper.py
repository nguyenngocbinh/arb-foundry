from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.models import MarketType, OrderBook, OrderBookLevel
from arb_foundry.paper import PaperFillStatus, PaperTradingSimulator

NOW = datetime(2026, 10, 14, 10, tzinfo=UTC)
FEES = {"binance": "0.001", "okx": "0.001"}


def book(
    exchange: str,
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
    *,
    symbol: str = "BTC/USDT",
    market_type: MarketType = MarketType.SPOT,
) -> OrderBook:
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        bids=tuple(OrderBookLevel(price, quantity) for price, quantity in bids),
        asks=tuple(OrderBookLevel(price, quantity) for price, quantity in asks),
        timestamp=NOW,
        market_type=market_type,
    )


def test_simulates_full_fill_fees_and_slippage() -> None:
    buy = book("binance", [("99", "5")], [("100", "2")])
    sell = book("okx", [("103", "2")], [("104", "5")])

    result = PaperTradingSimulator().simulate(
        buy, sell, quantity="1", fee_rates=FEES, slippage_bps="10"
    )

    assert result.status is PaperFillStatus.FILLED
    assert result.filled_quantity == Decimal("1")
    assert result.buy_vwap == Decimal("100.1")
    assert result.sell_vwap == Decimal("102.897")
    assert result.buy_fee == Decimal("0.1001")
    assert result.sell_fee == Decimal("0.102897")
    assert result.gross_pnl == Decimal("2.797")
    assert result.net_pnl == Decimal("2.594003")
    assert result.slippage_cost == Decimal("0.203")


def test_rejects_when_either_leg_lacks_depth_and_partial_disabled() -> None:
    buy = book("binance", [("99", "5")], [("100", "0.5")])
    sell = book("okx", [("103", "5")], [("104", "5")])

    result = PaperTradingSimulator().simulate(
        buy, sell, quantity="1", fee_rates=FEES
    )

    assert result.status is PaperFillStatus.REJECTED
    assert result.filled_quantity == Decimal("0")
    assert result.remaining_quantity == Decimal("1")
    assert result.net_pnl == Decimal("0")
    assert "insufficient depth" in result.rejection_reason


def test_simulates_paired_partial_fill_when_enabled() -> None:
    buy = book("binance", [("99", "5")], [("100", "0.5")])
    sell = book("okx", [("103", "0.8")], [("104", "5")])

    result = PaperTradingSimulator().simulate(
        buy, sell, quantity="1", fee_rates=FEES, allow_partial_fills=True
    )

    assert result.status is PaperFillStatus.PARTIAL
    assert result.filled_quantity == Decimal("0.5")
    assert result.remaining_quantity == Decimal("0.5")
    assert result.buy_notional == Decimal("50")
    assert result.sell_notional == Decimal("51.5")


def test_uses_vwap_for_multiple_book_levels() -> None:
    buy = book("binance", [("99", "5")], [("100", "0.5"), ("102", "0.5")])
    sell = book("okx", [("105", "0.25"), ("103", "0.75")], [("106", "5")])

    result = PaperTradingSimulator().simulate(
        buy, sell, quantity="1", fee_rates={"binance": "0", "okx": "0"}
    )

    assert result.buy_vwap == Decimal("101")
    assert result.sell_vwap == Decimal("103.5")
    assert result.gross_pnl == Decimal("2.5")
    assert result.net_pnl == Decimal("2.5")


@pytest.mark.parametrize("quantity", ["0", "-1", "NaN"])
def test_rejects_invalid_quantity(quantity: str) -> None:
    buy = book("binance", [("99", "5")], [("100", "5")])
    sell = book("okx", [("103", "5")], [("104", "5")])
    with pytest.raises(DataValidationError):
        PaperTradingSimulator().simulate(buy, sell, quantity=quantity, fee_rates=FEES)


def test_rejects_mismatched_symbols_and_non_spot_books() -> None:
    buy = book("binance", [("99", "5")], [("100", "5")])
    other_symbol = book("okx", [("103", "5")], [("104", "5")], symbol="ETH/USDT")
    with pytest.raises(DataValidationError, match="same symbol"):
        PaperTradingSimulator().simulate(buy, other_symbol, quantity="1", fee_rates=FEES)

    perpetual = book(
        "okx", [("103", "5")], [("104", "5")], market_type=MarketType.PERPETUAL
    )
    with pytest.raises(DataValidationError, match="spot order books"):
        PaperTradingSimulator().simulate(buy, perpetual, quantity="1", fee_rates=FEES)
