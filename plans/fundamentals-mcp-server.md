# Fundamentals MCP Server — Implementation Plan

## 1. Overview

MyPF currently has strong coverage for **price data and technicals** via the `marketdata` MCP server (yfinance), but zero coverage for **fundamental financial data** — income statements, balance sheets, cash flows, valuation ratios, analyst estimates, insider activity, and SEC filings.

This plan adds a new `fundamentals` MCP server that wraps the [Financial Datasets API](https://api.financialdatasets.ai) to fill that gap. Once live, Archie will be able to answer questions like:

- "What does NVDA's free cash flow trend look like over the last 8 quarters?"
- "Show me AAPL's key valuation ratios vs. MSFT"
- "Any notable insider trades in PLTR recently?"
- "Pull the latest 10-Q for TSLA"
- "Run a basic DCF on META using analyst estimates and historical cash flows"

This is inspired by the open-source [dexter](https://github.com/virattt/dexter) project (15k+ stars) which uses the same Financial Datasets API for its agent tooling.

---

## 2. Data Source

### Financial Datasets API

- **Base URL**: `https://api.financialdatasets.ai`
- **Auth**: API key passed as `X-API-Key` header
- **Format**: JSON responses
- **Coverage**: US equities (SEC filers). Covers ~8,000+ tickers with standardized financial data.

### Key Endpoint Groups

| Endpoint Path | Description |
|---|---|
| `/financials/income-statements` | Revenue, EBITDA, net income, EPS, etc. |
| `/financials/balance-sheets` | Assets, liabilities, equity, debt, cash |
| `/financials/cash-flow-statements` | Operating/investing/financing cash flows, FCF |
| `/financials/ratios` | P/E, P/B, P/S, EV/EBITDA, ROE, current ratio, etc. |
| `/analyst-estimates` | Revenue/EPS estimates, consensus, analyst count |
| `/insider-trades` | Form 4 insider buy/sell activity |
| `/sec-filings` | 10-K, 10-Q, 8-K filing metadata + links |
| `/company/facts` | Company name, sector, industry, market cap, description |
| `/prices/snapshot` | Latest price (but we already have this via marketdata MCP) |

### API Key Setup

- Env var: `FINANCIAL_DATASETS_API_KEY`
- Add to `backend/app/core/config.py` as a new `Settings` field:
  ```python
  financial_datasets_api_key: str = Field(default="", alias="FINANCIAL_DATASETS_API_KEY")
  ```
- Pass into the MCP subprocess via the `env` dict (same pattern as `PYTHONPATH` and `DATABASE_URL` for the marketdata server)

---

## 3. Tools to Implement

Each tool follows the same pattern as `marketdata.py`: decorated with `@mcp.tool()`, accepts simple typed params, returns a JSON string via `_fmt()`. All tools take `ticker` as the first argument.

### 3.1 `get_income_statements`

Fetch income statement data for a company.

| Param | Type | Default | Description |
|---|---|---|---|
| `ticker` | `str` | required | e.g. AAPL, NVDA, MSFT |
| `period` | `str` | `"annual"` | `annual`, `quarterly`, or `ttm` |
| `limit` | `int` | `4` | Number of periods to return |

**Returns**: `{ ok, ticker, period, count, items: [{ date, revenue, cost_of_revenue, gross_profit, operating_income, ebitda, net_income, eps_basic, eps_diluted, shares_outstanding, ... }] }`

**API call**: `GET /financials/income-statements?ticker={ticker}&period={period}&limit={limit}`

### 3.2 `get_balance_sheets`

Fetch balance sheet data.

| Param | Type | Default | Description |
|---|---|---|---|
| `ticker` | `str` | required | |
| `period` | `str` | `"annual"` | `annual`, `quarterly`, or `ttm` |
| `limit` | `int` | `4` | |

**Returns**: `{ ok, ticker, period, count, items: [{ date, total_assets, total_liabilities, total_equity, cash_and_equivalents, total_debt, current_assets, current_liabilities, ... }] }`

**API call**: `GET /financials/balance-sheets?ticker={ticker}&period={period}&limit={limit}`

### 3.3 `get_cash_flow_statements`

Fetch cash flow statement data.

| Param | Type | Default | Description |
|---|---|---|---|
| `ticker` | `str` | required | |
| `period` | `str` | `"annual"` | `annual`, `quarterly`, or `ttm` |
| `limit` | `int` | `4` | |

**Returns**: `{ ok, ticker, period, count, items: [{ date, operating_cash_flow, capital_expenditure, free_cash_flow, investing_cash_flow, financing_cash_flow, dividends_paid, share_repurchases, ... }] }`

**API call**: `GET /financials/cash-flow-statements?ticker={ticker}&period={period}&limit={limit}`

### 3.4 `get_key_ratios`

Fetch financial ratios and valuation metrics.

| Param | Type | Default | Description |
|---|---|---|---|
| `ticker` | `str` | required | |
| `period` | `str` | `"annual"` | `annual`, `quarterly`, or `ttm` |
| `limit` | `int` | `4` | |

**Returns**: `{ ok, ticker, period, count, items: [{ date, pe_ratio, pb_ratio, ps_ratio, ev_to_ebitda, roe, roa, current_ratio, quick_ratio, debt_to_equity, gross_margin, operating_margin, net_margin, free_cash_flow_yield, ... }] }`

**API call**: `GET /financials/ratios?ticker={ticker}&period={period}&limit={limit}`

### 3.5 `get_analyst_estimates`

Fetch consensus analyst estimates (forward-looking).

| Param | Type | Default | Description |
|---|---|---|---|
| `ticker` | `str` | required | |
| `limit` | `int` | `4` | Number of estimate periods |

**Returns**: `{ ok, ticker, count, items: [{ date, period, revenue_estimate_avg, revenue_estimate_low, revenue_estimate_high, eps_estimate_avg, eps_estimate_low, eps_estimate_high, num_analysts, ... }] }`

**API call**: `GET /analyst-estimates?ticker={ticker}&limit={limit}`

### 3.6 `get_insider_trades`

Fetch recent insider trading activity (Form 4 filings).

| Param | Type | Default | Description |
|---|---|---|---|
| `ticker` | `str` | required | |
| `limit` | `int` | `20` | |

**Returns**: `{ ok, ticker, count, items: [{ filing_date, insider_name, insider_title, transaction_type, shares, price_per_share, total_value, shares_owned_after, ... }] }`

**API call**: `GET /insider-trades?ticker={ticker}&limit={limit}`

### 3.7 `get_sec_filings`

Fetch SEC filing metadata and links.

| Param | Type | Default | Description |
|---|---|---|---|
| `ticker` | `str` | required | |
| `filing_type` | `str` | `""` | Optional filter: `10-K`, `10-Q`, `8-K`, or empty for all |
| `limit` | `int` | `10` | |

**Returns**: `{ ok, ticker, filing_type, count, items: [{ filing_date, filing_type, description, filing_url, ... }] }`

**API call**: `GET /sec-filings?ticker={ticker}&type={filing_type}&limit={limit}`

### 3.8 `get_company_facts`

Fetch company profile and metadata (sector, industry, description, market cap).

| Param | Type | Default | Description |
|---|---|---|---|
| `ticker` | `str` | required | |

**Returns**: `{ ok, ticker, name, sector, industry, market_cap, description, employees, website, exchange, ... }`

**API call**: `GET /company/facts?ticker={ticker}`

### 3.9 `get_price_snapshot` — SKIP

The existing `marketdata` MCP server already provides `get_price_snapshot` via yfinance. No need to duplicate this. The fundamentals server focuses exclusively on data that yfinance does not provide well (standardized financials, ratios, estimates, insider trades, filings).

### 3.10 `financial_search`

A meta-tool that accepts a natural language query about a company's financials and returns a structured summary by calling the appropriate sub-tools internally.

| Param | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | required | Natural language query, e.g. "AAPL revenue growth over last 3 years" |
| `ticker` | `str` | `""` | Optional explicit ticker (extracted from query if not provided) |

**Implementation**: This is a convenience routing layer, NOT an LLM call. It uses keyword matching to determine which underlying tool(s) to call:

- Keywords `revenue`, `income`, `earnings`, `eps`, `profit` -> `get_income_statements`
- Keywords `debt`, `assets`, `equity`, `liabilities`, `balance` -> `get_balance_sheets`
- Keywords `cash flow`, `fcf`, `capex`, `dividends` -> `get_cash_flow_statements`
- Keywords `ratio`, `pe`, `valuation`, `margin`, `roe` -> `get_key_ratios`
- Keywords `estimate`, `forecast`, `consensus`, `analyst` -> `get_analyst_estimates`
- Keywords `insider`, `form 4`, `buying`, `selling` -> `get_insider_trades`
- Keywords `filing`, `10-k`, `10-q`, `8-k`, `sec` -> `get_sec_filings`
- Keywords `company`, `profile`, `sector`, `industry` -> `get_company_facts`
- Fallback: return `get_key_ratios` + `get_company_facts` as a general overview

**Returns**: Combined JSON from the matched sub-tool(s).

**Note**: This tool is a convenience for Archie. In practice, the LLM will usually call the specific tools directly. This meta-tool is most useful for ambiguous or broad queries.

---

## 4. File Structure

### New file

```
backend/mcp_servers/fundamentals.py    # ~200-300 lines
```

### Internal structure of `fundamentals.py`

```python
"""Financial Datasets API MCP server for fundamental data."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# ── Logging (file-based — stdout reserved for MCP protocol) ──
_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR", "/app/logs"))
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("fundamentals-mcp")
# ... same pattern as marketdata.py ...

_API_BASE = "https://api.financialdatasets.ai"
_API_KEY = os.environ.get("FINANCIAL_DATASETS_API_KEY", "")

mcp = FastMCP(
    "fundamentals",
    instructions=(
        "Fundamental financial data tools powered by Financial Datasets API. "
        "Use for income statements, balance sheets, cash flows, ratios, "
        "analyst estimates, insider trades, SEC filings, and company profiles."
    ),
)

def _fmt(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)

def _get(path: str, params: dict) -> dict:
    """Thin httpx wrapper for Financial Datasets API calls."""
    headers = {"X-API-Key": _API_KEY}
    # Strip None/empty params
    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    resp = httpx.get(f"{_API_BASE}{path}", params=clean, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()

@mcp.tool()
def get_income_statements(ticker: str, period: str = "annual", limit: int = 4) -> str:
    ...

# ... remaining tools ...

if __name__ == "__main__":
    mcp.run()
```

### Dependency

- Add `httpx` to requirements if not already present (yfinance likely already pulls it in, but verify).
- No other new dependencies needed. The `mcp` package and `FastMCP` are already used by `marketdata.py`.

---

## 5. Integration — Registering with the Claude Agent SDK Runtime

The new MCP server must be registered in **three places**, mirroring exactly how `marketdata` is set up today:

### 5.1 Tool list in `claude_sdk_config.py`

Add a new constant alongside `_MARKET_MCP_TOOLS`:

```python
_FUNDAMENTALS_MCP_TOOLS = [
    "mcp__fundamentals__get_income_statements",
    "mcp__fundamentals__get_balance_sheets",
    "mcp__fundamentals__get_cash_flow_statements",
    "mcp__fundamentals__get_key_ratios",
    "mcp__fundamentals__get_analyst_estimates",
    "mcp__fundamentals__get_insider_trades",
    "mcp__fundamentals__get_sec_filings",
    "mcp__fundamentals__get_company_facts",
    "mcp__fundamentals__financial_search",
]
```

Export it alongside the other tool lists so the three runtime files can import it.

### 5.2 Chat runtime — `claude_chat_runtime.py`

In `ClaudeChatRuntime._build_options()`, after the marketdata server registration block, add:

```python
fundamentals_script = _MCP_SERVER_DIR / "fundamentals.py"
if fundamentals_script.is_file():
    mcp_servers["fundamentals"] = {
        "type": "stdio",
        "command": sys.executable,
        "args": [str(fundamentals_script)],
        "env": {**_mcp_env, "FINANCIAL_DATASETS_API_KEY": settings.financial_datasets_api_key},
    }
    allowed_tools.extend(_FUNDAMENTALS_MCP_TOOLS)
```

Also add friendly labels to `_TOOL_LABELS`:

```python
# Fundamentals MCP tools
"mcp__fundamentals__get_income_statements": "Fetching income statements",
"mcp__fundamentals__get_balance_sheets": "Fetching balance sheets",
"mcp__fundamentals__get_cash_flow_statements": "Fetching cash flow data",
"mcp__fundamentals__get_key_ratios": "Looking up financial ratios",
"mcp__fundamentals__get_analyst_estimates": "Checking analyst estimates",
"mcp__fundamentals__get_insider_trades": "Checking insider trades",
"mcp__fundamentals__get_sec_filings": "Looking up SEC filings",
"mcp__fundamentals__get_company_facts": "Fetching company profile",
"mcp__fundamentals__financial_search": "Searching financial data",
```

### 5.3 Agent runtime — `claude_agent_runtime.py`

In `run_claude_analyst_cycle()`, after the marketdata MCP block, add the same server registration pattern. Import `_FUNDAMENTALS_MCP_TOOLS` from `claude_sdk_config`.

### 5.4 Task scheduler — `task_scheduler_service.py`

Same pattern: after the marketdata registration block, add the fundamentals server. This ensures scheduled tasks (e.g., weekly portfolio reviews) can pull fundamental data.

### 5.5 Subagent access

In `claude_sdk_config.py`, add `_FUNDAMENTALS_MCP_TOOLS` to the `researcher` and `quant` subagent tool lists so they can access fundamental data when delegated to.

### 5.6 Config setting

Add to `backend/app/core/config.py`:

```python
financial_datasets_api_key: str = Field(default="", alias="FINANCIAL_DATASETS_API_KEY")
```

---

## 6. Archie Enhancements

### 6.1 Update Archie's CLAUDE.md

Add a new section under "Tooling You Have":

```markdown
### Fundamentals MCP — use for ALL fundamental financial queries
- `get_income_statements` — revenue, net income, EPS, margins by period
- `get_balance_sheets` — assets, liabilities, equity, debt, cash position
- `get_cash_flow_statements` — operating/investing/financing cash flows, FCF
- `get_key_ratios` — P/E, P/B, EV/EBITDA, ROE, margins, debt ratios
- `get_analyst_estimates` — consensus revenue/EPS forecasts, analyst count
- `get_insider_trades` — Form 4 insider buy/sell activity
- `get_sec_filings` — 10-K, 10-Q, 8-K filing metadata and links
- `get_company_facts` — company profile, sector, industry, market cap
- `financial_search` — natural language search across all fundamental data
```

Update the Tool Routing Rule:

```markdown
When you need a price, quote, candle data, or technical indicator: **always use marketdata MCP**.
When you need financial statements, ratios, estimates, insider trades, or filings: **always use fundamentals MCP**.
When you need account balances, held positions, or to place/cancel orders: **use T212 MCP**.
```

### 6.2 Add DCF Analysis Instructions

Add a DCF valuation framework to Archie's instructions:

1. **Gather data** using fundamentals MCP (cash flow statements, income statements, key ratios, analyst estimates, balance sheets)
2. **Assumptions** — revenue growth from analyst estimates, FCF margin from trailing average, WACC 9-14% depending on cap, terminal growth 2-3%
3. **Output** — assumptions table, projected FCF schedule, implied share price, sensitivity table
4. **Risk check** — compare DCF-implied value against analyst price targets

### 6.3 System prompt update

Update the system prompt in `claude_chat_runtime.py` to mention fundamentals tools alongside market data tools.

---

## 7. Estimated Effort

| Component | Estimate |
|---|---|
| `fundamentals.py` — MCP server with all 9 tools | ~200-250 lines |
| `config.py` — add API key field | ~2 lines |
| `claude_sdk_config.py` — tool list + subagent updates | ~20 lines |
| `claude_chat_runtime.py` — server registration + labels | ~25 lines |
| `claude_agent_runtime.py` — server registration | ~15 lines |
| `task_scheduler_service.py` — server registration | ~15 lines |
| Archie CLAUDE.md updates | ~40 lines of markdown |
| DCF skill file (optional) | ~30 lines of markdown |
| **Total** | **~350-400 lines across all files** |

---

## 8. Open Questions

### API Pricing and Rate Limits

- **Pricing**: Financial Datasets API has a free tier (limited calls/month) and paid plans. Need to verify which tier we need for typical usage.
- **Rate limits**: Check per-second/per-minute limits. If needed, add retry with backoff in `_get()`.
- **Coverage gaps**: API focuses on US SEC filers. Non-US equities (e.g., LSE-listed stocks in the ISA) may not be available. Handle gracefully with a clear "not covered" message.

### Prioritization

If shipping incrementally:

1. **Phase 1** (highest value): `get_income_statements`, `get_balance_sheets`, `get_cash_flow_statements`, `get_key_ratios`, `get_company_facts` — covers 80% of fundamental analysis queries.
2. **Phase 2**: `get_analyst_estimates`, `get_insider_trades` — forward-looking analysis and sentiment.
3. **Phase 3**: `get_sec_filings`, `financial_search` — nice-to-haves.

### Error Handling

- Handle unknown tickers (404/422) gracefully.
- Consider caching: financial statements only update quarterly, so a 1-hour TTL cache could reduce API calls significantly.

### Overlap with yfinance

- yfinance provides some fundamental data but it is unreliable and inconsistently formatted. The Financial Datasets API provides standardized, well-documented data — this is a deliberate upgrade, not a duplication.

### Testing

- Smoke test: verify MCP server starts without error.
- Integration test: call each tool with a known ticker (e.g., AAPL) and assert response shape, gated behind API key env var.
