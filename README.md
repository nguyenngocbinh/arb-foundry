# Arb Foundry

A modular cryptocurrency arbitrage **research** engine, rebuilt incrementally with a paper-trading-first safety model.

> **Safety status:** research and public-market-data stage. Live order submission is not implemented or enabled. Never commit exchange API keys.

## Scope

- Supported public spot market-data adapters: Binance, OKX, Bybit, KuCoin.
- Strategies planned: cross-exchange spot arbitrage and spot–perpetual delta-neutral research.
- Pipeline: normalized market data → depth-aware opportunity analysis → paper simulation → reporting.
- A health-check service can check public API endpoints immediately and repeat at a configurable interval (24 hours by default).

## Current status

Implemented: validated configuration and domain models; UTC timestamp utilities; read-only REST adapters for the four exchanges; symbol normalization; stale-data validation; and a health-check runner that records the latest per-exchange status in memory. The opportunity scanner, persistent health history, paper-trading simulator, and live runtime/service wiring are not implemented yet. This is not a trading bot.

See the [rebuild roadmap](https://github.com/nguyenngocbinh/arb-foundry/issues/1) and [HTML architecture page](docs/architecture.html).

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

The public adapter methods make real HTTP requests. Unit tests use deterministic fixtures and do not call exchanges. The periodic health runner must be started by the host application's lifecycle to remain active; no background service is automatically launched by importing this package.

Some public endpoints do not include a source timestamp in their ticker response. In those cases, the adapter timestamps the response at receipt time; freshness checks therefore measure the age of the local snapshot, not exchange-side event latency.

## Safety invariants

- Paper trading only by default.
- No live order submission, exchange credentials, or authenticated endpoints.
- Future scanner must reject stale data and insufficient order-book depth.
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
