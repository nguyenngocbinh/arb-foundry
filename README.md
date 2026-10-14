# Arb Foundry

A modular cryptocurrency arbitrage **research** engine, rebuilt incrementally with a paper-trading-first safety model.

> **Safety status:** research and public-market-data stage. Live order submission is not implemented or enabled. Never commit exchange API keys.

## Scope

- Supported public spot market-data adapters: Binance, OKX, Bybit, KuCoin.
- Strategies planned: cross-exchange spot arbitrage and spot–perpetual delta-neutral research.
- Pipeline: normalized market data → depth-aware opportunity analysis → paper simulation → reporting.
- A health-check service can check public API endpoints immediately and repeat at a configurable interval (24 hours by default).

## Current status

Implemented: validated configuration and domain models; UTC timestamp utilities; read-only REST adapters for the four exchanges; symbol normalization; stale-data validation; in-memory health-check runner; depth-aware spot scanner; explicit symbol-filter validation; and a deterministic paper fill simulator for visible depth, fees, slippage, and optional paired partial fills. The scanner and simulator never place orders. Simulated balances/positions, paper-trade audit events, and versioned SQLite portfolio snapshots are implemented. Deterministic scanner replay from checksummed SQLite market snapshots is implemented; strategy-level reporting, latency modelling, persistent health history, and live runtime/service wiring remain open. This is not a trading bot.

