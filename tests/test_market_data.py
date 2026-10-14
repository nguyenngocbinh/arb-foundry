from datetime import UTC, datetime, timedelta

import pytest

from arb_foundry.errors import DataValidationError, StaleMarketDataError
from arb_foundry.market_data import normalize_symbol, validate_freshness

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("btc/usdt", "BTC/USDT"), ("BTC-USDT", "BTC/USDT"), (" ETH / USDT ", "ETH/USDT")],
)
def test_normalize_symbol(value: str, expected: str) -> None:
    assert normalize_symbol(value) == expected


@pytest.mark.parametrize("value", ["BTCUSDT", "BTC/", "/USDT", "BTC/USDT/EXTRA", "BTC / USDT"])
def test_normalize_symbol_rejects_ambiguous_or_invalid_values(value: str) -> None:
    with pytest.raises(DataValidationError):
        normalize_symbol(value)


def test_freshness_accepts_recent_data() -> None:
    validate_freshness(NOW - timedelta(seconds=4), 5, now=NOW)


def test_freshness_rejects_stale_data() -> None:
    with pytest.raises(StaleMarketDataError, match="maximum is 5s"):
        validate_freshness(NOW - timedelta(seconds=6), 5, now=NOW)


def test_freshness_rejects_future_and_naive_timestamps() -> None:
    with pytest.raises(DataValidationError, match="future"):
        validate_freshness(NOW + timedelta(seconds=6), 5, now=NOW)
    with pytest.raises(DataValidationError, match="timezone-aware"):
        validate_freshness(datetime(2026, 10, 14, 12), 5, now=NOW)
