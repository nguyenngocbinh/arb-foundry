from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arb_foundry.errors import DataValidationError, ExchangeAPIError
from arb_foundry.exchanges import (
    BinanceAdapter,
    BybitAdapter,
    KuCoinAdapter,
    OKXAdapter,
    default_adapters,
)

NOW_MS = int(datetime(2026, 10, 14, 12, tzinfo=UTC).timestamp() * 1000)
BIDS = [["100", "2"], ["99", "3"]]
ASKS = [["101", "4"], ["102", "5"]]


@pytest.mark.parametrize(
    ("adapter", "payload"),
    [
        (
            BinanceAdapter(),
            {"bidPrice": "100", "askPrice": "101", "bidQty": "2", "askQty": "4"},
        ),
        (
            OKXAdapter(),
            {"code": "0", "data": [{
                "bidPx": "100", "askPx": "101", "bidSz": "2", "askSz": "4",
                "ts": str(NOW_MS),
            }]},
        ),
        (
            BybitAdapter(),
            {"retCode": 0, "time": NOW_MS, "result": {"list": [{
                "bid1Price": "100", "ask1Price": "101", "bid1Size": "2", "ask1Size": "4",
            }]}},
        ),
        (
            KuCoinAdapter(),
            {"code": "200000", "data": {
                "bestBid": "100", "bestAsk": "101", "bestBidSize": "2", "bestAskSize": "4",
            }},
        ),
    ],
)
def test_exchange_ticker_payloads_normalize(adapter, payload) -> None:
    ticker = adapter.parse_ticker(payload, "BTC/USDT")
    assert ticker.exchange == adapter.exchange
    assert ticker.symbol == "BTC/USDT"
    assert ticker.bid == Decimal("100")
    assert ticker.ask == Decimal("101")
    assert ticker.bid_quantity == Decimal("2")


@pytest.mark.parametrize(
    ("adapter", "payload"),
    [
        (BinanceAdapter(), {"bids": BIDS, "asks": ASKS}),
        (OKXAdapter(), {"code": "0", "data": [{"bids": BIDS, "asks": ASKS, "ts": str(NOW_MS)}]}),
        (BybitAdapter(), {"retCode": 0, "time": NOW_MS, "result": {
            "b": BIDS, "a": ASKS, "ts": str(NOW_MS),
        }}),
        (KuCoinAdapter(), {"code": "200000", "data": {
            "bids": BIDS, "asks": ASKS, "time": NOW_MS,
        }}),
    ],
)
def test_exchange_order_books_normalize(adapter, payload) -> None:
    book = adapter.parse_order_book(payload, "BTC/USDT")
    assert book.exchange == adapter.exchange
    assert book.symbol == "BTC/USDT"
    assert book.bids[0].price == Decimal("100")
    assert book.asks[0].price == Decimal("101")


def test_default_adapters_cover_required_exchanges() -> None:
    assert {adapter.exchange for adapter in default_adapters()} == {
        "binance", "okx", "bybit", "kucoin",
    }


def test_exchange_symbol_mappings() -> None:
    assert BinanceAdapter().ticker_params("BTC/USDT") == {"symbol": "BTCUSDT"}
    assert OKXAdapter().ticker_params("BTC/USDT") == {"instId": "BTC-USDT"}
    assert BybitAdapter().ticker_params("BTC/USDT") == {
        "category": "spot", "symbol": "BTCUSDT",
    }
    assert KuCoinAdapter().ticker_params("BTC/USDT") == {"symbol": "BTC-USDT"}


def test_exchange_api_errors_are_checked() -> None:
    with pytest.raises(ExchangeAPIError):
        OKXAdapter()._ensure_success({"code": "50000", "msg": "error"})
    with pytest.raises(ExchangeAPIError):
        BybitAdapter()._ensure_success({"retCode": 10001, "retMsg": "error"})


def test_malformed_order_book_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        BinanceAdapter().parse_order_book({"bids": [], "asks": ASKS}, "BTC/USDT")


def test_kucoin_order_book_limit_is_bounded() -> None:
    with pytest.raises(DataValidationError):
        KuCoinAdapter().order_book_params("BTC/USDT", 21)
