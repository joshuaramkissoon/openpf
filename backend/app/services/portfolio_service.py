from __future__ import annotations

import re
from datetime import datetime
from threading import Lock
from typing import Any, Literal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import AccountSnapshot, PositionSnapshot
from app.services.analytics import (
    concentration_hhi,
    estimate_portfolio_beta,
    estimated_portfolio_volatility,
    signal_for_symbol,
)
from app.services.config_store import ACCOUNT_KINDS, ConfigStore
from app.services.fx import get_fx_rate
from app.services.t212_client import T212AuthError, T212Error, T212RateLimitError, build_t212_client, normalize_instrument_code

AccountViewKind = Literal["all", "invest", "stocks_isa"]
settings = get_settings()

_refresh_lock = Lock()
_last_refresh_ts: datetime | None = None
_refresh_cooldown_seconds = 6

_SYMBOL_RE = re.compile(r"[^A-Z0-9_.-]+")
_TICKER_FROM_DICT_STR_RE = re.compile(r"[\"']?TICKER[\"']?\s*:\s*[\"']([^\"']+)[\"']", re.IGNORECASE)


def _mock_portfolio() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    account = {
        "currency": "USD",
        "cash": {"availableToTrade": 2125.37},
        "invested": 14890.12,
        "total": 17015.49,
        "result": 1963.41,
    }

    positions = [
        {"ticker": "AAPL_US_EQ", "quantity": 28, "averagePrice": 168.2, "currentPrice": 191.6, "ppl": 655.2},
        {"ticker": "MSFT_US_EQ", "quantity": 12, "averagePrice": 358.1, "currentPrice": 422.8, "ppl": 776.4},
        {"ticker": "NVDA_US_EQ", "quantity": 16, "averagePrice": 86.7, "currentPrice": 128.4, "ppl": 667.2},
        {"ticker": "QQQ_US_EQ", "quantity": 7, "averagePrice": 375.2, "currentPrice": 438.5, "ppl": 443.1},
        {"ticker": "XOM_US_EQ", "quantity": 20, "averagePrice": 109.6, "currentPrice": 114.2, "ppl": 92.0},
    ]
    return account, positions


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        for symbol in ("$", "£", "€"):
            cleaned = cleaned.replace(symbol, "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in (
            "value",
            "amount",
            "price",
            "current",
            "free",
            "total",
            "invested",
            "result",
            "ppl",
            "available",
            "availableToTrade",
        ):
            nested = _coerce_float(value.get(key))
            if nested is not None:
                return nested
    return None


def _first_float(*values: Any, default: float = 0.0) -> float:
    for value in values:
        parsed = _coerce_float(value)
        if parsed is not None:
            return parsed
    return default


def _extract_symbol_from_candidate(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, dict):
        for key in ("ticker", "symbol", "instrumentCode", "instrument", "code", "name"):
            extracted = _extract_symbol_from_candidate(value.get(key))
            if extracted:
                return extracted
        return None

    raw = str(value).strip().upper()
    if not raw:
        return None

    if raw.startswith("{") and raw.endswith("}"):
        match = _TICKER_FROM_DICT_STR_RE.search(raw)
        if match:
            raw = match.group(1).strip().upper()

    cleaned = _SYMBOL_RE.sub("", raw.replace(" ", ""))
    if not cleaned:
        return None
    return cleaned


def _extract_symbols(position: dict[str, Any]) -> tuple[str, str]:
    for key in ("instrumentCode", "ticker", "instrument", "symbol"):
        extracted = _extract_symbol_from_candidate(position.get(key))
        if not extracted:
            continue
        code = normalize_instrument_code(extracted)
        ticker = code.split("_")[0]
        return ticker, code

    raise ValueError(f"Position payload missing ticker/symbol: {position}")


def _extract_price(position: dict[str, Any]) -> float:
    return _first_float(
        position.get("currentPrice"),
        position.get("price"),
        position.get("lastPrice"),
        position.get("current"),
        position.get("instrument", {}).get("currentPrice") if isinstance(position.get("instrument"), dict) else None,
        default=0.0,
    )


