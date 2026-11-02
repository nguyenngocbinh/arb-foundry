"""Autonomous, paper-only spot arbitrage supervisor."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from arb_foundry.errors import ConfigurationError, DataValidationError
from arb_foundry.exchanges import PublicRestAdapter, default_adapters
from arb_foundry.kill_switch import KillSwitchStore
from arb_foundry.market_data import normalize_symbol
from arb_foundry.models import OrderBook
from arb_foundry.paper import PaperFillStatus, PaperTradingSimulator
from arb_foundry.paper_store import PaperTradeStore
from arb_foundry.portfolio import PortfolioLedger, RiskEngine, RiskLimits
from arb_foundry.portfolio_store import PortfolioStateStore
from arb_foundry.scanner import SpotArbitrageScanner


def _decimal(name: str, raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError) as exc:
        raise ConfigurationError(f"{name} must be a decimal number") from exc
    if not value.is_finite():
        raise ConfigurationError(f"{name} must be finite")
    return value


def _csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _json_balances(raw: str) -> dict[tuple[str, str], Decimal]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ConfigurationError("AGENT_INITIAL_BALANCES must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError("AGENT_INITIAL_BALANCES must be a JSON object")
    balances: dict[tuple[str, str], Decimal] = {}
    for key, amount in payload.items():
        if not isinstance(key, str) or ":" not in key:
            raise ConfigurationError("balance keys must use exchange:asset")
        exchange, asset = key.split(":", 1)
        balances[(exchange, asset)] = _decimal("initial balance", str(amount))
    return balances


@dataclass(frozen=True, slots=True)
class SpotAgentConfig:
    """Small-capital research settings; no private exchange credentials."""

    symbols: tuple[str, ...] = ("BTC/USDT",)
    exchanges: tuple[str, ...] = ("binance", "okx", "bybit", "kucoin")
    fee_rates: dict[str, Decimal] | None = None
    target_quantity: Decimal = Decimal("0.0003")
    min_net_spread_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    max_data_age_seconds: int = 5
    max_snapshot_skew_seconds: int = 2
    max_trade_notional: Decimal = Decimal("50")
    max_exchange_exposure: Decimal = Decimal("250")
    max_drawdown_pct: Decimal = Decimal("3")
    max_daily_loss: Decimal = Decimal("10")
    order_book_depth: int = 20
    database_dir: Path = Path("data/agent")
    portfolio_id: str = "small-capital-spot"
    initial_balances: dict[tuple[str, str], Decimal] | None = None
    allow_partial_fills: bool = False

    def __post_init__(self) -> None:
        if not self.symbols or not self.exchanges:
            raise ConfigurationError("at least one symbol and exchange are required")
        object.__setattr__(self, "symbols", tuple(normalize_symbol(s) for s in self.symbols))
        exchanges = tuple(item.strip().lower() for item in self.exchanges)
        if len(set(exchanges)) != len(exchanges):
            raise ConfigurationError("exchange list must not contain duplicates")
        object.__setattr__(self, "exchanges", exchanges)
        if any(not symbol.endswith("/USDT") for symbol in self.symbols):
            raise ConfigurationError("small-capital profile currently supports USDT-quoted spot pairs only")
        if len(exchanges) < 2:
            raise ConfigurationError("at least two exchanges are required")
        if self.target_quantity <= 0 or self.min_net_spread_bps < 0 or self.slippage_bps < 0:
            raise ConfigurationError("quantity must be positive and spread/slippage non-negative")
        if self.max_data_age_seconds <= 0 or self.max_snapshot_skew_seconds < 0:
            raise ConfigurationError("invalid market-data freshness limits")
        if self.order_book_depth < 1:
            raise ConfigurationError("order_book_depth must be positive")
        fees = self.fee_rates or {exchange: Decimal("0.001") for exchange in exchanges}
        fees = {key.strip().lower(): value for key, value in fees.items()}
        if set(exchanges) - set(fees):
            raise ConfigurationError("fee rate required for every configured exchange")
        if any(value < 0 or value >= 1 for value in fees.values()):
            raise ConfigurationError("fee rates must be in [0, 1)")
        object.__setattr__(self, "fee_rates", fees)
        for name in ("max_trade_notional", "max_exchange_exposure", "max_daily_loss"):
            if getattr(self, name) <= 0:
                raise ConfigurationError(f"{name} must be greater than zero")
        if not Decimal("0") < self.max_drawdown_pct <= Decimal("100"):
            raise ConfigurationError("max_drawdown_pct must be in (0, 100]")
        if not self.portfolio_id.strip():
            raise ConfigurationError("portfolio_id must not be empty")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "SpotAgentConfig":
        env = os.environ if environ is None else environ
        exchanges = _csv(env.get("AGENT_EXCHANGES", "binance,okx,bybit,kucoin"))
        default_fee = _decimal("AGENT_DEFAULT_FEE_RATE", env.get("AGENT_DEFAULT_FEE_RATE", "0.001"))
        fees = {exchange: default_fee for exchange in exchanges}
        return cls(
            symbols=_csv(env.get("AGENT_SYMBOLS", "BTC/USDT")),
            exchanges=exchanges,
            fee_rates=fees,
            target_quantity=_decimal("AGENT_TARGET_QUANTITY", env.get("AGENT_TARGET_QUANTITY", "0.0003")),
            min_net_spread_bps=_decimal("AGENT_MIN_NET_SPREAD_BPS", env.get("AGENT_MIN_NET_SPREAD_BPS", "10")),
            slippage_bps=_decimal("AGENT_SLIPPAGE_BPS", env.get("AGENT_SLIPPAGE_BPS", "5")),
            max_data_age_seconds=int(env.get("AGENT_MAX_DATA_AGE_SECONDS", "5")),
            max_snapshot_skew_seconds=int(env.get("AGENT_MAX_SNAPSHOT_SKEW_SECONDS", "2")),
            max_trade_notional=_decimal("AGENT_MAX_TRADE_NOTIONAL", env.get("AGENT_MAX_TRADE_NOTIONAL", "50")),
            max_exchange_exposure=_decimal("AGENT_MAX_EXCHANGE_EXPOSURE", env.get("AGENT_MAX_EXCHANGE_EXPOSURE", "250")),
            max_drawdown_pct=_decimal("AGENT_MAX_DRAWDOWN_PCT", env.get("AGENT_MAX_DRAWDOWN_PCT", "3")),
            max_daily_loss=_decimal("AGENT_MAX_DAILY_LOSS", env.get("AGENT_MAX_DAILY_LOSS", "10")),
            order_book_depth=int(env.get("AGENT_ORDER_BOOK_DEPTH", "20")),
            database_dir=Path(env.get("AGENT_DATABASE_DIR", "data/agent")),
            portfolio_id=env.get("AGENT_PORTFOLIO_ID", "small-capital-spot"),
            initial_balances=_json_balances(env.get("AGENT_INITIAL_BALANCES", "{}")),
            allow_partial_fills=env.get("AGENT_ALLOW_PARTIAL_FILLS", "false").lower() == "true",
        )


@dataclass(frozen=True, slots=True)
class AgentCycleReport:
    scanned_books: int
    candidate_count: int
    simulated_count: int
    approved_count: int
    rejected_count: int
    opportunities: tuple[dict[str, str], ...]
    decisions: tuple[dict[str, object], ...]


class AutonomousSpotAgent:
    """Fetch, scan, simulate, risk-check and persist one paper-only cycle."""

    def __init__(self, config: SpotAgentConfig, *, adapters: Iterable[PublicRestAdapter] | None = None) -> None:
        self.config = config
        available = tuple(adapters) if adapters is not None else default_adapters()
        self.adapters = {adapter.exchange: adapter for adapter in available if adapter.exchange in config.exchanges}
        missing = set(config.exchanges) - set(self.adapters)
        if missing:
            raise ConfigurationError(f"missing exchange adapters: {', '.join(sorted(missing))}")
        self.scanner = SpotArbitrageScanner(
            max_data_age_seconds=config.max_data_age_seconds,
            min_net_spread_bps=config.min_net_spread_bps,
        )
        self.simulator = PaperTradingSimulator(
            max_data_age_seconds=config.max_data_age_seconds,
            max_snapshot_skew_seconds=config.max_snapshot_skew_seconds,
        )
        self.risk = RiskEngine(RiskLimits(
            max_trade_notional=config.max_trade_notional,
            max_exchange_exposure=config.max_exchange_exposure,
            max_drawdown_pct=config.max_drawdown_pct,
            max_daily_loss=config.max_daily_loss,
        ))

    def _fetch_books(self) -> list[OrderBook]:
        books: list[OrderBook] = []
        failures: list[str] = []
        for symbol in self.config.symbols:
            for exchange in self.config.exchanges:
                try:
                    books.append(self.adapters[exchange].get_order_book(symbol, limit=self.config.order_book_depth))
                except Exception as exc:
                    failures.append(f"{exchange} {symbol}: {type(exc).__name__}: {exc}")
        if not books:
            raise DataValidationError("no order books fetched: " + "; ".join(failures))
        return books

    @staticmethod
    def _mark_prices(books: list[OrderBook]) -> dict[str, Decimal]:
        marks: dict[str, list[Decimal]] = {}
        for book in books:
            base, quote = book.symbol.split("/", 1)
            if quote == "USDT":
                mid = (book.bids[0].price + book.asks[0].price) / Decimal("2")
                marks.setdefault(base, []).append(mid)
        return {asset: sum(values, Decimal("0")) / len(values) for asset, values in marks.items()}

    def run_once(self) -> AgentCycleReport:
        """Run one cycle; the persisted paper kill switch must be explicitly armed."""
        self.config.database_dir.mkdir(parents=True, exist_ok=True)
        books = self._fetch_books()
        scan = self.scanner.scan(books, fee_rates=self.config.fee_rates or {}, target_quantity=self.config.target_quantity)
        book_by_venue = {(book.exchange, book.symbol): book for book in books}
        decisions: list[dict[str, object]] = []
        opportunities: list[dict[str, str]] = []
        approved = rejected = simulated = 0
        with KillSwitchStore(self.config.database_dir / "paper-kill-switch.sqlite3") as switch, \
             PaperTradeStore(self.config.database_dir / "paper-trades.sqlite3") as trades, \
             PortfolioStateStore(self.config.database_dir / "portfolio.sqlite3") as states:
            switch.require_armed()
            self.simulator.kill_switch = switch
            ledger = states.load_latest(portfolio_id=self.config.portfolio_id)
            if ledger is None:
                ledger = PortfolioLedger(self.config.initial_balances or {}, quote_asset="USDT")
                states.save(ledger, portfolio_id=self.config.portfolio_id)
            marks = self._mark_prices(books)
            for item in scan.opportunities:
                opportunities.append({
                    "symbol": item.symbol, "buy_exchange": item.buy_exchange,
                    "sell_exchange": item.sell_exchange, "net_spread_bps": str(item.net_spread_bps),
                    "quantity": str(item.quantity),
                })
                buy = book_by_venue[(item.buy_exchange, item.symbol)]
                sell = book_by_venue[(item.sell_exchange, item.symbol)]
                try:
                    result = self.simulator.simulate(
                        buy, sell, quantity=item.quantity, fee_rates=self.config.fee_rates or {},
                        slippage_bps=self.config.slippage_bps,
                        allow_partial_fills=self.config.allow_partial_fills,
                    )
                    simulated += 1
                    decision = self.risk.evaluate(result, ledger, mark_prices=marks)
                    accepted = decision.approved and result.status is not PaperFillStatus.REJECTED
                    trades.record(
                        result, buy_snapshot_at=buy.timestamp, sell_snapshot_at=sell.timestamp,
                        strategy="autonomous-spot-agent",
                    )
                    if accepted:
                        states.apply_trade_and_save(ledger, result, portfolio_id=self.config.portfolio_id)
                        approved += 1
                    else:
                        rejected += 1
                        trades.record_rejection(
                            symbol=item.symbol, buy_exchange=item.buy_exchange, sell_exchange=item.sell_exchange,
                            buy_snapshot_at=buy.timestamp, sell_snapshot_at=sell.timestamp,
                            reason=decision.reason if not decision.approved else (result.rejection_reason or "simulator rejected"),
                            context={"risk_metrics": decision.metrics}, strategy="autonomous-spot-agent",
                        )
                    decisions.append({
                        "symbol": item.symbol, "buy_exchange": item.buy_exchange,
                        "sell_exchange": item.sell_exchange, "approved": accepted,
                        "reason": decision.reason, "net_pnl": str(result.net_pnl),
                        "metrics": decision.metrics,
                    })
                except Exception as exc:
                    rejected += 1
                    reason = f"{type(exc).__name__}: {exc}"
                    decisions.append({"symbol": item.symbol, "approved": False, "reason": reason})
                    trades.record_rejection(
                        symbol=item.symbol, buy_exchange=item.buy_exchange, sell_exchange=item.sell_exchange,
                        buy_snapshot_at=buy.timestamp, sell_snapshot_at=sell.timestamp,
                        reason=reason, strategy="autonomous-spot-agent",
                    )
        return AgentCycleReport(
            scanned_books=len(books), candidate_count=len(scan.opportunities),
            simulated_count=simulated, approved_count=approved, rejected_count=rejected,
            opportunities=tuple(opportunities), decisions=tuple(decisions),
        )
