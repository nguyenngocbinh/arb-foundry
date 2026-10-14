from datetime import UTC
from threading import Event

import pytest

from arb_foundry.health import HealthCheckService


class FakeAdapter:
    def __init__(self, exchange: str, error: Exception | None = None) -> None:
        self.exchange = exchange
        self.error = error
        self.calls = 0

    def health_check(self) -> None:
        self.calls += 1
        if self.error:
            raise self.error

    def get_ticker(self, symbol: str):
        raise NotImplementedError

    def get_order_book(self, symbol: str, limit: int = 20):
        raise NotImplementedError


def test_health_service_records_success_and_failure() -> None:
    healthy = FakeAdapter("binance")
    broken = FakeAdapter("okx", TimeoutError("timeout"))
    service = HealthCheckService([healthy, broken], interval_hours=24)

    results = service.check_all()

    assert results["binance"].healthy is True
    assert results["binance"].error is None
    assert results["okx"].healthy is False
    assert "TimeoutError" in results["okx"].error
    assert results["okx"].checked_at.tzinfo == UTC
    assert healthy.calls == broken.calls == 1


def test_health_service_returns_copy_of_latest_status() -> None:
    service = HealthCheckService([FakeAdapter("binance")])
    service.check_all()
    copy = service.latest
    copy.clear()
    assert "binance" in service.latest


@pytest.mark.parametrize("interval", [0, -1])
def test_health_service_rejects_invalid_interval(interval: int) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        HealthCheckService([FakeAdapter("binance")], interval_hours=interval)


def test_health_service_requires_unique_exchange_names() -> None:
    with pytest.raises(ValueError, match="unique"):
        HealthCheckService([FakeAdapter("binance"), FakeAdapter("binance")])


def test_periodic_runner_checks_immediately_and_stops_cleanly(monkeypatch) -> None:
    adapter = FakeAdapter("binance")
    service = HealthCheckService([adapter], interval_hours=24)
    stop = Event()

    original_check = service.check_all

    def check_and_stop():
        results = original_check()
        stop.set()
        return results

    monkeypatch.setattr(service, "check_all", check_and_stop)
    service.run_forever(stop)

    assert adapter.calls == 1
