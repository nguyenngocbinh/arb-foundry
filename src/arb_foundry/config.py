"""Validated application settings, sourced from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from arb_foundry.errors import ConfigurationError

_ALLOWED_ENVIRONMENTS = {"development", "test", "production"}
_ALLOWED_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


def _positive_int(name: str, value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return parsed


def _strict_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean (true/false)")


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with safe defaults and no exchange credentials."""

    app_env: str = "development"
    log_level: str = "INFO"
    market_data_stale_after_seconds: int = 5
    api_health_check_interval_hours: int = 24
    paper_trading_enabled: bool = True

    def __post_init__(self) -> None:
        if self.app_env not in _ALLOWED_ENVIRONMENTS:
            raise ConfigurationError(
                f"APP_ENV must be one of: {', '.join(sorted(_ALLOWED_ENVIRONMENTS))}"
            )
        normalized_level = self.log_level.upper()
        if normalized_level not in _ALLOWED_LOG_LEVELS:
            raise ConfigurationError(
                f"LOG_LEVEL must be one of: {', '.join(sorted(_ALLOWED_LOG_LEVELS))}"
            )
        object.__setattr__(self, "log_level", normalized_level)
        if self.market_data_stale_after_seconds <= 0:
            raise ConfigurationError("MARKET_DATA_STALE_AFTER_SECONDS must be greater than zero")
        if self.api_health_check_interval_hours <= 0:
            raise ConfigurationError("API_HEALTH_CHECK_INTERVAL_HOURS must be greater than zero")
        if not isinstance(self.paper_trading_enabled, bool):
            raise ConfigurationError("PAPER_TRADING_ENABLED must be a boolean")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from a mapping (or process environment) with validation."""
        values = os.environ if environ is None else environ
        app_env = values.get("APP_ENV", "development").strip().lower()
        log_level = values.get("LOG_LEVEL", "INFO").strip().upper()
        stale_after = _positive_int(
            "MARKET_DATA_STALE_AFTER_SECONDS",
            values.get("MARKET_DATA_STALE_AFTER_SECONDS", "5"),
        )
        health_interval = _positive_int(
            "API_HEALTH_CHECK_INTERVAL_HOURS",
            values.get("API_HEALTH_CHECK_INTERVAL_HOURS", "24"),
        )
        paper_trading = _strict_bool(
            "PAPER_TRADING_ENABLED", values.get("PAPER_TRADING_ENABLED", "true")
        )
        return cls(
            app_env=app_env,
            log_level=log_level,
            market_data_stale_after_seconds=stale_after,
            api_health_check_interval_hours=health_interval,
            paper_trading_enabled=paper_trading,
        )