See the [rebuild roadmap](https://github.com/nguyenngocbinh/arb-foundry/issues/1), [HTML architecture page](docs/architecture.html), and [calculation methodology](docs/calculation-methodology.md) for formulas, units, examples, assumptions, and limitations.

## Development setup

Requires Python 3.11 or newer.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
ruff check .
```

## Configuration

Settings are read from environment variables. Public market-data requests require no API keys or exchange credentials.

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | `development`, `test`, or `production` |
| `LOG_LEVEL` | `INFO` | Standard Python logging level |
| `MARKET_DATA_STALE_AFTER_SECONDS` | `5` | Maximum allowed market-data age for consumers |
| `API_HEALTH_CHECK_INTERVAL_HOURS` | `24` | Interval for the health-check runner |
| `PAPER_TRADING_ENABLED` | `true` | Safe default for future paper simulation |

## Market data quick start

```python
from arb_foundry.exchanges import default_adapters
from arb_foundry.health import HealthCheckService
from arb_foundry.market_data import validate_freshness

adapters = default_adapters()
for adapter in adapters:
    ticker = adapter.get_ticker("BTC/USDT")
    book = adapter.get_order_book("BTC/USDT", limit=20)
    validate_freshness(ticker.timestamp, max_age_seconds=5)
    print(adapter.exchange, ticker.bid, ticker.ask, len(book.bids), len(book.asks))

# For a long-running service, start this in a managed worker/thread.
health = HealthCheckService(adapters, interval_hours=24)
health.check_all()  # latest status is available from health.latest
```

## Spot scanner quick start

The scanner accepts order-book snapshots and an explicit fee rate per exchange. Fee rates are decimal fractions of quote notional: `0.001` means `0.1%`. The target quantity is in base-asset units, e.g. `0.01` BTC. It only reports candidates that can fill the entire target quantity on both legs, pass freshness checks, and meet the configured net-spread threshold.

```python
from decimal import Decimal

from arb_foundry.exchanges import default_adapters
from arb_foundry.scanner import SpotArbitrageScanner

adapters = default_adapters()
books = [adapter.get_order_book("BTC/USDT", limit=20) for adapter in adapters]
scanner = SpotArbitrageScanner(max_data_age_seconds=5, min_net_spread_bps=Decimal("5"))
report = scanner.scan(
    books,
    fee_rates={
        "binance": Decimal("0.001"),
        "okx": Decimal("0.001"),
        "bybit": Decimal("0.001"),
        "kucoin": Decimal("0.001"),
    },
    target_quantity=Decimal("0.01"),
)
for item in report.opportunities:
    print(item.buy_exchange, "->", item.sell_exchange, item.net_spread_bps, "bps")
for rejection in report.rejections:
    print("REJECTED:", rejection.reason)
```

The example uses illustrative fee rates, not verified account-specific fee tiers. Configure actual public fee assumptions for research before interpreting results. The scanner's net calculation assumes fees are charged on quote notional for both legs. It does not account for withdrawal/transfer costs, inventory imbalance, latency, slippage beyond the visible book snapshot, rebates, funding, or execution probability. A reported opportunity is not a guarantee of profit.

## Market constraints quick start

Rules are explicit per exchange and symbol; the library does not guess filters or fetch private account settings. Use current, trusted exchange metadata before constructing these rules.

```python
from arb_foundry.market_constraints import SymbolConstraints
from arb_foundry.models import TradeSide

rules = SymbolConstraints(
    exchange="binance",
    symbol="BTC/USDT",
    price_tick="0.1",
    quantity_step="0.00001",
    min_quantity="0.00001",
    min_notional="5",
)
quantity = rules.round_quantity_down("0.012349")
buy_limit_price = rules.round_price("65000.01", TradeSide.BUY)
rules.validate_order(price=buy_limit_price, quantity=quantity)
```

## Paper simulation quick start

The simulator estimates a paired fill against supplied snapshots. It performs no network calls, sends no orders, and does not maintain exchange balances.

```python
from arb_foundry.exchanges import default_adapters
from arb_foundry.paper import PaperTradingSimulator

adapters = {adapter.exchange: adapter for adapter in default_adapters()}
buy_book = adapters["binance"].get_order_book("BTC/USDT", limit=20)
sell_book = adapters["okx"].get_order_book("BTC/USDT", limit=20)
result = PaperTradingSimulator().simulate(
    buy_book,
    sell_book,
    quantity="0.001",
    fee_rates={"binance": "0.001", "okx": "0.001"},
    slippage_bps="5",
    allow_partial_fills=False,
)
print(result.status, result.filled_quantity, result.net_pnl)
```

The simulator validates each book's freshness and the cross-venue timestamp skew before estimating fills. You can also pass both venues' `SymbolConstraints` to enforce price tick, quantity step, min/max quantity, and min-notional rules before a simulated fill. It applies adverse slippage and fees to adjusted quote notionals, and rejects incomplete paired liquidity by default. Optional partial-fill mode models only the common filled quantity; it does not model the risk of an unhedged residual or balance/inventory constraints. See [calculation methodology](docs/calculation-methodology.md) for formulas and assumptions.

## Paper-trade audit store

`PaperTradeStore` uses SQLite to append simulation results and the timestamps of both source snapshots. It stores no credentials and never submits orders.

```python
from arb_foundry.paper_store import PaperTradeStore

with PaperTradeStore("data/paper-trades.sqlite3") as store:
    event_id = store.record(
        result,
        buy_snapshot_at=buy_book.timestamp,
        sell_snapshot_at=sell_book.timestamp,
    )
    print(store.get(event_id))
    print(store.list_events(limit=100))
```

The store provides append/query operations for simulator outcomes and separate pre-trade rejection events. It remains an append-only application interface, not a tamper-proof audit system. Portfolio accounting and risk checks are available in arb_foundry.portfolio; portfolio state is currently in-memory and must be reconstructed by the caller.

The public adapter methods make real HTTP requests. Unit tests use deterministic fixtures and do not call exchanges. The periodic health runner must be started by the host application's lifecycle to remain active; no background service is automatically launched by importing this package.

Some public endpoints do not include a source timestamp in their ticker response. In those cases, the adapter timestamps the response at receipt time; freshness checks therefore measure the age of the local snapshot, not exchange-side event latency.

## Safety invariants

- Paper trading only by default.
- No live order submission, exchange credentials, or authenticated endpoints.
- Scanner rejects stale data and insufficient order-book depth for the configured target size.
- Fee rates are explicit assumptions and must not be represented as exchange-verified unless verified separately.
- Each milestone needs tests and evidence before its roadmap item is marked complete.
- Work on feature branches; never merge into `main` without explicit approval.

## Layout

```text
src/arb_foundry/   Python package
tests/             Automated tests
docs/              Architecture and operating notes
.github/workflows/ CI
```

## License

No license has been selected. Until one is added, do not assume this repository grants broad reuse rights.


## Paper portfolio and risk checks

The in-memory PortfolioLedger tracks balances per exchange and asset, realized PnL, daily realized PnL, mark-to-market equity, exposure by exchange, peak equity and drawdown. A paired spot simulation requires quote-asset balance on the buy venue and base-asset inventory on the sell venue; the ledger will not invent transfers or borrow inventory.

RiskEngine evaluates inventory sufficiency, maximum trade notional, optional per-exchange exposure, mark-to-market drawdown and daily realized loss. It returns a decision and metrics but never applies a trade. Call PortfolioLedger.apply_trade only after an approved decision. Supply asset marks in the portfolio quote currency (USDT by default). The ledger is in-memory in this milestone; SQLite stores audit events, not recoverable portfolio state.


## Durable paper portfolio state

Portfolio accounting can be recovered after a restart with a separate SQLite state store. It stores versioned snapshots of balances by venue/asset, quote asset, total and UTC-daily realized PnL, and the peak equity observed by valuation. Decimal values are stored as strings.

```python
from arb_foundry.portfolio import PortfolioLedger
from arb_foundry.portfolio_store import PortfolioStateStore

database = "data/paper-state.sqlite3"
with PortfolioStateStore(database) as store:
    ledger = store.load_latest(portfolio_id="research-btc")
    if ledger is None:
        ledger = PortfolioLedger(
            {("binance", "USDT"): "1000", ("okx", "BTC"): "0.02"},
            quote_asset="USDT",
        )
        store.save(ledger, portfolio_id="research-btc")

    # After the simulator returns a FILLED/PARTIAL result and risk approves it:
    # store.apply_trade_and_save(ledger, result, portfolio_id="research-btc")
    print(ledger.balances)
    print(store.list_snapshots(portfolio_id="research-btc", limit=10))
```

Important: `apply_trade_and_save` is the safe durable path for accounting. It computes a candidate state, commits its snapshot, then updates the supplied in-memory ledger. If validation or SQLite commit fails, that ledger remains unchanged. It does not run the risk engine for you; call it only after the paper result and risk decision are accepted. Save again after a valuation if you need the updated peak-equity watermark to survive restart.

Snapshots are append-only at the application API. A SHA-256 checksum detects accidental payload changes, but is not a cryptographic signature and cannot prevent an actor with database write access from rewriting both payload and checksum. Back up the SQLite file, restrict write access, and treat checksum failures as a stop-and-investigate condition. This store persists portfolio state only; full order-book replay, strategy-level reporting, and a persistent kill switch are separate follow-up milestones.


## Reproducible market scan snapshots

`MarketReplayStore` records the complete order books supplied to one scanner pass, the explicit fee assumptions, target base quantity, scanner thresholds, and the exact UTC clock used for freshness checks. It writes canonical JSON and a SHA-256 checksum to SQLite; `replay_scan(run_id)` reconstructs the typed order books and reruns the scanner with the saved clock/configuration.

```python
from arb_foundry.exchanges import default_adapters
from arb_foundry.market_replay import MarketReplayStore
from arb_foundry.scanner import SpotArbitrageScanner

adapters = {adapter.exchange: adapter for adapter in default_adapters()}
books = [
    adapters["binance"].get_order_book("BTC/USDT", limit=20),
    adapters["okx"].get_order_book("BTC/USDT", limit=20),
]
scanner = SpotArbitrageScanner(max_data_age_seconds=5, min_net_spread_bps="5")
with MarketReplayStore("data/market-replay.sqlite3") as store:
    run_id = store.record_scan(
        books,
        fee_rates={"binance": "0.001", "okx": "0.001"},
        target_quantity="0.01",
        scanner=scanner,
    )
    reproduced_report = store.replay_scan(run_id)
    print(run_id, reproduced_report.opportunities, reproduced_report.rejections)
```

Replay uses the **original scan clock**, not the current time, so a previously fresh book does not become stale merely because replay occurs later. Replay is deterministic for the persisted scanner inputs/configuration and code version; it is not a promise that a live opportunity would have filled. The store does not persist exchange credentials, query exchanges, or submit orders. SHA-256 detects accidental corruption but is not tamper-proof against someone who can modify the SQLite database and recompute the digest. Preserve the database and code revision together for auditability. Schema/version errors and checksum mismatches fail closed.
