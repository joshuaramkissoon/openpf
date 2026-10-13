# Settings audit (Oct 2026)

Every setting in the app, who needs it and how often, its default, and what
happened to it in the Settings redesign. The old Settings had 13 tabs; the new
one has 6 pages, each named for what you're trying to do:

| Page | For |
|------|-----|
| **Accounts** | Connect brokers and exchanges, read-only or read + trade. Trading 212 keys open inline. |
| **Safety** | Order limits (with a plain-English summary), paper vs live, standing permissions. |
| **Archie** | Models, data-provider keys, usage. |
| **Notifications** | Telegram, and how chatty it is. |
| **Appearance & privacy** | Theme, privacy mode, company logos. |
| **Advanced** | Background jobs, Labs (leveraged desk), diagnostics. |

Help moved to the foot of the nav. Saving is inline everywhere: fields save
when you leave them, and toggles save straight away. Each change shows
"Saving…" then "Saved" (or the error) next to its label. There are no Save
buttons. Expert knobs sit behind a disclosure. Each setting can be found
with ⌘K (type "telegram" or "daily limit").

## Inventory

**Who** = everyone / connected-trading users / power users. **How often** =
once (setup), rarely, sometimes.

| Setting (old place) | Who · how often | Default | Decision | Where now |
|---|---|---|---|---|
| Theme (General) | everyone · once | System | **keep** | Appearance & privacy |
| Privacy mode (General) | everyone · sometimes (P key) | Off | **keep**; Select → segmented control | Appearance & privacy |
| Company logos (new, logos branch) | everyone · once | — | **slot reserved** (`LOGOS-SETTING-SLOT`) | Appearance & privacy |
| Connections list (Connections) | everyone · once | — | **keep**, renamed Accounts | Accounts |
| Crypto read/trade choice (Connections guide) | crypto users · once | Read | **reworked**: two explicit cards, *Read only* / *Read + trade*, with the exchange's exact key boxes as a checklist | Accounts → Add |
| Crypto page (Crypto) | crypto users · rarely | — | **removed** as a page. Its status line duplicated Accounts. The UK notes are now a disclosure on Accounts, and the cap moved to Safety | — |
| Broker mode Paper/Live (Broker & keys) | trading users · once | paper (sandbox: live) | **move**; now asks for confirmation before going live | Safety → Orders |
| T212 environment Demo/Live (Broker & keys) | T212 users · once | demo | **move**, labelled Practice / Real | Accounts → Trading 212 → Keys |
| T212 read key + secret ×2 accounts (Broker & keys) | T212 users · once | — | **keep**; hidden behind *Add / Replace*, so you see status rather than four empty password boxes | Accounts → Trading 212 → Keys |
| T212 execution key + secret ×2 (Broker & keys) | T212 traders · once | — | **keep**, renamed "Trade key", with *Test* next to it | same |
| Account sync on/off ×2 (Broker & keys) | rarely | on | **keep** | same |
| Live execution on/off ×2 (Broker & keys) | rarely | on | **keep**; only shown once a trade key exists | same |
| Scheduler / automation engine (Broker & keys) | power · once | env `INPROC_SCHEDULER_ENABLED` | **move**, renamed "Run scheduled jobs" | Advanced → Background |
| Auto-execute leveraged trades (Broker & keys) | Labs only | off | **move**; only shown when the leveraged desk is on | Advanced → Labs |
| Leveraged desk on/off (Labs) | Labs only | off | **hide** in Advanced → Labs (the engine is disabled by default) | Advanced → Labs |
| Max single order £ (Risk rails) | everyone · sometimes | £500 | **keep**, renamed "Per order"; saves inline; spotlit by `?focus=` | Safety → Limits |
| Max daily notional £ (Risk rails) | everyone · sometimes | £1,500 | **keep**, renamed "Per day" | Safety → Limits |
| Max position weight 0–1 (Risk rails) | sometimes | 0.25 | **keep**, entered as % ("Largest holding") instead of a 0–1 fraction | Safety → Limits |
| Max crypto % of book (Risk rails) | crypto users · rarely | 5% | **keep**, renamed "Crypto cap" | Safety → Limits |
| Duplicate window sec (Risk rails) | rarely | 90 s | **keep**, behind *More limits* | Safety → More limits |
| Standing permissions + kill switch (Permissions) | sometimes | none / not paused | **merge** into Safety | Safety → Standing permissions |
| Per-job Claude models (Models) | power · rarely | tuned defaults | **keep**: the chat model is shown, the other 7 jobs sit behind *Per-job models* | Archie |
| Quick-job providers + Together price (Models) | power · rarely | tuned defaults | **keep**, behind a disclosure | Archie |
| Data-provider API keys (Data providers) | power · once | none | **merge** into Archie, behind *API keys* | Archie → Data providers |
| Usage (Usage) | sometimes | — | **merge** into Archie, behind *Show usage* | Archie → Usage |
| Telegram enabled (Notifications) | Telegram users · once | off | **keep**; can't be switched on until a token and chat ID are set | Notifications |
| Bot token, chat ID (Notifications) | once | — | **keep**, with how-to-get-it help | Notifications |
| General updates on/off (Notifications) | sometimes | on | **keep**, as "How chatty": *Strong ideas only* / *Every run* | Notifications |
| High-conviction threshold 0–1 (Notifications) | rarely | 0.68 | **keep** as % ("A strong idea is at least"), behind *Fine-tune* | Notifications → Fine-tune |
| Poll commands, allowed user IDs (Notifications) | rarely | on / [] | **keep**, behind *Fine-tune* | Notifications → Fine-tune |
| Diagnostics panel (Diagnostics) | rarely | — | **move**, behind *Show diagnostics* | Advanced |
| Help guide (Help) | sometimes | — | **keep** at the foot of the nav | `/settings/help` |
| "Control Tower" header + full-panel mode (`SettingsPanel`) | nobody | — | **removed** (dead: only the per-tab split was used) | — |

