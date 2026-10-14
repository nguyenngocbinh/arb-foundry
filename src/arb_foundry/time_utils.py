"""UTC timestamp helpers.

All timestamps crossing the domain boundary must be timezone-aware and are
normalized to UTC. Naive datetimes are rejected rather than guessed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from arb_foundry.errors import DataValidationError


def ensure_utc(value: datetime) -> datetime:
    """Return an aware datetime normalized to UTC; reject naive values."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataValidationError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and normalize it to UTC.

    A trailing Z is accepted. Timestamps without an explicit UTC offset are
    rejected because silently assuming a timezone can corrupt market ordering.
    """
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DataValidationError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    return ensure_utc(parsed)
