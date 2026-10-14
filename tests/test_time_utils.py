from datetime import UTC, datetime, timedelta, timezone

import pytest

from arb_foundry.errors import DataValidationError
from arb_foundry.time_utils import ensure_utc, parse_timestamp, utc_now


def test_parse_timestamp_with_z() -> None:
    assert parse_timestamp("2026-10-14T10:00:00Z") == datetime(
        2026, 10, 14, 10, 0, tzinfo=UTC
    )


def test_parse_timestamp_converts_offset_to_utc() -> None:
    parsed = parse_timestamp("2026-10-14T17:00:00+07:00")
    assert parsed == datetime(2026, 10, 14, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", ["2026-10-14T10:00:00", "not-a-date", ""])
def test_invalid_or_naive_timestamp_is_rejected(value: str) -> None:
    with pytest.raises(DataValidationError):
        parse_timestamp(value)


def test_ensure_utc_converts_aware_datetime() -> None:
    local = datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=7)))
    assert ensure_utc(local) == datetime(2026, 1, 1, 0, tzinfo=UTC)


def test_utc_now_is_timezone_aware() -> None:
    assert utc_now().utcoffset() == timedelta(0)
