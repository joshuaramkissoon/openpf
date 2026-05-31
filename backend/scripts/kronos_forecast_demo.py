#!/usr/bin/env python3
"""Standalone Kronos forecast demo — run this on the Mac mini M4 to validate.

Loads the Kronos model (default Kronos-base), forecasts a real symbol via
the same `kronos_service` the app uses, and prints device, latency, the
forecast summary, and a compact band table. Optionally saves a PNG plot.

Usage (from the backend/ directory):

    pip install -r requirements.txt -r requirements-forecast.txt
    python scripts/kronos_forecast_demo.py AAPL
    python scripts/kronos_forecast_demo.py PLTR --horizon 20 --samples 30 --plot

The first run downloads the model weights from Hugging Face (one-time).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure `app` and `vendor` are importable when run as a plain script.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services import kronos_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Kronos forecast demo")
    parser.add_argument("symbol", nargs="?", default="AAPL", help="Ticker symbol (default AAPL)")
    parser.add_argument("--horizon", type=int, default=30, help="Trading days to forecast")
    parser.add_argument("--lookback", type=int, default=256, help="Historical days fed to the model")
    parser.add_argument("--samples", type=int, default=20, help="Sample paths for the bands")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--plot", action="store_true", help="Save a PNG plot (needs matplotlib)")
    args = parser.parse_args()

    available, reason = kronos_service.forecast_available()
    print(f"Forecast available: {available}" + (f" ({reason})" if reason else ""))
    if not available:
        print("\nInstall forecast deps first:")
        print("    pip install -r requirements.txt -r requirements-forecast.txt")
        return 1

    print(f"Model: {kronos_service.KRONOS_MODEL_REPO}  (max_context={kronos_service.KRONOS_MAX_CONTEXT})")
    print(f"Forecasting {args.symbol}: horizon={args.horizon} lookback={args.lookback} samples={args.samples}\n")

    t0 = time.time()
    result = kronos_service.forecast(
        args.symbol,
        horizon=args.horizon,
        lookback=args.lookback,
        samples=args.samples,
        temperature=args.temperature,
        top_p=args.top_p,
        use_cache=False,
    )
    elapsed = time.time() - t0

    s = result["summary"]
    print(f"Device:           {result['device']}")
    print(f"Total time:       {elapsed:.1f}s  (~{elapsed / max(args.samples, 1):.2f}s/path)")
    print(f"Last close:       {result['last_close']}  ({result['last_date']})")
    print(f"Median terminal:  {s['median_terminal_close']}")
    print(f"Expected return:  {s['expected_return_pct']:+.2f}%  over {args.horizon} trading days")
    print(f"P(up):            {s['prob_up'] * 100:.0f}%")
    print(f"Terminal P10-P90: ±{s['terminal_spread_pct'] / 2:.2f}%\n")

    print(f"{'date':<12}{'p10':>12}{'p50':>12}{'p90':>12}")
    print("-" * 48)
    fc = result["forecast"]
    # Print first few, last few, to keep it compact.
    show = fc if len(fc) <= 12 else fc[:5] + [{"date": "...", "p10": "", "p50": "", "p90": ""}] + fc[-5:]
    for p in show:
        if p["date"] == "...":
            print(f"{'...':<12}{'':>12}{'':>12}{'':>12}")
            continue
        print(f"{p['date']:<12}{p['p10']:>12.2f}{p['p50']:>12.2f}{p['p90']:>12.2f}")

    if args.plot:
        _plot(result, args.symbol)

    return 0


def _plot(result: dict, symbol: str) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd
    except Exception as exc:  # noqa: BLE001
        print(f"\n(plot skipped: {exc})")
        return

    hist = result["history"]
    fc = result["forecast"]
    hx = [pd.Timestamp(p["date"]) for p in hist]
    hy = [p["close"] for p in hist]
    fx = [pd.Timestamp(p["date"]) for p in fc]
    p10 = [p["p10"] for p in fc]
    p50 = [p["p50"] for p in fc]
    p90 = [p["p90"] for p in fc]

    plt.figure(figsize=(10, 5))
    plt.plot(hx, hy, color="#2196F3", linewidth=1.5, label="History (close)")
    plt.plot(fx, p50, color="#E6A700", linewidth=2, label="Forecast median (p50)")
    plt.fill_between(fx, p10, p90, color="#FFD54F", alpha=0.3, label="P10–P90 band")
    plt.title(f"{symbol} — Kronos {result['horizon']}d forecast ({result['model']})")
    plt.legend(loc="upper left")
    plt.grid(True, alpha=0.3)
    out = _BACKEND_ROOT / f"kronos_forecast_{symbol.upper()}.png"
    plt.tight_layout()
    plt.savefig(out, dpi=120)
    print(f"\nPlot saved to {out}")


if __name__ == "__main__":
    raise SystemExit(main())
