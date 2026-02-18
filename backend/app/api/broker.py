from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.config_store import ACCOUNT_KINDS, ConfigStore
from app.services.t212_client import T212AuthError, T212Error, build_t212_client

router = APIRouter(prefix="/broker", tags=["broker"])


def _check_account(store: ConfigStore, account_kind: Literal["invest", "stocks_isa"]) -> dict:
    broker = store.get_broker()
    creds = store.get_account_credentials(account_kind)

    if not creds.get("enabled", True):
        return {
            "ok": False,
            "account_kind": account_kind,
            "env": broker.get("t212_base_env", "demo"),
            "detail": "account disabled",
        }

    if not creds.get("t212_api_key") or not creds.get("t212_api_secret"):
        return {
            "ok": False,
            "account_kind": account_kind,
            "env": broker.get("t212_base_env", "demo"),
            "detail": "credentials not set",
        }

    client = build_t212_client(store, account_kind=account_kind)
    try:
        summary = client.get_account_summary()
        cash = summary.get("cash") if isinstance(summary.get("cash"), dict) else {}
        return {
            "ok": True,
            "account_kind": account_kind,
            "env": broker.get("t212_base_env", "demo"),
            "currency": summary.get("currency") or summary.get("currencyCode"),
            "availableToTrade": cash.get("availableToTrade"),
            "total": summary.get("total") or summary.get("totalValue") or summary.get("equity"),
            "detail": "authenticated",
        }
    except T212AuthError as exc:
        return {
            "ok": False,
            "account_kind": account_kind,
            "env": broker.get("t212_base_env", "demo"),
            "detail": str(exc),
        }
    except T212Error as exc:
        return {
            "ok": False,
            "account_kind": account_kind,
            "env": broker.get("t212_base_env", "demo"),
            "detail": f"broker error: {exc}",
        }


@router.get("/auth-check")
def auth_check(db: Session = Depends(get_db)) -> dict:
    store = ConfigStore(db)
    result = {kind: _check_account(store, kind) for kind in ACCOUNT_KINDS}
    result["ok_any"] = any(result[k]["ok"] for k in ACCOUNT_KINDS)
    return result


@router.get("/auth-check/{account_kind}")
def auth_check_single(account_kind: Literal["invest", "stocks_isa"], db: Session = Depends(get_db)) -> dict:
    store = ConfigStore(db)
    return _check_account(store, account_kind)
