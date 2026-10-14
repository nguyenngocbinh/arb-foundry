import logging

from arb_foundry.config import Settings
from arb_foundry.logging_config import configure_logging


def test_logging_config_sets_level() -> None:
    configure_logging(Settings(log_level="DEBUG"), force=True)
    assert logging.getLogger().level == logging.DEBUG