def _extract_avg_price(position: dict[str, Any]) -> float:
    return _first_float(
        position.get("averagePricePaid"),
        position.get("averagePrice"),
        position.get("avgPrice"),
        position.get("averageOpenPrice"),
        position.get("openPrice"),
        position.get("walletImpact", {}).get("averagePricePaid") if isinstance(position.get("walletImpact"), dict) else None,
        position.get("instrument", {}).get("averagePrice") if isinstance(position.get("instrument"), dict) else None,
        default=0.0,
    )


def _extract_total_cost(position: dict[str, Any], quantity: float, average_price: float) -> float:
    total_cost = _first_float(
        position.get("totalCost"),
        position.get("walletImpact", {}).get("totalCost") if isinstance(position.get("walletImpact"), dict) else None,
        default=average_price * quantity,
    )
    if total_cost <= 0 and quantity > 0 and average_price > 0:
        total_cost = average_price * quantity
    return total_cost


def _extract_unrealized_ppl(position: dict[str, Any], value: float, total_cost: float, quantity: float, price: float, average_price: float) -> float:
    return _first_float(
        position.get("walletImpact", {}).get("unrealizedProfitLoss") if isinstance(position.get("walletImpact"), dict) else None,
        position.get("unrealizedProfitLoss"),
        position.get("ppl"),
        position.get("result"),
        default=value - total_cost if total_cost > 0 else (price - average_price) * quantity,
    )


def _extract_currency(position: dict[str, Any], default_currency: str) -> str:
    for key in ("currencyCode", "currency", "currency_code"):
        raw = position.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().upper()
    instrument = position.get("instrument")
    if isinstance(instrument, dict):
        raw = instrument.get("currency") or instrument.get("currencyCode")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().upper()
    return default_currency


def _parse_account_summary(account_payload: dict[str, Any], positions_payload: list[dict[str, Any]]) -> dict[str, Any]:
    cash = account_payload.get("cash") if isinstance(account_payload.get("cash"), dict) else {}
    summary = account_payload.get("summary") if isinstance(account_payload.get("summary"), dict) else {}
    investments = account_payload.get("investments") if isinstance(account_payload.get("investments"), dict) else {}

    currency = (
        str(account_payload.get("currency") or account_payload.get("currencyCode") or summary.get("currency") or "USD")
        .strip()
        .upper()
    )

    free_cash = _first_float(
        account_payload.get("freeCash"),
        account_payload.get("free"),
        account_payload.get("available"),
        cash.get("availableToTrade"),
        cash.get("free"),
        cash.get("available"),
        summary.get("freeCash"),
        default=0.0,
    )

    invested = _first_float(
        account_payload.get("invested"),
        account_payload.get("investedValue"),
        investments.get("currentValue"),
        investments.get("totalCost"),
        summary.get("invested"),
        default=0.0,
    )

    pie_cash = _first_float(
        account_payload.get("pieCash"),
        summary.get("pieCash"),
        default=0.0,
    )

    total = _first_float(
        account_payload.get("total"),
        account_payload.get("totalValue"),
        account_payload.get("equity"),
        account_payload.get("accountValue"),
        summary.get("total"),
        default=0.0,
    )

    ppl = _first_float(
        account_payload.get("result"),
        account_payload.get("ppl"),
        account_payload.get("profitLoss"),
        summary.get("result"),
        default=0.0,
    )
    if ppl == 0.0:
        unrealized = _coerce_float(investments.get("unrealizedProfitLoss"))
        realized = _coerce_float(investments.get("realizedProfitLoss"))
        ppl = float(unrealized or 0.0) + float(realized or 0.0)

    position_total = 0.0
    for raw in positions_payload:
        quantity = _first_float(raw.get("quantity"), default=0.0)
        price = _extract_price(raw)
        value = _first_float(
            raw.get("value"),
            raw.get("marketValue"),
            raw.get("currentValue"),
            raw.get("walletImpact", {}).get("currentValue") if isinstance(raw.get("walletImpact"), dict) else None,
            default=quantity * price,
        )
        position_total += max(value, 0.0)

    if invested <= 0 and position_total > 0:
        invested = position_total

    if total <= 0:
        total = position_total + max(free_cash, 0.0)

    return {
        "currency": currency,
        "free_cash": free_cash,
        "invested": invested,
        "pie_cash": pie_cash,
        "total": total,
        "ppl": ppl,
    }


