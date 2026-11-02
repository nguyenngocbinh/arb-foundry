from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arb_foundry.agent_runtime import AutonomousSpotAgent, SpotAgentConfig
from arb_foundry.errors import ConfigurationError, DataValidationError
from arb_foundry.kill_switch import KillSwitchStore
from arb_foundry.models import OrderBook, OrderBookLevel
from arb_foundry.portfolio_store import PortfolioStateStore


def test_small_capital_profile_has_conservative_defaults() -> None:
    config = SpotAgentConfig()
    assert config.target_quantity == Decimal("0.0003")
    assert config.max_trade_notional == Decimal("50")
    assert config.max_drawdown_pct == Decimal("3")
    assert config.min_net_spread_bps == Decimal("10")
    assert config.allow_partial_fills is False


def test_rejects_non_usdt_quote_for_small_capital_profile() -> None:
    with pytest.raises(ConfigurationError):
        SpotAgentConfig(symbols=("BTC/USD",))


def test_requires_two_venues() -> None:
    with pytest.raises(ConfigurationError):
        SpotAgentConfig(exchanges=("binance",))


def test_environment_profile_parses_fee_and_initial_balances() -> None:
    config = SpotAgentConfig.from_env({
        "AGENT_SYMBOLS": "BTC/USDT",
        "AGENT_EXCHANGES": "binance,okx",
        "AGENT_DEFAULT_FEE_RATE": "0.0008",
        "AGENT_INITIAL_BALANCES": '{"binance:USDT":"100","okx:BTC":"0.01","okx:USDT":"100"}',
    })
    assert config.fee_rates == {"binance": Decimal("0.0008"), "okx": Decimal("0.0008")}
    assert config.initial_balances == {
        ("binance", "USDT"): Decimal("100"),
        ("okx", "BTC"): Decimal("0.01"),
        ("okx", "USDT"): Decimal("100"),
    }


def test_rejects_invalid_balance_json() -> None:
    with pytest.raises(ConfigurationError):
        SpotAgentConfig.from_env({"AGENT_INITIAL_BALANCES": "not-json"})


class FakeAdapter:
    def __init__(
        self,
        exchange: str,
        bids: tuple[tuple[str, str], ...],
        asks: tuple[tuple[str, str], ...],
    ):
        self.exchange = exchange
        now = datetime.now(UTC)
        self.book = OrderBook(
            exchange=exchange,
            symbol="BTC/USDT",
            bids=tuple(OrderBookLevel(price, qty) for price, qty in bids),
            asks=tuple(OrderBookLevel(price, qty) for price, qty in asks),
            timestamp=now,
        )

    def get_order_book(self, symbol: str, *, limit: int = 20) -> OrderBook:
        return self.book


def test_cycle_requires_explicit_paper_kill_switch_arm(tmp_path) -> None:
    config = SpotAgentConfig(
        symbols=("BTC/USDT",), exchanges=("binance", "okx"),
        database_dir=tmp_path,
    )
    agent = AutonomousSpotAgent(config, adapters=(
        FakeAdapter("binance", (("99", "2"),), (("100", "2"),)),
        FakeAdapter("okx", (("103", "2"),), (("104", "2"),)),
    ))
    with pytest.raises(DataValidationError, match="kill switch"):
        agent.run_once()


def test_armed_cycle_uses_inventory_risk_gate_and_persists_state(tmp_path) -> None:
    config = SpotAgentConfig(
        symbols=("BTC/USDT",),
        exchanges=("binance", "okx"),
        fee_rates={"binance": Decimal("0.001"), "okx": Decimal("0.001")},
        database_dir=tmp_path,
        initial_balances={
            ("binance", "USDT"): Decimal("100"),
            ("okx", "BTC"): Decimal("0.01"),
            ("okx", "USDT"): Decimal("100"),
        },
    )
    agent = AutonomousSpotAgent(config, adapters=(
        FakeAdapter("binance", (("99", "2"),), (("100", "2"),)),
        FakeAdapter("okx", (("103", "2"),), (("104", "2"),)),
    ))
    with KillSwitchStore(tmp_path / "paper-kill-switch.sqlite3") as switch:
        switch.arm("unit test paper approval")

    report = agent.run_once()
    assert report.candidate_count == 1
    assert report.simulated_count == 1
    assert report.approved_count == 1
    assert report.rejected_count == 0
    with PortfolioStateStore(tmp_path / "portfolio.sqlite3") as store:
        ledger = store.load_latest(portfolio_id=config.portfolio_id)
        assert ledger is not None
        assert ledger.balances[("binance", "BTC")] == Decimal("0.0003")
        assert ledger.balances[("okx", "BTC")] == Decimal("0.0097")
