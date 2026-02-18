"""Trading 212 MCP Server for Archie.

Runs as a standalone stdio MCP server. Credentials are injected via
environment variables by the Claude Agent SDK — never stored on disk.

All HTTP calls originate from the local machine, bypassing Cloudflare's
datacenter-IP blocks that affect WebFetch.

Usage (standalone):
    T212_BASE_ENV=live \
    T212_API_KEY_INVEST=... T212_API_SECRET_INVEST=... \
    python -m mcp_servers.t212

Usage (via Claude Agent SDK):
    Configured automatically in ClaudeAgentOptions.mcp_servers
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP

# ──────────────────────────────────────────────
# Logging (file-based — stdout is reserved for MCP protocol)
# ──────────────────────────────────────────────

_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR", "/app/logs"))
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("t212-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "t212.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

AccountKind = Literal["invest", "stocks_isa"]

_BASE_URLS = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}

_REQUEST_TIMEOUT = 30.0
_MAX_RETRIES = 2
_RETRY_BACKOFF = 1.5  # seconds, doubles each attempt

# ──────────────────────────────────────────────
# Rate Limiter
# ──────────────────────────────────────────────


class _RateLimiter:
    """Per-endpoint token-bucket limiter driven by T212 response headers."""

    def __init__(self) -> None:
        self._resets: dict[str, float] = {}  # endpoint_group -> unix timestamp

    async def wait_if_needed(self, group: str) -> None:
        reset_at = self._resets.get(group)
        if reset_at is None:
            return
        now = time.time()
        if now < reset_at:
            delay = reset_at - now + 0.15  # 150ms safety margin
            logger.debug("rate-limit: sleeping %.2fs for %s", delay, group)
            await asyncio.sleep(delay)

    def update(self, group: str, headers: httpx.Headers) -> None:
        remaining = headers.get("x-ratelimit-remaining")
        reset = headers.get("x-ratelimit-reset")
        if remaining is not None and reset is not None:
            try:
                if int(remaining) <= 0:
                    self._resets[group] = float(reset)
                else:
                    self._resets.pop(group, None)
            except (ValueError, TypeError):
                pass


_rate_limiter = _RateLimiter()

# ──────────────────────────────────────────────
# HTTP Client
# ──────────────────────────────────────────────


def _auth_header(api_key: str, api_secret: str) -> str:
    raw = f"{api_key}:{api_secret}".encode("utf-8")
    return f"Basic {base64.b64encode(raw).decode('utf-8')}"


def _resolve_credentials(account: AccountKind) -> tuple[str, str]:
    """Resolve API key + secret for the given account from env vars.

    Tries multiple naming conventions used across the project.
    Raises ValueError with a clear message if credentials are missing.
    """
    if account == "invest":
        key = (
            os.environ.get("T212_API_KEY_INVEST")
            or os.environ.get("T212_INVEST_API_KEY")
            or os.environ.get("T212_API_KEY")
            or ""
        ).strip()
        secret = (
            os.environ.get("T212_API_SECRET_INVEST")
            or os.environ.get("T212_INVEST_API_SECRET")
            or os.environ.get("T212_API_SECRET")
            or ""
        ).strip()
    else:
        key = (
            os.environ.get("T212_API_KEY_STOCKS_ISA")
            or os.environ.get("T212_STOCKS_ISA_API_KEY")
            or ""
        ).strip()
        secret = (
            os.environ.get("T212_API_SECRET_STOCKS_ISA")
            or os.environ.get("T212_STOCKS_ISA_API_SECRET")
            or ""
        ).strip()

    if not key or not secret:
        raise ValueError(
            f"Missing T212 credentials for '{account}'. "
            f"Set T212_API_KEY_{account.upper()} and T212_API_SECRET_{account.upper()} env vars."
        )
    return key, secret


def _base_url() -> str:
    env = os.environ.get("T212_BASE_ENV", "live").strip().lower()
    url = _BASE_URLS.get(env)
    if not url:
        raise ValueError(f"Invalid T212_BASE_ENV '{env}'. Must be 'demo' or 'live'.")
    return url


async def _request(
    method: str,
    path: str,
    account: AccountKind,
    *,
    rate_group: str | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """Make an authenticated request to the T212 API with rate limiting and retries."""
    api_key, api_secret = _resolve_credentials(account)
    url = f"{_base_url()}{path}"
    group = rate_group or path
    headers = {
        "Authorization": _auth_header(api_key, api_secret),
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        await _rate_limiter.wait_if_needed(group)

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                response = await client.request(
                    method, url, headers=headers, params=params, json=json_body
                )
        except httpx.TimeoutException:
            last_error = TimeoutError(f"T212 request timed out: {method} {path}")
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_BACKOFF * (2**attempt))
                continue
            break
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_BACKOFF * (2**attempt))
                continue
            break

        _rate_limiter.update(group, response.headers)

        if response.status_code == 200:
            return response.json()

        if response.status_code == 204:
            return {"status": "ok", "message": "No content"}

        if response.status_code == 401:
            raise PermissionError(
                f"T212 auth failed for '{account}' account. Check API key and secret."
            )

        if response.status_code == 403:
            raise PermissionError(
                f"T212 access denied for '{account}' account. "
                "Key may lack required permissions or IP may be restricted."
            )

        if response.status_code == 429:
            reset = response.headers.get("x-ratelimit-reset")
            if reset:
                _rate_limiter._resets[group] = float(reset)
            if attempt < _MAX_RETRIES:
                wait = float(reset or time.time() + 5) - time.time() + 0.2
                logger.info("rate-limited on %s, waiting %.1fs", path, wait)
                await asyncio.sleep(max(0.5, wait))
                continue
            raise RuntimeError(
                f"T212 rate limit exceeded on {path}. Try again in a few seconds."
            )

        if response.status_code >= 500 and attempt < _MAX_RETRIES:
            last_error = RuntimeError(f"T212 server error {response.status_code}")
            await asyncio.sleep(_RETRY_BACKOFF * (2**attempt))
            continue

        # 4xx client error — don't retry
        body = response.text[:500]
        raise RuntimeError(
            f"T212 API error {response.status_code} on {method} {path}: {body}"
        )

    raise last_error or RuntimeError(f"T212 request failed after {_MAX_RETRIES + 1} attempts")


def _fmt(data: Any) -> str:
    """Format response data as indented JSON for the agent."""
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _validate_account(account: str) -> AccountKind:
    normalised = account.strip().lower().replace(" ", "_").replace("-", "_")
    if normalised in ("invest", "stocks_isa"):
        return normalised  # type: ignore[return-value]
    raise ValueError(
        f"Invalid account '{account}'. Must be 'invest' or 'stocks_isa'."
    )


# ──────────────────────────────────────────────
# MCP Server & Tools
# ──────────────────────────────────────────────

mcp = FastMCP(
    "trading212",
    instructions=(
        "Trading 212 API tools for portfolio management. "
        "Each tool requires an 'account' parameter: 'invest' or 'stocks_isa'. "
        "All data comes from the live T212 API — values are real-time."
    ),
)


# ── Account & Portfolio ──


@mcp.tool()
async def get_account_summary(account: str = "invest") -> str:
    """Get account balance, available cash, total investments, and P&L.

    Args:
        account: 'invest' or 'stocks_isa'
    """
    acct = _validate_account(account)
    data = await _request("GET", "/equity/account/summary", acct, rate_group="summary")
    return _fmt(data)


@mcp.tool()
async def get_positions(account: str = "invest") -> str:
    """Get all open positions with current price, quantity, P&L, and average price.

    Args:
        account: 'invest' or 'stocks_isa'
    """
    acct = _validate_account(account)
    data = await _request("GET", "/equity/positions", acct, rate_group="positions")
    if isinstance(data, list):
        return _fmt({"count": len(data), "positions": data})
    return _fmt(data)


@mcp.tool()
async def get_pending_orders(account: str = "invest") -> str:
    """Get all pending (unfilled) orders.

    Args:
        account: 'invest' or 'stocks_isa'
    """
    acct = _validate_account(account)
    data = await _request("GET", "/equity/orders", acct, rate_group="orders")
    if isinstance(data, list):
        return _fmt({"count": len(data), "orders": data})
    return _fmt(data)


# ── Trading ──


@mcp.tool()
async def place_market_order(
    account: str,
    ticker: str,
    quantity: float,
) -> str:
    """Place a market order (buy or sell).

    Args:
        account: 'invest' or 'stocks_isa'
        ticker: Instrument code (e.g. 'AAPL_US_EQ', 'TSLA_US_EQ')
        quantity: Positive to buy, negative to sell
    """
    acct = _validate_account(account)
    data = await _request(
        "POST",
        "/equity/orders/market",
        acct,
        rate_group="place_order",
        json_body={"ticker": ticker.strip(), "quantity": quantity},
    )
    return _fmt(data)


@mcp.tool()
async def place_limit_order(
    account: str,
    ticker: str,
    quantity: float,
    limit_price: float,
    time_validity: str = "Day",
) -> str:
    """Place a limit order.

    Args:
        account: 'invest' or 'stocks_isa'
        ticker: Instrument code
        quantity: Positive to buy, negative to sell
        limit_price: The limit price
        time_validity: 'Day' or 'GTC' (good till cancelled)
    """
    acct = _validate_account(account)
    data = await _request(
        "POST",
        "/equity/orders/limit",
        acct,
        rate_group="place_order",
        json_body={
            "ticker": ticker.strip(),
            "quantity": quantity,
            "limitPrice": limit_price,
            "timeValidity": time_validity,
        },
    )
    return _fmt(data)


@mcp.tool()
async def place_stop_order(
    account: str,
    ticker: str,
    quantity: float,
    stop_price: float,
    time_validity: str = "Day",
) -> str:
    """Place a stop order.

    Args:
        account: 'invest' or 'stocks_isa'
        ticker: Instrument code
        quantity: Positive to buy, negative to sell
        stop_price: The stop trigger price
        time_validity: 'Day' or 'GTC' (good till cancelled)
    """
    acct = _validate_account(account)
    data = await _request(
        "POST",
        "/equity/orders/stop",
        acct,
        rate_group="place_order",
        json_body={
            "ticker": ticker.strip(),
            "quantity": quantity,
            "stopPrice": stop_price,
            "timeValidity": time_validity,
        },
    )
    return _fmt(data)


@mcp.tool()
async def place_stop_limit_order(
    account: str,
    ticker: str,
    quantity: float,
    stop_price: float,
    limit_price: float,
    time_validity: str = "Day",
) -> str:
    """Place a stop-limit order.

    Args:
        account: 'invest' or 'stocks_isa'
        ticker: Instrument code
        quantity: Positive to buy, negative to sell
        stop_price: The stop trigger price
        limit_price: The limit price (after stop triggers)
        time_validity: 'Day' or 'GTC' (good till cancelled)
    """
    acct = _validate_account(account)
    data = await _request(
        "POST",
        "/equity/orders/stop_limit",
        acct,
        rate_group="place_order",
        json_body={
            "ticker": ticker.strip(),
            "quantity": quantity,
            "stopPrice": stop_price,
            "limitPrice": limit_price,
            "timeValidity": time_validity,
        },
    )
    return _fmt(data)


@mcp.tool()
async def cancel_order(account: str, order_id: str) -> str:
    """Cancel a pending order by its ID.

    Args:
        account: 'invest' or 'stocks_isa'
        order_id: The order ID to cancel (from get_pending_orders)
    """
    acct = _validate_account(account)
    data = await _request(
        "DELETE",
        f"/equity/orders/{order_id.strip()}",
        acct,
        rate_group="cancel_order",
    )
    return _fmt(data)


# ── Research ──


@mcp.tool()
async def search_instruments(query: str, account: str = "invest") -> str:
    """Search for tradeable instruments by name or ticker.

    Returns matching instruments with their codes, names, and types.
    Rate limit: 1 request per 50 seconds.

    Args:
        query: Search term (e.g. 'Apple', 'AAPL', 'Palantir')
        account: 'invest' or 'stocks_isa' (credentials for auth)
    """
    acct = _validate_account(account)
    all_instruments = await _request(
        "GET", "/equity/metadata/instruments", acct, rate_group="instruments"
    )

    if not isinstance(all_instruments, list):
        return _fmt(all_instruments)

    q = query.strip().lower()
    matches = []
    for inst in all_instruments:
        name = str(inst.get("name", "")).lower()
        ticker = str(inst.get("ticker", "")).lower()
        short_name = str(inst.get("shortName", "")).lower()
        if q in name or q in ticker or q in short_name:
            matches.append(inst)

    matches = matches[:25]  # Cap results
    return _fmt({"query": query, "count": len(matches), "instruments": matches})


@mcp.tool()
async def get_exchanges(account: str = "invest") -> str:
    """Get exchange metadata including trading hours.

    Rate limit: 1 request per 30 seconds.

    Args:
        account: 'invest' or 'stocks_isa' (credentials for auth)
    """
    acct = _validate_account(account)
    data = await _request(
        "GET", "/equity/metadata/exchanges", acct, rate_group="exchanges"
    )
    return _fmt(data)


# ── History ──


@mcp.tool()
async def get_order_history(
    account: str = "invest",
    ticker: str | None = None,
    limit: int = 50,
) -> str:
    """Get historical (filled/cancelled) orders.

    Args:
        account: 'invest' or 'stocks_isa'
        ticker: Optional filter by instrument code
        limit: Max orders to return (default 50, max 50 per page)
    """
    acct = _validate_account(account)
    limit = max(1, min(limit, 50))
    params: dict[str, Any] = {"limit": limit}
    if ticker:
        params["ticker"] = ticker.strip()

    data = await _request(
        "GET", "/equity/history/orders", acct, rate_group="history", params=params
    )
    return _fmt(data)


@mcp.tool()
async def get_dividend_history(
    account: str = "invest",
    ticker: str | None = None,
    limit: int = 50,
) -> str:
    """Get dividend payment history.

    Args:
        account: 'invest' or 'stocks_isa'
        ticker: Optional filter by instrument code
        limit: Max records to return (default 50, max 50 per page)
    """
    acct = _validate_account(account)
    limit = max(1, min(limit, 50))
    params: dict[str, Any] = {"limit": limit}
    if ticker:
        params["ticker"] = ticker.strip()

    data = await _request(
        "GET", "/equity/history/dividends", acct, rate_group="history", params=params
    )
    return _fmt(data)


@mcp.tool()
async def get_transaction_history(
    account: str = "invest",
    limit: int = 50,
) -> str:
    """Get account transaction history (deposits, withdrawals, fees, etc.).

    Args:
        account: 'invest' or 'stocks_isa'
        limit: Max records to return (default 50, max 50 per page)
    """
    acct = _validate_account(account)
    limit = max(1, min(limit, 50))
    data = await _request(
        "GET",
        "/equity/history/transactions",
        acct,
        rate_group="history",
        params={"limit": limit},
    )
    return _fmt(data)


@mcp.tool()
async def request_csv_export(
    account: str,
    from_date: str,
    to_date: str,
    include_orders: bool = True,
    include_dividends: bool = True,
    include_transactions: bool = True,
) -> str:
    """Request a CSV export of account data for a date range.

    Returns the export request status. Check back with get_csv_export_status.

    Args:
        account: 'invest' or 'stocks_isa'
        from_date: Start date (ISO format, e.g. '2024-01-01T00:00:00Z')
        to_date: End date (ISO format, e.g. '2024-12-31T23:59:59Z')
        include_orders: Include order history
        include_dividends: Include dividend payments
        include_transactions: Include account transactions
    """
    acct = _validate_account(account)
    data_included: dict[str, bool] = {}
    if include_orders:
        data_included["includedOrders"] = True
    if include_dividends:
        data_included["includedDividends"] = True
    if include_transactions:
        data_included["includedTransactions"] = True

    data = await _request(
        "POST",
        "/equity/history/exports",
        acct,
        rate_group="exports",
        json_body={
            "dataIncluded": data_included,
            "timeFrom": from_date,
            "timeTo": to_date,
        },
    )
    return _fmt(data)


@mcp.tool()
async def get_csv_export_status(account: str = "invest") -> str:
    """Check the status of CSV export requests and get download URLs.

    Args:
        account: 'invest' or 'stocks_isa'
    """
    acct = _validate_account(account)
    data = await _request(
        "GET", "/equity/history/exports", acct, rate_group="exports"
    )
    return _fmt(data)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
