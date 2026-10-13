# OpenPF

**An open-source AI agent for your Trading 212 portfolio — on a leash.**

Archie (built on Claude) reads your Invest and Stocks ISA accounts, tells you how to put idle cash to work, sizes bets on the themes you believe in, reacts to the news that matters to *your* holdings, and builds you any view of your money you ask for. It can never trade on its own: every order is a proposal you approve.

Self-hosted. Your keys, your data, your machine.

![Home](docs/assets/home.png)

## Try it in a minute — no broker account needed

```bash
git clone https://github.com/joshuaramkissoon/openpf && cd openpf
make demo            # or ./scripts/demo.sh
```

Open http://localhost:5173. The demo runs against a **sandbox broker**: a built-in mock of the Trading 212 API with a realistic two-account portfolio, priced with real market data. Approve a trade and it fills against the mock. Nothing touches a real account.

Archie uses your Claude login (the `claude` CLI) or `ANTHROPIC_API_KEY`.

## What it does

**Home: what's my money doing, and what needs me?** One number, one chart, and a short list of decisions: proposals to approve and signals worth a look, deduplicated and capped so they don't pile up.

**Archie.** Ask anything about your portfolio. "How should I deploy my £5k idle cash?" gets you specific, sized, per-account proposals that respect your risk rails. Each one lands in Decisions for a one-tap approve.

![Archie](docs/assets/archie.png)

**Plays: sized bets on themes.** Tell it what you believe ("AI needs power", "the nuclear renaissance"). Archie researches a basket, then sizes it against your actual book:
- Conviction × volatility sets the total, capped as a share of your portfolio.
- Overlap with what you already hold counts towards the target.
- Entry is staged in tranches.
- Kill criteria are machine-checkable and checked daily.

Staging a tranche creates proposals. Nothing executes without you.

![A play](docs/assets/play-draft.png)

**Views: the app builds itself into what you want.** Start from presets ranked for your portfolio (what moved today, where your risk is, currency exposure, income, winners & losers, cash drag, ISA allowance, theme map), or ask in a sentence: "show me my portfolio split by theme with today's move." A fast model turns it into a declarative chart spec (treemaps, allocations, ranked bars, curves, with drill-down) in seconds, pinned in your sidebar and running live on your data. Edit it with a sentence and revert to any version. Specs are data, not code: they read a whitelisted set of portfolio data and nothing else. No network, no trading. Requests a view can't serve (trades, crypto, bank accounts) get a clear answer and the closest preset.

![A view Archie built](docs/assets/view.png)

**Recipes: Archie writes his own routines.** Say "every Friday, check my plays against their kill rules and tell me what moved" and Archie writes a recipe. A recipe is a real skill (the steps, the tools to use, the shape of the output) plus a trigger and a delivery. The trigger can be a schedule or an event such as "a holding reports this week" or "a holding moves 6% in a day". Results land on Home and in Activity, and in Telegram if you want. It ships with built-ins: morning brief, weekly review, watchlist review, play check-in, deploy idle cash, macro on my book, earnings week prep, big-move explainer and ISA allowance top-up. Refine any recipe with a sentence ("shorter", "skip crypto"). Every change is versioned with a diff you can revert. Recipes are instructions only: they can't add tools, touch risk rails or place orders, so the most a recipe can do is propose. Changes Archie suggests during an unattended run wait for your approval.

## Safety model

OpenPF is built so that a confused or prompt-injected agent cannot move your money.

- **Archie can only propose.** Its broker tools are read-only. To act, it calls `propose_trade`, which checks your risk rails and queues a proposal.
- **It only acts on its own where you've granted a standing permission.** In Settings → Permissions you can let Archie place, say, the next tranche of a play you already approved, rebalance trades back into your bands, or small idle-cash buys into approved plays. Each permission has a per-trade cap, a daily cap, an optional expiry and an off switch; one switch pauses them all. In live mode a permission only applies once you've confirmed it while live, and editing it asks again. Anything new, outside a play, over a cap or in another category still waits for you. Archie can suggest a permission when his track record earns it, but no agent tool can create or widen one. Every trade placed this way shows in Activity as "auto · <permission>", with a one-tap revoke.
- **One execution gate, enforced in code.** Orders reach the broker only through `execute_intent`, and only for proposals you approved, by hand or through a standing permission. It re-checks every rail: per-order and daily notional caps, duplicate window and cash.
- **An honest track record.** Every proposal is tracked forward from its price when proposed, at 1 day, 1 week, 1 month and 3 months against a benchmark (SPY by default), including the ones you rejected or let expire. Activity → Track record shows the hit rate by category with the sample size, and Archie quotes the same numbers.
- **Execution keys never enter an agent process.** Trading 212 lets you create separate read and trade keys. Only the API server holds the trade key. Crypto exchange keys must be trade-only (keys that can withdraw are refused on connect) and are never given to any agent process. Agent hooks also block calls to the app's own API, to trading212.com and to the crypto exchanges' APIs.
- **Safe defaults.**
  - New installs run in paper mode on T212's demo environment.
  - The server binds to localhost.
  - `OPENPF_API_TOKEN` locks the API when you expose it on your LAN or Tailscale.
  - Telegram ignores everyone not on your allowlist.
