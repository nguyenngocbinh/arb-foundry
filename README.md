# Arb Foundry

A modular cryptocurrency arbitrage **research** engine, rebuilt incrementally with a paper-trading-first safety model.

> **Safety status:** research scaffold only. Live order submission is not implemented or enabled. Never commit exchange API keys.

## Scope

- Exchanges planned: Binance, OKX, Bybit, KuCoin.
- Strategies planned: cross-exchange spot arbitrage and spot–perpetual delta-neutral research.
- Pipeline: normalized market data → depth-aware opportunity analysis → paper simulation → reporting.
- Exchange API health checks are planned every 24 hours.

## Current status

The foundation now includes validated settings, UTC timestamp helpers, typed domain models for tickers/order books/trades/opportunities, typed application errors, and logging configuration. Exchange adapters, the opportunity scanner, persistence, and paper trading are not implemented yet. Do not interpret this scaffold as a trading bot.

See the [rebuild roadmap](https://github.com/nguyenngocbinh/arb-foundry/issues/1).

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

Copy `.env.example` to `.env` if needed. Settings are read from environment variables; the foundation does not read exchange credentials and requires no API keys.

Supported settings:

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | `development`, `test`, or `production` |
| `LOG_LEVEL` | `INFO` | Standard Python logging level |
| `MARKET_DATA_STALE_AFTER_SECONDS` | `5` | Intended freshness threshold for market data |
| `API_HEALTH_CHECK_INTERVAL_HOURS` | `24` | Planned exchange health-check cadence |
| `PAPER_TRADING_ENABLED` | `true` | Safe default for future paper simulation |

## Safety invariants

- Paper trading only by default.
- No live order submission or exchange credentials in the foundation.
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
