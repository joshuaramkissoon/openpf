# Connections: brokers, exchanges and aggregators

OpenPF can hold many accounts at once: Trading 212 Invest and ISA, several crypto exchanges, Interactive Brokers, Alpaca, and brokers that have no public API (Robinhood, Schwab, Fidelity, Vanguard and others) through an aggregator. All of them feed one book. Connections are **read-only first**. OpenPF trades only where it can, and only after you add a trade key.

Research checked October 2026 against official docs where reachable. This is a self-hosted app, so each user brings their own keys. We use no scraping and no unofficial libraries that break a provider's terms.

## What's viable

| Provider | Official API | Read-only / scoped keys | Auth model | Where | Self-hoster can get keys alone? | In OpenPF |
|---|---|---|---|---|---|---|
| **Trading 212** | Yes (public API, beta) | Yes: per-key permissions (Account data, Portfolio, History, Metadata, Orders) and an optional IP allowlist | API key + secret (Basic) | UK, EU | Yes | **Read + trade** (separate read key and IP-restricted execution key) |
| **Interactive Brokers: Flex Web Service** | Yes | Read-only by design: it can only return reports | Token (6 h–1 y expiry, optional IP restriction) + Flex Query ID | Worldwide incl. IBKR UK | Yes, in Client Portal | **Read** (positions + cash, daily) |
| IBKR Client Portal / Web API | Yes | No read-only scope | Local gateway + browser login, or OAuth (third parties need IBKR approval) | Worldwide | Gateway yes, OAuth no | Not built: needs a running gateway and frequent re-login |
| **Alpaca** (Trading API) | Yes | **No**: a key can trade. The Trading API can't move money out | Key ID + secret headers | US; many non-US countries, rolling out by country. UK live accounts unconfirmed; paper open to anyone | Yes | **Read** (only GET `/v2/account`, `/v2/positions`); trade not enabled |
| Schwab Trader API | Yes | No | OAuth app on Schwab Developer; refresh token expires after **7 days**, then a manual login | US | Needs an approved app | Not built: use SnapTrade |
| tastytrade | Yes | Read and trade OAuth scopes | Personal OAuth app + refresh token; 15-min session tokens | US (plus some non-US) | Yes | Not built yet: SnapTrade covers it |
| Robinhood (stocks) | **No** official API | — | — | US | — | Via SnapTrade only |
| Robinhood Crypto Trading API | Yes | Yes: per-action permissions (read-only possible) | Ed25519 key pair; you register the public key | US only | Yes | Not built yet (planned) |
| **SnapTrade** (aggregator) | Yes | Yes: `connectionType: read` (default) | Personal API key (clientId + consumerKey), HMAC-SHA256 signed requests; you log in to the broker on SnapTrade's portal | US, Canada, some EU brokers (Robinhood, Schwab, Fidelity, Vanguard, Webull, E*TRADE, IBKR, …) | **Yes: free Personal key for your own accounts** (snaptrade.com/personal listed 20 connections; the pricing page shows different tiers, so check current limits) | **Read** |
| Plaid Investments | Yes | Read-only product | Plaid Link (JS) + access tokens + webhooks | US, Canada (Trial); production needs review | Trial plan: US/CA developers, 10 items free | Not built: heavier client flow, US/CA only. SnapTrade covers the same brokers |
| Yodlee / Finicity / Mesh / Vezgo | Yes | Varies | Business contracts | Varies | **No**: commercial onboarding | Not viable |
| **Kraken** | Yes | Yes: Query Funds, Query open/closed orders & trades, Query ledger | Key + secret (HMAC) | UK (FCA-registered, GBP pairs), EU, US | Yes | **Read or trade** (never withdraw) |
| **Binance** | Yes | Yes: "Enable Reading" only | Key + secret (HMAC); IP restriction recommended | Not onboarding new UK users since Oct 2023 (an FCA application is reported for 2027). Elsewhere yes | Yes | **Read or trade** (never withdraw, no margin/futures) |
| **Coinbase** (Advanced Trade, CDP keys) | Yes | Yes: View / Trade / Transfer | CDP key name + private key (JWT). CCXT signs with **ECDSA**, so choose ECDSA, not the Ed25519 default | UK (FCA-registered), EU, US | Yes | **Read or trade** (Transfer refused) |

### Conclusions

