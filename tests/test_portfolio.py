from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.paper import PaperFillStatus, PaperTradeResult
from arb_foundry.portfolio import PortfolioLedger, RiskEngine, RiskLimits

NOW = datetime(2026, 10, 14, 10, tzinfo=UTC)


def result(
    *,
    buy_notional: str = "100",
    sell_notional: str = "105",
    buy_fee: str = "0.1",
    sell_fee: str = "0.105",
    quantity: str = "1",
    net_pnl: str = "4.795",
    status: PaperFillStatus = PaperFillStatus.FILLED,
    symbol: str = "BTC/USDT",
) -> PaperTradeResult:
    rejected = status is PaperFillStatus.REJECTED
    return PaperTradeResult(
        status=status,
        symbol=symbol,
        buy_exchange="binance",
        sell_exchange="okx",
        requested_quantity=Decimal(quantity),
        filled_quantity=Decimal("0") if rejected else Decimal(quantity),
        remaining_quantity=Decimal(quantity) if rejected else Decimal("0"),
        buy_vwap=None if rejected else Decimal(buy_notional) / Decimal(quantity),
        sell_vwap=None if rejected else Decimal(sell_notional) / Decimal(quantity),
        buy_notional=Decimal("0") if rejected else Decimal(buy_notional),
        sell_notional=Decimal("0") if rejected else Decimal(sell_notional),
        buy_fee=Decimal("0") if rejected else Decimal(buy_fee),
        sell_fee=Decimal("0") if rejected else Decimal(sell_fee),
        gross_pnl=Decimal("0") if rejected else Decimal(sell_notional) - Decimal(buy_notional),
        net_pnl=Decimal("0") if rejected else Decimal(net_pnl),
        slippage_cost=Decimal("0"),
        rejection_reason="depth unavailable" if rejected else None,
    )


def portfolio() -> PortfolioLedger:
    return PortfolioLedger(
        {
            ("binance", "USDT"): "1000",
            ("binance", "BTC"): "0",
            ("okx", "USDT"): "0",
            ("okx", "BTC"): "2",
        }
    )


def test_applies_paired_trade_atomically_and_tracks_pnl() -> None:
    ledger = portfolio()
    trade = result()

    ledger.apply_trade(trade, now=NOW)

    assert ledger.balance("binance", "USDT") == Decimal("899.9")
    assert ledger.balance("binance", "BTC") == Decimal("1")
    assert ledger.balance("okx", "BTC") == Decimal("1")
    assert ledger.balance("okx", "USDT") == Decimal("104.895")
    assert ledger.realized_pnl == Decimal("4.795")
    assert ledger.daily_realized_pnl(NOW) == Decimal("4.795")


def test_insufficient_inventory_does_not_mutate_any_balance() -> None:
    ledger = PortfolioLedger({("binance", "USDT"): "1000", ("okx", "BTC"): "0"})
    before = ledger.balances

    with pytest.raises(DataValidationError, match="insufficient BTC"):
        ledger.apply_trade(result(), now=NOW)

    assert ledger.balances == before
    assert ledger.realized_pnl == 0


def test_rejects_result_without_fill_and_invalid_initial_balance() -> None:
    with pytest.raises(DataValidationError, match="negative"):
        PortfolioLedger({("binance", "USDT"): "-1"})

    with pytest.raises(DataValidationError, match="only filled or partial"):
        portfolio().apply_trade(result(status=PaperFillStatus.REJECTED), now=NOW)


def test_marks_equity_exposure_and_drawdown() -> None:
    ledger = portfolio()
    first = ledger.valuation({"BTC": "100"}, now=NOW)
    assert first.equity == Decimal("1200")
    assert first.exposure_by_exchange == {
        "binance": Decimal("1000"),
        "okx": Decimal("200"),
    }

    ledger.apply_trade(result(), now=NOW)
    after = ledger.valuation({"BTC": "90"}, now=NOW + timedelta(minutes=1))
    expected_equity = Decimal("1184.795")
    assert after.equity == expected_equity
    assert after.peak_equity == Decimal("1200")
    assert after.drawdown_pct == (
        (Decimal("1200") - expected_equity) / Decimal("1200") * Decimal("100")
    )


def test_valuation_requires_mark_for_nonzero_assets() -> None:
    with pytest.raises(DataValidationError, match="missing mark price"):
        portfolio().valuation({})


def test_risk_engine_approves_within_limits() -> None:
    decision = RiskEngine(
        RiskLimits(
            max_trade_notional="200",
            max_exchange_exposure="1500",
            max_drawdown_pct="20",
            max_daily_loss="100",
        )
    ).evaluate(result(), portfolio(), mark_prices={"BTC": "100"}, now=NOW)

    assert decision.approved is True
    assert decision.reason == "approved"


@pytest.mark.parametrize(
    ("limits", "expected_reason"),
    [
        (RiskLimits(max_trade_notional="99"), "max_trade_notional"),
        (
            RiskLimits(max_trade_notional="200", max_exchange_exposure="150"),
            "max_exchange_exposure",
        ),
    ],
)
def test_risk_engine_rejects_limit_breaches(limits: RiskLimits, expected_reason: str) -> None:
    decision = RiskEngine(limits).evaluate(
        result(), portfolio(), mark_prices={"BTC": "100"}, now=NOW
    )
    assert decision.approved is False
    assert expected_reason in decision.reason


def test_risk_engine_rejects_daily_loss_limit() -> None:
    losing_trade = result(
        buy_notional="100",
        sell_notional="98",
        buy_fee="0.1",
        sell_fee="0.098",
        net_pnl="-2.198",
    )
    decision = RiskEngine(
        RiskLimits(max_trade_notional="200", max_daily_loss="1")
    ).evaluate(losing_trade, portfolio(), mark_prices={"BTC": "100"}, now=NOW)
    assert decision.approved is False
    assert "max_daily_loss" in decision.reason


def test_risk_engine_rejects_insufficient_inventory() -> None:
    ledger = PortfolioLedger({("binance", "USDT"): "1000", ("okx", "BTC"): "0"})
    decision = RiskEngine(RiskLimits(max_trade_notional="200")).evaluate(
        result(), ledger, mark_prices={"BTC": "100"}, now=NOW
    )
    assert decision.approved is False
    assert "insufficient BTC" in decision.reason


def test_risk_engine_rejects_simulator_rejection() -> None:
    decision = RiskEngine(RiskLimits(max_trade_notional="200")).evaluate(
        result(status=PaperFillStatus.REJECTED),
        portfolio(),
        mark_prices={"BTC": "100"},
        now=NOW,
    )
    assert decision.approved is False
    assert "simulator rejected" in decision.reason


def test_portfolio_rejects_mismatched_quote_asset() -> None:
    ledger = PortfolioLedger({("binance", "USDT"): "1000", ("okx", "BTC"): "2"})
    with pytest.raises(DataValidationError, match="does not match portfolio"):
        ledger.apply_trade(result(symbol="BTC/USDC"), now=NOW)
