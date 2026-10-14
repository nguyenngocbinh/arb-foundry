"""In-memory paper portfolio accounting and deterministic risk checks.

Balances are tracked per exchange and asset. The module is simulation-only:
it never connects to an exchange, transfers funds, or submits orders.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from arb_foundry.errors import DataValidationError
from arb_foundry.paper import PaperFillStatus, PaperTradeResult
from arb_foundry.time_utils import ensure_utc, utc_now

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")


def _decimal(name: str, value: Decimal | int | float | str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataValidationError(f"{name} must be a valid decimal number") from exc
    if not parsed.is_finite():
        raise DataValidationError(f"{name} must be finite")
    return parsed


def _identity(name: str, value: str, *, upper: bool = False) -> str:
    if not isinstance(value, str):
        raise DataValidationError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise DataValidationError(f"{name} must be non-empty and contain no whitespace")
    return normalized.upper() if upper else normalized.lower()


def _split_symbol(symbol: str) -> tuple[str, str]:
    parts = symbol.split("/")
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise DataValidationError("symbol must use BASE/QUOTE format")
    return parts[0].strip().upper(), parts[1].strip().upper()


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    """A mark-to-market snapshot expressed in one quote currency."""

    quote_asset: str
    equity: Decimal
    peak_equity: Decimal
    drawdown_pct: Decimal
    exposure_by_exchange: dict[str, Decimal]
    daily_realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """A deterministic pre-trade risk decision with a human-readable reason."""

    approved: bool
    reason: str
    metrics: dict[str, str]


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Hard paper-risk limits. All monetary limits use the configured quote asset."""

    max_trade_notional: Decimal
    max_exchange_exposure: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    max_daily_loss: Decimal | None = None

    def __post_init__(self) -> None:
        trade_limit = _decimal("max_trade_notional", self.max_trade_notional)
        if trade_limit <= 0:
            raise DataValidationError("max_trade_notional must be greater than zero")
        object.__setattr__(self, "max_trade_notional", trade_limit)
        for name in ("max_exchange_exposure", "max_drawdown_pct", "max_daily_loss"):
            raw = getattr(self, name)
            if raw is None:
                continue
            parsed = _decimal(name, raw)
            if parsed <= 0:
                raise DataValidationError(f"{name} must be greater than zero")
            if name == "max_drawdown_pct" and parsed > _HUNDRED:
                raise DataValidationError("max_drawdown_pct must not exceed 100")
            object.__setattr__(self, name, parsed)


