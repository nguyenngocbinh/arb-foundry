# Latency, fill probability, and legging-risk model

## Scope and safety boundary

`arb_foundry.execution_model` is a deterministic, seeded Monte Carlo model for
paper research. It does not send requests to exchanges, create orders, estimate
profitability from live fills, or change the existing depth-based simulator.
It makes execution assumptions visible so evaluation can test how sensitive
results are to them.

## Inputs and units

- Buy and sell latency bounds are non-negative integer milliseconds.
- Each leg's latency is sampled uniformly from its inclusive minimum/maximum
  bounds. This is a placeholder distribution, not a measured network model.
- Each leg has an explicit fill probability in the closed interval [0, 1].
- Leg fills are sampled as independent Bernoulli outcomes. Real fills can be
  correlated by market moves, queue position, shared infrastructure, and order
  timing; independence is a simplifying assumption, not a market fact.
- The requested quantity is expressed in base-asset units, for example BTC.
- A seeded `random.Random` stream makes a scenario repeatable for the same
  code version, assumptions, seed, and trial count.

## Outcomes and legging risk

| Outcome | Buy leg | Sell leg | Simulated inventory delta |
|---|---|---|---:|
| `paired_fill` | Filled | Filled | 0 |
| `buy_leg_only` | Filled | Not filled | +requested quantity |
| `sell_leg_only` | Not filled | Filled | -requested quantity |
| `no_fill` | Not filled | Not filled | 0 |

The model reports the one-leg-only rate as `legging_rate`. This is an exposure
indicator, not a hedge instruction or a valuation of that exposure. It does
not automatically submit a hedge, liquidate inventory, or calculate the loss
from price movement during the unhedged interval.

## Assumptions versus calibration

`source="assumed"` is the default and must be described as a scenario input,
not an empirically observed fill rate. To label assumptions `calibrated`, the
caller must provide a positive `calibration_sample_size` and a non-empty
`source_note` that identifies the supporting dataset/method.

These metadata fields are assertions by the caller; the library does not verify
that the dataset exists, is representative, or supports the probability. Before
using empirical calibration, preserve timestamped observations, define the
fill outcome and observation window, account for rejected/censored orders, and
validate out of sample. Do not infer calibrated fill probabilities from public
order-book snapshots alone.

## Sensitivity workflow

Use `compare_execution_scenarios` to compare named optimistic/base/stress
assumptions with a shared seed. It reports paired-fill rate, one-leg-only rate,
no-fill rate, and average latency for each scenario. A shared seed improves
repeatability but does not remove model error. Run multiple seeds and compare
ranges before drawing conclusions; retain the seed, code revision, inputs, and
trial count with any report.

Example:

```python
from arb_foundry.execution_model import (
    ExecutionAssumptions,
    compare_execution_scenarios,
)

scenarios = {
    "base_assumption": ExecutionAssumptions(
        buy_latency_min_ms=20,
        buy_latency_max_ms=80,
        sell_latency_min_ms=20,
        sell_latency_max_ms=100,
        buy_fill_probability="0.90",
        sell_fill_probability="0.88",
    ),
    "stress_assumption": ExecutionAssumptions(
        buy_latency_min_ms=80,
        buy_latency_max_ms=250,
        sell_latency_min_ms=100,
        sell_latency_max_ms=350,
        buy_fill_probability="0.70",
        sell_fill_probability="0.60",
    ),
}

report = compare_execution_scenarios(
    scenarios,
    quantity="0.01",
    trials=10_000,
    seed=20261015,
)
for name, result in report.items():
    print(name, result.paired_fill_rate, result.legging_rate)
```

The numbers above are illustrative assumptions only; they are not measured
Binance, OKX, Bybit, or KuCoin execution statistics.

## Known limitations

- Latency uses a uniform integer distribution and does not model tails,
  queueing, network jitter, exchange matching-engine time, or acknowledgement
  latency separately.
- Per-leg fills are independent and binary; there are no partial fills,
  cancel/replace races, order-book depletion, queue position, or price impact.
- The model does not connect fill probability to latency or price movement.
- It does not compute PnL, fees, funding, adverse selection, hedging cost, or
  the duration/value of unhedged exposure.
- A `calibrated` label is caller-supplied metadata, not independent evidence.
- This is not an order execution engine and must not be used to submit live
  orders or imply that a real fill is guaranteed.

These limitations are intentional for this milestone. A later order-lifecycle
simulation should add partial fills, cancellation races, unknown exchange
states, and mark-to-market cost for unhedged exposure before any live adapter
is considered.
