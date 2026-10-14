from datetime import UTC, datetime
from decimal import Decimal
import sqlite3

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.paper import PaperFillStatus, PaperTradeResult
from arb_foundry.portfolio import PortfolioLedger
from arb_foundry.portfolio_store import PortfolioStateStore

NOW = datetime(2026, 10, 14, 10, tzinfo=UTC)


def make_result(symbol: str = "BTC/USDT") -> PaperTradeResult:
    return PaperTradeResult(
        status=PaperFillStatus.FILLED,
        symbol=symbol,
        buy_exchange="binance",
        sell_exchange="okx",
        requested_quantity=Decimal("1"),
        filled_quantity=Decimal("1"),
        remaining_quantity=Decimal("0"),
        buy_vwap=Decimal("100"),
        sell_vwap=Decimal("105"),
        buy_notional=Decimal("100"),
        sell_notional=Decimal("105"),
        buy_fee=Decimal("0.1"),
        sell_fee=Decimal("0.105"),
        gross_pnl=Decimal("5"),
        net_pnl=Decimal("4.795"),
        slippage_cost=Decimal("0"),
        rejection_reason=None,
    )


def make_ledger() -> PortfolioLedger:
    return PortfolioLedger(
        {
            ("binance", "USDT"): "1000",
            ("binance", "BTC"): "0",
            ("okx", "USDT"): "0",
            ("okx", "BTC"): "2",
        }
    )


def test_state_store_saves_and_restores_latest_snapshot(tmp_path) -> None:
    database = tmp_path / "portfolio" / "state.sqlite3"
    ledger = make_ledger()
    with PortfolioStateStore(database) as store:
        first_id = store.save(ledger, saved_at=NOW)
        store.apply_trade_and_save(ledger, make_result(), now=NOW, saved_at=NOW)
        assert ledger.realized_pnl == Decimal("4.795")
        assert [row["snapshot_id"] for row in store.list_snapshots(limit=5)] == [
            first_id + 1, first_id
        ]

    with PortfolioStateStore(database) as reopened:
        restored = reopened.load_latest()
        assert restored is not None
        assert restored.balances == ledger.balances
        assert restored.realized_pnl == Decimal("4.795")
        assert restored.daily_realized_pnl(NOW) == Decimal("4.795")


def test_state_store_separates_portfolios_and_returns_none_when_missing(tmp_path) -> None:
    with PortfolioStateStore(tmp_path / "state.sqlite3") as store:
        assert store.load_latest(portfolio_id="missing") is None
        store.save(make_ledger(), portfolio_id="paper-a", saved_at=NOW)
        store.save(
            PortfolioLedger({("bybit", "USDT"): "12"}),
            portfolio_id="paper-b",
            saved_at=NOW,
        )
        assert store.load_latest(portfolio_id="paper-a").balance(
            "binance", "USDT"
        ) == Decimal("1000")
        assert store.load_latest(portfolio_id="paper-b").balance(
            "bybit", "USDT"
        ) == Decimal("12")


def test_state_store_detects_snapshot_payload_corruption(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    with PortfolioStateStore(database) as store:
        store.save(make_ledger(), saved_at=NOW)
        store._connection.execute(
            "UPDATE portfolio_snapshots SET payload_json = ? WHERE snapshot_id = 1",
            ('{"schema_version":1}',),
        )
        store._connection.commit()
        with pytest.raises(DataValidationError, match="checksum mismatch"):
            store.load_latest()


def test_apply_trade_and_save_does_not_mutate_memory_when_trade_fails(tmp_path) -> None:
    ledger = make_ledger()
    before = ledger.to_snapshot()

    with PortfolioStateStore(tmp_path / "state.sqlite3") as store:
        with pytest.raises(DataValidationError, match="does not match portfolio"):
            store.apply_trade_and_save(
                ledger, make_result("BTC/USDC"), now=NOW, saved_at=NOW
            )
    assert ledger.to_snapshot() == before


def test_apply_trade_and_save_does_not_mutate_memory_when_sqlite_fails(tmp_path) -> None:
    ledger = make_ledger()
    before = ledger.to_snapshot()
    with PortfolioStateStore(tmp_path / "state.sqlite3") as store:
        store._connection.close()
        with pytest.raises(sqlite3.ProgrammingError):
            store.apply_trade_and_save(ledger, make_result(), now=NOW, saved_at=NOW)
    assert ledger.to_snapshot() == before


def test_state_store_validates_query_limit_and_portfolio_id(tmp_path) -> None:
    with PortfolioStateStore(tmp_path / "state.sqlite3") as store:
        with pytest.raises(DataValidationError, match="limit"):
            store.list_snapshots(limit=0)
        with pytest.raises(DataValidationError, match="portfolio_id"):
            store.save(make_ledger(), portfolio_id="bad id", saved_at=NOW)
