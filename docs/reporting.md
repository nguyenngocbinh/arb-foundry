# Paper-trading reports and evaluation

## Purpose and safety boundary

The reporting module summarizes local SQLite audit events written by
`PaperTradeStore`. It is deterministic for the persisted event rows and
does not fetch exchange data, run a strategy, infer missing fills, or place
orders. Treat these figures as paper-simulation diagnostics, not realized
exchange performance.

## Record strategy labels at write time

Pass the strategy name when writing both outcomes and pre-trade rejections:

```python
store.record(
    result,
    buy_snapshot_at=buy_book.timestamp,
    sell_snapshot_at=sell_book.timestamp,
    strategy="classic-spot-spot",
)
store.record_rejection(
    symbol="BTC/USDT",
    buy_exchange="binance",
    sell_exchange="okx",
    buy_snapshot_at=buy_book.timestamp,
    sell_snapshot_at=sell_book.timestamp,
    reason="risk limit exceeded",
    strategy="classic-spot-spot",
)
```

The label is caller-supplied metadata; the store does not independently verify
which strategy produced an event. Existing databases are migrated in place:
the `strategy` column is added without dropping events. Historical rows receive
the label `unspecified`; do not retroactively attribute them to a strategy
without independent evidence.

## Build a report

```python
from datetime import UTC, datetime
from arb_foundry.paper_store import PaperTradeStore
from arb_foundry.reporting import build_report

with PaperTradeStore("data/paper.sqlite3") as store:
    report = build_report(
        store,
        since=datetime(2026, 10, 1, tzinfo=UTC),
        until=datetime(2026, 11, 1, tzinfo=UTC),
        group_by=("symbol", "exchange_pair", "strategy", "day"),
    )

    for row in report.rows:
        print(
            dict(row.dimensions),
            "net_pnl=", row.net_pnl,
            "fees=", row.fees,
            "slippage=", row.slippage_cost,
            "pretrade_rejections=", row.pretrade_rejections,
        )
```

Supported dimensions: `symbol`, `buy_exchange`, `sell_exchange`,
`exchange_pair` (buy venue to sell venue), `strategy`, and `day` (UTC
calendar date). Multiple dimensions can be combined. Use an empty tuple to
request one overall total.

## Metric definitions

- `attempts`: simulator result events recorded, including simulator rejections.
- `filled` and `partial`: simulator outcomes by status.
- `simulator_rejections`: persisted results whose status is rejected.
- `pretrade_rejections`: separate audit events recorded before a result exists,
  such as a risk rejection or validation exception.
- `gross_pnl`: sum of persisted simulator gross PnL.
- `fees`: buy fee plus sell fee for every recorded simulator result.
- `slippage_cost`: sum of the simulator's estimated slippage-cost field.
- `net_pnl`: sum of persisted simulator net PnL.

**Do not calculate net PnL by subtracting fees or slippage from the reported
net PnL again.** The simulator calculates net PnL using the slippage-adjusted
buy/sell VWAPs and subtracts the resulting fees. The separate fee and slippage
columns are explanatory breakdowns and are not additional deductions from
`net_pnl`.

Reports use `recorded_at` for filtering, not market snapshot time. The interval
is start-inclusive and end-exclusive: `since <= recorded_at < until`. Pass
timezone-aware datetimes; values are normalized to UTC.

## Completeness and interpretation limits

Each underlying store query is capped by `limit` (maximum 10,000 rows). If a
query returns exactly that many rows, `possibly_truncated` is set to true:
the report must not be presented as complete. Split the requested time window
into smaller intervals and rerun, or use a future streaming/export facility.
The current report reads the newest rows first, so a wide window with high event
volume may omit older rows.

PnL is only as complete as the stored events. Unrecorded attempts, duplicated
event application, external deposits/withdrawals, transfer costs, funding,
taxes, latency, legging risk, and live fill probability are not reconstructed
by this module. Event IDs are not deduplicated by the report. Strategy labels
and audit files remain inside the same SQLite trust boundary; file access and
backups must be controlled. This is an application-level audit interface, not a
tamper-proof ledger.
