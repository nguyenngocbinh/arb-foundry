from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.models import MarketType, OrderBook, OrderBookLevel
from arb_foundry.scanner import SpotArbitrageScanner

NOW = datetime(2026, 10, 14, 10, tzinfo=UTC)
FEES = {"binance": "0.001", "okx": "0.001", "bybit": "0.001", "kucoin": "0.001"}


def book(
    exchange: str,
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
    *,
    symbol: str = "BTC/USDT",
    timestamp: datetime = NOW,
    market_type: MarketType = MarketType.SPOT,
) -> OrderBook:
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        bids=tuple(OrderBookLevel(price, quantity) for price, quantity in bids),
        asks=tuple(OrderBookLevel(price, quantity) for price, quantity in asks),
        timestamp=timestamp,
        market_type=market_type,
    )


def test_scanner_finds_depth_weighted_opportunity_after_fees() -> None:
    buy = book("binance", [("99", "5")], [("100", "1"), ("102", "2")])
    sell = book("okx", [("105", "1"), ("103", "2")], [("106", "5")])

    report = SpotArbitrageScanner().scan(
        [buy, sell], fee_rates=FEES, target_quantity="2", now=NOW
    )

    assert len(report.opportunities) == 1
    opportunity = report.opportunities[0]
    assert opportunity.buy_exchange == "binance"
    assert opportunity.sell_exchange == "okx"
    assert opportunity.symbol == "BTC/USDT"
    assert opportunity.quantity == Decimal("2")
    assert opportunity.buy_price == Decimal("101")
    assert opportunity.sell_price == Decimal("104")
    assert abs(opportunity.gross_spread_bps - Decimal("297.029702970297")) < Decimal("0.000001")
    assert abs(opportunity.net_spread_bps - Decimal("276.732673267326")) < Decimal("0.000001")
    assert opportunity.detected_at == NOW
    assert report.accepted_books == 2


def test_scanner_rejects_insufficient_depth_instead_of_partial_fill() -> None:
    buy = book("binance", [("99", "5")], [("100", "1")])
    sell = book("okx", [("105", "5")], [("106", "5")])

    report = SpotArbitrageScanner().scan(
        [buy, sell], fee_rates=FEES, target_quantity="2", now=NOW
    )

    assert report.opportunities == ()
    assert any("insufficient depth" in item.reason for item in report.rejections)


def test_scanner_rejects_stale_book() -> None:
    buy = book("binance", [("99", "5")], [("100", "5")], timestamp=NOW - timedelta(seconds=10))
    sell = book("okx", [("105", "5")], [("106", "5")])

    report = SpotArbitrageScanner(max_data_age_seconds=5).scan(
        [buy, sell], fee_rates=FEES, target_quantity="1", now=NOW
    )

    assert report.opportunities == ()
    assert any("stale" in item.reason for item in report.rejections)


def test_scanner_rejects_net_spread_below_threshold_after_fees() -> None:
    buy = book("binance", [("99", "5")], [("100", "5")])
    sell = book("okx", [("100.1", "5")], [("101", "5")])

    report = SpotArbitrageScanner(min_net_spread_bps="20").scan(
        [buy, sell], fee_rates=FEES, target_quantity="1", now=NOW
    )

    assert report.opportunities == ()
    assert any("below threshold" in item.reason for item in report.rejections)


def test_scanner_normalizes_symbols_before_matching() -> None:
    buy = book("binance", [("99", "5")], [("100", "5")], symbol="btc-usdt")
    sell = book("okx", [("105", "5")], [("106", "5")], symbol="BTC/USDT")

    report = SpotArbitrageScanner().scan(
        [buy, sell], fee_rates=FEES, target_quantity="1", now=NOW
    )

    assert len(report.opportunities) == 1
    assert report.opportunities[0].symbol == "BTC/USDT"


def test_scanner_does_not_compare_different_symbols() -> None:
    buy = book("binance", [("99", "5")], [("100", "5")], symbol="BTC/USDT")
    sell = book("okx", [("105", "5")], [("106", "5")], symbol="ETH/USDT")

    report = SpotArbitrageScanner().scan(
        [buy, sell], fee_rates=FEES, target_quantity="1", now=NOW
    )

    assert report.opportunities == ()


def test_scanner_rejects_non_spot_books() -> None:
    buy = book("binance", [("99", "5")], [("100", "5")])
    sell = book(
        "okx", [("105", "5")], [("106", "5")], market_type=MarketType.PERPETUAL
    )

    report = SpotArbitrageScanner().scan(
        [buy, sell], fee_rates=FEES, target_quantity="1", now=NOW
    )

    assert report.opportunities == ()
    assert any("only spot" in item.reason for item in report.rejections)


def test_scanner_rejects_duplicate_books_for_exchange_symbol() -> None:
    buy1 = book("binance", [("99", "5")], [("100", "5")])
    buy2 = book("binance", [("98", "5")], [("99", "5")])
    sell = book("okx", [("105", "5")], [("106", "5")])

    report = SpotArbitrageScanner().scan(
        [buy1, buy2, sell], fee_rates=FEES, target_quantity="1", now=NOW
    )

    assert report.opportunities == ()
    assert any("duplicate order books" in item.reason for item in report.rejections)


@pytest.mark.parametrize("quantity", ["0", "-1", "NaN"])
def test_scanner_rejects_invalid_target_quantity(quantity: str) -> None:
    scanner = SpotArbitrageScanner()
    with pytest.raises(DataValidationError):
        scanner.scan([], fee_rates=FEES, target_quantity=quantity, now=NOW)


@pytest.mark.parametrize("rate", ["-0.01", "1", "NaN"])
def test_scanner_rejects_invalid_fee_rates(rate: str) -> None:
    with pytest.raises(DataValidationError):
        SpotArbitrageScanner().scan(
            [book("binance", [("99", "5")], [("100", "5")])],
            fee_rates={"binance": rate},
            target_quantity="1",
            now=NOW,
        )


def test_scanner_requires_fee_rate_for_each_exchange_in_scope() -> None:
    books = [
        book("binance", [("99", "5")], [("100", "5")]),
        book("okx", [("105", "5")], [("106", "5")]),
    ]
    with pytest.raises(DataValidationError, match="missing fee rate"):
        SpotArbitrageScanner().scan(
            books, fee_rates={"binance": "0.001"}, target_quantity="1", now=NOW
        )


def test_scanner_rejects_naive_now_timestamp() -> None:
    with pytest.raises(DataValidationError, match="timezone-aware"):
        SpotArbitrageScanner().scan(
            [], fee_rates=FEES, target_quantity="1", now=datetime(2026, 10, 14, 10)
        )


def test_scanner_returns_candidates_in_descending_net_spread_order() -> None:
    books = [
        book("binance", [("99", "5")], [("100", "5")]),
        book("okx", [("103", "5")], [("104", "5")]),
        book("bybit", [("106", "5")], [("107", "5")]),
    ]

    report = SpotArbitrageScanner().scan(
        books, fee_rates=FEES, target_quantity="1", now=NOW
    )

    assert len(report.opportunities) >= 2
    spreads = [item.net_spread_bps for item in report.opportunities]
    assert spreads == sorted(spreads, reverse=True)
