import pytest

from arb_foundry.config import Settings
from arb_foundry.errors import ConfigurationError


def test_defaults_are_safe() -> None:
    settings = Settings.from_env({})
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.market_data_stale_after_seconds == 5
    assert settings.api_health_check_interval_hours == 24
    assert settings.paper_trading_enabled is True


def test_environment_values_are_normalized_and_parsed() -> None:
    settings = Settings.from_env(
        {
            "APP_ENV": "TEST",
            "LOG_LEVEL": "debug",
            "MARKET_DATA_STALE_AFTER_SECONDS": "12",
            "API_HEALTH_CHECK_INTERVAL_HOURS": "24",
            "PAPER_TRADING_ENABLED": "false",
        }
    )
    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.market_data_stale_after_seconds == 12
    assert settings.paper_trading_enabled is False


@pytest.mark.parametrize("value", ["", "wat", "2"])
def test_invalid_boolean_is_rejected(value: str) -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env({"PAPER_TRADING_ENABLED": value})


@pytest.mark.parametrize("value", ["0", "-1", "nope"])
def test_invalid_stale_after_is_rejected(value: str) -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env({"MARKET_DATA_STALE_AFTER_SECONDS": value})


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env({"APP_ENV": "local-prod"})


def test_log_level_is_normalized() -> None:
    assert Settings(log_level="warning").log_level == "WARNING"
