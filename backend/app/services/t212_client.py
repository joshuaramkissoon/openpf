from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.config_store import AccountKind, ConfigStore


class T212Error(RuntimeError):
    pass


class T212AuthError(T212Error):
    pass


class T212RateLimitError(T212Error):
    pass


@dataclass
class T212Client:
    api_key: str
    api_secret: str
    base_env: str = "demo"

    @property
    def base_url(self) -> str:
        if self.base_env == "live":
            return "https://live.trading212.com/api/v0"
        return "https://demo.trading212.com/api/v0"

    @property
    def auth_header(self) -> str:
        key = (self.api_key or "").strip()
        secret = (self.api_secret or "").strip()
        raw = f"{key}:{secret}".encode("utf-8")
        encoded = base64.b64encode(raw).decode("utf-8")
        return f"Basic {encoded}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, *, params: dict | None = None, payload: dict | None = None) -> tuple[Any, dict[str, Any]]:
        if not (self.api_key or "").strip() or not (self.api_secret or "").strip():
            raise T212AuthError("Trading 212 API credentials are not configured")

        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.request(method, url, headers=self._headers(), params=params, json=payload)
        except httpx.RequestError as exc:
            raise T212Error(f"Trading 212 request failed: {exc}") from exc

        if response.status_code in (401, 403):
            key = (self.api_key or "").strip()
            hint = (
                f"Trading 212 auth failed ({response.status_code}) on env={self.base_env}. "
                f"Check key/secret, env (demo/live), account type (Invest/Stocks ISA), and IP restriction. "
                f"key_len={len(key)}"
            )
            raise T212AuthError(hint)

        if response.status_code >= 400:
            detail = response.text
            if response.status_code == 429:
                reset_at = response.headers.get("x-ratelimit-reset", "")
                raise T212RateLimitError(
                    f"Trading 212 rate limit hit (429). reset={reset_at}. detail={detail[:300]}"
                )
            raise T212Error(f"Trading 212 API error {response.status_code}: {detail[:500]}")

        data = response.json() if response.content else {}
        limits = {
            "limit": response.headers.get("x-ratelimit-limit"),
            "remaining": response.headers.get("x-ratelimit-remaining"),
            "reset": response.headers.get("x-ratelimit-reset"),
        }
        return data, limits

    def get_account_summary(self) -> dict[str, Any]:
        data, limits = self._request("GET", "/equity/account/summary")
        data["_ratelimit"] = limits
        return data

    def get_positions(self) -> list[dict[str, Any]]:
        data, _ = self._request("GET", "/equity/positions")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        return []

    def get_pending_orders(self) -> list[dict[str, Any]]:
        data, _ = self._request("GET", "/equity/orders")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        return []

    def get_instruments_metadata(self) -> list[dict[str, Any]]:
        data, _ = self._request("GET", "/equity/metadata/instruments")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        return []

    def get_exchanges_metadata(self) -> list[dict[str, Any]]:
        data, _ = self._request("GET", "/equity/metadata/exchanges")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        return []

    def place_market_order(self, instrument_code: str, quantity: float, *, extended_hours: bool = False) -> dict[str, Any]:
        payload = {
            "ticker": instrument_code,
            "quantity": quantity,
            "extendedHours": extended_hours,
        }
        data, _ = self._request("POST", "/equity/orders/market", payload=payload)
        return data

    def place_limit_order(self, instrument_code: str, quantity: float, limit_price: float) -> dict[str, Any]:
        payload = {
            "ticker": instrument_code,
            "quantity": quantity,
            "limitPrice": limit_price,
        }
        data, _ = self._request("POST", "/equity/orders/limit", payload=payload)
        return data

    def place_stop_order(self, instrument_code: str, quantity: float, stop_price: float) -> dict[str, Any]:
        payload = {
            "ticker": instrument_code,
            "quantity": quantity,
            "stopPrice": stop_price,
        }
        data, _ = self._request("POST", "/equity/orders/stop", payload=payload)
        return data

    def place_stop_limit_order(self, instrument_code: str, quantity: float, stop_price: float, limit_price: float) -> dict[str, Any]:
        payload = {
            "ticker": instrument_code,
            "quantity": quantity,
            "stopPrice": stop_price,
            "limitPrice": limit_price,
        }
        data, _ = self._request("POST", "/equity/orders/stop_limit", payload=payload)
        return data

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/equity/orders/{order_id}")


def build_t212_client(config_store: ConfigStore, account_kind: AccountKind = "invest") -> T212Client:
    broker = config_store.get_broker()
    creds = config_store.get_account_credentials(account_kind)

    return T212Client(
        api_key=creds.get("t212_api_key", ""),
        api_secret=creds.get("t212_api_secret", ""),
        base_env=broker.get("t212_base_env", "demo"),
    )


def normalize_instrument_code(symbol: str) -> str:
    value = symbol.strip().upper()
    if "_" in value:
        return value
    return f"{value}_US_EQ"
