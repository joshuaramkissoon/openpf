from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

import app.quant as quant
from app.schemas.charts import ChartResponse, ForecastResponse
from app.services.leveraged_market import (
    LeveragedMarketError,
    _download_history_frame,
    to_yfinance_ticker,
)
from app.services.kronos_service import (
    ForecastError,
    ForecastUnavailableError,
    forecast as run_forecast,
)

router = APIRouter(prefix="/charts", tags=["charts"])

_INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}

_OVERLAY_KEYS = {"sma20", "sma50", "sma200", "bollinger"}
_PANEL_KEYS = {"rsi", "macd", "atr"}


@router.get("/candles", response_model=ChartResponse)
def get_candles(
    ticker: str = Query(..., description="Ticker symbol (T212 code or raw symbol)"),
    period: str = Query("3mo", description="History period, e.g. 1mo, 3mo, 1y"),
    interval: str = Query("1d", description="Candle interval, e.g. 1d, 1h, 5m"),
    indicators: str = Query("", description="Comma-separated indicator keys: sma20,sma50,sma200,bollinger,rsi,macd,atr"),
) -> ChartResponse:
    # ------------------------------------------------------------------
    # 1. Download OHLCV frame
    # ------------------------------------------------------------------
    try:
        df = _download_history_frame(ticker, period, interval)
    except LeveragedMarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data for {ticker}")

    # ------------------------------------------------------------------
    # 2. Determine intraday mode and build candle list
    # ------------------------------------------------------------------
    intraday = interval in _INTRADAY_INTERVALS

    candles: list[dict] = []
    for _, row in df.iterrows():
        ts = row["date"]
        if intraday:
            time_val: str | float = float(int(ts.timestamp()))
        else:
            time_val = ts.strftime("%Y-%m-%d")

        candles.append({
            "time": time_val,
            "open": float(row.get("open", 0.0) or 0.0),
            "high": float(row.get("high", 0.0) or 0.0),
            "low": float(row.get("low", 0.0) or 0.0),
            "close": float(row.get("close", 0.0) or 0.0),
            "volume": float(row.get("volume", 0.0) or 0.0),
        })

    # ------------------------------------------------------------------
    # 3. Parse requested indicators
    # ------------------------------------------------------------------
    requested = {k.strip().lower() for k in indicators.split(",") if k.strip()}

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    index = df["date"]

    # ------------------------------------------------------------------
    # 4. Compute overlay indicators
    # ------------------------------------------------------------------
    overlays: dict[str, list] = {}

    if "sma20" in requested:
        overlays["sma20"] = quant.indicator_to_points(index, quant.sma(close, 20), intraday)

    if "sma50" in requested:
        overlays["sma50"] = quant.indicator_to_points(index, quant.sma(close, 50), intraday)

    if "sma200" in requested:
        overlays["sma200"] = quant.indicator_to_points(index, quant.sma(close, 200), intraday)

    if "bollinger" in requested:
        bb_upper, bb_middle, bb_lower = quant.bollinger_bands(close)
        overlays["bollinger_upper"] = quant.indicator_to_points(index, bb_upper, intraday)
        overlays["bollinger_middle"] = quant.indicator_to_points(index, bb_middle, intraday)
        overlays["bollinger_lower"] = quant.indicator_to_points(index, bb_lower, intraday)

    # ------------------------------------------------------------------
    # 5. Compute panel indicators
    # ------------------------------------------------------------------
    panels: dict[str, list] = {}

    if "rsi" in requested:
        panels["rsi"] = quant.indicator_to_points(index, quant.rsi(close), intraday)

    if "macd" in requested:
        macd_line, signal_line, histogram = quant.macd(close)
        panels["macd"] = quant.macd_to_points(index, macd_line, signal_line, histogram, intraday)

    if "atr" in requested:
        panels["atr"] = quant.indicator_to_points(index, quant.atr(high, low, close), intraday)

    # ------------------------------------------------------------------
    # 6. Build response
    # ------------------------------------------------------------------
    yf_ticker = to_yfinance_ticker(ticker)

    return ChartResponse(
        ok=True,
        ticker=ticker.upper().strip(),
        yfinance_ticker=yf_ticker,
        period=period,
        interval=interval,
        candles=candles,
        overlays=overlays,
        panels=panels,
        markers=[],
    )


@router.get("/forecast", response_model=ForecastResponse)
def get_forecast(
    ticker: str = Query(..., description="Ticker symbol (T212 code or raw symbol)"),
    horizon: int = Query(30, ge=1, le=120, description="Trading days to forecast ahead"),
    lookback: int = Query(256, ge=32, le=2048, description="Historical trading days fed to the model"),
    samples: int = Query(20, ge=1, le=60, description="Independent sample paths for the uncertainty bands"),
    temperature: float = Query(1.0, gt=0.0, le=2.0, description="Sampling temperature"),
    top_p: float = Query(0.9, gt=0.0, le=1.0, description="Nucleus sampling threshold"),
) -> ForecastResponse:
    """Probabilistic close-price forecast for a ticker via the Kronos model.

    Returns the historical close tail plus per-step p10/p50/p90 bands. The
    Kronos model + torch load lazily on first call (downloading weights once);
    requests before that are cheap.
    """
    try:
        result = run_forecast(
            ticker,
            horizon=horizon,
            lookback=lookback,
            samples=samples,
            temperature=temperature,
            top_p=top_p,
        )
    except ForecastUnavailableError as exc:
        # 503: feature not installed/loadable on this host.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ForecastError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ForecastResponse(**result)
