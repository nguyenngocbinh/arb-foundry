"""Read-only public REST adapters for Binance, OKX, Bybit, and KuCoin.

These adapters do not accept credentials and expose no order-submission methods.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from arb_foundry.errors import DataValidationError, ExchangeAPIError, RateLimitError
from arb_foundry.market_data import normalize_symbol
from arb_foundry.models import MarketType, OrderBook, OrderBookLevel, Ticker
from arb_foundry.time_utils import utc_now


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DataValidationError(f"exchange returned invalid {field}") from exc
    if not result.is_finite():
        raise DataValidationError(f"exchange returned non-finite {field}")
    return result


def _timestamp_ms(value: Any, fallback: datetime | None = None) -> datetime:
    if value in (None, ""):
        return fallback or utc_now()
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        raise DataValidationError("exchange returned an invalid millisecond timestamp") from exc


def _levels(values: Any, side: str) -> tuple[OrderBookLevel, ...]:
    if not isinstance(values, list):
        raise DataValidationError(f"exchange returned invalid order-book {side}")
    result: list[OrderBookLevel] = []
    for row in values:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            raise DataValidationError(f"exchange returned malformed {side} level")
        result.append(OrderBookLevel(_decimal(row[0], "price"), _decimal(row[1], "quantity")))
    return tuple(result)


class PublicRestAdapter:
    """Small standard-library HTTP client shared by read-only exchange adapters."""

    exchange = "unknown"
    base_url = ""
    timeout_seconds = 8.0
    user_agent = "arb-foundry/0.3.0 (public-market-data)"
    ticker_path = ""
    order_book_path = ""
    health_path = ""

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.base_url}{path}{query}",
            headers={"Accept": "application/json", "User-Agent": self.user_agent},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (418, 429):
                raise RateLimitError(
                    f"{self.exchange} public API rate limit (HTTP {exc.code})"
                ) from exc
            raise ExchangeAPIError(
                f"{self.exchange} public API returned HTTP {exc.code}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ExchangeAPIError(
                f"{self.exchange} public API request failed: {exc}"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExchangeAPIError(
                f"{self.exchange} public API returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ExchangeAPIError(
                f"{self.exchange} public API returned an unexpected payload"
            )
        self._ensure_success(payload)
        return payload

    def _ensure_success(self, payload: dict[str, Any]) -> None:
        """Exchange-specific response status validation."""

    def _exchange_symbol(self, symbol: str) -> str:
        return normalize_symbol(symbol).replace("/", "")

    def ticker_params(self, symbol: str) -> dict[str, str]:
        return {"symbol": self._exchange_symbol(symbol)}

    def order_book_params(self, symbol: str, limit: int) -> dict[str, str]:
        return {"symbol": self._exchange_symbol(symbol), "limit": str(limit)}

    def health_params(self) -> dict[str, str]:
        return {}

    def get_ticker(self, symbol: str) -> Ticker:
        canonical = normalize_symbol(symbol)
        payload = self._get_json(self.ticker_path, self.ticker_params(canonical))
        return self.parse_ticker(payload, canonical)

    def get_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        if limit < 1 or limit > 500:
            raise DataValidationError("order-book limit must be between 1 and 500")
        canonical = normalize_symbol(symbol)
        payload = self._get_json(self.order_book_path, self.order_book_params(canonical, limit))
        return self.parse_order_book(payload, canonical)

    def health_check(self) -> None:
        self._get_json(self.health_path, self.health_params())

    def parse_ticker(self, payload: dict[str, Any], symbol: str) -> Ticker:
        raise NotImplementedError

    def parse_order_book(self, payload: dict[str, Any], symbol: str) -> OrderBook:
        raise NotImplementedError


class BinanceAdapter(PublicRestAdapter):
    exchange = "binance"
    base_url = "https://api.binance.com"
    ticker_path = "/api/v3/ticker/bookTicker"
    order_book_path = "/api/v3/depth"
    health_path = "/api/v3/ping"

    def _ensure_success(self, payload: dict[str, Any]) -> None:
        if isinstance(payload.get("code"), (int, float)) and payload["code"] < 0:
            raise ExchangeAPIError(
                f"binance API error: {payload.get('msg', 'unknown error')}"
            )

    def parse_ticker(self, payload: dict[str, Any], symbol: str) -> Ticker:
        return Ticker(
            self.exchange, symbol, _decimal(payload.get("bidPrice"), "bid"),
            _decimal(payload.get("askPrice"), "ask"),
            _decimal(payload.get("bidQty"), "bid quantity"),
            _decimal(payload.get("askQty"), "ask quantity"),
            utc_now(), MarketType.SPOT,
        )

    def parse_order_book(self, payload: dict[str, Any], symbol: str) -> OrderBook:
        return OrderBook(
            self.exchange, symbol, _levels(payload.get("bids"), "bids"),
            _levels(payload.get("asks"), "asks"), utc_now(), MarketType.SPOT,
        )


class OKXAdapter(PublicRestAdapter):
    exchange = "okx"
    base_url = "https://www.okx.com"
    ticker_path = "/api/v5/market/ticker"
    order_book_path = "/api/v5/market/books"
    health_path = "/api/v5/public/time"

    def ticker_params(self, symbol: str) -> dict[str, str]:
        return {"instId": symbol.replace("/", "-")}

    def order_book_params(self, symbol: str, limit: int) -> dict[str, str]:
        return {"instId": symbol.replace("/", "-"), "sz": str(min(limit, 400))}

    def _ensure_success(self, payload: dict[str, Any]) -> None:
        if payload.get("code") not in (None, "0", 0):
            raise ExchangeAPIError(f"okx API error: {payload.get('msg', 'unknown error')}")

    def parse_ticker(self, payload: dict[str, Any], symbol: str) -> Ticker:
        rows = payload.get("data")
        if not isinstance(rows, list) or not rows:
            raise DataValidationError("okx ticker payload has no data")
        row = rows[0]
        return Ticker(
            self.exchange, symbol, _decimal(row.get("bidPx"), "bid"),
            _decimal(row.get("askPx"), "ask"),
            _decimal(row.get("bidSz"), "bid quantity"),
            _decimal(row.get("askSz"), "ask quantity"),
            _timestamp_ms(row.get("ts")), MarketType.SPOT,
        )

    def parse_order_book(self, payload: dict[str, Any], symbol: str) -> OrderBook:
        rows = payload.get("data")
        if not isinstance(rows, list) or not rows:
            raise DataValidationError("okx order-book payload has no data")
        row = rows[0]
        return OrderBook(
            self.exchange, symbol, _levels(row.get("bids"), "bids"),
            _levels(row.get("asks"), "asks"), _timestamp_ms(row.get("ts")),
            MarketType.SPOT,
        )


class BybitAdapter(PublicRestAdapter):
    exchange = "bybit"
    base_url = "https://api.bybit.com"
    ticker_path = "/v5/market/tickers"
    order_book_path = "/v5/market/orderbook"
    health_path = "/v5/market/time"

    def ticker_params(self, symbol: str) -> dict[str, str]:
        return {"category": "spot", "symbol": self._exchange_symbol(symbol)}

    def order_book_params(self, symbol: str, limit: int) -> dict[str, str]:
        return {
            "category": "spot",
            "symbol": self._exchange_symbol(symbol),
            "limit": str(min(limit, 200)),
        }

    def _ensure_success(self, payload: dict[str, Any]) -> None:
        if payload.get("retCode", 0) != 0:
            raise ExchangeAPIError(
                f"bybit API error: {payload.get('retMsg', 'unknown error')}"
            )

    def parse_ticker(self, payload: dict[str, Any], symbol: str) -> Ticker:
        rows = payload.get("result", {}).get("list", [])
        if not rows:
            raise DataValidationError("bybit ticker payload has no data")
        row = rows[0]
        return Ticker(
            self.exchange, symbol, _decimal(row.get("bid1Price"), "bid"),
            _decimal(row.get("ask1Price"), "ask"),
            _decimal(row.get("bid1Size"), "bid quantity"),
            _decimal(row.get("ask1Size"), "ask quantity"),
            _timestamp_ms(payload.get("time")), MarketType.SPOT,
        )

    def parse_order_book(self, payload: dict[str, Any], symbol: str) -> OrderBook:
        row = payload.get("result", {})
        return OrderBook(
            self.exchange, symbol, _levels(row.get("b", row.get("bids")), "bids"),
            _levels(row.get("a", row.get("asks")), "asks"),
            _timestamp_ms(row.get("ts") or payload.get("time")), MarketType.SPOT,
        )


class KuCoinAdapter(PublicRestAdapter):
    exchange = "kucoin"
    base_url = "https://api.kucoin.com"
    ticker_path = "/api/v1/market/orderbook/level1"
    order_book_path = "/api/v3/market/orderbook/level2_20"
    health_path = "/api/v1/timestamp"

    def ticker_params(self, symbol: str) -> dict[str, str]:
        return {"symbol": symbol.replace("/", "-")}

    def order_book_params(self, symbol: str, limit: int) -> dict[str, str]:
        if limit > 20:
            raise DataValidationError(
                "kucoin public adapter currently supports order-book limit up to 20"
            )
        return {"symbol": symbol.replace("/", "-")}

    def _ensure_success(self, payload: dict[str, Any]) -> None:
        if payload.get("code") not in (None, "200000"):
            raise ExchangeAPIError(
                f"kucoin API error: {payload.get('msg', 'unknown error')}"
            )

    def parse_ticker(self, payload: dict[str, Any], symbol: str) -> Ticker:
        row = payload.get("data", {})
        return Ticker(
            self.exchange, symbol, _decimal(row.get("bestBid"), "bid"),
            _decimal(row.get("bestAsk"), "ask"),
            _decimal(row.get("bestBidSize"), "bid quantity"),
            _decimal(row.get("bestAskSize"), "ask quantity"),
            utc_now(), MarketType.SPOT,
        )

    def parse_order_book(self, payload: dict[str, Any], symbol: str) -> OrderBook:
        row = payload.get("data", {})
        return OrderBook(
            self.exchange, symbol, _levels(row.get("bids"), "bids"),
            _levels(row.get("asks"), "asks"), _timestamp_ms(row.get("time")),
            MarketType.SPOT,
        )


def default_adapters() -> tuple[PublicRestAdapter, ...]:
    """Return the four supported read-only spot adapters."""
    return (BinanceAdapter(), OKXAdapter(), BybitAdapter(), KuCoinAdapter())
