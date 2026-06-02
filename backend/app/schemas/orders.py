from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

AccountKind = Literal["invest", "stocks_isa"]


class OrderItem(BaseModel):
    account_kind: str
    order_id: str | None = None
    ticker: str | None = None
    name: str | None = None
    side: str | None = None  # "buy" | "sell" (derived from signed quantity)
    type: str | None = None  # MARKET | LIMIT | STOP | STOP_LIMIT
    quantity: float | None = None
    filled_quantity: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    fill_price: float | None = None
    status: str | None = None
    value: float | None = None
    created_at: str | None = None
    raw: dict[str, Any] = {}


class AccountError(BaseModel):
    account_kind: str
    code: str
    message: str


class OrdersResponse(BaseModel):
    orders: list[OrderItem]
    errors: list[AccountError] = []


class CancelOrderResponse(BaseModel):
    ok: bool
    order_id: str
    account_kind: str
    message: str


class ExecKeyTestResult(BaseModel):
    result: Literal["ok", "ip_restricted", "auth_failed", "error", "not_configured", "untested"]
    code: str | None = None
    message: str | None = None
    checked_at: str | None = None


class AccountExecutionHealth(BaseModel):
    account_kind: str
    read_configured: bool
    exec_configured: bool
    exec_enabled: bool
    last_test: ExecKeyTestResult


class ExecutionHealthResponse(BaseModel):
    broker_mode: str  # paper | live
    base_env: str  # demo | live
    egress_ip: str | None = None
    accounts: dict[str, AccountExecutionHealth]


class ExecKeyTestRequest(BaseModel):
    account_kind: AccountKind


class ExecKeyTestResponse(BaseModel):
    account_kind: str
    egress_ip: str | None = None
    test: ExecKeyTestResult
