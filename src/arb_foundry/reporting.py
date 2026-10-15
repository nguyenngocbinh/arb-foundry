"""Deterministic summaries of persisted paper-trading events.

Reports read local SQLite audit events only. They do not fetch market data,
estimate missing fills, or imply live-trading performance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from arb_foundry.errors import DataValidationError
from arb_foundry.paper_store import PaperTradeStore
from arb_foundry.time_utils import ensure_utc

_ZERO = Decimal("0")
_GROUP_FIELDS = {"symbol", "buy_exchange", "sell_exchange", "exchange_pair", "strategy", "day"}
_STATUS_FILLED = "filled"
_STATUS_PARTIAL = "partial"
_STATUS_REJECTED = "rejected"


@dataclass(slots=True)
class _Accumulator:
    attempts: int = 0
    filled: int = 0
    partial: int = 0
    simulator_rejections: int = 0
    pretrade_rejections: int = 0
    gross_pnl: Decimal = _ZERO
    fees: Decimal = _ZERO
    slippage_cost: Decimal = _ZERO
    net_pnl: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class ReportRow:
    """Aggregated metrics for one group of paper events."""

    dimensions: tuple[tuple[str, str], ...]
    attempts: int
    filled: int
    partial: int
    simulator_rejections: int
    pretrade_rejections: int
    gross_pnl: Decimal
    fees: Decimal
    slippage_cost: Decimal
    net_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PaperTradingReport:
    """A reproducible report over a bounded UTC time window."""

    since: datetime
    until: datetime
    group_by: tuple[str, ...]
    rows: tuple[ReportRow, ...]
    trade_events_scanned: int
    pretrade_rejections_scanned: int
    possibly_truncated: bool


def _as_decimal(value: object, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise DataValidationError(f"invalid decimal in audit field {field_name}") from exc
    if not parsed.is_finite():
        raise DataValidationError(f"non-finite decimal in audit field {field_name}")
    return parsed


def _event_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise DataValidationError("audit event recorded_at must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DataValidationError("audit event has invalid recorded_at timestamp") from exc
    return ensure_utc(parsed)


def _dimension_value(
    field_name: str, event: dict[str, object], payload: dict[str, object] | None = None
) -> str:
    if field_name == "exchange_pair":
        return f"{event.get('buy_exchange', 'unknown')}->{event.get('sell_exchange', 'unknown')}"
    if field_name == "day":
        return _event_time(event.get("recorded_at")).date().isoformat()
    if field_name == "strategy":
        return str(event.get("strategy") or "unspecified")
    if field_name == "symbol":
        return str(event.get("symbol") or "unknown")
    if field_name in {"buy_exchange", "sell_exchange"}:
        return str(event.get(field_name) or "unknown")
    raise DataValidationError(f"unsupported report grouping: {field_name}")


def build_report(
    store: PaperTradeStore,
    *,
    since: datetime,
    until: datetime,
    group_by: tuple[str, ...] = ("symbol", "exchange_pair"),
    limit: int = 10_000,
) -> PaperTradingReport:
    """Aggregate persisted events in [since, until), using UTC event-record time.

    The limit bounds each source query to protect memory. If either query returns
    exactly that many rows, possibly_truncated is true. Use smaller time windows
    or a future streaming exporter before treating capped totals as complete.
    """
    start = ensure_utc(since)
    end = ensure_utc(until)
    if start >= end:
        raise DataValidationError("since must be earlier than until")
    if not 1 <= limit <= 10_000:
        raise DataValidationError("limit must be between 1 and 10000")
    if len(set(group_by)) != len(group_by):
        raise DataValidationError("group_by must not contain duplicate fields")
    unsupported = set(group_by) - _GROUP_FIELDS
    if unsupported:
        raise DataValidationError(
            f"unsupported report grouping(s): {', '.join(sorted(unsupported))}"
        )

    events = store.list_events(limit=limit)
    rejections = store.list_rejections(limit=limit)
    in_window_events = [
        event for event in events if start <= _event_time(event.get("recorded_at")) < end
    ]
    in_window_rejections = [
        event for event in rejections if start <= _event_time(event.get("recorded_at")) < end
    ]

    groups: dict[tuple[str, ...], _Accumulator] = {}

    def key_for(event: dict[str, object]) -> tuple[str, ...]:
        return tuple(_dimension_value(name, event) for name in group_by)

    def accumulator(key: tuple[str, ...]) -> _Accumulator:
        return groups.setdefault(key, _Accumulator())

    for event in in_window_events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise DataValidationError("paper audit payload must be a JSON object")
        row = accumulator(key_for(event))
        row.attempts += 1
        status = str(payload.get("status", event.get("status", ""))).lower()
        if status == _STATUS_FILLED:
            row.filled += 1
        elif status == _STATUS_PARTIAL:
            row.partial += 1
        elif status == _STATUS_REJECTED:
            row.simulator_rejections += 1
        else:
            raise DataValidationError(f"unknown paper trade status: {status}")
        row.gross_pnl += _as_decimal(payload.get("gross_pnl", "0"), "gross_pnl")
        row.fees += _as_decimal(payload.get("buy_fee", "0"), "buy_fee")
        row.fees += _as_decimal(payload.get("sell_fee", "0"), "sell_fee")
        row.slippage_cost += _as_decimal(payload.get("slippage_cost", "0"), "slippage_cost")
        row.net_pnl += _as_decimal(payload.get("net_pnl", "0"), "net_pnl")

    for event in in_window_rejections:
        accumulator(key_for(event)).pretrade_rejections += 1

    report_rows = tuple(
        ReportRow(
            dimensions=tuple(zip(group_by, key, strict=True)),
            attempts=value.attempts,
            filled=value.filled,
            partial=value.partial,
            simulator_rejections=value.simulator_rejections,
            pretrade_rejections=value.pretrade_rejections,
            gross_pnl=value.gross_pnl,
            fees=value.fees,
            slippage_cost=value.slippage_cost,
            net_pnl=value.net_pnl,
        )
        for key, value in sorted(groups.items())
    )
    return PaperTradingReport(
        since=start,
        until=end,
        group_by=group_by,
        rows=report_rows,
        trade_events_scanned=len(events),
        pretrade_rejections_scanned=len(rejections),
        possibly_truncated=len(events) == limit or len(rejections) == limit,
    )
