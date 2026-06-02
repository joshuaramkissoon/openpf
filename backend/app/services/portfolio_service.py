from __future__ import annotations

import re
from contextlib import contextmanager
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
from app.services.leveraged_market import resolve_yfinance_ticker
from app.services.t212_client import T212AuthError, T212Error, T212RateLimitError, build_t212_client, normalize_instrument_code

AccountViewKind = Literal["all", "invest", "stocks_isa"]
settings = get_settings()

_refresh_lock = Lock()
_last_refresh_ts: datetime | None = None
_refresh_cooldown_seconds = 6


@contextmanager
def _hold_acquired_lock():
    """Release ``_refresh_lock`` on exit. The caller must already hold it, so the
    acquisition policy (non-blocking on the request path, blocking for the
    background daily snapshot) lives in ``refresh_portfolio`` rather than here."""
    try:
        yield
    finally:
        _refresh_lock.release()

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


def refresh_portfolio(db: Session, force: bool = False) -> dict[str, Any]:
    global _last_refresh_ts

    now = datetime.utcnow()
    # `force` (explicit user click / background daily snapshot) bypasses the
    # cooldown; auto-load + 60s polling leave it off so the short cooldown
    # collapses bursts and protects the T212 rate limits. Checked BEFORE taking
    # the lock so a burst never even contends on it.
    if not force:
        cooled = _cooldown_response(db, now)
        if cooled is not None:
            return cooled

    # A request-path refresh must never queue behind an in-flight one (which may be
    # mid slow T212 / cashflow I/O) — that is what hangs the dashboard. Serve the
    # latest snapshot immediately instead. force=True (background daily snapshot)
    # blocks for the lock, since its whole job is to record a fresh point.
    if force:
        _refresh_lock.acquire()
    elif not _refresh_lock.acquire(blocking=False):
        cached = _latest_snapshot_response(db, "refresh-in-progress", now=now)
        if cached is not None:
            return cached
        _refresh_lock.acquire()  # cold start: nothing cached yet → wait it out

    result: dict[str, Any] | None = None
    did_full_refresh = False
    with _hold_acquired_lock():
        now = datetime.utcnow()
        # Re-check the cooldown now we hold the lock: a concurrent refresh may have
        # completed while we were waiting to acquire it.
        if not force:
            cooled = _cooldown_response(db, now)
            if cooled is not None:
                return cooled

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

        result = {
            "fetched_at": fetched_at,
            "positions_count": len(position_rows),
            "source": ",".join(source_parts) if source_parts else "unknown",
        }
        did_full_refresh = True

    # Keep external cashflows current (throttled, best-effort) so the return curve
    # nets out new deposits/withdrawals — OUTSIDE the refresh lock, because it can
    # page the T212 transactions feed with rate-limit backoff and would otherwise
    # stall every concurrent refresh while held.
    if did_full_refresh:
        try:
            from app.services.cashflow_service import maybe_sync_all

            maybe_sync_all(db)
        except Exception:  # noqa: BLE001
            pass

    return result


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


def _cooldown_response(db: Session, now: datetime) -> dict[str, Any] | None:
    """Cached summary when still inside the refresh cooldown window (no lock held)."""
    if not (_last_refresh_ts and (now - _last_refresh_ts).total_seconds() < _refresh_cooldown_seconds):
        return None
    return _latest_snapshot_response(db, "cooldown-cache", now=now)


