"""Exchange public-API health checks and a configurable periodic runner."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from threading import Event
from time import perf_counter

from arb_foundry.market_data import MarketDataAdapter
from arb_foundry.time_utils import utc_now


@dataclass(frozen=True, slots=True)
class HealthResult:
    """Latest health-check result for one exchange."""

    exchange: str
    checked_at: datetime
    healthy: bool
    latency_ms: float
    error: str | None = None


class HealthCheckService:
    """Run read-only checks and retain the latest status in process memory.

    Call run_forever() from the application's service lifecycle to check
    immediately on startup and then once per configured interval.
    """

    def __init__(
        self,
        adapters: Iterable[MarketDataAdapter],
        interval_hours: int = 24,
    ) -> None:
        if interval_hours <= 0:
            raise ValueError("interval_hours must be greater than zero")
        self._adapters = tuple(adapters)
        if not self._adapters:
            raise ValueError("at least one exchange adapter is required")
        names = [adapter.exchange for adapter in self._adapters]
        if len(set(names)) != len(names):
            raise ValueError("exchange adapter names must be unique")
        self.interval_hours = interval_hours
        self._latest: dict[str, HealthResult] = {}

    @property
    def latest(self) -> dict[str, HealthResult]:
        """Return a copy of the most recent per-exchange results."""
        return dict(self._latest)

    def check_all(self) -> dict[str, HealthResult]:
        """Check every adapter now and return the recorded results."""
        for adapter in self._adapters:
            started = perf_counter()
            checked_at = utc_now()
            try:
                adapter.health_check()
            except Exception as exc:
                result = HealthResult(
                    adapter.exchange,
                    checked_at,
                    False,
                    (perf_counter() - started) * 1000,
                    f"{type(exc).__name__}: {exc}",
                )
            else:
                result = HealthResult(
                    adapter.exchange, checked_at, True, (perf_counter() - started) * 1000
                )
            self._latest[adapter.exchange] = result
        return self.latest

    def run_forever(self, stop_event: Event | None = None) -> None:
        """Check at startup, then at the configured cadence until stopped.

        Pass a threading.Event to stop cleanly. Without one, this method runs
        until the process is interrupted.
        """
        stop = stop_event or Event()
        while not stop.is_set():
            self.check_all()
            if stop.wait(self.interval_hours * 60 * 60):
                break
