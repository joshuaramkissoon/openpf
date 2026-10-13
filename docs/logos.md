# Company logos

Equities show their company logo wherever `AssetIcon` is used: holdings, Decisions tiles, the
stock sheet, play holdings, instrument chips and chat proposal cards. Crypto keeps its bundled
coin icons. Anything without a logo falls back to the monogram tile.

## How it works

```
browser ──/api/logos/{key}──▶ OpenPF server ──(once per company)──▶ logo service
                               └── disk cache (LOGOS_DATA_DIR)
```

- The browser only talks to your OpenPF server. It never contacts a logo service, so page
  loads reveal nothing to a third party.
- The server asks the logo service once per company and keeps the answer on disk. A miss is
  kept for 7 days, then asked again. The UI also remembers misses for 7 days (localStorage).
- What the logo service receives is the instrument identifier (the ISIN when the broker's
  instrument list has it, else the ticker), your server's IP address, and a generic
  `OpenPF/2` user agent. It does not receive holdings, quantities, values or anything else.
- After each portfolio sync, the server warms the holdings' logos in the background: one
  request every 0.5 s, at most 80 per run. Logos that are already cached or recently missed are
  skipped. Set `LOGOS_PREWARM=false` to turn this off.
- **Settings → General → Company logos** is on by default. When it's off, the server fetches and
  serves nothing, the UI makes no logo requests, and every icon is a monogram.
- Cache folder: `LOGOS_DATA_DIR` (default `backend/data/logos`, gitignored). Deleting it is safe.

## Sources

| Order | Source | Lookup | Key | Terms (checked Oct 2026) |
|---|---|---|---|---|
| 1 | **[Elbstream Logo API](https://elbstream.com/logos)** (formerly Parqet's logo API; `parqet.com/api` now redirects there) | ISIN, then Yahoo-style symbol (`AAPL`, `BA.L`, `RHM.DE`) | none | Free "Hobby" tier: unlimited requests under a fair-use policy, **link back to elbstream.com required**. [Terms](https://elbstream.com/terms): logos remain their owners' property, to be used "in accordance with Fair Use principles, such as identifying stocks and companies". Nothing in the terms forbids server-side caching. |
| 2 | **[Wikidata](https://www.wikidata.org/wiki/Property:P154) → Wikimedia Commons** | ISIN (`P946`) → `logo image` (`P154`) | none | Data is CC0. Commons only hosts free or public-domain files; most logos there are `PD-textlogo`. Trademark rights still apply, and identifying the company is the intended use. Requests carry a descriptive User-Agent, per Wikimedia's policy. |

Why Elbstream: it needs no key and looks up by ISIN, which matters for UK and EU listings.
Its symbol route also understands exchange suffixes, so `BA.L` returns BAE Systems where a bare
`BA` would return Boeing. It returns square raster tiles at any size, and its terms allow this use.

The free tier asks for a visible link back to elbstream.com on any page that shows a logo. The app
covers this in two places when logos are on: a "Logos by Elbstream" line in the sidebar footer
(12px) and in the setting itself. Elbstream's product page says the link should be at least
12 *points*, which is about 16px. If that matters, enlarge the sidebar line or ask Elbstream
(sales@elbstream.com) about self-hosted open-source use.

Considered and not used:

- **Brandfetch**: free, ticker and ISIN routes. Its docs say "hotlink the CDN URL directly … don't
  download, cache, or re-host", and hotlinking would expose every holding to the CDN from the
  browser.
- **logo.dev** (Clearbit's successor): `img.logo.dev/ticker/{T}?token=` needs a key, and commercial
  use on the free plan needs attribution. It would work as an optional user-provided key later.
- **Logokit**: its free plan doesn't support caching or storing images.
- **Financial Modeling Prep** `image-stock/{T}.png`: works without a key, but it's a legacy
  endpoint and the terms of service couldn't be retrieved to confirm display or caching rights.

## Safety

All of this lives in `backend/app/services/logo_service.py` and is covered by `backend/tests/test_logos.py`.

- **Allow-list.** HTTPS only, port 443, and only `api.elbstream.com`, `query.wikidata.org`,
  `commons.wikimedia.org` and `upload.wikimedia.org`. Redirects are followed by hand, at most 4,
  and every hop is checked against the allow-list.
- **Limits.** 6 s timeout and a 200 KB cap, enforced while streaming (an oversize
  `Content-Length` is refused up front).
- **Raster only.** The upstream content type must be PNG, JPEG or WebP, and the bytes must match
  that type (magic bytes; PNG dimensions must be 1 to 2048). SVG is never stored or served:
  Elbstream is asked for `format=png`, and Wikimedia renders SVG logos to PNG thumbnails
  (`Special:FilePath/…?width=128`). A source that breaks a rule is skipped, not trusted.
- **Serving.** Responses carry our own content type plus `X-Content-Type-Options: nosniff` and
  `Content-Security-Policy: default-src 'none'; sandbox`. Keys must match `[A-Za-z0-9._-]{1,32}`,
  and cache filenames are hashes, so paths can't be traversed.
- **Agent.** The agent has no logo tool and no MCP server for it. A test asserts the MCP
  registry never mentions logos.
- **Sandbox mode** fetches normally, because the demo book holds only well-known public tickers.
  Its cache goes in its own folder: `LOGOS_DATA_DIR` if set, else a `logos/` folder next to
  `OPENPF_RUNTIME_DIR`, else `.demo/logos`. Sandbox mode refuses the live `backend/data/logos`.
  Tests never touch the network: upstream is an `httpx.MockTransport`, and `LOGOS_PREWARM=0`.

## Keys the UI sends

`AssetIcon` passes whatever symbol it shows. The server resolves it to an identity:

- **ISIN**: used as is.
- **T212 code** (`BAl_EQ`): ISIN from the instrument list, plus the Yahoo-style symbol.
- **Yahoo-style symbol** (`BA.L`): the matching T212 listing's ISIN, plus the symbol.
- **Bare ticker**: if you hold it, that holding's listing, so a London `BA` you own resolves to
  BAE. Otherwise it's treated as a US ticker.
