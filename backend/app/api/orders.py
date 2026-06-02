"""Broker-side order visibility + control (Trading 212).

Surfaces live/pending orders, order history, and manual cancellation — plus the
execution-key health that the IP-restricted write key needs. Reads use the
IP-unrestricted read key; cancellation uses the dedicated execution key. Every
write/error funnels through ``classify_t212_error`` for a typed UI envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.orders import (
    AccountError,
    AccountExecutionHealth,
    CancelOrderResponse,
    ExecKeyTestRequest,
    ExecKeyTestResponse,
    ExecKeyTestResult,
    ExecutionHealthResponse,
    OrderItem,
    OrdersResponse,
)
from app.services.config_store import ACCOUNT_KINDS, AccountKind, ConfigStore
from app.services.network_info import get_egress_ip
from app.services.portfolio_service import _instrument_meta_map
from app.services.t212_client import T212Error, build_t212_client
from app.services.t212_errors import (
    CODE_AUTH_FAILED,
    CODE_IP_RESTRICTED,
    CODE_VALIDATION,
    ClassifiedError,
    classify_t212_error,
)

router = APIRouter(prefix="/orders", tags=["orders"])

SingleAccount = Literal["invest", "stocks_isa"]


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ── instrument name map (best-effort) ──────────────────────────────────────


def _name_map(db: Session) -> dict[str, str]:
    """{UPPER instrument code: human name}, reusing the portfolio service's
    memoized T212 bulk-metadata resolver (one call, ~6h cache). Best-effort —
    returns {} if metadata is unavailable so order views never block on names."""
    try:
        meta = _instrument_meta_map(db)
        return {code: info.get("name", "") for code, info in meta.items() if info.get("name")}
    except Exception:  # noqa: BLE001 — name enrichment is optional
        return {}


# ── normalisation ──────────────────────────────────────────────────────────


def _f(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _get(d: dict, *keys: str) -> Any:
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d.get(k)
    return None


def _side(quantity: Any) -> str | None:
    q = _f(quantity)
    if q is None:
        return None
    return "buy" if q >= 0 else "sell"


def _normalize_pending(account: str, o: dict, names: dict[str, str]) -> OrderItem:
    ticker = str(_get(o, "ticker", "instrumentCode") or "") or None
    qty = _get(o, "quantity")
    return OrderItem(
        account_kind=account,
        order_id=str(_get(o, "id", "orderId") or "") or None,
        ticker=ticker,
        name=names.get((ticker or "").upper()) if ticker else None,
        side=_side(qty),
        type=str(_get(o, "type", "orderType") or "") or None,
        quantity=_f(qty),
        filled_quantity=_f(_get(o, "filledQuantity", "filledValue")),
        limit_price=_f(_get(o, "limitPrice")),
        stop_price=_f(_get(o, "stopPrice")),
        status=str(_get(o, "status") or "") or None,
        value=_f(_get(o, "value")),
        created_at=_get(o, "creationTime", "dateCreated", "dateModified"),
        raw=o if isinstance(o, dict) else {},
    )


def _normalize_history(account: str, item: dict, names: dict[str, str]) -> OrderItem:
    # History items come as {"order": {...}, "fill": {...}}; tolerate a flat shape.
    order = item.get("order") if isinstance(item.get("order"), dict) else item
    fill = item.get("fill") if isinstance(item.get("fill"), dict) else {}
    ticker = str(_get(order, "ticker", "instrumentCode") or "") or None
    qty = _get(order, "quantity") or _get(fill, "quantity")
    wallet = fill.get("walletImpact") if isinstance(fill.get("walletImpact"), dict) else {}
    return OrderItem(
        account_kind=account,
        order_id=str(_get(order, "id", "orderId") or "") or None,
        ticker=ticker,
        name=names.get((ticker or "").upper()) if ticker else None,
        side=_side(qty),
        type=str(_get(order, "type", "orderType") or "") or None,
        quantity=_f(qty),
        filled_quantity=_f(_get(fill, "quantity")),
        limit_price=_f(_get(order, "limitPrice")),
        stop_price=_f(_get(order, "stopPrice")),
        fill_price=_f(_get(fill, "price")),
        status=str(_get(order, "status") or _get(fill, "status") or "") or None,
        value=_f(_get(wallet, "netValue") or _get(fill, "value")),
        created_at=_get(order, "dateCreated", "creationTime", "dateModified") or _get(fill, "date", "filledAt"),
        raw=item if isinstance(item, dict) else {},
    )


def _resolve_accounts(account: str, store: ConfigStore) -> list[AccountKind]:
    value = (account or "all").strip().lower()
    if value == "all":
        configured = [
            k for k in ACCOUNT_KINDS
            if str(store.get_account_credentials(k).get("t212_api_key", "")).strip()
        ]
        return configured or list(ACCOUNT_KINDS)
    if value not in ACCOUNT_KINDS:
        raise HTTPException(status_code=400, detail=f"invalid account '{account}'")
    return [value]  # type: ignore[list-item]


def _resolve_instrument_codes(term: str, db: Session, *, limit: int = 3) -> list[str]:
    """Map a user filter term to full T212 instrument code(s) for the server-side
    ``ticker`` history filter. Prefers an exact symbol/code match (so 'nvda' →
    'NVDA_US_EQ'); falls back to substring on code or name. Capped to keep the
    number of per-ticker history calls small."""
    q = (term or "").strip().upper()
    if len(q) < 1:
        return []
    meta = _instrument_meta_map(db)
    exact: list[str] = []
    partial: list[str] = []
    for code, info in meta.items():
        symbol = code.split("_", 1)[0]
        name = str(info.get("name", "")).upper()
        if symbol == q or code == q:
            exact.append(code)
        elif q in code or q in name:
            partial.append(code)
    # Exact matches win; otherwise shortest codes first (closest match).
    codes = exact or sorted(partial, key=len)
    return codes[:limit]


# ── endpoints ────────────────────────────────────────────────────────────


@router.get("/pending", response_model=OrdersResponse)
def pending_orders(account: str = Query("all"), db: Session = Depends(get_db)) -> OrdersResponse:
    store = ConfigStore(db)
    names = _name_map(db)
    orders: list[OrderItem] = []
    errors: list[AccountError] = []
    for kind in _resolve_accounts(account, store):
        try:
            client = build_t212_client(store, account_kind=kind)  # read key
            for o in client.get_pending_orders():
                orders.append(_normalize_pending(kind, o, names))
        except Exception as exc:  # noqa: BLE001 — surface per-account, keep the rest
            c = classify_t212_error(exc, account_kind=kind)
            errors.append(AccountError(account_kind=kind, code=c.code, message=c.message))
    # Newest first across accounts (ISO-8601 strings sort chronologically; None last).
    orders.sort(key=lambda o: o.created_at or "", reverse=True)
    return OrdersResponse(orders=orders, errors=errors)


@router.get("/history", response_model=OrdersResponse)
def order_history(
    account: str = Query("all"),
    ticker: str | None = Query(None),
    limit: int = Query(50, ge=1, le=50),
    cursor: str | None = Query(None),
    db: Session = Depends(get_db),
) -> OrdersResponse:
    store = ConfigStore(db)
    names = _name_map(db)
    orders: list[OrderItem] = []
    errors: list[AccountError] = []
    next_cursors: dict[str, str | None] = {}
    seen: set[tuple[str, str]] = set()
    filtering = bool(ticker and ticker.strip())

    # With a filter term: resolve it to instrument code(s) and use T212's
    # server-side `ticker` filter to pull that instrument's FULL history (cheap —
    # one instrument has few orders), so no cursor paging is needed. Without a
    # term: one page, with a per-account cursor for "Load more". A `cursor` only
    # applies when one account was requested (the frontend pages "all" by issuing
    # one call per account).
    codes = _resolve_instrument_codes(ticker, db) if filtering else []
    requested = (account or "").strip().lower()

    for kind in _resolve_accounts(account, store):
        try:
            client = build_t212_client(store, account_kind=kind)  # read key
            if filtering:
                items: list[dict] = []
                for code in codes:
                    items.extend(client.get_orders_for_ticker(code))
            else:
                acct_cursor = cursor if requested == kind else None
                items, next_cursors[kind] = client.get_orders_history_page(limit=limit, cursor=acct_cursor)
            for item in items:
                normalized = _normalize_history(kind, item, names)
                key = (kind, normalized.order_id or "")
                if normalized.order_id and key in seen:
                    continue
                seen.add(key)
                orders.append(normalized)
        except Exception as exc:  # noqa: BLE001
            c = classify_t212_error(exc, account_kind=kind)
            errors.append(AccountError(account_kind=kind, code=c.code, message=c.message))
            next_cursors[kind] = None
    # Newest first across accounts (was grouped by account before this sort).
    orders.sort(key=lambda o: o.created_at or "", reverse=True)
    return OrdersResponse(orders=orders, errors=errors, next_cursors=next_cursors)


@router.delete("/{order_id}", response_model=CancelOrderResponse)
def cancel_order(
    order_id: str,
    account: SingleAccount = Query(...),
    db: Session = Depends(get_db),
) -> CancelOrderResponse:
    store = ConfigStore(db)
    exec_creds = store.get_account_exec_credentials(account)
    if not exec_creds.get("exec_enabled", True):
        raise HTTPException(
            status_code=400,
            detail=ClassifiedError(
                CODE_VALIDATION,
                f"live execution is disabled for {account} (enable it in Settings)",
                {"account_kind": account},
            ).as_detail(),
        )
    if not exec_creds.get("t212_api_key") or not exec_creds.get("t212_api_secret"):
        raise HTTPException(
            status_code=400,
            detail=ClassifiedError(
                CODE_VALIDATION,
                f"no execution key configured for {account} (add it in Settings → Credentials)",
                {"account_kind": account},
            ).as_detail(),
        )
    client = build_t212_client(store, account_kind=account, purpose="execute")
    try:
        client.cancel_order(order_id.strip())
    except (T212Error, Exception) as exc:  # noqa: BLE001
        classified = classify_t212_error(exc, account_kind=account)
        raise HTTPException(status_code=classified.status_code, detail=classified.as_detail()) from exc
    return CancelOrderResponse(ok=True, order_id=order_id, account_kind=account, message="order cancelled")


def _probe_exec_key(store: ConfigStore, account: AccountKind) -> ExecKeyTestResult:
    creds = store.get_account_exec_credentials(account)
    if not creds.get("t212_api_key") or not creds.get("t212_api_secret"):
        return ExecKeyTestResult(
            result="not_configured",
            message="No execution key set for this account.",
            checked_at=_now_iso(),
        )
    client = build_t212_client(store, account_kind=account, purpose="execute")
    try:
        # Lightweight authenticated GET using the exec key — confirms the key is
        # valid AND that this machine's IP is allowed by its restriction.
        client.get_account_summary()
        return ExecKeyTestResult(
            result="ok",
            message="Execution key authenticated from this IP.",
            checked_at=_now_iso(),
        )
    except Exception as exc:  # noqa: BLE001
        classified = classify_t212_error(exc, account_kind=account)
        result = {
            CODE_IP_RESTRICTED: "ip_restricted",
            CODE_AUTH_FAILED: "auth_failed",
        }.get(classified.code, "error")
        return ExecKeyTestResult(
            result=result,  # type: ignore[arg-type]
            code=classified.code,
            message=classified.message,
            checked_at=_now_iso(),
        )


@router.get("/execution-health", response_model=ExecutionHealthResponse)
def execution_health(db: Session = Depends(get_db)) -> ExecutionHealthResponse:
    store = ConfigStore(db)
    broker = store.get_broker()
    public = store.credentials_public()
    status_map = store.get_execution_status() or {}

    accounts: dict[str, AccountExecutionHealth] = {}
    for kind in ACCOUNT_KINDS:
        p = public.get(kind, {})
        last = status_map.get(kind)
        last_test = ExecKeyTestResult(**last) if isinstance(last, dict) else ExecKeyTestResult(result="untested")
        accounts[kind] = AccountExecutionHealth(
            account_kind=kind,
            read_configured=bool(p.get("configured")),
            exec_configured=bool(p.get("exec_configured")),
            exec_enabled=bool(p.get("exec_enabled", True)),
            last_test=last_test,
        )

    return ExecutionHealthResponse(
        broker_mode=str(broker.get("broker_mode", "paper")),
        base_env=str(broker.get("t212_base_env", "demo")),
        egress_ip=get_egress_ip(),
        accounts=accounts,
    )


@router.post("/execution-test", response_model=ExecKeyTestResponse)
def execution_test(payload: ExecKeyTestRequest, db: Session = Depends(get_db)) -> ExecKeyTestResponse:
    store = ConfigStore(db)
    account = payload.account_kind
    test = _probe_exec_key(store, account)
    store.set_execution_status(account, test.model_dump())
    return ExecKeyTestResponse(account_kind=account, egress_ip=get_egress_ip(force=True), test=test)
