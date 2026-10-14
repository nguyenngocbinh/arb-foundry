"""Application logging configuration."""

from __future__ import annotations

import logging

from arb_foundry.config import Settings


def configure_logging(settings: Settings, *, force: bool = False) -> None:
    """Configure readable timestamped logs; existing handlers are preserved by default."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)sZ %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=force,
    )