Code removed: `components/SettingsPanel.tsx` (the 800-line form) and
`features/settings/crypto.tsx`.

## Links that keep working

Old tab routes redirect, keeping their query string (`registry.ts`,
`LEGACY_ROUTES`):

| Old | New |
|---|---|
| `/settings/general` | `/settings/appearance` |
| `/settings/connections`, `/settings/crypto` | `/settings/accounts` |
| `/settings/broker` | `/settings/accounts?manage=t212` |
| `/settings/risk?focus=…` ("Change limits") | `/settings/safety?focus=…` |
| `/settings/permissions?suggestion=…` | `/settings/safety?section=permissions&suggestion=…` |
| `/settings/models`, `/providers`, `/usage` | `/settings/archie` (`?section=providers` / `usage`) |
| `/settings/diagnostics`, `/labs` | `/settings/advanced?section=…` |

New deep links: `/settings/accounts?add=binance` opens that exchange's guide,
and `/settings/appearance?focus=privacy` jumps to privacy mode. On mobile,
`/settings` is the list half of a list → detail flow.

## Why "Kraken doesn't allow buying"

OpenPF can trade on Kraken with a trade key. Four things made it look like it
couldn't:

1. **Everything defaulted to read-only.** The provider summary said "Start
   read-only; add a trade key later", the picker preselected *Read-only*, and
   *Trade* was a small grey tab beside it.
2. **Reconnecting downgraded a trade key.** Picking an exchange that was
   already connected (Add → Kraken) reopened it in *read* mode. Pasting a
   key there saved the connection as read-only, even if it had been trading.
   It now reopens in the connection's current mode.
3. **The way out was easy to miss.** A read-only exchange showed only a small
   *Enable trading* button in the row's toolbar, and a read-only suggestion
   said only "Place it in Kraken". The row now has a banner ("Read-only:
   Archie can suggest trades here but can't buy or sell"), and suggestions
   offer *Enable trading*.
4. **The trade-key guide told you to leave Cancel/Close Orders off.** The
   steps also didn't match Kraken's current permission names. Both are fixed
   (see below).

## Exchange key permissions (checked Oct 2026)

**Kraken** (Kraken Pro → profile icon → Settings → API → Create API key):

| Box | Read only | Read + trade |
|---|---|---|
| Funds · Query Funds | tick | tick |
| Funds · Deposit Funds | leave off | leave off |
| Funds · Withdraw Funds | **never** | **never** |
| Orders & Trades · Query Open Orders & Trades | tick | tick |
| Orders & Trades · Query Closed Orders & Trades | tick | tick |
| Orders & Trades · Create & Modify Orders | leave off | tick |
| Orders & Trades · Cancel/Close Orders | leave off | tick |
| Other · Query Ledger Entries | optional | optional |
| Other · Export Data, Access WebSockets API | leave off | leave off |

IP whitelisting is optional.

**Binance** (Account → API Management → Create API → System generated → Edit
restrictions):

| Box | Read only | Read + trade |
|---|---|---|
| Enable Reading | tick (the default) | tick |
| Enable Spot & Margin Trading | leave off | tick |
| Enable Withdrawals | **never** | **never** |
| Margin Loan/Repay/Transfer, Futures, Universal Transfer, Internal Transfer | leave off | leave off |

Restrict access to trusted IPs is recommended. UK: Binance stopped
onboarding new UK customers in October 2023 and isn't FCA-authorised.
Existing UK accounts keep access; new UK sign-ups aren't possible.

On connect, OpenPF still refuses any key that can withdraw or transfer, in
either mode.