def refresh_portfolio(db: Session) -> dict[str, Any]:
    global _last_refresh_ts

    with _refresh_lock:
        now = datetime.utcnow()
        if _last_refresh_ts and (now - _last_refresh_ts).total_seconds() < _refresh_cooldown_seconds:
            existing_accounts = _latest_accounts(db)
            if existing_accounts:
                existing_positions = _latest_positions(db)
                return {
                    "fetched_at": max((row.fetched_at for row in existing_accounts), default=now),
                    "positions_count": len(existing_positions),
                    "source": "cooldown-cache",
                }

        config = ConfigStore(db)
        enabled_accounts = config.enabled_account_kinds()
        fetched_at = now

        source_parts: list[str] = []
        account_rows: list[AccountSnapshot] = []
        position_rows: list[PositionSnapshot] = []

        if not enabled_accounts:
            account_payload, positions_payload = _mock_portfolio()
            normalized_account = _parse_account_summary(account_payload, positions_payload)

            account_rows.append(
                AccountSnapshot(
                    fetched_at=fetched_at,
                    account_kind="invest",
                    currency=normalized_account["currency"],
                    free_cash=normalized_account["free_cash"],
                    invested=normalized_account["invested"],
                    pie_cash=normalized_account["pie_cash"],
                    total=normalized_account["total"],
                    ppl=normalized_account["ppl"],
                )
            )
            for raw in positions_payload:
                ticker, instrument_code = _extract_symbols(raw)
                quantity = _first_float(raw.get("quantity"), default=0.0)
                price = _extract_price(raw)
                avg_price = _extract_avg_price(raw)
                value = _first_float(raw.get("value"), default=quantity * price)
                total_cost = _extract_total_cost(raw, quantity, avg_price)
                if avg_price <= 0 and quantity > 0 and total_cost > 0:
                    avg_price = total_cost / quantity
                ppl = _extract_unrealized_ppl(raw, value, total_cost, quantity, price, avg_price)
                position_rows.append(
                    PositionSnapshot(
                        fetched_at=fetched_at,
                        account_kind="invest",
                        ticker=ticker,
                        instrument_code=instrument_code,
                        quantity=quantity,
                        average_price=avg_price,
                        current_price=price,
                        total_cost=total_cost,
                        value=value,
                        ppl=ppl,
                        currency=normalized_account["currency"],
                    )
                )
            source_parts.append("mock")
        else:
            for account_kind in enabled_accounts:
                try:
                    client = build_t212_client(config, account_kind=account_kind)
                    account_payload = client.get_account_summary()
                    positions_payload = client.get_positions()
                    normalized_account = _parse_account_summary(account_payload, positions_payload)

                    account_rows.append(
                        AccountSnapshot(
                            fetched_at=fetched_at,
                            account_kind=account_kind,
                            currency=normalized_account["currency"],
                            free_cash=normalized_account["free_cash"],
                            invested=normalized_account["invested"],
                            pie_cash=normalized_account["pie_cash"],
                            total=normalized_account["total"],
                            ppl=normalized_account["ppl"],
                        )
                    )

                    for raw in positions_payload:
                        try:
                            ticker, instrument_code = _extract_symbols(raw)
                        except ValueError:
                            continue
                        quantity = _first_float(raw.get("quantity"), default=0.0)
                        price = _extract_price(raw)
                        avg_price = _extract_avg_price(raw)
                        value = _first_float(
                            raw.get("value"),
                            raw.get("marketValue"),
                            raw.get("currentValue"),
                            raw.get("walletImpact", {}).get("currentValue") if isinstance(raw.get("walletImpact"), dict) else None,
                            default=quantity * price,
                        )
                        total_cost = _extract_total_cost(raw, quantity, avg_price)
                        if avg_price <= 0 and quantity > 0 and total_cost > 0:
                            avg_price = total_cost / quantity
                        ppl = _extract_unrealized_ppl(raw, value, total_cost, quantity, price, avg_price)

                        position_rows.append(
                            PositionSnapshot(
                                fetched_at=fetched_at,
                                account_kind=account_kind,
                                ticker=ticker,
                                instrument_code=instrument_code,
                                quantity=quantity,
                                average_price=avg_price,
                                current_price=price,
                                total_cost=total_cost,
                                value=value,
                                ppl=ppl,
                                currency=_extract_currency(raw, normalized_account["currency"]),
                            )
                        )

                    source_parts.append(f"t212-{client.base_env}:{account_kind}")

                except (T212RateLimitError, T212Error, T212AuthError) as exc:
                    source_parts.append(f"error:{account_kind}:{exc.__class__.__name__}")
                except Exception as exc:
                    source_parts.append(f"error:{account_kind}:Unexpected")

            if not account_rows:
                existing_accounts = _latest_accounts(db)
                if existing_accounts:
                    existing_positions = _latest_positions(db)
                    return {
                        "fetched_at": max((row.fetched_at for row in existing_accounts), default=fetched_at),
                        "positions_count": len(existing_positions),
                        "source": "stale-cache",
                    }

                account_payload, positions_payload = _mock_portfolio()
                normalized_account = _parse_account_summary(account_payload, positions_payload)
                account_rows.append(
                    AccountSnapshot(
                        fetched_at=fetched_at,
                        account_kind="invest",
                        currency=normalized_account["currency"],
                        free_cash=normalized_account["free_cash"],
                        invested=normalized_account["invested"],
                        pie_cash=normalized_account["pie_cash"],
                        total=normalized_account["total"],
                        ppl=normalized_account["ppl"],
                    )
                )
                source_parts.append("mock-fallback")

        db.add_all(account_rows)
        db.add_all(position_rows)
        db.commit()
        _last_refresh_ts = fetched_at

        return {
            "fetched_at": fetched_at,
            "positions_count": len(position_rows),
            "source": ",".join(source_parts) if source_parts else "unknown",
        }