def _latest_snapshot_response(db: Session, source: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Summary of the latest stored snapshot, or None if nothing is recorded yet."""
    accounts = _latest_accounts(db)
    if not accounts:
        return None
    positions = _latest_positions(db)
    return {
        "fetched_at": max((row.fetched_at for row in accounts), default=now or datetime.utcnow()),
        "positions_count": len(positions),
        "source": source,
    }


_INSTRUMENT_NAME_TTL_SECONDS = 6 * 3600
_instrument_meta_cache: tuple[float, dict[str, dict[str, str]]] | None = None


def _instrument_meta_map(db: Session) -> dict[str, dict[str, str]]:
    """Best-effort {UPPER instrument code: metadata} from T212 bulk metadata.

    The positions feed carries no human-readable name and our DB upper-cases the
    instrument code on ingestion (destroying the lowercase exchange letter, e.g.
    ``NUCGl_EQ`` → ``NUCGL_EQ``). We join against the bulk
    /equity/metadata/instruments list (one call, cached ~6h), keyed by the
    UPPER-cased code so it matches our stored codes, but we retain the
    ORIGINAL-case ``ticker`` so the venue can be recovered for yfinance
    resolution. Never raises — returns {} when metadata is unavailable.

    Each value: ``{"name": ..., "ticker": <original-case>, "currency": <code>}``.
    """
    global _instrument_meta_cache
    now = datetime.now().timestamp()
    if _instrument_meta_cache and (now - _instrument_meta_cache[0]) < _INSTRUMENT_NAME_TTL_SECONDS:
        return _instrument_meta_cache[1]

    mapping: dict[str, dict[str, str]] = {}
    try:
        store = ConfigStore(db)
        client = None
        for acct in ("invest", "stocks_isa"):
            try:
                client = build_t212_client(store, acct)
                break
            except Exception:  # noqa: BLE001 — try the other account's creds
                continue
        if client is not None:
            for inst in client.get_instruments_metadata():
                if not isinstance(inst, dict):
                    continue
                ticker = str(inst.get("ticker", "")).strip()
                if not ticker:
                    continue
                name = inst.get("name") or inst.get("shortName")
                mapping[ticker.upper()] = {
                    "name": str(name) if name else "",
                    "ticker": ticker,
                    "currency": str(inst.get("currencyCode", "") or ""),
                }
    except Exception:  # noqa: BLE001 — metadata is best-effort, never block the snapshot
        mapping = {}

    if mapping:
        _instrument_meta_cache = (now, mapping)
    return mapping


def get_portfolio_snapshot(
    db: Session,
    account_kind: AccountViewKind = "all",
    display_currency: str | None = None,
    strip_prices: bool = False,
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
    meta_map = _instrument_meta_map(db)

    def _resolve_meta(code: str | None, ticker: str | None, currency: str | None) -> dict[str, Any]:
        """Resolve display name, venue currency, and yfinance ticker for a row.

        Prefers original-case T212 metadata (so the lowercase exchange letter is
        recovered) and falls back to the stored upper-cased code + currency.
        """
        meta = meta_map.get((code or "").upper()) or meta_map.get((ticker or "").upper()) or {}
        original = meta.get("ticker") or code or ticker or ""
        venue_currency = meta.get("currency") or currency or ""
        yf_ticker = None
        try:
            yf_ticker = resolve_yfinance_ticker(original, venue_currency)
        except Exception:  # noqa: BLE001 — chart ticker is best-effort
            yf_ticker = None
        return {
            "name": meta.get("name") or None,
            "instrument_currency": venue_currency or None,
            "yfinance_ticker": yf_ticker,
        }

    # Per-account base currency (ISA=GBP, Invest=USD) for converting money fields.
    account_currency_map = {a.account_kind: a.currency for a in accounts_all}

    enriched: list[dict[str, Any]] = []
    for p in positions:
        raw_total_cost = float(getattr(p, "total_cost", 0.0) or 0.0)
        if raw_total_cost <= 0 and abs(float(p.value or 0.0) - float(p.ppl or 0.0)) > 0:
            raw_total_cost = max(float(p.value or 0.0) - float(p.ppl or 0.0), 0.0)
        # CURRENCY FIX: money fields (value / ppl / cost) are in the ACCOUNT base
        # currency (ISA=GBP, Invest=USD); price fields (current/avg) are in the
        # INSTRUMENT currency (USD for PLTR, GBX for LSE ETPs, …). T212 tags every
        # position row with the *instrument* currency, so converting the money
        # fields with it double-converts ISA holdings (~1.34x too low) and makes
        # the sheet's value disagree with last-price × qty. Convert money from the
        # account currency, prices from the instrument currency.
        money_ccy = account_currency_map.get(p.account_kind) or p.currency
        converted_total_cost = _to_target(raw_total_cost, money_ccy)
        converted_value = _to_target(p.value, money_ccy)
        converted_ppl = _to_target(p.ppl, money_ccy)
        converted_avg_price = _to_target(p.average_price, p.currency)
        converted_current_price = _to_target(p.current_price, p.currency)
        weight = (converted_value / total_value) if total_value else 0.0

        resolved = _resolve_meta(p.instrument_code, p.ticker, p.currency)

        # Technicals must run on the resolved yfinance ticker (e.g. NUCG.L),
        # not the raw upper-cased T212 code — otherwise London/Xetra/Euronext
        # holdings always fall through to risk_flag="no-market-data".
        signal = None
        if p.instrument_code in signal_targets:
            signal_symbol = resolved["yfinance_ticker"] or p.instrument_code
            signal = signal_cache.get(signal_symbol)
            if signal is None:
                signal = signal_for_symbol(signal_symbol)
                signal_cache[signal_symbol] = signal

        enriched.append(
            {
                "account_kind": p.account_kind,
                "ticker": p.ticker,
                "instrument_code": p.instrument_code,
                "name": resolved["name"],
                "yfinance_ticker": resolved["yfinance_ticker"],
                "instrument_currency": resolved["instrument_currency"],
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

    # When building LLM context (strip_prices=True), drop price-derived fields:
    # they are computed from cached T212 data and may be stale, so Archie must
    # fetch live prices via the marketdata MCP tools instead. The UI/Telegram
    # paths keep them so the dashboard can show value, equity, and weight.
    if strip_prices:
        for row in enriched:
            for key in ("current_price", "value", "ppl", "weight"):
                row.pop(key, None)

    def _account_item(a: Any) -> dict[str, Any]:
        item = {
            "fetched_at": a.fetched_at,
            "account_kind": a.account_kind,
            "currency": target_currency,
            "free_cash": _to_target(a.free_cash, a.currency),
        }
        if not strip_prices:
            item.update(
                {
                    "invested": _to_target(a.invested, a.currency),
                    "pie_cash": _to_target(a.pie_cash, a.currency),
                    "total": _to_target(a.total, a.currency),
                    "ppl": _to_target(a.ppl, a.currency),
                }
            )
        return item

    account_items = [
        _account_item(a)
        for a in sorted(accounts_all, key=lambda row: row.account_kind)
    ]

    fetched_at = max((a.fetched_at for a in accounts_all), default=datetime.utcnow())
    aggregate_kind = account_kind

    aggregate_account = {
        "fetched_at": fetched_at,
        "account_kind": aggregate_kind,
        "currency": target_currency,
        "free_cash": free_cash,
    }
    if not strip_prices:
        aggregate_account.update(
            {
                "invested": invested,
                "pie_cash": pie_cash,
                "total": total_value,
                "ppl": ppl,
            }
        )

    return {
        "account": aggregate_account,
        "accounts": account_items,
        "positions": enriched,
        "metrics": metrics,
    }


def portfolio_history(
    db: Session,
    account_kind: AccountViewKind = "all",
    display_currency: str | None = None,
    days: int = 365,
) -> dict[str, Any]:
    """Equity curve + return curve over time from stored AccountSnapshot rows.

    Groups snapshots by timestamp (accounts are fetched together), sums the
    selected account(s) into the display currency at the FX rate that held on
    each point's own date (backfilled, not spot), then downsamples to the last
    point per day. Each point also carries ``gain`` — cumulative value change
    net of external contributions since the window start — so the UI can toggle
    between absolute value and true return. ``return_pct`` is a modified-Dietz
    money-weighted return over the window (accounts for deposit/withdrawal
    timing). Depth depends on how long the app has been recording snapshots.
    """
    from datetime import timedelta

    from app.services.cashflow_service import CONTRIBUTION_TYPES, get_cashflows
    from app.services.historical_fx import ensure_history, load_fx_history

    requested = (display_currency or "").upper().strip()
    if requested not in {"GBP", "USD"}:
        requested = (settings.portfolio_display_currency or "").upper().strip()
    target = requested if requested in {"GBP", "USD"} else "GBP"

    cutoff = datetime.utcnow() - timedelta(days=max(1, days))
    q = db.query(AccountSnapshot).filter(AccountSnapshot.fetched_at >= cutoff)
    if account_kind != "all":
        q = q.filter(AccountSnapshot.account_kind == account_kind)
    rows = q.order_by(AccountSnapshot.fetched_at.asc()).all()

    # Backfill daily FX across the snapshot span so each point converts at its
    # own date's rate, then look up nearest-prior (handles weekends/holidays).
    if rows:
        span_start = min(r.fetched_at for r in rows).date()
        span_end = max(r.fetched_at for r in rows).date()
        try:
            ensure_history(db, span_start - timedelta(days=7), span_end)
        except Exception:  # noqa: BLE001 — never let FX backfill break the curve
            pass
    fx = load_fx_history(db)

    def conv(amount: float, src: str | None, on: Any) -> float:
        return float(amount or 0.0) * fx.rate((src or target), target, on)

    by_ts: dict[Any, dict[str, float]] = {}
    for a in rows:
        on = a.fetched_at.date()
        d = by_ts.setdefault(a.fetched_at, {"total": 0.0, "invested": 0.0, "free_cash": 0.0})
        d["total"] += max(conv(a.total, a.currency, on), 0.0)
        d["invested"] += max(conv(a.invested, a.currency, on), 0.0)
        d["free_cash"] += max(conv(a.free_cash, a.currency, on), 0.0)

    # Keep the LAST snapshot of each calendar day for a clean daily series.
    by_day: dict[Any, tuple[Any, dict[str, float]]] = {}
    for ts in sorted(by_ts):
        by_day[ts.date()] = (ts, by_ts[ts])
    recorded = sorted(by_day.items())  # [(day, (ts, {...}))]

    # Reconstructed (estimated) history covering the period *before* the app
    # began recording live snapshots, summed per day across the selected
    # account(s) and converted to the display currency at each date's own FX.
    # Lets the curve span the full account lifetime, not just the recorded window.
    recon_by_day: dict[Any, dict[str, float]] = {}
    try:
        from app.services.equity_backfill import get_reconstructed_history

        for r in get_reconstructed_history(db, account_kind):
            if r.date < cutoff.date():
                continue
            rate = fx.rate((r.currency or target), target, r.date)
            agg = recon_by_day.setdefault(r.date, {"total": 0.0, "invested": 0.0, "free_cash": 0.0})
            agg["total"] += float(r.total or 0.0) * rate
            agg["invested"] += float(r.invested or 0.0) * rate
            agg["free_cash"] += float(r.cash or 0.0) * rate
    except Exception:  # noqa: BLE001 — reconstructed history is additive; never block the curve
        recon_by_day = {}

    first_recorded_day = recorded[0][0] if recorded else None

    # Index the reconstructed curve to the first *recorded* (exact) value at the
    # handoff, rather than to today's holdings. The reconstruction supplies the
    # SHAPE (from real trades + historical prices); scaling its level so it joins
    # the known recorded value removes the seam discontinuity and the distortion
    # from instruments yfinance can't price (delisted SPACs, Xetra ETPs). The
    # deep-past absolute level is therefore an estimate; relative moves are real.
    recon_scale = 1.0
    if first_recorded_day is not None:
        anchor = recon_by_day.get(first_recorded_day)
        recorded_first_total = recorded[0][1][1]["total"]
        if anchor and anchor.get("total", 0.0) > 0 and recorded_first_total > 0:
            recon_scale = max(0.5, min(recorded_first_total / anchor["total"], 2.5))

    # Unified daily series: reconstructed strictly before the first recorded
    # snapshot (exact recorded data wins wherever it exists); source-tagged so
    # the UI can distinguish estimated history from recorded.
    series: list[tuple[Any, Any, dict[str, float], str]] = []
    for day in sorted(recon_by_day):
        if first_recorded_day is None or day < first_recorded_day:
            raw = recon_by_day[day]
            v = {k: raw[k] * recon_scale for k in ("total", "invested", "free_cash")}
            ts = datetime.combine(day, datetime.min.time())
            series.append((day, ts, v, "reconstructed"))
    for day, (ts, v) in recorded:
        series.append((day, ts, v, "recorded"))

    def _point(day: Any, ts: Any, v: dict[str, float], source: str, gain: float) -> dict[str, Any]:
        return {"date": day.isoformat(), "t": ts.isoformat(),
                "total": round(v["total"], 2), "invested": round(v["invested"], 2),
                "free_cash": round(v["free_cash"], 2), "gain": round(gain, 2),
                "source": source}

    if len(series) < 2:
        points = [_point(day, ts, v, source, 0.0) for day, ts, v, source in series]
        return {"account_kind": account_kind, "currency": target, "points": points,
                "return_pct": 0.0, "net_contributed": 0.0,
                "start_value": points[0]["total"] if points else 0.0,
                "end_value": points[-1]["total"] if points else 0.0, "window_days": 0}

    # External cashflows within the window (target currency, at each flow's own
    # date's FX). Bound to (t0, last_day] — a flow after the last point isn't
    # reflected in any value yet, so counting it would distort the return.
    # Summing across accounts makes internal Invest↔ISA transfers self-cancel
    # (both legs are recorded), so no internal/external tagging is needed.
    t0 = series[0][0]
    last_day = series[-1][0]
    flow_events = [
        (f.occurred_at.date(), conv(f.amount, f.currency, f.occurred_at.date()))
        for f in get_cashflows(db, account_kind)
        if f.type in CONTRIBUTION_TYPES and t0 < f.occurred_at.date() <= last_day
    ]

    start_total = series[0][2]["total"]
    end_total = series[-1][2]["total"]
    points = []
    for day, ts, v, source in series:
        cum_flow = sum(amt for fd, amt in flow_events if fd <= day)
        gain = (v["total"] - start_total) - cum_flow
        points.append(_point(day, ts, v, source, gain))

    # Modified-Dietz: gain over average capital, weighting each flow by the
    # fraction of the window it was invested for. If average capital isn't
    # positive (e.g. a large early withdrawal), the ratio is meaningless — fall
    # back to a simple return on the starting value.
    span_days = max((last_day - t0).days, 1)
    net_flow = sum(amt for _, amt in flow_events)
    weighted_flow = sum(amt * ((last_day - fd).days / span_days) for fd, amt in flow_events)
    gain_total = (end_total - start_total) - net_flow
    denom = start_total + weighted_flow
    if denom > 0:
        return_pct = gain_total / denom
    elif start_total > 0:
        return_pct = gain_total / start_total
    else:
        return_pct = 0.0

    return {
        "account_kind": account_kind, "currency": target, "points": points,
        "return_pct": round(return_pct, 4), "net_contributed": round(net_flow, 2),
        "start_value": round(start_total, 2), "end_value": round(end_total, 2),
        "window_days": span_days,
    }
