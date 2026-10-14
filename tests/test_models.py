from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.models import (
    MarketType,
    Opportunity,
    OrderBook,
    OrderBookLevel,
    Ticker,
    Trade,
    TradeSide,
)

NOW = datetime(2026, 10, 14, 10, tzinfo=timezone.utc)


def test_ticker_normalizes_identity_and_timestamp() -> None:
    ticker = Ticker(
        exchange=" BINANCE ",
        symbol="btc/usdt",
        bid="100",
        ask=101,
        bid_quantity="2",
        ask_quantity="3",
        timestamp=NOW.astimezone(timezone(timedelta(hours=7))),
    )
    assert ticker.exchange == "binance"
    assert ticker.symbol == "BTC/USDT"
    assert ticker.bid == Decimal("100")
    assert ticker.timestamp == NOW
    assert ticker.market_type is MarketType.SPOT


def test_ticker_rejects_crossed_market_and_non_positive_values() -> None:
    with pytest.raises(DataValidationError):
        Ticker("binance", "BTCUSDT", 101, 100, 1, 1, NOW)
    with pytest.raises(DataValidationError):
        Ticker("binance", "BTCUSDT", 0, 100, 1, 1, NOW)


def test_order_book_validates_sorted_depth() -> None:
    book = OrderBook(
        "okx",
        "BTC-USDT",
        (OrderBookLevel(100, 2), OrderBookLevel(99, 3)),
        (OrderBookLevel(101, 4), OrderBookLevel(102, 5)),
        NOW,
    )
    assert book.bids[0].price == Decimal("100")
    assert book.symbol == "BTC-USDT"


def test_order_book_rejects_unsorted_or_crossed_levels() -> None:
    with pytest.raises(DataValidationError):
        OrderBook(
            "okx", "BTC-USDT",
            (OrderBookLevel(99, 1), OrderBookLevel(100, 1)),
            (OrderBookLevel(101, 1),), NOW,
        )
    with pytest.raises(DataValidationError):
        OrderBook(
            "okx", "BTC-USDT",
            (OrderBookLevel(101, 1),),
            (OrderBookLevel(101, 1),), NOW,
        )


def test_trade_normalizes_side_and_timestamp() -> None:
    trade = Trade("BYBIT", "ethusdt", "12.5", "0.4", NOW, "buy")
    assert trade.exchange == "bybit"
    assert trade.symbol == "ETHUSDT"
    assert trade.side is TradeSide.BUY
    assert trade.price == Decimal("12.5")


def test_opportunity_is_a_candidate_not_an_order() -> None:
    opportunity = Opportunity(
        symbol="BTCUSDT",
        buy_exchange="binance",
        sell_exchange="okx",
        buy_price="100",
        sell_price="101",
        quantity="0.5",
        gross_spread_bps="100",
        net_spread_bps="80",
        detected_at=NOW,
    )
    assert opportunity.net_spread_bps == Decimal("80")
    assert opportunity.strategy == "spot_spot"


def test_opportunity_rejects_same_exchange() -> None:
    with pytest.raises(DataValidationError):
        Opportunity("BTCUSDT", "binance", "BINANCE", 100, 101, 1, 100, 90, NOW)


def test_naive_domain_timestamp_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        Ticker("binance", "BTCUSDT", 100, 101, 1, 1, datetime(2026, 10, 14, 10))
