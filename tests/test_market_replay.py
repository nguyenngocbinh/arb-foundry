from datetime import UTC, datetime, timedelta

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.market_replay import MarketReplayStore
from arb_foundry.models import OrderBook, OrderBookLevel
from arb_foundry.scanner import SpotArbitrageScanner

NOW = datetime(2026, 10, 14, 10, tzinfo=UTC)
FEES = {"binance": "0.001", "okx": "0.001"}


def book(exchange: str, bids: list[tuple[str, str]], asks: list[tuple[str, str]], *, at=NOW):
    return OrderBook(
        exchange=exchange,
        symbol="BTC/USDT",
        bids=tuple(OrderBookLevel(price, quantity) for price, quantity in bids),
        asks=tuple(OrderBookLevel(price, quantity) for price, quantity in asks),
        timestamp=at,
    )


def books():
    return [
        book("binance", [("99", "3")], [("100", "3")]),
        book("okx", [("105", "3")], [("106", "3")]),
    ]


def test_market_replay_survives_reopen_and_reproduces_scan(tmp_path):
    database = tmp_path / "market.sqlite3"
    scanner = SpotArbitrageScanner(max_data_age_seconds=5, min_net_spread_bps="1")
    source_books = books()
    expected = scanner.scan(source_books, fee_rates=FEES, target_quantity="1", now=NOW)

    with MarketReplayStore(database) as store:
        run_id = store.record_scan(
            source_books, fee_rates=FEES, target_quantity="1", scanner=scanner, now=NOW
        )
        assert run_id == 1

    with MarketReplayStore(database) as store:
        actual = store.replay_scan(run_id)
        assert actual == expected
        runs = store.list_runs(limit=10)
        assert len(runs) == 1
        assert runs[0]["run_id"] == 1
        assert runs[0]["recorded_at"] == NOW.isoformat()
        assert runs[0]["schema_version"] == 1
        assert len(runs[0]["sha256"]) == 64


def test_replay_keeps_original_clock_for_stale_book_diagnostics(tmp_path):
    database = tmp_path / "market.sqlite3"
    stale_books = [
        book("binance", [("99", "3")], [("100", "3")], at=NOW - timedelta(seconds=20)),
        books()[1],
    ]
    scanner = SpotArbitrageScanner(max_data_age_seconds=5)
    with MarketReplayStore(database) as store:
        run_id = store.record_scan(
            stale_books, fee_rates=FEES, target_quantity="1", scanner=scanner, now=NOW
        )
        report = store.replay_scan(run_id)
    assert report.opportunities == ()
    assert any("unusable market data" in item.reason for item in report.rejections)


def test_replay_fails_closed_when_snapshot_checksum_is_corrupted(tmp_path):
    with MarketReplayStore(tmp_path / "market.sqlite3") as store:
        run_id = store.record_scan(
            books(),
            fee_rates=FEES,
            target_quantity="1",
            scanner=SpotArbitrageScanner(),
            now=NOW,
        )
        store._connection.execute(
            "UPDATE market_scan_runs SET payload_json = ? WHERE run_id = ?",
            ('{"schema_version":1}', run_id),
        )
        store._connection.commit()
        with pytest.raises(DataValidationError, match="checksum mismatch"):
            store.replay_scan(run_id)


def test_record_scan_rejects_duplicate_normalized_fee_keys(tmp_path):
    with MarketReplayStore(tmp_path / "market.sqlite3") as store:
        with pytest.raises(DataValidationError, match="duplicate normalized"):
            store.record_scan(
                books(),
                fee_rates={"BINANCE": "0.001", "binance": "0.002", "okx": "0.001"},
                target_quantity="1",
                scanner=SpotArbitrageScanner(),
                now=NOW,
            )
        assert store.list_runs() == []


def test_replay_rejects_unknown_run_id_and_invalid_limits(tmp_path):
    with MarketReplayStore(tmp_path / "market.sqlite3") as store:
        with pytest.raises(DataValidationError, match="not found"):
            store.replay_scan(42)
        with pytest.raises(DataValidationError, match="limit"):
            store.list_runs(limit=0)