- **Leveraged desk (labs, off by default):** an optional regime-aware 3× ETP engine. It only places orders itself if you switch on auto-execute, and always within its own hard daily rails. Agents can tighten those rails, never loosen them.

## Crypto (Kraken, Coinbase Advanced Trade, Binance)

Crypto sits in the same book as your shares: it counts toward the portfolio total, Home's book-by-play, holdings, equity history and risk metrics, all in GBP. Trades go through the same approval gate. OpenPF talks to exchanges through [CCXT](https://github.com/ccxt/ccxt), so the exchange is a setting (Settings → Crypto):

| Exchange | Market orders in | Entry-tier fee (maker / taker) | UK status |
|---|---|---|---|
| **Kraken** (Kraken Pro) | GBP | 0.25% / 0.40% | FCA-registered for cryptoasset activities (Payward Ltd) |
| **Coinbase** Advanced Trade | GBP | 0.60% / 1.20% | FCA-registered (CB Payments Ltd). Buying in the simple Coinbase app adds a spread and a separate fee on top |
| **Binance** Spot | USDC | 0.10% / 0.10% | Not onboarding new UK customers since October 2023 and not FCA-authorised. Listed for users elsewhere |

Fees fall with volume. Each crypto proposal shows its estimated fee and slippage before you approve it.

**Keys must be trade-only, never withdraw.** A key that can only trade can't move coins off the exchange, even if it leaked. On connect, OpenPF checks the key's permissions with the exchange and **refuses any key that can withdraw or transfer**:

- **Coinbase:** reads the key's permissions (`/api/v3/brokerage/key_permissions`) and refuses `can_transfer`.
- **Binance:** reads the key's restrictions (`/sapi/v1/account/apiRestrictions`) and refuses withdrawals, internal transfer, universal transfer, margin and futures.
- **Kraken:** has no "what can this key do" endpoint, so OpenPF calls the withdrawal-*info* endpoint (a read that never moves money) with a made-up address name. Kraken answers "Permission denied" only when the key has no withdraw permission; any other answer means it does, and the key is refused. If the check can't run, the key is refused.

The verified key is stored like the Trading 212 keys: in the app's database, shown back only as its last four characters, never given to Archie or any MCP process. If the key changes (DB or `.env`), it's unused until it's verified again. Archie's `crypto` tools are read-only and keyless: balances come from the app's stored snapshots, prices from public market data. Agent hooks also block the exchanges' API hosts.

**Connect Kraken:**

