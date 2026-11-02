# Autonomous Spot Agent — Small-Capital Paper Profile

## Objective and safety boundary

This milestone adds a deterministic autonomous supervisor around the existing
Arb Foundry spot scanner, paper simulator, portfolio ledger, risk engine,
SQLite audit stores and persistent kill switch. It is **paper-only**. No
authenticated exchange endpoint, API secret, live order submission, cancel
endpoint or withdrawal capability is added.

The OpenAI Agents SDK is an optional post-cycle analyst. It receives a
completed report and has no tools. Its text cannot alter scanner thresholds,
risk decisions, balances or simulated fills. The deterministic supervisor runs
without an OpenAI API key.

## Cycle

1. Fetch fresh public spot order books for configured symbols and venues.
2. Run the existing depth-aware scanner with explicit fee assumptions.
3. For each candidate, use the existing paired-fill paper simulator.
4. Evaluate the simulated result using inventory, notional, venue exposure,
   drawdown and daily-loss limits.
5. Persist approved paper accounting snapshots and append trade/rejection audit
   events in SQLite.
6. Optionally request an LLM commentary report after the cycle.

A candidate is not a guaranteed fill or profit. A scanner snapshot is not a
simulated fill. Only a risk-approved simulated result is applied to the paper
ledger. Insufficient inventory is a hard rejection; the agent never invents
inter-exchange transfers or borrows assets.

## Small-capital defaults

| Setting | Default |
|---|---:|
| Symbols | BTC/USDT |
| Target quantity | 0.0003 BTC |
| Minimum net spread | 10 bps |
| Assumed fee per venue | 0.10% |
| Adverse slippage | 5 bps per leg |
| Maximum trade notional | 50 USDT |
| Maximum venue exposure | 250 USDT |
| Maximum drawdown | 3% |
| Maximum daily realized loss | 10 USDT |
| Maximum order-book age | 5 seconds |
| Maximum cross-venue timestamp skew | 2 seconds |
| Partial fills | Disabled |

These are conservative starting assumptions, not a recommendation or validated
capital allocation. Replace fee assumptions with the actual account fee tier
and calibrate execution costs from timestamped observations before drawing
profitability conclusions. The current latency/fill model is a sensitivity
model, not empirical execution calibration.

## Install and configure

Install the core and optional AI advisor:

    python -m pip install -e ".[dev,ai]"

Set a starting inventory explicitly. This is a paper ledger only; the agent
does not read exchange account balances:

    $env:AGENT_INITIAL_BALANCES='{"binance:USDT":"100","okx:BTC":"0.01","okx:USDT":"100"}'
    $env:AGENT_SYMBOLS='BTC/USDT'
    $env:AGENT_EXCHANGES='binance,okx'
    $env:AGENT_TARGET_QUANTITY='0.0003'
    $env:AGENT_MIN_NET_SPREAD_BPS='10'
    $env:AGENT_MAX_TRADE_NOTIONAL='50'
    $env:AGENT_DEFAULT_FEE_RATE='0.001'

Inventory must exist on the correct venue for both legs. Spot arbitrage
requires quote balance on the buy venue and base-asset inventory on the sell
venue. This version does not rebalance between exchanges.

## Operate

The SQLite kill switch initializes in HALTED state. Check status and
explicitly arm paper simulation before running:

    python -m arb_foundry.agent_cli status
    python -m arb_foundry.agent_cli arm-paper --reason "approved paper test"
    python -m arb_foundry.agent_cli run-once
    python -m arb_foundry.agent_cli run-loop --interval-seconds 15
    python -m arb_foundry.agent_cli halt-paper --reason "operator stop"

Run run-once repeatedly from a managed scheduler only after operational
monitoring is configured. The kill switch is checked at the start of every
cycle. It gates paper simulation only; it is not a live-order kill switch and
does not cancel in-flight exchange activity.

Optional LLM commentary:

    $env:AI_ADVISOR_ENABLED='true'
    $env:OPENAI_API_KEY='...'
    $env:OPENAI_MODEL='gpt-6'

Never commit API keys. The LLM receives only the cycle summary and has no
trading tools. If the LLM is unavailable, deterministic paper trading remains
available.

## Before any live-trading milestone

Do not enable live trading based on this milestone. First add and validate:
empirical per-venue fee and fill calibration; market-data event timestamps and
latency distributions; partial-fill/cancel-race/unknown-order-state handling;
unhedged exposure valuation and automated hedge policy; exchange balance and
position reconciliation; durable idempotent order-intent ledger; mandatory
human approval and independent global kill switch; secrets management and
least-privilege API keys (withdrawal disabled); exchange sandbox tests;
operational alerting; and out-of-sample paper results over multiple market
regimes. Live order submission remains a separate reviewed project.
