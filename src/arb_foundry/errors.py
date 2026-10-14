"""Typed exceptions used across Arb Foundry."""


class ArbFoundryError(Exception):
    """Base exception for expected application errors."""


class ConfigurationError(ArbFoundryError, ValueError):
    """Raised when application configuration is invalid."""


class DataValidationError(ArbFoundryError, ValueError):
    """Raised when market or domain data violates its contract."""


class StaleMarketDataError(ArbFoundryError):
    """Raised when a consumer attempts to use data that is too old."""
