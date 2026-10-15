"""Explicit latency/fill-probability assumptions for paper execution research.

This module is a seeded Monte Carlo model, not an exchange adapter or fill
guarantee. Probabilities are assumptions unless explicitly labelled calibrated
with a documented sample size.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Mapping

from arb_foundry.errors import DataValidationError

_ZERO = Decimal("0")
_ONE = Decimal("1")
_RANDOM_DENOMINATOR = Decimal(2**53)


def _decimal(name: str, value: Decimal | int | float | str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DataValidationError(f"{name} must be a valid decimal number") from exc
    if not parsed.is_finite():
        raise DataValidationError(f"{name} must be finite")
    return parsed


class AssumptionSource(StrEnum):
    ASSUMED = "assumed"
    CALIBRATED = "calibrated"


class ExecutionOutcome(StrEnum):
    PAIRED_FILL = "paired_fill"
    BUY_LEG_ONLY = "buy_leg_only"
    SELL_LEG_ONLY = "sell_leg_only"
    NO_FILL = "no_fill"


@dataclass(frozen=True, slots=True)
class ExecutionAssumptions:
    """Per-leg latency ranges and independent fill probabilities.

    Latencies are sampled uniformly from inclusive integer millisecond bounds.
    Fill outcomes are independent Bernoulli draws. This deliberately simple
    model does not claim that real venue fills are independent.
    """

    buy_latency_min_ms: int
    buy_latency_max_ms: int
    sell_latency_min_ms: int
    sell_latency_max_ms: int
    buy_fill_probability: Decimal
    sell_fill_probability: Decimal
    source: AssumptionSource = AssumptionSource.ASSUMED
    calibration_sample_size: int | None = None
    source_note: str = ""

    def __post_init__(self) -> None:
        for label in ("buy", "sell"):
            minimum = getattr(self, f"{label}_latency_min_ms")
            maximum = getattr(self, f"{label}_latency_max_ms")
            if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
                raise DataValidationError(f"{label}_latency_min_ms must be a non-negative integer")
            if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < minimum:
                raise DataValidationError(
                    f"{label}_latency_max_ms must be an integer >= its minimum"
                )
        for name in ("buy_fill_probability", "sell_fill_probability"):
            probability = _decimal(name, getattr(self, name))
            if probability < _ZERO or probability > _ONE:
                raise DataValidationError(f"{name} must be in [0, 1]")
            object.__setattr__(self, name, probability)
        if not isinstance(self.source, AssumptionSource):
            try:
                object.__setattr__(self, "source", AssumptionSource(self.source))
            except ValueError as exc:
                raise DataValidationError("source must be 'assumed' or 'calibrated'") from exc
        if self.source is AssumptionSource.CALIBRATED:
            if (
                isinstance(self.calibration_sample_size, bool)
                or not isinstance(self.calibration_sample_size, int)
                or self.calibration_sample_size <= 0
            ):
                raise DataValidationError(
                    "calibrated assumptions require a positive calibration_sample_size"
                )
            if not self.source_note.strip():
                raise DataValidationError("calibrated assumptions require a source_note")
        elif self.calibration_sample_size is not None:
            raise DataValidationError(
                "calibration_sample_size is only valid for calibrated assumptions"
            )


@dataclass(frozen=True, slots=True)
class ExecutionTrial:
    """One simulated paired execution attempt."""

    outcome: ExecutionOutcome
    requested_quantity: Decimal
    buy_filled: bool
    sell_filled: bool
    buy_latency_ms: int
    sell_latency_ms: int
    net_inventory_delta: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionEvaluation:
    """Aggregate outcome rates for one deterministic scenario."""

    trials: int
    paired_fills: int
    buy_leg_only: int
    sell_leg_only: int
    no_fills: int
    paired_fill_rate: Decimal
    legging_rate: Decimal
    no_fill_rate: Decimal
    average_buy_latency_ms: Decimal
    average_sell_latency_ms: Decimal
    assumptions_source: AssumptionSource
    calibration_sample_size: int | None


def simulate_execution_trial(
    assumptions: ExecutionAssumptions,
    *,
    quantity: Decimal | int | float | str,
    rng: random.Random,
) -> ExecutionTrial:
    """Simulate both legs with explicit assumptions and a caller-owned RNG."""
    requested = _decimal("quantity", quantity)
    if requested <= _ZERO:
        raise DataValidationError("quantity must be greater than zero")
    if not isinstance(rng, random.Random):
        raise DataValidationError("rng must be a random.Random instance")

    buy_latency = rng.randint(
        assumptions.buy_latency_min_ms, assumptions.buy_latency_max_ms
    )
    sell_latency = rng.randint(
        assumptions.sell_latency_min_ms, assumptions.sell_latency_max_ms
    )
    buy_draw = Decimal(rng.getrandbits(53)) / _RANDOM_DENOMINATOR
    sell_draw = Decimal(rng.getrandbits(53)) / _RANDOM_DENOMINATOR
    buy_filled = buy_draw < assumptions.buy_fill_probability
    sell_filled = sell_draw < assumptions.sell_fill_probability

    if buy_filled and sell_filled:
        outcome = ExecutionOutcome.PAIRED_FILL
        inventory_delta = _ZERO
    elif buy_filled:
        outcome = ExecutionOutcome.BUY_LEG_ONLY
        inventory_delta = requested
    elif sell_filled:
        outcome = ExecutionOutcome.SELL_LEG_ONLY
        inventory_delta = -requested
    else:
        outcome = ExecutionOutcome.NO_FILL
        inventory_delta = _ZERO

    return ExecutionTrial(
        outcome=outcome,
        requested_quantity=requested,
        buy_filled=buy_filled,
        sell_filled=sell_filled,
        buy_latency_ms=buy_latency,
        sell_latency_ms=sell_latency,
        net_inventory_delta=inventory_delta,
    )


def evaluate_execution_scenario(
    assumptions: ExecutionAssumptions,
    *,
    quantity: Decimal | int | float | str,
    trials: int,
    seed: int,
) -> ExecutionEvaluation:
    """Evaluate a scenario reproducibly; rates are simulated, not empirical."""
    if isinstance(trials, bool) or not isinstance(trials, int) or trials <= 0:
        raise DataValidationError("trials must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise DataValidationError("seed must be an integer")

    rng = random.Random(seed)
    results = [
        simulate_execution_trial(assumptions, quantity=quantity, rng=rng)
        for _ in range(trials)
    ]
    paired = sum(item.outcome is ExecutionOutcome.PAIRED_FILL for item in results)
    buy_only = sum(item.outcome is ExecutionOutcome.BUY_LEG_ONLY for item in results)
    sell_only = sum(item.outcome is ExecutionOutcome.SELL_LEG_ONLY for item in results)
    no_fill = sum(item.outcome is ExecutionOutcome.NO_FILL for item in results)
    denominator = Decimal(trials)
    return ExecutionEvaluation(
        trials=trials,
        paired_fills=paired,
        buy_leg_only=buy_only,
        sell_leg_only=sell_only,
        no_fills=no_fill,
        paired_fill_rate=Decimal(paired) / denominator,
        legging_rate=Decimal(buy_only + sell_only) / denominator,
        no_fill_rate=Decimal(no_fill) / denominator,
        average_buy_latency_ms=Decimal(
            sum(item.buy_latency_ms for item in results)
        ) / denominator,
        average_sell_latency_ms=Decimal(
            sum(item.sell_latency_ms for item in results)
        ) / denominator,
        assumptions_source=assumptions.source,
        calibration_sample_size=assumptions.calibration_sample_size,
    )


def compare_execution_scenarios(
    scenarios: Mapping[str, ExecutionAssumptions],
    *,
    quantity: Decimal | int | float | str,
    trials: int,
    seed: int,
) -> dict[str, ExecutionEvaluation]:
    """Compare named what-if scenarios using the same seed for reproducibility."""
    if not scenarios:
        raise DataValidationError("scenarios must not be empty")
    result: dict[str, ExecutionEvaluation] = {}
    for name, assumptions in scenarios.items():
        if not name.strip():
            raise DataValidationError("scenario names must not be empty")
        result[name] = evaluate_execution_scenario(
            assumptions, quantity=quantity, trials=trials, seed=seed
        )
    return result
