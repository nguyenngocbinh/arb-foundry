import random
from decimal import Decimal

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.execution_model import (
    AssumptionSource,
    ExecutionAssumptions,
    ExecutionOutcome,
    compare_execution_scenarios,
    evaluate_execution_scenario,
    simulate_execution_trial,
)


def assumptions(
    *,
    buy_probability: str = "0.8",
    sell_probability: str = "0.7",
    source: AssumptionSource = AssumptionSource.ASSUMED,
    sample_size: int | None = None,
    note: str = "",
) -> ExecutionAssumptions:
    return ExecutionAssumptions(
        buy_latency_min_ms=10,
        buy_latency_max_ms=30,
        sell_latency_min_ms=20,
        sell_latency_max_ms=50,
        buy_fill_probability=Decimal(buy_probability),
        sell_fill_probability=Decimal(sell_probability),
        source=source,
        calibration_sample_size=sample_size,
        source_note=note,
    )


def test_trial_reports_paired_fill_and_zero_inventory_delta() -> None:
    config = assumptions(buy_probability="1", sell_probability="1")
    result = simulate_execution_trial(config, quantity="0.25", rng=random.Random(7))

    assert result.outcome is ExecutionOutcome.PAIRED_FILL
    assert result.buy_filled and result.sell_filled
    assert result.net_inventory_delta == Decimal("0")
    assert 10 <= result.buy_latency_ms <= 30
    assert 20 <= result.sell_latency_ms <= 50


def test_buy_only_fill_is_explicit_unhedged_inventory() -> None:
    config = assumptions(buy_probability="1", sell_probability="0")
    result = simulate_execution_trial(config, quantity="0.25", rng=random.Random(7))

    assert result.outcome is ExecutionOutcome.BUY_LEG_ONLY
    assert result.net_inventory_delta == Decimal("0.25")


def test_sell_only_fill_is_explicit_negative_inventory_delta() -> None:
    config = assumptions(buy_probability="0", sell_probability="1")
    result = simulate_execution_trial(config, quantity="0.25", rng=random.Random(7))

    assert result.outcome is ExecutionOutcome.SELL_LEG_ONLY
    assert result.net_inventory_delta == Decimal("-0.25")


def test_neither_leg_fills() -> None:
    result = simulate_execution_trial(
        assumptions(buy_probability="0", sell_probability="0"),
        quantity="1",
        rng=random.Random(1),
    )
    assert result.outcome is ExecutionOutcome.NO_FILL
    assert result.net_inventory_delta == Decimal("0")


@pytest.mark.parametrize("probability", ["-0.01", "1.01", "NaN"])
def test_probability_must_be_finite_and_in_unit_interval(probability: str) -> None:
    with pytest.raises(DataValidationError, match="probability"):
        assumptions(buy_probability=probability)


def test_latency_bounds_are_validated() -> None:
    with pytest.raises(DataValidationError, match="latency"):
        ExecutionAssumptions(
            buy_latency_min_ms=30,
            buy_latency_max_ms=10,
            sell_latency_min_ms=0,
            sell_latency_max_ms=10,
            buy_fill_probability=Decimal("1"),
            sell_fill_probability=Decimal("1"),
        )


def test_calibrated_source_requires_sample_size_and_source_note() -> None:
    with pytest.raises(DataValidationError, match="sample_size"):
        assumptions(source=AssumptionSource.CALIBRATED)

    with pytest.raises(DataValidationError, match="source_note"):
        assumptions(
            source=AssumptionSource.CALIBRATED,
            sample_size=100,
            note=" ",
        )

    calibrated = assumptions(
        source=AssumptionSource.CALIBRATED,
        sample_size=100,
        note="offline replay of 100 recorded paper observations",
    )
    assert calibrated.calibration_sample_size == 100


def test_scenario_evaluation_is_reproducible_and_counts_all_trials() -> None:
    config = assumptions()
    first = evaluate_execution_scenario(config, quantity="0.1", trials=1000, seed=42)
    second = evaluate_execution_scenario(config, quantity="0.1", trials=1000, seed=42)

    assert first == second
    assert (
        first.paired_fills + first.buy_leg_only + first.sell_leg_only + first.no_fills
        == 1000
    )
    assert first.paired_fill_rate == Decimal(first.paired_fills) / Decimal(1000)
    assert first.legging_rate == Decimal(
        first.buy_leg_only + first.sell_leg_only
    ) / Decimal(1000)
    assert first.assumptions_source is AssumptionSource.ASSUMED
    assert first.calibration_sample_size is None


def test_scenario_comparison_keeps_assumptions_visible() -> None:
    scenarios = {
        "optimistic": assumptions(buy_probability="1", sell_probability="1"),
        "one_leg_risk": assumptions(buy_probability="1", sell_probability="0"),
    }
    result = compare_execution_scenarios(
        scenarios, quantity="1", trials=50, seed=99
    )

    assert result["optimistic"].paired_fill_rate == Decimal("1")
    assert result["optimistic"].legging_rate == Decimal("0")
    assert result["one_leg_risk"].paired_fill_rate == Decimal("0")
    assert result["one_leg_risk"].legging_rate == Decimal("1")


@pytest.mark.parametrize("trials", [0, -1, True, 1.5])
def test_evaluation_requires_positive_integer_trial_count(trials) -> None:
    with pytest.raises(DataValidationError, match="trials"):
        evaluate_execution_scenario(assumptions(), quantity="1", trials=trials, seed=1)


def test_trial_rejects_invalid_quantity_and_rng() -> None:
    with pytest.raises(DataValidationError, match="quantity"):
        simulate_execution_trial(assumptions(), quantity="0", rng=random.Random(1))
    with pytest.raises(DataValidationError, match="rng"):
        simulate_execution_trial(assumptions(), quantity="1", rng=object())  # type: ignore[arg-type]


def test_comparison_rejects_empty_scenarios() -> None:
    with pytest.raises(DataValidationError, match="scenarios"):
        compare_execution_scenarios({}, quantity="1", trials=10, seed=1)
