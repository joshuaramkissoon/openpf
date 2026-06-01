from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

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
                err = T212RateLimitError(
                    f"Trading 212 rate limit hit (429). reset={reset_at}. detail={detail[:300]}"
                )
                try:
                    err.reset_epoch = float(reset_at) if reset_at else None  # type: ignore[attr-defined]
                except (TypeError, ValueError):
                    err.reset_epoch = None  # type: ignore[attr-defined]
                raise err
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

    # ── Paginated history (orders / dividends) ───────────────────────────────
    #
    # T212 history endpoints are *aggressively* rate-limited (a tight per-minute
    # budget — fast pagination trips 429 within a few calls). These walk the
    # whole feed newest→oldest with a conservative inter-page sleep and a 429
    # backoff that honours the ``x-ratelimit-reset`` header, so a one-time
    # backfill completes without manual babysitting. ``on_page`` lets a caller
    # checkpoint progress (and abort by returning False) for resumable backfills.

    @staticmethod
    def _history_next_params(next_page_path: str) -> dict[str, str]:
        """Parse a T212 ``nextPagePath`` into query params.

        The orders feed returns a *full* path
        (``/api/v0/equity/history/orders?cursor=..&limit=50&instrumentCode=``)
        while the transactions feed returns a bare query string
        (``limit=50&cursor=..``). Handle both, and drop empty values (an empty
        ``instrumentCode=`` would otherwise filter to a non-existent instrument).
        """
        query = next_page_path.split("?", 1)[1] if "?" in next_page_path else next_page_path
        params: dict[str, str] = {}
        for kv in query.split("&"):
            if "=" not in kv:
                continue
            key, value = kv.split("=", 1)
            if value != "":
                params[key] = value
        return params

    def _paginate_history(
        self,
        path: str,
        *,
        limit: int = 50,
        max_pages: int = 400,
        page_sleep: float = 1.2,
        max_429_retries: int = 6,
        on_page: Callable[[list[dict[str, Any]], int], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every item from a cursor-paginated history feed, newest first.

        Retries on 429 with a bounded backoff (capped, honours reset header).
        ``on_page(items, page_index)`` is invoked once per fetched page; return
        False from it to stop early (resumable backfill checkpoints)."""
        params: dict[str, Any] = {"limit": limit}
        for page in range(max_pages):
            attempt = 0
            while True:
                try:
                    data, _meta = self._request("GET", path, params=params)
                    break
                except T212RateLimitError as exc:
                    attempt += 1
                    if attempt > max_429_retries:
                        raise
                    self._sleep_for_rate_limit(exc, attempt)
            items = data.get("items", []) or []
            if on_page is not None and on_page(items, page) is False:
                return
            for item in items:
                yield item
            nxt = data.get("nextPagePath")
            if not nxt or not items:
                return
            params = self._history_next_params(nxt)
            time.sleep(page_sleep)

    def _sleep_for_rate_limit(self, exc: T212RateLimitError, attempt: int) -> None:
        """Sleep before retrying a 429. Prefer the reset header; otherwise back
        off geometrically. Capped so a stuck reset can't hang a backfill."""
        delay = min(5.0 * (2 ** (attempt - 1)), 90.0)
        reset = getattr(exc, "reset_epoch", None)
        if isinstance(reset, (int, float)) and reset > 0:
            wait = reset - time.time()
            if 0 < wait < 120:
                delay = wait + 1.0
        time.sleep(delay)

    def get_order_history(self, *, max_pages: int = 400, page_sleep: float = 2.5) -> list[dict[str, Any]]:
        """All historical orders (each as ``{"order": {...}, "fill": {...}}``).
        ``fill.quantity`` is signed (negative = sell); ``fill.price`` is in the
        instrument currency; ``fill.walletImpact.netValue`` is the cash impact in
        the account currency."""
        return list(self._paginate_history("/equity/history/orders", max_pages=max_pages, page_sleep=page_sleep))

    def get_dividends(self, *, max_pages: int = 200, page_sleep: float = 2.5) -> list[dict[str, Any]]:
        """All dividend payments (``amount`` in ``currency``, paid ``paidOn``)."""
        return list(self._paginate_history("/equity/history/dividends", max_pages=max_pages, page_sleep=page_sleep))

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
