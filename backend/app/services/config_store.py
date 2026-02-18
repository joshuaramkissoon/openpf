from __future__ import annotations

import base64
from copy import deepcopy
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import AppConfig

settings = get_settings()

AccountKind = Literal["invest", "stocks_isa"]
ACCOUNT_KINDS: tuple[AccountKind, ...] = ("invest", "stocks_isa")

RISK_DEFAULT = {
    "max_single_order_notional": settings.max_single_order_notional,
    "max_daily_notional": settings.max_daily_notional,
    "max_position_weight": settings.max_position_weight,
    "duplicate_order_window_seconds": settings.duplicate_order_window_seconds,
}

BROKER_DEFAULT = {
    "broker_mode": settings.broker_mode,
    "autopilot_enabled": settings.autopilot_enabled,
    "t212_base_env": settings.t212_base_env,
}

WATCHLIST_DEFAULT = {
    "symbols": ["SPY", "QQQ", "MSFT", "AAPL", "NVDA", "AMZN", "GOOGL", "META"],
}

CREDENTIALS_DEFAULT = {
    "invest": {
        "t212_api_key": str(settings.t212_invest_api_key or settings.t212_api_key_invest or settings.t212_api_key or "").strip(),
        "t212_api_secret": str(settings.t212_invest_api_secret or settings.t212_api_secret_invest or settings.t212_api_secret or "").strip(),
        "enabled": True,
    },
    "stocks_isa": {
        "t212_api_key": str(settings.t212_stocks_isa_api_key or settings.t212_api_key_stocks_isa or "").strip(),
        "t212_api_secret": str(settings.t212_stocks_isa_api_secret or settings.t212_api_secret_stocks_isa or "").strip(),
        "enabled": True,
    },
}

TELEGRAM_DEFAULT = {
    "enabled": False,
    "poll_enabled": True,
    "chat_id": "",
    "bot_token": "",
    "high_conviction_threshold": 0.68,
    "notify_general_updates": True,
    "allowed_user_ids": [],
}

LEVERAGED_DEFAULT = {
    "enabled": True,
    "account_kind": "stocks_isa",
    "auto_execute_enabled": False,
    "per_position_notional": 200.0,
    "max_total_exposure": 600.0,
    "max_open_positions": 3,
    "take_profit_pct": 0.08,
    "stop_loss_pct": 0.05,
    "close_time_uk": "15:30",
    "allow_overnight": False,
    "scan_symbols": ["SPY", "QQQ", "NVDA", "PLTR", "TSLA", "SOXL", "TQQQ", "SQQQ"],
    "instrument_priority": ["3USL", "3ULS", "LQQ3", "3NVD", "3PLT"],
}


