"""Application logging configuration."""

from __future__ import annotations

import logging

from arb_foundry.config import Settings


def configure_logging(settings: Settings, *, force: bool = False) -> None:
    """Configure readable timestamped logs; existing handlers are preserved by default."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        # Do not append a UTC marker: the standard formatter uses local time by default.
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=force,
    )
