# T212 Execution Key Split + Orders Visibility — Design

**Date:** 2026-06-02
**Status:** Approved (build to completion)

## Problem

Today every Trading 212 call — reads *and* writes — uses a single API key per account
(`invest`, `stocks_isa`), stored in `ConfigStore`. We want:

1. **A dedicated, IP-restricted execution (write) key per account**, separate from the
   IP-unrestricted read keys. Reads must never break when the machine's IP rotates; only
   writes use the restricted key.
2. **Robust execution workflows** across every entry point (Execution tab, Archie chat,
   leveraged desk) using the execution key, with explicit account choice (no silent
   `invest` default).
3. **Visibility + control over broker orders** — see in-flight/pending orders, cancel them
   manually, browse order history with metadata. None of this is surfaced today.
4. **Toasts (shadcn sonner) + robust typed error handling** — insufficient funds, IP
   restriction, auth failure, rate limit, risk-guard blocks all produce clear feedback.

## Key facts about the current system (verified)

- Credentials resolve env → DB `ConfigStore` (`app_config` table, key `credentials`), keyed
  per account, each `{t212_api_key, t212_api_secret, enabled}`. `get_credentials()` lets env
  vars override stale DB values.
- Two client paths share those creds:
  - Sync `T212Client` (`app/services/t212_client.py`), built via
    `build_t212_client(config, account_kind)`. Used by `execution_service` and
    `leveraged_service`. HTTP Basic auth. 401/403 → `T212AuthError`, 429 →
    `T212RateLimitError`, other ≥400 → `T212Error`.
  - Async MCP server (`mcp_servers/t212.py`) for Archie. Resolves creds from **env vars only**,
    injected by `claude_sdk_config.resolve_t212_env()` (which reads the same DB). 403 → IP/perms.
- `execute_intent()` defaults the account to `invest` from `intent.meta.account_kind`.
  Leveraged is hard-pinned to `stocks_isa`.
- Order endpoints already wrapped: `get_pending_orders`, `get_order_history` (slow, walks ALL
  pages with 2.5s sleeps), `cancel_order`, plus all `place_*`. **No REST endpoints** expose
  live orders to the frontend; **no UI** surfaces them. Execution tab shows the app's *intent*
  queue, not broker orders.
- Frontend: shadcn fully set up; `sonner` installed but `<Toaster />` **not mounted**. Errors
  show in a single App-level `<Alert>` banner. Nav is the collapsible sidebar (`app-sidebar.tsx`),
  Trading group has Execution + Leveraged. API layer = axios + manual polling.

## Approach

### Credential model (read/write split)

Extend each account's credential entry in `ConfigStore`:

```
credentials[account] = {
  t212_api_key, t212_api_secret, enabled,   # READ key (unchanged field names — back-compat)
  exec_api_key, exec_api_secret,            # NEW: execution (write) key, IP-restricted
  exec_enabled,                             # NEW: live execution wired for this account?
}
```

- Keep `t212_api_key`/`secret` as the **read** key → all existing read paths + env-override
  logic unchanged.
- Add `exec_*` fields, normalized through the same `:`/`Basic ` paste-handling as read keys.
- Env fallbacks: `T212_EXEC_API_KEY_INVEST`, `T212_EXEC_API_SECRET_INVEST`,
  `T212_EXEC_API_KEY_STOCKS_ISA`, `T212_EXEC_API_SECRET_STOCKS_ISA`.
- `credentials_public()` exposes `read_configured`, `exec_configured`, `exec_enabled` per
  account (never the secrets).

### Routing the split

- **Sync client:** `build_t212_client(config, account, purpose="read"|"execute")`. The
  `T212Client` carries `purpose`; `place_*`/`cancel_order` assert `purpose == "execute"` and
  raise `T212Error` otherwise (defense-in-depth: the read key can never place a trade). Read
  call-sites default to `purpose="read"` → no behavior change. `execution_service` and
  `leveraged_service` build an `"execute"` client for the write call.
- **Async MCP (Archie):** `resolve_t212_env()` also injects `T212_EXEC_API_KEY_*`.
  `_resolve_credentials(account, scope="read"|"execute")` picks the pair; write tools
  (`place_*`, `cancel_order`) pass `scope="execute"`. Reads stay on the read key.

