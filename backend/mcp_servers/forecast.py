"""Kronos price-forecasting MCP server for Archie.

Exposes the vendored Kronos foundation model as a tool so Archie can
generate probabilistic price forecasts mid-conversation. Runs as its own
stdio subprocess (like t212/marketdata/scheduler) which keeps the heavy
torch dependency out of the main API process and lets the model lazy-load
on first call.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app.services.kronos_service import (
    DEFAULT_HORIZON,
    DEFAULT_LOOKBACK,
    DEFAULT_SAMPLES,
    ForecastError,
    ForecastUnavailableError,
    forecast as run_forecast,
    runtime_info,
)

# ── Logging (file-based — stdout is reserved for MCP protocol) ──
_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "mypf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("forecast-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "forecast.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "forecast",
    instructions=(
        "Probabilistic price forecasting via the Kronos foundation model. "
        "Use forecast_prices to project a holding's close price over a future "
        "horizon with p10/p50/p90 uncertainty bands. Forecasts are analysis "
        "only — never present them as certainties or as trade execution."
    ),
)


def _fmt(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


@mcp.tool()
def forecast_prices(
    ticker: str,
    horizon: int = DEFAULT_HORIZON,
    lookback: int = DEFAULT_LOOKBACK,
    samples: int = DEFAULT_SAMPLES,
    temperature: float = 1.0,
    top_p: float = 0.9,
) -> str:
    """Forecast future close prices for a ticker with uncertainty bands.

    Returns p10/p50/p90 close-price bands per future trading day plus
    summary stats (expected return, probability of finishing up). This is
    a probabilistic model output, not a guarantee — always frame it with
    its uncertainty and never imply a trade has been placed.

    Args:
        ticker: Symbol or T212 code, e.g. PLTR, NVDA, SPY, 3USL.
        horizon: Trading days to forecast ahead (default 30, max 120).
        lookback: Historical trading days fed to the model (default 256).
        samples: Independent sample paths for the bands (default 20, max 60).
        temperature: Sampling temperature; higher = wider/noisier paths.
        top_p: Nucleus sampling threshold.
    """
    logger.info(
        "forecast_prices ticker=%s horizon=%s lookback=%s samples=%s T=%s top_p=%s",
        ticker, horizon, lookback, samples, temperature, top_p,
    )
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
        logger.warning("forecast unavailable: %s", exc)
        return _fmt({"ok": False, "error": str(exc), "error_kind": "unavailable", "ticker": ticker})
    except ForecastError as exc:
        logger.warning("forecast failed ticker=%s: %s", ticker, exc)
        return _fmt({"ok": False, "error": str(exc), "error_kind": "input", "ticker": ticker})
    except Exception as exc:  # noqa: BLE001
        logger.exception("forecast crashed ticker=%s", ticker)
        return _fmt({"ok": False, "error": str(exc), "error_kind": "internal", "ticker": ticker})

    return _fmt(result)


@mcp.tool()
def forecast_status() -> str:
    """Report whether the Kronos forecasting model is available and loaded.

    Use this to check capability before promising a forecast — it does not
    download weights.
    """
    return _fmt({"ok": True, **runtime_info()})


if __name__ == "__main__":
    mcp.run()
