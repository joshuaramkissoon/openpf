# Kronos Price Forecasting

Integrates [Kronos](https://github.com/shiyu-coder/Kronos) — an open-source
(MIT) foundation model for financial candlestick (K-line) forecasting — into
openpf as a probabilistic price-forecasting capability for Archie and the
chart UI.

Kronos is a two-stage model (a tokenizer that discretises OHLCV data + a
decoder-only transformer) pre-trained on 45+ global exchanges. Given a
window of historical candles it autoregressively forecasts future candles.
We use the **Kronos-base** weights (102M params) — best open-source quality,
and comfortable on the Mac mini M4 via PyTorch's `mps` backend.

## What was built

| Piece | Path |
|-------|------|
| Vendored model code | `backend/vendor/kronos/` (pinned upstream commit, MIT license preserved) |
| Forecast service | `backend/app/services/kronos_service.py` |
| Forecast MCP server | `backend/mcp_servers/forecast.py` (`forecast_prices`, `forecast_status`) |
| Chat runtime wiring | `backend/app/services/claude_chat_runtime.py` (registers `forecast` MCP) |
| HTTP endpoint | `GET /charts/forecast` in `backend/app/api/charts.py` |
| Response schema | `ForecastResponse` in `backend/app/schemas/charts.py` |
| Chart overlay | forecast cone in `frontend/src/components/StockChart.tsx` |
| API client | `fetchForecast` in `frontend/src/api/charts.ts` |
| Validation script | `backend/scripts/kronos_forecast_demo.py` |
| Extra deps | `backend/requirements-forecast.txt` |

## Design decisions

- **Isolated dependency.** torch is heavy, so it is *not* in
  `requirements.txt`. The forecast MCP server runs as its own stdio
  subprocess (like t212/marketdata/scheduler), keeping torch out of the
  main API process. The service imports torch/Kronos **lazily** — the rest
  of the app runs fine without the forecast deps installed, and callers get
  a clear `ForecastUnavailableError` (HTTP 503) instead of an import crash.
- **Uncertainty bands.** Kronos averages internally when `sample_count > 1`,
  so to get a *spread* we treat the predictor as a black box and draw N
  independent sample paths (`predict()` with `sample_count=1`), then take
  per-step p10/p50/p90 quantiles of the forecasted close. The vendored model
  is left untouched.
- **Shared data source.** Forecasts reuse `market_data.fetch_history`
  (yfinance + cache + synthetic fallback), so they line up with the charts.
- **Analysis only.** Per the project risk guardrails, forecasts are framed
  as probabilistic cones with explicit uncertainty — never as certainties,
  and never wired to trade execution.

## Setup (Mac mini M4)

```bash
cd backend
pip install -r requirements.txt -r requirements-forecast.txt

# One-command validation — first run downloads Kronos-base weights from HF:
python scripts/kronos_forecast_demo.py AAPL --plot
python scripts/kronos_forecast_demo.py PLTR --horizon 20 --samples 30
```

The script prints the resolved device (`mps` on the M4), per-path latency,
the forecast summary, and a band table; `--plot` saves a PNG.

### Environment overrides

| Var | Default | Notes |
|-----|---------|-------|
| `KRONOS_MODEL` | `NeoQuasar/Kronos-base` | or `Kronos-small` / `Kronos-mini` |
| `KRONOS_TOKENIZER` | `NeoQuasar/Kronos-Tokenizer-base` | |
| `KRONOS_DEVICE` | auto (`mps`→`cuda`→`cpu`) | force a device |
| `KRONOS_MAX_CONTEXT` | 512 (2048 for mini) | |

## Usage

- **Archie (chat):** call the `forecast_prices` tool, e.g. *"forecast PLTR
  over the next month"* → p10/p50/p90 close bands + expected return / P(up).
- **HTTP:** `GET /charts/forecast?ticker=PLTR&horizon=30&samples=20`
- **UI:** pass `forecast` to `<StockChart ticker="PLTR" forecast />` to
  overlay the cone (daily interval only).

## Follow-ups (not yet built)

- Phase 3: aggregate per-holding forecasts into a portfolio-value cone for
  scenario analysis; surface in `MetricGrid` / `AgentBrief`.
- Walk-forward backtest of forecast accuracy vs realised prices before
  trusting it for anything beyond illustration.
- Optional fine-tuning on Josh's actual holdings' history.

## Note on a pre-existing issue

While wiring this up I found that `claude_chat_runtime.py` imports
`build_security_hooks`, `build_subagents`, and the `_*_MCP_TOOLS` constants
from `claude_sdk_config.py`, but that module (as committed) does not define
them — so the chat runtime is currently un-importable. The forecast wiring
was written to **not** depend on those symbols (it defines
`_FORECAST_MCP_TOOLS` locally), so it adds no new coupling. The missing
definitions are a separate fix.