def _latest_accounts(db: Session) -> list[AccountSnapshot]:
    latest_ts = db.execute(select(AccountSnapshot.fetched_at).order_by(desc(AccountSnapshot.fetched_at)).limit(1)).scalar_one_or_none()
    if latest_ts is None:
        return []
    return list(db.execute(select(AccountSnapshot).where(AccountSnapshot.fetched_at == latest_ts)).scalars().all())


def _latest_positions(db: Session) -> list[PositionSnapshot]:
    latest_ts = db.execute(select(PositionSnapshot.fetched_at).order_by(desc(PositionSnapshot.fetched_at)).limit(1)).scalar_one_or_none()
    if latest_ts is None:
        return []
    return list(db.execute(select(PositionSnapshot).where(PositionSnapshot.fetched_at == latest_ts)).scalars().all())


def get_portfolio_snapshot(
    db: Session,
    account_kind: AccountViewKind = "all",
    display_currency: str | None = None,
) -> dict[str, Any]:
    accounts_all = _latest_accounts(db)
    positions_all = _latest_positions(db)

    if not accounts_all:
        refresh_portfolio(db)
        accounts_all = _latest_accounts(db)
        positions_all = _latest_positions(db)

    if not accounts_all:
        raise RuntimeError("Unable to load account snapshot")

    if account_kind == "all":
        accounts = accounts_all
        positions = positions_all
    else:
        accounts = [a for a in accounts_all if a.account_kind == account_kind]
        positions = [p for p in positions_all if p.account_kind == account_kind]

    if not accounts:
        # account filter selected but no data for that account kind.
        accounts = []
        positions = []

    requested = (display_currency or "").upper().strip()
    if requested not in {"GBP", "USD"}:
        requested = (settings.portfolio_display_currency or "").upper().strip()
    target_currency = requested if requested in {"GBP", "USD"} else "GBP"

    def _to_target(amount: float, source_currency: str | None) -> float:
        source = (source_currency or "").upper().strip() or target_currency
        return float(amount or 0.0) * get_fx_rate(source, target_currency)

    total_value = sum(max(_to_target(a.total, a.currency), 0.0) for a in accounts)
    free_cash = sum(max(_to_target(a.free_cash, a.currency), 0.0) for a in accounts)
    invested = sum(max(_to_target(a.invested, a.currency), 0.0) for a in accounts)
    pie_cash = sum(max(_to_target(a.pie_cash, a.currency), 0.0) for a in accounts)
    ppl = sum(_to_target(a.ppl, a.currency) for a in accounts)

    if total_value <= 0 and positions:
        total_value = sum(max(_to_target(p.value, p.currency), 0.0) for p in positions) + free_cash

    signal_budget = 12
    signal_targets = {
        p.instrument_code
        for p in sorted(positions, key=lambda row: row.value, reverse=True)[:signal_budget]
        if p.instrument_code
    }
    signal_cache: dict[str, Any] = {}

    enriched: list[dict[str, Any]] = []
    for p in positions:
        raw_total_cost = float(getattr(p, "total_cost", 0.0) or 0.0)
        if raw_total_cost <= 0 and abs(float(p.value or 0.0) - float(p.ppl or 0.0)) > 0:
            raw_total_cost = max(float(p.value or 0.0) - float(p.ppl or 0.0), 0.0)
        converted_total_cost = _to_target(raw_total_cost, p.currency)
        converted_value = _to_target(p.value, p.currency)
        converted_avg_price = _to_target(p.average_price, p.currency)
        converted_current_price = _to_target(p.current_price, p.currency)
        converted_ppl = _to_target(p.ppl, p.currency)
        weight = (converted_value / total_value) if total_value else 0.0

        signal = None
        if p.instrument_code in signal_targets:
            signal = signal_cache.get(p.instrument_code)
            if signal is None:
                signal = signal_for_symbol(p.instrument_code)
                signal_cache[p.instrument_code] = signal

        enriched.append(
            {
                "account_kind": p.account_kind,
                "ticker": p.ticker,
                "instrument_code": p.instrument_code,
                "quantity": p.quantity,
                "average_price": converted_avg_price,
                "current_price": converted_current_price,
                "total_cost": converted_total_cost,
                "value": converted_value,
                "ppl": converted_ppl,
                "weight": weight,
                "momentum_63d": signal.momentum_63d if signal else None,
                "rsi_14": signal.rsi_14 if signal else None,
                "trend_score": signal.trend_score if signal else None,
                "volatility_30d": signal.volatility_30d if signal else None,
                "risk_flag": signal.risk_flag if signal else None,
            }
        )

    metrics = {
        "total_value": total_value,
        "free_cash": free_cash,
        "cash_ratio": (free_cash / total_value) if total_value else 0.0,
        "concentration_hhi": concentration_hhi(enriched),
        "top_position_weight": max((row["weight"] for row in enriched), default=0.0),
        "estimated_beta": estimate_portfolio_beta(enriched, max_assets=8),
        "estimated_volatility": estimated_portfolio_volatility(enriched),
    }

    account_items = [
        {
            "fetched_at": a.fetched_at,
            "account_kind": a.account_kind,
            "currency": target_currency,
            "free_cash": _to_target(a.free_cash, a.currency),
            "invested": _to_target(a.invested, a.currency),
            "pie_cash": _to_target(a.pie_cash, a.currency),
            "total": _to_target(a.total, a.currency),
            "ppl": _to_target(a.ppl, a.currency),
        }
        for a in sorted(accounts_all, key=lambda row: row.account_kind)
    ]

    fetched_at = max((a.fetched_at for a in accounts_all), default=datetime.utcnow())
    aggregate_kind = account_kind

    return {
        "account": {
            "fetched_at": fetched_at,
            "account_kind": aggregate_kind,
            "currency": target_currency,
            "free_cash": free_cash,
            "invested": invested,
            "pie_cash": pie_cash,
            "total": total_value,
            "ppl": ppl,
        },
        "accounts": account_items,
        "positions": enriched,
        "metrics": metrics,
    }