class ConfigStore:
    def __init__(self, db: Session):
        self.db = db

    def _get_or_create(self, key: str, default_value: dict[str, Any]) -> AppConfig:
        record = self.db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
        if record:
            return record

        record = AppConfig(key=key, value=deepcopy(default_value))
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def get(self, key: str, default_value: dict[str, Any]) -> dict[str, Any]:
        record = self._get_or_create(key, default_value)
        return record.value or deepcopy(default_value)

    def set(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        record = self._get_or_create(key, value)
        record.value = value
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record.value

    def get_risk(self) -> dict[str, Any]:
        return self.get("risk_config", RISK_DEFAULT)

    def set_risk(self, value: dict[str, Any]) -> dict[str, Any]:
        merged = {**self.get_risk(), **value}
        return self.set("risk_config", merged)

    def get_broker(self) -> dict[str, Any]:
        return self.get("broker_config", BROKER_DEFAULT)

    def set_broker(self, value: dict[str, Any]) -> dict[str, Any]:
        merged = {**self.get_broker(), **value}
        return self.set("broker_config", merged)

    def get_watchlist(self) -> dict[str, Any]:
        return self.get("watchlist", WATCHLIST_DEFAULT)

    def set_watchlist(self, value: dict[str, Any]) -> dict[str, Any]:
        symbols = [s.strip().upper() for s in value.get("symbols", []) if s.strip()]
        return self.set("watchlist", {"symbols": symbols})

    @staticmethod
    def _normalize_credentials_fields(api_key: str, api_secret: str) -> dict[str, str]:
        key = (api_key or "").strip()
        secret = (api_secret or "").strip()

        # Case 1: pasted "API_KEY:API_SECRET"
        if ":" in key and not secret and not key.lower().startswith("basic "):
            left, right = key.split(":", 1)
            key = left.strip()
            secret = right.strip()

        # Case 2: pasted "Basic <base64(key:secret)>"
        if key.lower().startswith("basic ") and not secret:
            raw = key[6:].strip()
            try:
                decoded = base64.b64decode(raw).decode("utf-8")
                if ":" in decoded:
                    left, right = decoded.split(":", 1)
                    key = left.strip()
                    secret = right.strip()
            except Exception:
                pass

        key = key.replace("\n", "").replace("\r", "").strip()
        secret = secret.replace("\n", "").replace("\r", "").strip()
        return {"t212_api_key": key, "t212_api_secret": secret}

    def _normalize_credentials_map(self, value: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = deepcopy(CREDENTIALS_DEFAULT)

        # Legacy single-account support.
        if "t212_api_key" in value or "t212_api_secret" in value:
            normalized = self._normalize_credentials_fields(
                str(value.get("t212_api_key", "")),
                str(value.get("t212_api_secret", "")),
            )
            merged["invest"] = {
                **merged["invest"],
                **normalized,
                "enabled": bool(value.get("enabled", True)),
            }
            return merged

        for kind in ACCOUNT_KINDS:
            entry = value.get(kind, {}) if isinstance(value.get(kind), dict) else {}
            normalized = self._normalize_credentials_fields(
                str(entry.get("t212_api_key", merged[kind]["t212_api_key"])),
                str(entry.get("t212_api_secret", merged[kind]["t212_api_secret"])),
            )
            merged[kind] = {
                **merged[kind],
                **normalized,
                "enabled": bool(entry.get("enabled", merged[kind].get("enabled", True))),
            }

        return merged

    def get_credentials(self) -> dict[str, Any]:
        current = self.get("credentials", CREDENTIALS_DEFAULT)
        normalized = self._normalize_credentials_map(current)
        # If account-specific credentials are provided through environment variables,
        # treat them as source-of-truth so stale DB values cannot shadow them.
        env_defaults = self._normalize_credentials_map(CREDENTIALS_DEFAULT)
        changed = normalized != current

        for kind in ACCOUNT_KINDS:
            env_entry = env_defaults.get(kind, {})
            env_key = str(env_entry.get("t212_api_key", "")).strip()
            env_secret = str(env_entry.get("t212_api_secret", "")).strip()
            if env_key and env_secret:
                if str(normalized[kind].get("t212_api_key", "")).strip() != env_key:
                    normalized[kind]["t212_api_key"] = env_key
                    changed = True
                if str(normalized[kind].get("t212_api_secret", "")).strip() != env_secret:
                    normalized[kind]["t212_api_secret"] = env_secret
                    changed = True
                if "enabled" not in normalized[kind]:
                    normalized[kind]["enabled"] = bool(env_entry.get("enabled", True))
                    changed = True

        if changed:
            self.set("credentials", normalized)
        return normalized

    def set_credentials(self, value: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_credentials_map(value)
        return self.set("credentials", normalized)

    def set_account_credentials(self, account_kind: AccountKind, value: dict[str, Any]) -> dict[str, Any]:
        all_creds = self.get_credentials()
        entry = all_creds.get(account_kind, {})
        incoming_key = value.get("t212_api_key")
        incoming_secret = value.get("t212_api_secret")
        key_value = entry.get("t212_api_key", "") if incoming_key in (None, "") else incoming_key
        secret_value = entry.get("t212_api_secret", "") if incoming_secret in (None, "") else incoming_secret
        normalized = self._normalize_credentials_fields(
            str(key_value),
            str(secret_value),
        )
        all_creds[account_kind] = {
            **entry,
            **normalized,
            "enabled": bool(value.get("enabled", entry.get("enabled", True))),
        }
        return self.set_credentials(all_creds)

    def get_account_credentials(self, account_kind: AccountKind) -> dict[str, Any]:
        creds = self.get_credentials()
        return creds.get(account_kind, {"t212_api_key": "", "t212_api_secret": "", "enabled": False})

    def credentials_public(self) -> dict[str, Any]:
        creds = self.get_credentials()
        out: dict[str, Any] = {}
        for kind in ACCOUNT_KINDS:
            entry = creds.get(kind, {})
            out[kind] = {
                "account_kind": kind,
                "enabled": bool(entry.get("enabled", True)),
                "configured": bool(str(entry.get("t212_api_key", "")).strip() and str(entry.get("t212_api_secret", "")).strip()),
            }
        return out

    def enabled_account_kinds(self) -> list[AccountKind]:
        creds = self.get_credentials()
        enabled: list[AccountKind] = []
        for kind in ACCOUNT_KINDS:
            entry = creds.get(kind, {})
            configured = bool(str(entry.get("t212_api_key", "")).strip() and str(entry.get("t212_api_secret", "")).strip())
            if bool(entry.get("enabled", True)) and configured:
                enabled.append(kind)
        return enabled

    def get_telegram(self) -> dict[str, Any]:
        return self.get("telegram_config", TELEGRAM_DEFAULT)

    def set_telegram(self, value: dict[str, Any]) -> dict[str, Any]:
        current = self.get_telegram()
        merged = {**current, **value}
        if value.get("bot_token") in (None, ""):
            merged["bot_token"] = current.get("bot_token", "")

        user_ids = []
        for raw in merged.get("allowed_user_ids", []):
            try:
                user_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        merged["allowed_user_ids"] = sorted(set(user_ids))

        merged["chat_id"] = str(merged.get("chat_id", "")).strip()
        merged["enabled"] = bool(merged.get("enabled", False))
        merged["poll_enabled"] = bool(merged.get("poll_enabled", True))
        merged["notify_general_updates"] = bool(merged.get("notify_general_updates", True))
        merged["high_conviction_threshold"] = float(merged.get("high_conviction_threshold", 0.68))
        return self.set("telegram_config", merged)

    def telegram_public(self) -> dict[str, Any]:
        cfg = self.get_telegram()
        return {
            "enabled": bool(cfg.get("enabled")),
            "poll_enabled": bool(cfg.get("poll_enabled", True)),
            "chat_id": str(cfg.get("chat_id", "")),
            "high_conviction_threshold": float(cfg.get("high_conviction_threshold", 0.68)),
            "notify_general_updates": bool(cfg.get("notify_general_updates", True)),
            "allowed_user_ids": [int(x) for x in cfg.get("allowed_user_ids", [])],
            "bot_token_configured": bool(str(cfg.get("bot_token", "")).strip()),
        }

    def get_leveraged(self) -> dict[str, Any]:
        current = self.get("leveraged_config", LEVERAGED_DEFAULT)
        merged = {**LEVERAGED_DEFAULT, **(current or {})}
        merged["enabled"] = bool(merged.get("enabled", True))
        merged["auto_execute_enabled"] = bool(merged.get("auto_execute_enabled", False))
        # Leveraged execution rail: ISA only.
        merged["account_kind"] = "stocks_isa"
        merged["per_position_notional"] = float(merged.get("per_position_notional", 200.0))
        merged["max_total_exposure"] = float(merged.get("max_total_exposure", 600.0))
        merged["max_open_positions"] = int(merged.get("max_open_positions", 3))
        merged["take_profit_pct"] = float(merged.get("take_profit_pct", 0.08))
        merged["stop_loss_pct"] = float(merged.get("stop_loss_pct", 0.05))
        merged["close_time_uk"] = str(merged.get("close_time_uk", "15:30")).strip() or "15:30"
        merged["allow_overnight"] = bool(merged.get("allow_overnight", False))
        merged["scan_symbols"] = [str(x).strip().upper() for x in merged.get("scan_symbols", []) if str(x).strip()]
        merged["instrument_priority"] = [
            str(x).strip().upper() for x in merged.get("instrument_priority", []) if str(x).strip()
        ]
        return merged

    def set_leveraged(self, value: dict[str, Any]) -> dict[str, Any]:
        merged = {**self.get_leveraged(), **value}
        stored = self.set("leveraged_config", merged)
        # Re-read through normalizer for clamps / symbol cleanup.
        normalized = {**LEVERAGED_DEFAULT, **(stored or {})}
        normalized["enabled"] = bool(normalized.get("enabled", True))
        normalized["auto_execute_enabled"] = bool(normalized.get("auto_execute_enabled", False))
        normalized["account_kind"] = "stocks_isa"
        normalized["per_position_notional"] = float(normalized.get("per_position_notional", 200.0))
        normalized["max_total_exposure"] = float(normalized.get("max_total_exposure", 600.0))
        normalized["max_open_positions"] = int(normalized.get("max_open_positions", 3))
        normalized["take_profit_pct"] = float(normalized.get("take_profit_pct", 0.08))
        normalized["stop_loss_pct"] = float(normalized.get("stop_loss_pct", 0.05))
        normalized["close_time_uk"] = str(normalized.get("close_time_uk", "15:30")).strip() or "15:30"
        normalized["allow_overnight"] = bool(normalized.get("allow_overnight", False))
        normalized["scan_symbols"] = [str(x).strip().upper() for x in normalized.get("scan_symbols", []) if str(x).strip()]
        normalized["instrument_priority"] = [
            str(x).strip().upper() for x in normalized.get("instrument_priority", []) if str(x).strip()
        ]
        return self.set("leveraged_config", normalized)

    def assembled_public(self) -> dict[str, Any]:
        risk = self.get_risk()
        broker = self.get_broker()
        watchlist = self.get_watchlist().get("symbols", [])
        telegram = self.telegram_public()
        credentials = self.credentials_public()
        leveraged = self.get_leveraged()
        return {
            "risk": risk,
            "broker": broker,
            "watchlist": watchlist,
            "telegram": telegram,
            "credentials": credentials,
            "leveraged": leveraged,
        }
