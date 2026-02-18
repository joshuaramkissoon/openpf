from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services.claude_sdk_config import project_root
from app.services.config_store import ConfigStore
from app.services.t212_client import T212Error, build_t212_client


def _instrument_dir() -> Path:
    root = project_root()
    out = root / ".claude" / "runtime" / "memory" / "instruments"
    out.mkdir(parents=True, exist_ok=True)
    return out


def instrument_cache_paths() -> dict[str, Path]:
    base = _instrument_dir()
    return {
        "all": base / "all-instruments.json",
        "meta": base / "cache-meta.json",
    }


def refresh_instrument_cache(db: Session) -> dict[str, Any]:
    store = ConfigStore(db)
    enabled = store.enabled_account_kinds()
    if not enabled:
        raise RuntimeError("No Trading 212 account configured for instrument cache sync")

    combined: dict[str, dict[str, Any]] = {}
    fetched_accounts: list[str] = []

    for account_kind in enabled:
        client = build_t212_client(store, account_kind=account_kind)
        try:
            items = client.get_instruments_metadata()
        except T212Error as exc:
            continue

        fetched_accounts.append(account_kind)
        for row in items:
            ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
            if not ticker:
                continue
            current = combined.get(ticker)
            normalized = {
                "ticker": ticker,
                "shortName": row.get("shortName") or row.get("symbol") or ticker,
                "name": row.get("name") or row.get("description") or ticker,
                "type": row.get("type") or row.get("instrumentType") or "",
                "exchange": row.get("exchange") or row.get("exchangeCode") or "",
                "currency": row.get("currency") or row.get("currencyCode") or "",
                "isin": row.get("isin") or "",
                "workingScheduleId": row.get("workingScheduleId"),
                "minTradeQuantity": row.get("minTradeQuantity"),
                "maxOpenQuantity": row.get("maxOpenQuantity"),
                "accountKinds": sorted({*(current or {}).get("accountKinds", []), account_kind}),
            }
            combined[ticker] = normalized

    if not combined:
        raise RuntimeError("Failed to fetch instruments from Trading 212")

    rows = sorted(combined.values(), key=lambda item: item.get("ticker") or "")
    by_type = Counter(str(row.get("type") or "UNKNOWN") for row in rows)
    by_exchange = Counter(str(row.get("exchange") or "UNKNOWN") for row in rows)

    now = datetime.now(tz=timezone.utc).isoformat()
    paths = instrument_cache_paths()

    paths["all"].write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    meta = {
        "updated_at": now,
        "total_instruments": len(rows),
        "accounts": fetched_accounts,
        "types": dict(by_type.most_common()),
        "exchanges": dict(by_exchange.most_common()),
    }
    paths["meta"].write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "updated_at": now,
        "total_instruments": len(rows),
        "accounts": fetched_accounts,
        "paths": {k: str(v) for k, v in paths.items()},
    }