- **A typical UK setup:** Trading 212 Invest + ISA (read + trade), Kraken (start read-only, add a trade key when wanted), and optionally Binance (read-only; Binance isn't onboarding new UK users).
- **Cleanest read-only broker path:** IBKR Flex Web Service. A token plus a query id, reports only, and IP restriction if you like.
- **Broad coverage for everyone else:** SnapTrade with a free **Personal** key. That key is meant for exactly this case: one person reading their own accounts. Personal keys skip user registration; SnapTrade resolves the user from the key. OpenPF also handles a Commercial key by registering one SnapTrade user.
- **Not built, and why:** Schwab direct (7-day manual re-auth; use SnapTrade), Robinhood stocks (no official API; use SnapTrade), Plaid (Link/webhook flow, US/CA trial only), IBKR Web API (gateway + session babysitting). tastytrade and Robinhood Crypto are viable and are next on the list.

## How it works

- **Model:** `backend/app/services/connections_service.py`. A connection holds one or more accounts. Each account has an `account_kind` that snapshots, the book, holdings and intents use:
  - `invest`, `stocks_isa`: Trading 212. The keys stay in the existing `credentials` row, so behaviour is unchanged. The connection is a view over them.
  - `crypto`: the original single crypto connection, which keeps its `crypto_config` row. Existing installs need no migration.
  - `crypto:<exchange>`, `ibkr:<account id>`, `alpaca:<live|paper>`, `snap:<hash>`: connections added in Settings → Accounts, stored in the `connections` row.
- **Capabilities:** every connection can `read`. Only Trading 212 and crypto connections added with a trade key can `trade`. `account_kind="crypto"` in a proposal resolves to the exchange you trade on.
- **Secrets:** fields marked secret are Fernet-encrypted at rest (`secret_box.py`). The key comes from `OPENPF_SECRETS_KEY` or from `openpf-secrets.key` next to the SQLite DB (mode 0600). **Back that file up with the DB.** Without it, saved keys can't be read and the connections show an error asking you to reconnect. Secrets are never returned by the API, and an agent process can never decrypt them. Trading 212 keys and the original crypto slot keep their existing storage, which is not encrypted.
- **Sync:** each portfolio refresh fetches every connection at the same snapshot timestamp. IBKR Flex reports are fetched at most hourly, SnapTrade every 15 minutes, and crypto and Alpaca on every refresh. A provider that is throttled or briefly down carries its last rows forward, so it never vanishes from the totals, and the error shows on the connection. When a connection first joins, its value is recorded as an opening contribution, so connecting an account doesn't count as a gain.
- **FX:** each account keeps its base currency. Positions are converted with the existing `fx` helpers, so totals are in GBP (or USD).

## Safety

- **The gate.** `execution_service.execute_intent` calls `broker_router.refusal_for` before it approves or claims anything. An intent for a read-only account is refused with "Place it yourself in <broker>", including:
  - when a caller tries to re-route it to a tradable account;
  - under a standing permission (`permissions_service.evaluate` uses the same check).

  Unknown accounts are refused too.
- **Suggestions, not orders.** `propose_trade` with a read-only account records a `proposed` intent marked `read_only` with `place_in`. The Decisions tile shows **Place it in <broker>** instead of Approve. These suggestions are tracked like any other proposal.
- **Withdrawal is always refused.** Crypto keys that can withdraw or transfer are refused in both read and trade mode. A trade connection also needs a key that can trade.
- **Agents never see keys or hosts.** `mcp_registry` scrubs `IBKR_*`, `ALPACA_*`, `APCA_*`, `SNAPTRADE_*` and other provider env vars, plus `OPENPF_SECRETS_KEY`, from every child process. The Bash/WebFetch hooks block:
  - the provider hosts: interactivebrokers.com, alpaca.markets, snaptrade.com, schwabapi.com, tastytrade, robinhood.com;
  - `/api/connections`;
  - the secrets module and key file.
- **Host pinning.** Every provider client checks each request against that provider's own API hosts and doesn't follow redirects.
- **Sandbox.** `build_provider_client` returns only offline fixtures (an IBKR account, an Alpaca paper account, Robinhood and Schwab accounts via SnapTrade). The fixtures accept only the fake sandbox credentials, which the form prefills. The real HTTP clients refuse to be built, or to reach any host, in a sandbox process. Crypto connections other than the mock Kraken are read-only fixtures. `verify_isolation` asserts all of this at startup.

Tests: `backend/tests/test_connections.py`.

## Sources

- IBKR Flex Web Service — [Generate a report](https://www.interactivebrokers.com/docs/web-api/flex-web-service/using-flex-web-service/generate-the-report), [error codes](https://www.interactivebrokers.com/docs/web-api/flex-web-service/error-codes) (1018: 1 req/s, 10/min per token; 1019: generation in progress)
- SnapTrade — [Getting started](https://docs.snaptrade.com/docs/getting-started), [Personal vs Commercial](https://docs.snaptrade.com/docs/personal-vs-commercial), [Request signatures](https://docs.snaptrade.com/docs/request-signatures), [Personal plan](https://snaptrade.com/personal), [OpenAPI](https://docs.snaptrade.com/openapi.yaml)
- Plaid — [Trial plan](https://support.plaid.com/hc/en-us/articles/39994173227159-What-is-the-Plaid-Trial-plan)
- Alpaca — [Non-US live accounts](https://alpaca.markets/learn/live-trading-account-non-us/)
- Schwab — refresh-token behaviour per [schwabr docs](https://stat.ethz.ch/CRAN/web/packages/schwabr/schwabr.pdf)
- tastytrade — [OAuth2](https://developer.tastytrade.com/docs/authentication/oauth2/)
- Robinhood — [Crypto Trading API](https://robinhood.com/us/en/support/articles/crypto-api)
- Kraken — [API keys](https://docs.kraken.com/exchange/guides/rest/api-keys); Binance UK — [The Block](https://www.theblock.co/amp/post/257721/binance-to-temporarily-stop-accepting-new-uk-users-after-fca-restriction)
- Trading 212 — [API docs](https://docs.trading212.com)
- Coinbase/CCXT — CCXT 4.5's Coinbase signer supports ECDSA (ES256) CDP keys only (checked in the installed library)
