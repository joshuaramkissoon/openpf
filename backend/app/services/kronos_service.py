"""Kronos price-forecasting service.

Wraps the vendored Kronos foundation model (backend/vendor/kronos) to
produce probabilistic OHLCV forecasts for a symbol. The model and its
heavy dependency (torch) are imported lazily so the rest of the backend
runs fine without them — callers get a clear `ForecastUnavailableError`
instead of an import crash.

Uncertainty bands: Kronos averages internally when `sample_count > 1`, so
to get a *spread* we treat the predictor as a black box and draw several
independent sample paths (`predict()` with `sample_count=1`), then take
per-step quantiles of the forecasted close. This keeps the vendored model
untouched.

Design notes for the Mac mini M4: Kronos auto-detects the Apple Silicon
`mps` backend (falling back to cuda, then cpu). Kronos-base (102M params)
is light — sub-second to a few seconds per path on an M4. Override the
device with KRONOS_DEVICE if needed.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from app.services.market_data import MarketDataError, fetch_history, normalize_symbol_for_yf

logger = logging.getLogger(__name__)


class ForecastUnavailableError(RuntimeError):
    """Raised when forecasting deps/model are not installed or loadable."""


class ForecastError(RuntimeError):
    """Raised when a forecast cannot be produced for valid-input reasons."""


# ── Configuration (overridable via environment) ──────────────────────────
# Default to Kronos-base — best open-source quality, comfortable on an M4.
KRONOS_MODEL_REPO = os.environ.get("KRONOS_MODEL", "NeoQuasar/Kronos-base")
KRONOS_TOKENIZER_REPO = os.environ.get("KRONOS_TOKENIZER", "NeoQuasar/Kronos-Tokenizer-base")
KRONOS_DEVICE = os.environ.get("KRONOS_DEVICE") or None  # None → model auto-detects
# Kronos-mini supports a 2048 context; small/base are 512.
_DEFAULT_MAX_CONTEXT = 2048 if "mini" in KRONOS_MODEL_REPO.lower() else 512
KRONOS_MAX_CONTEXT = int(os.environ.get("KRONOS_MAX_CONTEXT", _DEFAULT_MAX_CONTEXT))

# Sensible request bounds.
MAX_HORIZON = 120
MAX_SAMPLES = 60
DEFAULT_HORIZON = 30
DEFAULT_LOOKBACK = 256
DEFAULT_SAMPLES = 20

_CACHE_TTL_SECONDS = 30 * 60


@dataclass
class _PredictorState:
    predictor: object | None = None
    device: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_state = _PredictorState()
_forecast_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def forecast_available() -> tuple[bool, str | None]:
    """Return (available, reason). Cheap import probe; does not load weights."""
    try:
        import torch  # noqa: F401
        from vendor.kronos import KronosPredictor  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, None


def _get_predictor():
    """Lazily construct and cache the KronosPredictor singleton."""
    if _state.predictor is not None:
        return _state.predictor

    with _state.lock:
        if _state.predictor is not None:
            return _state.predictor

        try:
            import torch
            from vendor.kronos import Kronos, KronosPredictor, KronosTokenizer
        except Exception as exc:  # noqa: BLE001
            raise ForecastUnavailableError(
                "Forecasting dependencies are not installed. Install with: "
                "pip install -r requirements-forecast.txt"
            ) from exc

        device = KRONOS_DEVICE
        if device is None:
            if torch.cuda.is_available():
                device = "cuda:0"
            elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"

        logger.info(
            "Loading Kronos model=%s tokenizer=%s device=%s (first call downloads weights)",
            KRONOS_MODEL_REPO, KRONOS_TOKENIZER_REPO, device,
        )
        t0 = time.time()
        try:
            tokenizer = KronosTokenizer.from_pretrained(KRONOS_TOKENIZER_REPO)
            model = Kronos.from_pretrained(KRONOS_MODEL_REPO)
            predictor = KronosPredictor(
                model, tokenizer, device=device, max_context=KRONOS_MAX_CONTEXT
            )
        except Exception as exc:  # noqa: BLE001
            raise ForecastUnavailableError(f"Failed to load Kronos model: {exc}") from exc

        logger.info("Kronos loaded in %.1fs on %s", time.time() - t0, device)
        _state.predictor = predictor
        _state.device = device
        return predictor


def _prepare_history(symbol: str, lookback: int) -> tuple[pd.DataFrame, pd.Series]:
    """Fetch OHLCV history and return (df, x_timestamp) trimmed to `lookback`.

    Uses the existing market_data layer (yfinance + cache + synthetic
    fallback), so forecasts share the same data source as the charts.
    """
    # Pull a generous window so we can trim to exactly `lookback` rows.
    lookback_days = max(int(lookback * 2), 365)
    try:
        hist = fetch_history(symbol, lookback_days=lookback_days)
    except MarketDataError as exc:
        raise ForecastError(str(exc)) from exc

    if hist is None or hist.empty:
        raise ForecastError(f"No price history available for {symbol!r}")

    hist = hist.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(hist) < 30:
        raise ForecastError(
            f"Not enough history for {symbol!r} to forecast ({len(hist)} rows)"
        )

    hist = hist.tail(min(lookback, KRONOS_MAX_CONTEXT)).reset_index(drop=True)

    df = hist[["open", "high", "low", "close", "volume"]].copy().astype(float)
    # Kronos derives calendar features from timestamps; daily candles → midnight.
    x_timestamp = pd.to_datetime(hist["date"])
    return df, x_timestamp


def _future_timestamps(last_ts: pd.Timestamp, horizon: int) -> pd.Series:
    """Generate `horizon` future business-day timestamps after `last_ts`."""
    start = (last_ts + pd.Timedelta(days=1)).normalize()
    future = pd.bdate_range(start=start, periods=horizon)
    return pd.Series(future)


def forecast(
    symbol: str,
    *,
    horizon: int = DEFAULT_HORIZON,
    lookback: int = DEFAULT_LOOKBACK,
    samples: int = DEFAULT_SAMPLES,
    temperature: float = 1.0,
    top_p: float = 0.9,
    use_cache: bool = True,
) -> dict:
    """Produce a probabilistic close-price forecast for `symbol`.

    Returns a dict with the historical tail, per-step close quantile bands
    (p10/p50/p90), and summary statistics. Raises ForecastUnavailableError
    if the model/deps are missing, or ForecastError for bad inputs/data.
    """
    norm = normalize_symbol_for_yf(symbol)
    if not norm:
        raise ForecastError(f"Invalid symbol: {symbol!r}")

    horizon = max(1, min(int(horizon), MAX_HORIZON))
    lookback = max(32, min(int(lookback), KRONOS_MAX_CONTEXT))
    samples = max(1, min(int(samples), MAX_SAMPLES))
    temperature = float(temperature)
    top_p = float(top_p)

    cache_key = (norm, horizon, lookback, samples, round(temperature, 3), round(top_p, 3))
    if use_cache:
        with _cache_lock:
            hit = _forecast_cache.get(cache_key)
            if hit and (time.time() - hit[0]) <= _CACHE_TTL_SECONDS:
                return hit[1]

    df, x_timestamp = _prepare_history(norm, lookback)
    last_close = float(df["close"].iloc[-1])
    last_ts = pd.Timestamp(x_timestamp.iloc[-1])
    y_timestamp = _future_timestamps(last_ts, horizon)

    predictor = _get_predictor()

    # Draw independent sample paths to build an uncertainty cone. Each call
    # uses sample_count=1 so paths are not pre-averaged by the model.
    close_paths = np.empty((samples, horizon), dtype=np.float64)
    for i in range(samples):
        pred_df = predictor.predict(
            df=df,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=horizon,
            T=temperature,
            top_p=top_p,
            sample_count=1,
            verbose=False,
        )
        close_paths[i, :] = np.asarray(pred_df["close"].values, dtype=np.float64)

    result = _summarize(
        symbol=symbol,
        norm=norm,
        df=df,
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        close_paths=close_paths,
        last_close=last_close,
        horizon=horizon,
        lookback=lookback,
        samples=samples,
    )

    if use_cache:
        with _cache_lock:
            if len(_forecast_cache) > 256:
                _forecast_cache.clear()
            _forecast_cache[cache_key] = (time.time(), result)
    return result


def _summarize(
    *,
    symbol: str,
    norm: str,
    df: pd.DataFrame,
    x_timestamp: pd.Series,
    y_timestamp: pd.Series,
    close_paths: np.ndarray,
    last_close: float,
    horizon: int,
    lookback: int,
    samples: int,
) -> dict:
    """Turn raw sample paths into per-step bands and summary stats."""
    p10 = np.percentile(close_paths, 10, axis=0)
    p50 = np.percentile(close_paths, 50, axis=0)
    p90 = np.percentile(close_paths, 90, axis=0)

    forecast_points = [
        {
            "date": pd.Timestamp(ts).strftime("%Y-%m-%d"),
            "p10": round(float(p10[i]), 6),
            "p50": round(float(p50[i]), 6),
            "p90": round(float(p90[i]), 6),
        }
        for i, ts in enumerate(y_timestamp)
    ]

    history_points = [
        {
            "date": pd.Timestamp(ts).strftime("%Y-%m-%d"),
            "close": round(float(c), 6),
        }
        for ts, c in zip(x_timestamp, df["close"].values)
    ]

    terminal = close_paths[:, -1]
    median_terminal = float(np.median(terminal))
    expected_return = (median_terminal / last_close - 1.0) if last_close else 0.0
    prob_up = float(np.mean(terminal > last_close))
    # Annualised-ish dispersion of the terminal forecast, as a risk read.
    terminal_spread = (
        float((np.percentile(terminal, 90) - np.percentile(terminal, 10)) / last_close)
        if last_close
        else 0.0
    )

    return {
        "ok": True,
        "symbol": symbol.upper().strip(),
        "yfinance_ticker": norm,
        "model": KRONOS_MODEL_REPO,
        "device": _state.device,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "horizon": horizon,
        "lookback": lookback,
        "samples": samples,
        "last_close": round(last_close, 6),
        "last_date": pd.Timestamp(x_timestamp.iloc[-1]).strftime("%Y-%m-%d"),
        "summary": {
            "median_terminal_close": round(median_terminal, 6),
            "expected_return_pct": round(expected_return * 100.0, 3),
            "prob_up": round(prob_up, 4),
            "terminal_spread_pct": round(terminal_spread * 100.0, 3),
        },
        "history": history_points,
        "forecast": forecast_points,
    }


def runtime_info() -> dict:
    """Lightweight status for diagnostics endpoints (no weight download)."""
    available, reason = forecast_available()
    return {
        "available": available,
        "reason": reason,
        "model": KRONOS_MODEL_REPO,
        "tokenizer": KRONOS_TOKENIZER_REPO,
        "device": _state.device,
        "max_context": KRONOS_MAX_CONTEXT,
        "loaded": _state.predictor is not None,
        "configured_device": KRONOS_DEVICE or "auto",
    }
