from datetime import UTC, datetime, timedelta

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.kill_switch import KillSwitchState, KillSwitchStore
from arb_foundry.models import OrderBook, OrderBookLevel
from arb_foundry.paper import PaperTradingSimulator

NOW = datetime(2026, 10, 15, 8, tzinfo=UTC)


def _book(exchange: str, *, timestamp: datetime = NOW) -> OrderBook:
    if exchange == "binance":
        bids, asks = (OrderBookLevel("99", "2"),), (OrderBookLevel("100", "2"),)
    else:
        bids, asks = (OrderBookLevel("102", "2"),), (OrderBookLevel("103", "2"),)
    return OrderBook(
        exchange=exchange,
        symbol="BTC/USDT",
        bids=bids,
        asks=asks,
        timestamp=timestamp,
    )


def test_new_database_starts_halted_and_requires_explicit_arm(tmp_path) -> None:
    database = tmp_path / "kill-switch.sqlite3"
    with KillSwitchStore(database) as store:
        status = store.status()
        assert status.state is KillSwitchState.HALTED
        assert "explicit arm required" in status.reason
        with pytest.raises(DataValidationError, match="blocked by kill switch"):
            store.require_armed()

        armed = store.arm("operator approved paper run", changed_at=NOW)
        assert armed.state is KillSwitchState.ARMED
        assert store.require_armed().event_id == armed.event_id

    # State survives process/store recreation; it is not reset to a safe-looking
    # armed state or silently reinitialized while the persisted row exists.
    with KillSwitchStore(database) as reopened:
        assert reopened.status().state is KillSwitchState.ARMED
        halted = reopened.halt("operator stop", changed_at=NOW + timedelta(seconds=1))
        assert halted.state is KillSwitchState.HALTED

    with KillSwitchStore(database) as reopened:
        assert reopened.status().state is KillSwitchState.HALTED
        history = reopened.list_events()
        assert [event["state"] for event in history[:3]] == ["halted", "armed", "halted"]


def test_simulator_checks_switch_before_processing_inputs(tmp_path) -> None:
    with KillSwitchStore(tmp_path / "switch.sqlite3") as store:
        simulator = PaperTradingSimulator(kill_switch=store)
        with pytest.raises(DataValidationError, match="blocked by kill switch"):
            simulator.simulate(None, None, quantity="not-a-number", fee_rates={})


def test_armed_switch_allows_simulation_and_halt_blocks_next_attempt(tmp_path) -> None:
    with KillSwitchStore(tmp_path / "switch.sqlite3") as store:
        store.arm("test run", changed_at=NOW)
        simulator = PaperTradingSimulator(kill_switch=store)
        result = simulator.simulate(
            _book("binance"),
            _book("okx"),
            quantity="1",
            fee_rates={"binance": "0.001", "okx": "0.001"},
            now=NOW,
        )
        assert result.status.value == "filled"

        store.halt("test stop", changed_at=NOW + timedelta(seconds=1))
        with pytest.raises(DataValidationError, match="test stop"):
            simulator.simulate(
                _book("binance"),
                _book("okx"),
                quantity="1",
                fee_rates={"binance": "0.001", "okx": "0.001"},
                now=NOW,
            )


def test_corrupt_state_or_missing_audit_event_fails_closed(tmp_path) -> None:
    database = tmp_path / "switch.sqlite3"
    with KillSwitchStore(database) as store:
        store.arm("approved", changed_at=NOW)
        store._connection.execute(
            "UPDATE kill_switch_current SET changed_at = 'not-a-timestamp' WHERE singleton_id = 1"
        )
        store._connection.commit()
        with pytest.raises(DataValidationError, match="timestamp invalid"):
            store.require_armed()

        store._connection.execute(
            "UPDATE kill_switch_current SET changed_at = ? WHERE singleton_id = 1",
            (NOW.isoformat(),),
        )
        store._connection.execute("DELETE FROM kill_switch_events")
        store._connection.commit()
        with pytest.raises(DataValidationError, match="audit event missing"):
            store.require_armed()


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
def test_reason_validation(reason: str, tmp_path) -> None:
    with KillSwitchStore(tmp_path / "switch.sqlite3") as store:
        with pytest.raises(DataValidationError, match="reason"):
            store.arm(reason)


@pytest.mark.parametrize("limit", [0, -1, 10_001, True])
def test_event_limit_validation(limit: int, tmp_path) -> None:
    with KillSwitchStore(tmp_path / "switch.sqlite3") as store:
        with pytest.raises(DataValidationError, match="limit"):
            store.list_events(limit=limit)