1. Kraken Pro → Settings → API → Create API key.
2. Funds permissions: tick **Query**. Leave **Deposit** and **Withdraw** unticked.
3. Orders and trades: tick **Query open orders & trades**, **Query closed orders & trades** and **Create & modify orders**. Leave **Cancel & close orders**, **Export data**, Earn and WebSocket permissions unticked. **Query ledger entries** is optional (lets OpenPF read deposits so they aren't counted as gains).
4. Optionally restrict the key to your machine's IP address.
5. In OpenPF: Settings → Crypto → Kraken, paste the key and the private key (secret) → **Verify & connect**.
6. Deposit GBP on Kraken. Your crypto cap (Settings → Risk rails, default **5% of the whole book**) limits how much crypto any proposal can add.

**The Starter crypto play** (Plays → New → "Or start from a template", or ask Archie "I want to get into crypto but I'm nervous") drafts a small plan: 1–3% of the book from conviction, never above the cap, bought in equal weekly amounts over 8–12 weeks (Bitcoin, plus Ether at 20% if you want). It comes with kill rules (pause new buys if the sleeve falls 40% from its peak; stop at the cap) and a plain-English explanation of volatility, fees, custody and tax. Each weekly buy is an ordinary play-tranche proposal you approve.

**UK facts** (not advice):

- Crypto held on an exchange is **not protected by the FSCS**. If the exchange fails or is hacked, you may not get it back.
- Selling, swapping or spending crypto is a **disposal for Capital Gains Tax**. Gains above the annual exempt amount are taxable, and you need records of every buy and sell.
- UK retail investors can also get crypto exposure through **crypto exchange-traded notes (ETNs)** at a regular broker. That's a different product with its own costs and risks.

In sandbox mode the crypto account is a built-in mock exchange with fake keys and real prices (offline fixtures as a fallback). Real exchange keys are ignored, and the real exchange client refuses to start or send a request.

## Connect your Trading 212 account

1. In Trading 212: Settings → API → generate a key for each account (Invest, Stocks ISA). Make one **read** key and, if you want to approve trades from OpenPF, a separate **trade** key (ideally IP-restricted).
2. Set up and run:

   ```bash
   cd backend && cp .env.example .env    # add ANTHROPIC_API_KEY (or use your claude login)
   python3 -m venv ../.venv && ../.venv/bin/pip install -r requirements.txt
   ../.venv/bin/uvicorn app.main:app --port 8000
   # second terminal
   cd frontend && npm install && npm run dev
   ```

3. Open http://localhost:5173 → Settings → Broker, paste your keys, and pick paper or live.

Keep it running on an always-on machine with `./scripts/serve.sh` (see `docs/running-on-mac-mini.md`), or use Docker with `make up`.

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude access (optional if you're logged in with the `claude` CLI) |
| `BROKER_MODE` | `paper` (default) or `live`; Settings → Broker overrides it once saved |
| `T212_BASE_ENV` | `demo` (default), `live`, or `sandbox` |
| `OPENPF_SANDBOX` | `1` = use the built-in mock broker; real broker keys are ignored, and the DB, Archie's runtime (artifacts + memory) and views default to `.demo/`. The app refuses to start if pointed at the live instance's data |
| `OPENPF_RUNTIME_DIR` | Archie's per-instance folder (working dir, `artifacts/`, `memory/`). Default `.claude/runtime` |
| `CORS_ORIGINS` | Extra origins allowed to call the API. State-changing requests from other sites are refused |
| `OPENPF_API_TOKEN` | Require a token for the API and chat (set it before exposing beyond localhost) |
| `OPENPF_USER_NAME` | How Archie addresses you |
| `TOGETHER_API_KEY` | Fast view building on Together AI (otherwise views use Claude; with neither, the closest preset) |
| `VIEWS_SPEC_PROVIDER` / `VIEWS_SPEC_MODEL` | `together` (default) or `anthropic`; Together model id (default `deepseek-ai/DeepSeek-V4.1-Flash`) |
| `VIEWS_MODEL` / `CLAUDE_PLAYS_MODEL` | Models for the custom-code view fallback and play drafting |
| `MAX_CRYPTO_PCT_OF_BOOK` | Crypto cap as a fraction of the whole book (default `0.05`); Settings → Risk rails overrides it |
| `CRYPTO_EXCHANGE` / `CRYPTO_API_KEY` / `CRYPTO_API_SECRET` | Optional. A crypto key from `.env` stays unused until it's verified as trade-only in Settings → Crypto |

## How it's built

```
frontend/  React + Vite + Tailwind + shadcn
  src/app/            shell, routing, data hooks
  src/features/       home · plays · views (agent-built pages + sandbox runtime)
backend/   FastAPI + SQLite + APScheduler + Claude Agent SDK
  app/services/       portfolio, proposals + execution gate, plays, views builder, scheduler
  app/services/sandbox_broker.py   mock Trading 212 for demos and development
  mcp_servers/        Archie's tools: trading212 (read-only), marketdata, fundamentals,
                      proposals, plays, views, scheduler, watchlist, …
.claude/runtime/      Archie's identity, skills and (gitignored) memory
```

Run the tests with `cd backend && ../.venv/bin/python -m pytest -q`. The frontend builds with `cd frontend && npm run build`.

## Status

This is a personal project, released because the pattern seemed worth sharing: an agent that operates a real brokerage account behind a hard approval gate, and an app that grows the views you ask for. Expect rough edges. Issues and PRs welcome.

## Disclaimer

OpenPF is software for managing your own account. It is not financial advice, and nothing it says is a recommendation. You are responsible for every trade you approve. Trading 212's API is in beta and may change.

## License

MIT
