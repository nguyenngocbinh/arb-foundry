# Arb Foundry

A modular cryptocurrency arbitrage **research** engine, rebuilt incrementally with a paper-trading-first safety model.

> **Safety status:** research and public-market-data stage. Live order submission is not implemented or enabled. Never commit exchange API keys.

## Scope

- Supported public spot market-data adapters: Binance, OKX, Bybit, KuCoin.
- Strategies planned: cross-exchange spot arbitrage and spot–perpetual delta-neutral research.
- Pipeline: normalized market data → depth-aware opportunity analysis → paper simulation → reporting.
- A health-check service can check public API endpoints immediately and repeat at a configurable interval (24 hours by default).

## Current status

Implemented: validated configuration and domain models; UTC timestamp utilities; read-only REST adapters for the four exchanges; symbol normalization; stale-data validation; in-memory health-check runner; and a depth-aware spot scanner that estimates executable-size VWAP and subtracts configured buy/sell fees. The scanner reports rejected inputs/candidates and does not place orders. Paper-trading simulation, persistent health history, and live runtime/service wiring are not implemented. This is not a trading bot.

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

The simulator applies adverse slippage to both legs, computes fees on adjusted quote notionals, and rejects incomplete paired liquidity by default. The optional partial-fill mode simulates only the common filled quantity; it does not model the risk of an unhedged residual or any balance/inventory constraints. See [calculation methodology](docs/calculation-methodology.md) for formulas and worked examples.

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
