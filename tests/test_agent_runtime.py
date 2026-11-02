from decimal import Decimal

import pytest

from arb_foundry.agent_runtime import SpotAgentConfig
from arb_foundry.errors import ConfigurationError


def test_small_capital_profile_has_conservative_defaults() -> None:
    config = SpotAgentConfig()
    assert config.target_quantity == Decimal("0.001")
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
