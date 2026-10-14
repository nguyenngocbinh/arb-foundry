"""Typed exceptions used across Arb Foundry."""


class ArbFoundryError(Exception):
    """Base exception for expected application errors."""


class ConfigurationError(ArbFoundryError, ValueError):
    """Raised when application configuration is invalid."""


class DataValidationError(ArbFoundryError, ValueError):
    """Raised when market or domain data violates its contract."""


class StaleMarketDataError(ArbFoundryError):
    """Raised when a consumer attempts to use data that is too old."""


class ExchangeAPIError(ArbFoundryError):
    """Raised when a public exchange API request fails or returns invalid data."""


class RateLimitError(ExchangeAPIError):
    """Raised when an exchange throttles a public API request."""