class PortfolioLedger:
    """Track simulated spot balances and realized PnL without exchange connectivity."""

    def __init__(
        self,
        initial_balances: Mapping[tuple[str, str], Decimal | int | float | str],
        *,
        quote_asset: str = "USDT",
    ) -> None:
        self.quote_asset = _identity("quote_asset", quote_asset, upper=True)
        self._balances: dict[tuple[str, str], Decimal] = {}
        for key, raw_amount in initial_balances.items():
            if not isinstance(key, tuple) or len(key) != 2:
                raise DataValidationError("balance keys must be (exchange, asset) tuples")
            exchange = _identity("exchange", key[0])
            asset = _identity("asset", key[1], upper=True)
            amount = _decimal(f"balance {exchange}/{asset}", raw_amount)
            if amount < 0:
                raise DataValidationError("initial balances must not be negative")
            normalized_key = (exchange, asset)
            if normalized_key in self._balances:
                raise DataValidationError(f"duplicate normalized balance key: {normalized_key}")
            self._balances[normalized_key] = amount
        self._realized_pnl = _ZERO
        self._daily_realized: dict[str, Decimal] = {}
        self._peak_equity: Decimal | None = None

    @property
    def balances(self) -> dict[tuple[str, str], Decimal]:
        """Return a defensive copy of balances keyed by (exchange, asset)."""
        return dict(self._balances)

    @property
    def realized_pnl(self) -> Decimal:
        return self._realized_pnl


    def to_snapshot(self) -> dict[str, object]:
        """Return a JSON-safe, versioned snapshot for durable paper-state recovery."""
        return {
            "schema_version": 1,
            "quote_asset": self.quote_asset,
            "balances": [
                {"exchange": exchange, "asset": asset, "amount": str(amount)}
                for (exchange, asset), amount in sorted(self._balances.items())
            ],
            "realized_pnl": str(self._realized_pnl),
            "daily_realized_pnl": {
                day: str(amount) for day, amount in sorted(self._daily_realized.items())
            },
            "peak_equity": (
                str(self._peak_equity) if self._peak_equity is not None else None
            ),
        }

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, object]) -> PortfolioLedger:
        """Validate and restore a version-1 snapshot without silently defaulting fields."""
        if not isinstance(snapshot, Mapping):
            raise DataValidationError("portfolio snapshot must be a mapping")
        if type(snapshot.get("schema_version")) is not int or snapshot.get("schema_version") != 1:
            raise DataValidationError("unsupported portfolio snapshot schema_version")
        quote_asset = snapshot.get("quote_asset")
        raw_balances = snapshot.get("balances")
        raw_daily = snapshot.get("daily_realized_pnl")
        if not isinstance(quote_asset, str) or not isinstance(raw_balances, list):
            raise DataValidationError("portfolio snapshot has invalid quote_asset or balances")
        if not isinstance(raw_daily, Mapping):
            raise DataValidationError("portfolio snapshot daily_realized_pnl must be a mapping")

        balances: dict[tuple[str, str], str] = {}
        for item in raw_balances:
            if not isinstance(item, Mapping):
                raise DataValidationError("portfolio snapshot balance entries must be mappings")
            exchange, asset, amount = (
                item.get("exchange"), item.get("asset"), item.get("amount")
            )
            if not isinstance(exchange, str) or not isinstance(asset, str):
                raise DataValidationError("portfolio snapshot balance identity must be strings")
            if not isinstance(amount, str):
                raise DataValidationError("portfolio snapshot balance amount must be a string")
            key = (_identity("exchange", exchange), _identity("asset", asset, upper=True))
            if key in balances:
                raise DataValidationError(f"duplicate normalized balance key: {key}")
            balances[key] = amount

        ledger = cls(balances, quote_asset=quote_asset)
        raw_realized = snapshot.get("realized_pnl")
        if not isinstance(raw_realized, str):
            raise DataValidationError("portfolio snapshot realized_pnl must be a string")
        ledger._realized_pnl = _decimal("realized_pnl", raw_realized)

        daily: dict[str, Decimal] = {}
        for raw_day, raw_amount in raw_daily.items():
            if not isinstance(raw_day, str) or not isinstance(raw_amount, str):
                raise DataValidationError("daily realized PnL keys and values must be strings")
            try:
                parsed_day = datetime.strptime(raw_day, "%Y-%m-%d").date()
            except ValueError as exc:
                raise DataValidationError("daily realized PnL keys must use YYYY-MM-DD") from exc
            if parsed_day.isoformat() != raw_day:
                raise DataValidationError("daily realized PnL keys must use YYYY-MM-DD")
            daily[raw_day] = _decimal(f"daily realized PnL for {raw_day}", raw_amount)
        ledger._daily_realized = daily

        raw_peak = snapshot.get("peak_equity")
        if raw_peak is not None:
            if not isinstance(raw_peak, str):
                raise DataValidationError("portfolio snapshot peak_equity must be a string or null")
            peak = _decimal("peak_equity", raw_peak)
            if peak < 0:
                raise DataValidationError("portfolio snapshot peak_equity must not be negative")
            ledger._peak_equity = peak
        return ledger

    def _replace_with(self, other: PortfolioLedger) -> None:
        """Replace this ledger's state from a validated ledger; internal persistence hook."""
        if not isinstance(other, PortfolioLedger):
            raise DataValidationError("other must be a PortfolioLedger")
        self.quote_asset = other.quote_asset
        self._balances = dict(other._balances)
        self._realized_pnl = other._realized_pnl
        self._daily_realized = dict(other._daily_realized)
        self._peak_equity = other._peak_equity

    def balance(self, exchange: str, asset: str) -> Decimal:
        """Return a balance, defaulting to zero when the key has never been seen."""
        return self._balances.get(
            (_identity("exchange", exchange), _identity("asset", asset, upper=True)), _ZERO
        )

    def daily_realized_pnl(self, now: datetime | None = None) -> Decimal:
        """Return realized PnL for the UTC date containing now."""
        current = ensure_utc(now) if now is not None else utc_now()
        return self._daily_realized.get(current.date().isoformat(), _ZERO)

    def projected_balances(self, result: PaperTradeResult) -> dict[tuple[str, str], Decimal]:
        """Validate a result and return its post-trade balances without mutating state."""
        if not isinstance(result, PaperTradeResult):
            raise DataValidationError("result must be a PaperTradeResult")
        if result.status is PaperFillStatus.REJECTED or result.filled_quantity <= 0:
            raise DataValidationError("only filled or partial paper results can change balances")
        base_asset, quote_asset = _split_symbol(result.symbol)
        if quote_asset != self.quote_asset:
            raise DataValidationError(
                f"trade quote asset {quote_asset} does not match portfolio quote asset "
                f"{self.quote_asset}"
            )
        buy_key = (result.buy_exchange.lower(), quote_asset)
        buy_base_key = (result.buy_exchange.lower(), base_asset)
        sell_base_key = (result.sell_exchange.lower(), base_asset)
        sell_quote_key = (result.sell_exchange.lower(), quote_asset)
        buy_cost = result.buy_notional + result.buy_fee
        sell_proceeds = result.sell_notional - result.sell_fee
        if sell_proceeds < 0:
            raise DataValidationError("sell proceeds after fees must not be negative")
        projected = dict(self._balances)
        if projected.get(buy_key, _ZERO) < buy_cost:
            raise DataValidationError(
                f"insufficient {quote_asset} balance on {result.buy_exchange} for buy leg"
            )
        if projected.get(sell_base_key, _ZERO) < result.filled_quantity:
            raise DataValidationError(
                f"insufficient {base_asset} balance on {result.sell_exchange} for sell leg"
            )
        projected[buy_key] = projected.get(buy_key, _ZERO) - buy_cost
        projected[buy_base_key] = projected.get(buy_base_key, _ZERO) + result.filled_quantity
        projected[sell_base_key] = projected.get(sell_base_key, _ZERO) - result.filled_quantity
        projected[sell_quote_key] = projected.get(sell_quote_key, _ZERO) + sell_proceeds
        return projected

    def apply_trade(
        self, result: PaperTradeResult, *, now: datetime | None = None
    ) -> None:
        """Apply both simulated legs atomically after checking inventory sufficiency."""
        projected = self.projected_balances(result)
        current = ensure_utc(now) if now is not None else utc_now()
        self._balances = projected
        self._realized_pnl += result.net_pnl
        day_key = current.date().isoformat()
        self._daily_realized[day_key] = self._daily_realized.get(day_key, _ZERO) + result.net_pnl

    @staticmethod
    def _marks(
        mark_prices: Mapping[str, Decimal | int | float | str], quote_asset: str
    ) -> dict[str, Decimal]:
        marks = {quote_asset: _ONE}
        for raw_asset, raw_price in mark_prices.items():
            asset = _identity("mark asset", raw_asset, upper=True)
            price = _decimal(f"mark price for {asset}", raw_price)
            if price <= 0:
                raise DataValidationError(f"mark price for {asset} must be greater than zero")
            marks[asset] = price
        return marks

    def _equity_and_exposure(
        self,
        balances: Mapping[tuple[str, str], Decimal],
        mark_prices: Mapping[str, Decimal | int | float | str],
    ) -> tuple[Decimal, dict[str, Decimal]]:
        marks = self._marks(mark_prices, self.quote_asset)
        equity = _ZERO
        exposure: dict[str, Decimal] = {}
        for (exchange, asset), amount in balances.items():
            if amount == 0:
                continue
            if asset not in marks:
                raise DataValidationError(f"missing mark price for non-zero asset {asset}")
            value = amount * marks[asset]
            equity += value
            exposure[exchange] = exposure.get(exchange, _ZERO) + value
        return equity, exposure

    def valuation(
        self,
        mark_prices: Mapping[str, Decimal | int | float | str],
        *,
        now: datetime | None = None,
    ) -> PortfolioValuation:
        """Mark balances to the quote asset and update the observed peak equity."""
        current = ensure_utc(now) if now is not None else utc_now()
        equity, exposure = self._equity_and_exposure(self._balances, mark_prices)
        if equity < 0:
            raise DataValidationError("portfolio equity must not be negative")
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
        peak = self._peak_equity
        drawdown = _ZERO if peak == 0 else (peak - equity) / peak * _HUNDRED
        return PortfolioValuation(
            quote_asset=self.quote_asset,
            equity=equity,
            peak_equity=peak,
            drawdown_pct=max(_ZERO, drawdown),
            exposure_by_exchange=exposure,
            daily_realized_pnl=self.daily_realized_pnl(current),
        )


