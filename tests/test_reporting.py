import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.models import OrderBook, OrderBookLevel
from arb_foundry.paper import PaperTradingSimulator
from arb_foundry.paper_store import PaperTradeStore
from arb_foundry.reporting import build_report

NOW = datetime(2026, 10, 14, 10, tzinfo=UTC)


def _book(exchange: str, *, timestamp: datetime = NOW) -> OrderBook:
    return OrderBook(
        exchange=exchange,
        symbol="BTC/USDT",
        bids=(OrderBookLevel("102", "2"),),
        asks=(OrderBookLevel("100", "2"),),
        timestamp=timestamp,
    )


def _result():
    return PaperTradingSimulator().simulate(
        _book("binance"),
        _book("okx"),
        quantity="1",
        fee_rates={"binance": "0.001", "okx": "0.001"},
        slippage_bps="10",
        now=NOW,
    )


def test_report_groups_pnl_costs_and_rejections_by_strategy(tmp_path) -> None:
    database = tmp_path / "paper.sqlite3"
    result = _result()
    with PaperTradeStore(database) as store:
        store.record(
            result,
            buy_snapshot_at=NOW,
            sell_snapshot_at=NOW,
            recorded_at=NOW,
            strategy="Momentum",
        )
        store.record_rejection(
            symbol="btc/usdt",
            buy_exchange="binance",
            sell_exchange="okx",
            buy_snapshot_at=NOW,
            sell_snapshot_at=NOW,
            reason="risk limit",
            recorded_at=NOW,
            strategy="Mean Reversion",
        )
        report = build_report(
            store,
            since=NOW - timedelta(minutes=1),
            until=NOW + timedelta(minutes=1),
            group_by=("strategy", "exchange_pair"),
        )

    assert len(report.rows) == 2
    trade_row = next(row for row in report.rows if dict(row.dimensions)["strategy"] == "momentum")
    rejection_row = next(
        row for row in report.rows if dict(row.dimensions)["strategy"] == "mean reversion"
    )
    assert dict(trade_row.dimensions)["exchange_pair"] == "binance->okx"
    assert trade_row.attempts == 1
    assert trade_row.filled == 1
    assert trade_row.gross_pnl == result.gross_pnl
    assert trade_row.fees == result.buy_fee + result.sell_fee
    assert trade_row.slippage_cost == result.slippage_cost
    assert trade_row.net_pnl == result.net_pnl
    assert rejection_row.pretrade_rejections == 1
    assert rejection_row.net_pnl == Decimal("0")
    assert not report.possibly_truncated


def test_report_uses_start_inclusive_end_exclusive_window(tmp_path) -> None:
    with PaperTradeStore(tmp_path / "paper.sqlite3") as store:
        store.record(
            _result(),
            buy_snapshot_at=NOW,
            sell_snapshot_at=NOW,
            recorded_at=NOW,
            strategy="baseline",
        )
        report = build_report(
            store, since=NOW, until=NOW + timedelta(seconds=1), group_by=("day",)
        )
        excluded = build_report(
            store, since=NOW + timedelta(seconds=1), until=NOW + timedelta(seconds=2)
        )

    assert len(report.rows) == 1
    assert dict(report.rows[0].dimensions)["day"] == "2026-10-14"
    assert excluded.rows == ()


@pytest.mark.parametrize(
    ("since", "until", "group_by", "limit"),
    [
        (NOW, NOW, ("symbol",), 100),
        (NOW, NOW + timedelta(seconds=1), ("unknown",), 100),
        (NOW, NOW + timedelta(seconds=1), ("symbol", "symbol"), 100),
        (NOW, NOW + timedelta(seconds=1), ("symbol",), 0),
    ],
)
def test_report_validates_window_grouping_and_limit(tmp_path, since, until, group_by, limit):
    with PaperTradeStore(tmp_path / "paper.sqlite3") as store:
        with pytest.raises(DataValidationError):
            build_report(store, since=since, until=until, group_by=group_by, limit=limit)


def test_existing_database_migrates_without_losing_old_events(tmp_path) -> None:
    database = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE paper_trade_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            status TEXT NOT NULL,
            buy_exchange TEXT NOT NULL,
            sell_exchange TEXT NOT NULL,
            buy_snapshot_at TEXT NOT NULL,
            sell_snapshot_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO paper_trade_events (
            recorded_at, symbol, status, buy_exchange, sell_exchange,
            buy_snapshot_at, sell_snapshot_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (NOW.isoformat(), "BTC/USDT", "filled", "binance", "okx", NOW.isoformat(),
         NOW.isoformat(), "{}"),
    )
    connection.commit()
    connection.close()

    with PaperTradeStore(database) as store:
        event = store.get(1)

    assert event["strategy"] == "unspecified"