### Explicit account choice at execution time

- `execute_intent(db, intent_id, *, force_live, account_kind=None)` — `account_kind` from the
  request overrides `intent.meta.account_kind`; validated to `invest`/`stocks_isa`.
- `POST /agent/intents/{id}/execute` body gains optional `account_kind`.
- Execution-tab UI: an account selector on the Execute control; the chosen account is sent.
- Archie execution subagent prompt: must confirm/echo the target account, never assume.

### Orders + execution-health REST (`app/api/orders.py`, mounted at `/orders`)

- `GET /orders/pending?account=invest|stocks_isa|all` — live pending orders (read key),
  enriched with instrument name where cheap.
- `GET /orders/history?account=&ticker=&limit=` — bounded fast fetch. Add
  `T212Client.get_orders_history_page(limit, cursor=None)` (single page, no full backfill).
- `DELETE /orders/{order_id}?account=` — cancel (exec key). Typed result.
- `GET /orders/execution-health` — per account: read/exec configured, exec_enabled, broker
  mode (paper/live), base env (demo/live), last test result + classification, current egress IP.
- `POST /orders/execution-test?account=` — probe the exec key with a lightweight authenticated
  GET; classify `ok|ip_restricted|auth_failed|error`; return egress IP. Persist last-result.

Egress IP: server-side GET to an IP-echo service (`https://api.ipify.org`), short timeout,
cached briefly; `null` on failure (UI degrades gracefully).

### Typed error envelope

Map exceptions to `{detail, code, meta}` with `code ∈ {insufficient_funds, ip_restricted,
auth_failed, rate_limited, risk_blocked, validation, broker_error}`:

- our cash guard + T212 "insufficient" body → `insufficient_funds`
- 403 (exec key) → `ip_restricted` (meta carries egress IP)
- 401 → `auth_failed`; 429 → `rate_limited`; risk guards/rails → `risk_blocked`
- other 4xx/5xx → `broker_error`

A small `classify_t212_error()` helper centralizes this; the orders router and the intent
execute endpoint return the envelope as HTTPException detail (`{code, message, meta}`).

### Frontend

- Mount `<Toaster richColors position="top-right" />` in app root (providers).
- `api/orders.ts`: `getPendingOrders`, `getOrderHistory`, `cancelOrder`, `getExecutionHealth`,
  `testExecutionKey`. Add `parseApiErrorCode()` → `{code, message, meta}` for toast routing.
- `OrdersWorkspace` component (shadcn Table/Card/Badge/Dialog/Select/Tooltip):
  - Execution health card: per-account read/exec status, broker mode, egress IP (copy),
    Test button, last-test badge.
  - Pending orders table + Cancel (confirm dialog → toast). Auto-refresh ~20s while visible.
  - Order history table with ticker filter.
- Nav: add `orders` section (the known 5-step pattern: SectionKey, NAV item, label,
  description, render case).
- IntentQueue: account selector feeding the execute call.
- Settings: per-account Execution key/secret fields under the read-key fields, each with a
  Test button; show exec_enabled + last test status.

## Where the key goes

Recommended: **Settings → Credentials (DB-backed)** — both the sync client and Archie's MCP
read from `ConfigStore`, so one paste keeps everything in sync and makes re-paste-on-rotation
one click. Fallback: `.env` `T212_EXEC_API_KEY_*` / `T212_EXEC_API_SECRET_*` (used only if DB
empty).

## Out of scope (YAGNI)

- Manual order-placement ticket in the Orders tab (view + cancel only; placement stays via
  Archie / intent queue / leveraged).
- Order modification/amend, trailing/OCO orders, deep history pagination beyond the first page
  (ticker filter covers lookups), CSV export UI.

## Testing

- Unit (isolated, no live DB/keys): credential split normalization + public projection;
  `build_t212_client` purpose routing; write-on-read-client guard; error classifier;
  `resolve_t212_env` exec-key injection; account_kind override in `execute_intent` (paper mode,
  in-memory DB).
- Endpoint tests via `TestClient` for the orders router shape (mock the T212 client).
- Frontend: `tsc` typecheck + `vite build`.
- Note: pre-existing `test_smoke.py` requires live portfolio data and is expected to fail in
  an isolated worktree; not a regression signal for this work.
