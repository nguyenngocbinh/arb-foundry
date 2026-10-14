from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arb_foundry.errors import DataValidationError, StaleMarketDataError
from arb_foundry.market_constraints import SymbolConstraints
from arb_foundry.models import MarketType, OrderBook, OrderBookLevel
from arb_foundry.paper import PaperFillStatus, PaperTradingSimulator
from arb_foundry.paper_store import PaperTradeStore

NOW = datetime(2026, 10, 14, 10, tzinfo=UTC)
FEES = {"binance": "0.001", "okx": "0.001"}


def book(
    exchange: str,
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
    *,
    symbol: str = "BTC/USDT",
    market_type: MarketType = MarketType.SPOT,
    timestamp: datetime = NOW,
) -> OrderBook:
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        bids=tuple(OrderBookLevel(price, quantity) for price, quantity in bids),
        asks=tuple(OrderBookLevel(price, quantity) for price, quantity in asks),
        timestamp=timestamp,
        market_type=market_type,
    )


def rules(exchange: str) -> SymbolConstraints:
    return SymbolConstraints(
        exchange=exchange,
        symbol="BTC/USDT",
        price_tick="0.1",
        quantity_step="0.001",
        min_quantity="0.001",
        min_notional="10",
        max_quantity="5",
    )


def test_simulates_full_fill_fees_and_slippage() -> None:
    buy = book("binance", [("99", "5")], [("100", "2")])
    sell = book("okx", [("103", "2")], [("104", "5")])

    result = PaperTradingSimulator().simulate(
        buy, sell, quantity="1", fee_rates=FEES, slippage_bps="10", now=NOW
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
        buy, sell, quantity="1", fee_rates=FEES, now=NOW
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
        buy, sell, quantity="1", fee_rates=FEES, allow_partial_fills=True, now=NOW
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
        buy, sell, quantity="1", fee_rates={"binance": "0", "okx": "0"}, now=NOW
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
        PaperTradingSimulator().simulate(
            buy, sell, quantity=quantity, fee_rates=FEES, now=NOW
        )


def test_rejects_mismatched_symbols_and_non_spot_books() -> None:
    buy = book("binance", [("99", "5")], [("100", "5")])
    other_symbol = book("okx", [("103", "5")], [("104", "5")], symbol="ETH/USDT")
    with pytest.raises(DataValidationError, match="same symbol"):
        PaperTradingSimulator().simulate(
            buy, other_symbol, quantity="1", fee_rates=FEES, now=NOW
        )

    perpetual = book(
        "okx", [("103", "5")], [("104", "5")], market_type=MarketType.PERPETUAL
    )
    with pytest.raises(DataValidationError, match="spot order books"):
        PaperTradingSimulator().simulate(
            buy, perpetual, quantity="1", fee_rates=FEES, now=NOW
        )


def test_rejects_stale_book_and_cross_venue_timestamp_skew() -> None:
    buy = book("binance", [("99", "5")], [("100", "5")])
    stale = book(
        "okx", [("103", "5")], [("104", "5")], timestamp=NOW - timedelta(seconds=10)
    )
    with pytest.raises(StaleMarketDataError):
        PaperTradingSimulator(max_data_age_seconds=5).simulate(
            buy, stale, quantity="1", fee_rates=FEES, now=NOW
        )

    skewed = book(
        "okx", [("103", "5")], [("104", "5")], timestamp=NOW - timedelta(seconds=4)
    )
    with pytest.raises(DataValidationError, match="timestamp skew"):
        PaperTradingSimulator(max_snapshot_skew_seconds=2).simulate(
            buy, skewed, quantity="1", fee_rates=FEES, now=NOW
        )


def test_rejects_invalid_venue_constraints_before_simulating() -> None:
    buy = book("binance", [("99", "5")], [("100", "5")])
    sell = book("okx", [("103", "5")], [("104", "5")])
    result = PaperTradingSimulator().simulate(
        buy,
        sell,
        quantity="1",
        fee_rates=FEES,
        buy_constraints=rules("binance"),
        sell_constraints=rules("okx"),
        now=NOW,
    )
    assert result.status is PaperFillStatus.FILLED

    with pytest.raises(DataValidationError, match="must match"):
        PaperTradingSimulator().simulate(
            buy,
            sell,
            quantity="1",
            fee_rates=FEES,
            buy_constraints=rules("bybit"),
            sell_constraints=rules("okx"),
            now=NOW,
        )

    with pytest.raises(DataValidationError, match="min_notional"):
        strict_buy_rules = SymbolConstraints(
            exchange="binance",
            symbol="BTC/USDT",
            price_tick="0.1",
            quantity_step="0.001",
            min_quantity="0.001",
            min_notional="1000",
        )
        PaperTradingSimulator().simulate(
            buy,
            sell,
            quantity="1",
            fee_rates=FEES,
            buy_constraints=strict_buy_rules,
            sell_constraints=rules("okx"),
            now=NOW,
        )


def test_paper_trade_store_persists_retrievable_audit_events(tmp_path) -> None:
    buy = book("binance", [("99", "5")], [("100", "5")])
    sell = book("okx", [("103", "5")], [("104", "5")])
    result = PaperTradingSimulator().simulate(
        buy, sell, quantity="1", fee_rates=FEES, now=NOW
    )
    database = tmp_path / "audit" / "paper.sqlite3"
    with PaperTradeStore(database) as store:
        event_id = store.record(
            result, buy_snapshot_at=buy.timestamp, sell_snapshot_at=sell.timestamp,
            recorded_at=NOW,
        )
        event = store.get(event_id)
        assert event["event_id"] == event_id
        assert event["payload"]["net_pnl"] == str(result.net_pnl)
        assert event["buy_snapshot_at"] == NOW.isoformat()
        assert store.list_events(limit=1)[0]["event_id"] == event_id

    with PaperTradeStore(database) as reopened:
        assert reopened.get(event_id)["payload"]["status"] == "filled"


def test_store_validates_query_limit(tmp_path) -> None:
    with PaperTradeStore(tmp_path / "paper.sqlite3") as store:
        with pytest.raises(DataValidationError, match="limit"):
            store.list_events(limit=0)