class RiskEngine:
    """Evaluate paper trades against inventory, notional, exposure and loss limits."""

    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def evaluate(
        self,
        result: PaperTradeResult,
        portfolio: PortfolioLedger,
        *,
        mark_prices: Mapping[str, Decimal | int | float | str],
        now: datetime | None = None,
    ) -> RiskDecision:
        """Return an approval/rejection decision; never applies the trade itself."""
        current = ensure_utc(now) if now is not None else utc_now()
        metrics: dict[str, str] = {
            "requested_quantity": str(result.requested_quantity),
            "filled_quantity": str(result.filled_quantity),
            "net_pnl": str(result.net_pnl),
        }
        if result.status is PaperFillStatus.REJECTED:
            return RiskDecision(False, "paper simulator rejected the trade", metrics)
        notional = max(result.buy_notional, result.sell_notional)
        metrics["trade_notional"] = str(notional)
        metrics["max_trade_notional"] = str(self.limits.max_trade_notional)
        if notional > self.limits.max_trade_notional:
            return RiskDecision(False, "max_trade_notional exceeded", metrics)
        try:
            projected = portfolio.projected_balances(result)
            current_value = portfolio.valuation(mark_prices, now=current)
            projected_equity, projected_exposure = portfolio._equity_and_exposure(
                projected, mark_prices
            )
        except DataValidationError as exc:
            return RiskDecision(False, str(exc), metrics)

        metrics["equity"] = str(current_value.equity)
        metrics["projected_equity"] = str(projected_equity)
        if self.limits.max_exchange_exposure is not None:
            metrics["max_exchange_exposure"] = str(self.limits.max_exchange_exposure)
            for exchange, amount in projected_exposure.items():
                metrics[f"exposure:{exchange}"] = str(amount)
                if amount > self.limits.max_exchange_exposure:
                    return RiskDecision(
                        False, f"max_exchange_exposure exceeded on {exchange}", metrics
                    )

        if self.limits.max_drawdown_pct is not None:
            peak = max(current_value.peak_equity, projected_equity)
            drawdown = _ZERO if peak == 0 else (peak - projected_equity) / peak * _HUNDRED
            metrics["projected_drawdown_pct"] = str(max(_ZERO, drawdown))
            metrics["max_drawdown_pct"] = str(self.limits.max_drawdown_pct)
            if drawdown > self.limits.max_drawdown_pct:
                return RiskDecision(False, "max_drawdown_pct exceeded", metrics)

        if self.limits.max_daily_loss is not None:
            projected_daily_pnl = portfolio.daily_realized_pnl(current) + result.net_pnl
            metrics["projected_daily_realized_pnl"] = str(projected_daily_pnl)
            metrics["max_daily_loss"] = str(self.limits.max_daily_loss)
            if projected_daily_pnl < -self.limits.max_daily_loss:
                return RiskDecision(False, "max_daily_loss exceeded", metrics)

        return RiskDecision(True, "approved", metrics)
