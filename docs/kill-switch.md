# Persistent paper kill switch

## Safety contract

`KillSwitchStore` persists an explicit state in SQLite and records every
state transition in an event table. A new database starts in `halted`; a
caller must explicitly invoke `arm(reason)` before simulations using that
store can proceed. `halt(reason)` blocks the next simulation attempt that
checks the same store.

This feature is a **paper-simulation gate**, not a live-execution kill switch:
Arb Foundry does not submit real orders. It cannot cancel a simulation that has
already passed its check, stop another process that does not use the same
store, or provide distributed mutual exclusion across machines.

## Minimal usage

```python
from arb_foundry.kill_switch import KillSwitchStore
from arb_foundry.paper import PaperTradingSimulator

with KillSwitchStore("data/paper.sqlite3") as kill_switch:
    # New databases start HALTED. Explicitly arm only after reviewing settings.
    kill_switch.arm("operator approved local paper run")

    simulator = PaperTradingSimulator(kill_switch=kill_switch)
    result = simulator.simulate(
        buy_book,
        sell_book,
        quantity="0.01",
        fee_rates={"binance": "0.001", "okx": "0.001"},
        now=scan_time,
    )

    # Persisting HALTED blocks subsequent calls using this simulator/store.
    kill_switch.halt("manual stop")
```

Pass the same `KillSwitchStore` to the simulator that is used by the runner.
For compatibility with existing callers, `PaperTradingSimulator` still
accepts `kill_switch=None`; that legacy mode has **no kill-switch enforcement**.
A safety-sensitive runner must always pass a store and should test that wiring
in its own integration tests. The switch cannot protect a code path that
bypasses the simulator or uses a simulator without the store.

## State machine and restart behavior

- `HALTED`: simulations that check this store raise `DataValidationError`
  before input validation or fill calculation.
- `ARMED`: the gate permits the simulator to continue with its normal
  freshness, skew, depth, fee and symbol checks.
- New database: initialize to `HALTED`; never auto-arm.
- Restart: read the persisted state. An `ARMED` state remains armed until
  explicitly halted; a `HALTED` state remains halted. Restart does not reset
  the state.
- Missing current row: initialization inserts `HALTED`. A missing/invalid
  current state or missing referenced audit event detected by the gate fails
  closed with a validation error.
- Unreadable database, lock timeout or SQLite I/O error: the error propagates;
  the simulator does not catch it and therefore does not proceed.
- Corrupt/unknown state: the gate raises and does not infer `ARMED`.
- A failed state transition rolls back both the current-state update and its
  audit event.

## Persistence and audit model

The SQLite database contains `kill_switch_current` (one current state) and
`kill_switch_events` (append-only transitions through this API). State plus
event are written in one SQLite transaction using `BEGIN IMMEDIATE`. Event
history is returned newest first by `list_events(limit=...)`. Use the same
database path across restarts. Keep it on a reliable local disk and restrict
write access.

The history is not tamper-proof: an actor with direct database write access can
change both tables. SQLite locking coordinates database transactions, but the
check and later simulation are not one atomic transaction; a concurrent halt
may occur after a call has passed its gate. The current implementation is for
single-runner/local research workflows, not a multi-host production safety
control. Monitor and back up the state database.

## Operational checklist

1. Create/open the store at application startup and pass it into every
   `PaperTradingSimulator` used by the runner.
2. Start halted; inspect config and risk limits before explicitly calling
   `arm("...")`.
3. On operator stop, call `halt("...")` and verify `status()`.
4. On shutdown, close the store cleanly. Do not delete the SQLite file to
   reset state; deletion recreates a HALTED switch, but also discards history.
5. Add process supervision and alerts separately; this module does not manage
   service lifecycle, signals, or notifications.
