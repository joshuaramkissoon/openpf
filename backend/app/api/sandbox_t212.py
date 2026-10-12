"""Mock Trading 212 v0 API served in-process (see ``services/sandbox_broker``).

Mounted at ``/sandbox-t212/api/v0`` only when sandbox mode is enabled, so a real
deployment never exposes it.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.services import sandbox_broker as sbx

router = APIRouter(prefix=sbx.SANDBOX_PREFIX, tags=["sandbox"], include_in_schema=False)


def _err(exc: sbx.SandboxError) -> JSONResponse:
    return JSONResponse({"code": "SandboxError", "message": exc.message}, status_code=exc.status)


def _auth(request: Request) -> tuple[str, str]:
    return sbx.authenticate(request.headers.get("authorization"))


def _params(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.query_params.items()}


@router.get("/equity/account/summary")
def summary(request: Request) -> Any:
    try:
        account, _ = _auth(request)
        return sbx.account_summary(account)
    except sbx.SandboxError as exc:
        return _err(exc)


@router.get("/equity/positions")
def positions(request: Request) -> Any:
    try:
        account, _ = _auth(request)
        return sbx.positions(account)
    except sbx.SandboxError as exc:
        return _err(exc)


@router.get("/equity/orders")
def pending(request: Request) -> Any:
    try:
        account, _ = _auth(request)
        return sbx.pending_orders(account)
    except sbx.SandboxError as exc:
        return _err(exc)


@router.get("/equity/history/orders")
def order_history(request: Request) -> Any:
    try:
        account, _ = _auth(request)
        return sbx.order_history(account, _params(request))
    except sbx.SandboxError as exc:
        return _err(exc)


@router.get("/equity/history/dividends")
def dividends(request: Request) -> Any:
    try:
        account, _ = _auth(request)
        return sbx.dividends(account, _params(request))
    except sbx.SandboxError as exc:
        return _err(exc)


@router.get("/equity/history/transactions")
@router.get("/history/transactions")
def transactions(request: Request) -> Any:
    try:
        account, _ = _auth(request)
        return sbx.transactions(account, _params(request))
    except sbx.SandboxError as exc:
        return _err(exc)


@router.get("/equity/metadata/instruments")
def instruments(request: Request) -> Any:
    try:
        _auth(request)
        return sbx.instruments()
    except sbx.SandboxError as exc:
        return _err(exc)


@router.get("/equity/metadata/exchanges")
def exchanges(request: Request) -> Any:
    try:
        _auth(request)
        return sbx.exchanges()
    except sbx.SandboxError as exc:
        return _err(exc)


@router.post("/equity/orders/{kind}")
async def place(kind: str, request: Request) -> Any:
    if kind not in {"market", "limit", "stop", "stop_limit"}:
        return JSONResponse({"message": "unknown order type"}, status_code=404)
    try:
        account, scope = _auth(request)
        body = await request.json()
        return sbx.place_order(account, scope, kind, body)
    except sbx.SandboxError as exc:
        return _err(exc)


@router.delete("/equity/orders/{order_id}")
def cancel(order_id: str, request: Request) -> Any:
    try:
        account, scope = _auth(request)
        sbx.cancel_order(account, scope, order_id)
        return Response(status_code=200)
    except sbx.SandboxError as exc:
        return _err(exc)


@router.post("/reset")
def reset() -> Any:
    sbx.reset()
    return {"status": "reset"}
