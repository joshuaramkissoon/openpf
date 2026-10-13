import { useEffect, useState } from "react"
import { ExternalLink, KeyRound, Loader2, ShieldAlert, ShieldCheck, Unplug } from "lucide-react"
import { toast } from "sonner"

import {
  connectCrypto,
  disconnectCrypto,
  getCryptoExchanges,
  type CryptoExchange,
  type CryptoExchangeId,
} from "@/api/crypto"
import { Section } from "@/components/kit"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { parseApiError } from "@/lib/api-error"
import { setCryptoStatus, useCryptoStatus } from "@/lib/crypto-status"
import { cn } from "@/lib/utils"

const pct = (v: number) => `${(v * 100).toFixed(2).replace(/\.?0+$/, "")}%`

/** Settings → Crypto: pick an exchange, paste a TRADE-ONLY key, OpenPF verifies it can't withdraw. */
export function CryptoSettings() {
  const status = useCryptoStatus()
  const [exchanges, setExchanges] = useState<CryptoExchange[]>([])
  const [picked, setPicked] = useState<CryptoExchangeId>("kraken")
  const [key, setKey] = useState("")
  const [secret, setSecret] = useState("")
  const [working, setWorking] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  useEffect(() => {
    void getCryptoExchanges().then(setExchanges).catch(() => {})
  }, [])
  useEffect(() => {
    if (status?.exchange) setPicked(status.exchange)
  }, [status?.exchange])

  const ex = exchanges.find((e) => e.id === picked)

  async function connect() {
    setWorking(true)
    setRefusal(null)
    try {
      const s = await connectCrypto(picked, key.trim(), secret.trim())
      setCryptoStatus(s)
      setKey("")
      setSecret("")
      toast.success(`${s.label} connected`, { description: "Key verified as trade-only — it can't withdraw." })
    } catch (e) {
      setRefusal(parseApiError(e).message)
    } finally {
      setWorking(false)
    }
  }

  async function disconnect() {
    setWorking(true)
    try {
      setCryptoStatus(await disconnectCrypto())
      toast("Crypto exchange disconnected", { description: "The key was deleted from OpenPF. Revoke it on the exchange too." })
    } catch (e) {
      toast.error(parseApiError(e).message)
    } finally {
      setWorking(false)
    }
  }

  return (
    <div className="space-y-8">
      {status?.connected ? (
        <div className="rounded-xl border border-border p-4">
          <div className="flex flex-wrap items-start gap-3">
            <ShieldCheck className="mt-0.5 size-5 shrink-0 text-positive" />
            <div className="min-w-0 flex-1 space-y-1">
              <p className="text-sm font-medium">
                {status.label} connected <span className="font-normal text-muted-foreground">· {status.product} · key {status.key_hint}</span>
              </p>
              <p className="text-sm text-muted-foreground">
                Verified trade-only: can read balances {status.verified?.can_trade === false ? "but can't trade" : "and place orders"}, can't
                withdraw.{" "}
                {status.verified?.method ? <span className="text-xs">({status.verified.method})</span> : null}
              </p>
              <p className="text-xs text-muted-foreground">
                Market orders in {status.quote}
                {status.fees ? ` · fees ${pct(status.fees.maker)} maker / ${pct(status.fees.taker)} taker (${status.fees.source})` : ""} · crypto
                capped at {pct(status.max_crypto_pct_of_book)} of your book (Risk rails)
              </p>
              {status.sandbox ? (
                <p className="text-xs text-warning">Sandbox: a mock exchange with fake keys — no real exchange is reachable.</p>
              ) : null}
            </div>
            {!status.sandbox ? (
              <Button variant="outline" size="sm" disabled={working} onClick={() => void disconnect()}>
                <Unplug className="size-3.5" />
                Disconnect
              </Button>
            ) : null}
          </div>
        </div>
      ) : status?.needs_verification ? (
        <p className="rounded-xl border border-warning/40 bg-warning/5 p-4 text-sm">
          A key is stored but hasn't been verified as trade-only, so OpenPF won't use it. Paste it below to check it.
        </p>
      ) : null}

      <Section
        title={status?.connected ? "Switch exchange" : "Connect an exchange"}
        description="OpenPF trades crypto through the exchange you pick, under the same approval gate as stocks."
      >
        <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Exchange">
          {exchanges.map((e) => (
            <button
              key={e.id}
              type="button"
              role="radio"
              aria-checked={picked === e.id}
              onClick={() => {
                setPicked(e.id)
                setRefusal(null)
              }}
              className={cn(
                "rounded-xl border p-3 text-left transition-colors",
                picked === e.id ? "border-foreground/40 bg-muted/50" : "border-border hover:border-foreground/20",
              )}
            >
              <p className="text-sm font-medium">{e.label}</p>
              <p className="text-xs text-muted-foreground">{e.product}</p>
              <p className="mt-2 font-mono text-xs tabular-nums text-foreground/80">
                {pct(e.fees.taker)} taker · {e.quote}
              </p>
              {e.id === "binance" ? <p className="mt-1 text-[11px] leading-snug text-warning">Not open to new UK customers</p> : null}
            </button>
          ))}
        </div>
        {ex ? <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{ex.fee_note} {ex.uk}</p> : null}
      </Section>

      {ex ? (
        <Section title={`Create a trade-only ${ex.label} key`}>
          <div className="space-y-4 rounded-xl border border-border p-4">
            <div className="flex items-start gap-2.5 rounded-lg bg-negative/5 p-3 text-sm">
              <ShieldAlert className="mt-0.5 size-4 shrink-0 text-negative" />
              <p className="leading-relaxed">
                <span className="font-semibold">Never give OpenPF a key that can withdraw or transfer.</span> A key that can only
                trade can't move coins off the exchange, even if it leaked. OpenPF checks this when you connect and refuses any key
                that can withdraw. {ex.notes}
              </p>
            </div>
            <ol className="list-decimal space-y-1.5 pl-5 text-sm leading-relaxed">
              {ex.key_steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            <a
              href={ex.key_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            >
              Open {ex.label} API key settings <ExternalLink className="size-3" />
            </a>
          </div>
        </Section>
      ) : null}

      {ex ? (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            void connect()
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="crypto-key">{ex.id === "coinbase" ? "API key name" : "API key"}</Label>
            <Input
              id="crypto-key"
              autoComplete="off"
              spellCheck={false}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={ex.id === "coinbase" ? "organizations/…/apiKeys/…" : "Paste the key"}
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="crypto-secret">{ex.id === "coinbase" ? "Private key" : "Secret"}</Label>
            {ex.id === "coinbase" ? (
              <Textarea
                id="crypto-secret"
                spellCheck={false}
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder="-----BEGIN EC PRIVATE KEY-----"
                className="min-h-24 font-mono text-xs"
              />
            ) : (
              <Input
                id="crypto-secret"
                type="password"
                autoComplete="off"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder="Paste the secret"
                className="font-mono text-xs"
              />
            )}
            <p className="text-xs text-muted-foreground">
              Stored only in this app's database, shown back only as the last 4 characters, and never given to Archie.
            </p>
          </div>
          {refusal ? (
            <p role="alert" className="flex items-start gap-2 rounded-lg border border-negative/30 bg-negative/5 p-3 text-sm">
              <ShieldAlert className="mt-0.5 size-4 shrink-0 text-negative" />
              <span>{refusal}</span>
            </p>
          ) : null}
          <Button type="submit" size="sm" disabled={working || !key.trim() || !secret.trim()}>
            {working ? <Loader2 className="size-3.5 animate-spin" /> : <KeyRound className="size-3.5" />}
            Verify &amp; connect
          </Button>
        </form>
      ) : null}

      <Section title="Before you buy crypto (UK)">
        <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-muted-foreground">
          <li>
            Crypto held on an exchange is <span className="text-foreground">not covered by the FSCS</span>. If the exchange fails or is
            hacked, you may not get it back.
          </li>
          <li>
            Selling, swapping or spending crypto is a disposal for <span className="text-foreground">Capital Gains Tax</span>. Keep a
            record of every buy and sell.
          </li>
          <li>
            Alternative: UK retail investors can also hold crypto exposure through exchange-traded notes (ETNs) at a regular broker —
            a different product with its own fees and risks.
          </li>
          <li>Crypto prices are far more volatile than shares; falls of more than 70% have happened more than once.</li>
        </ul>
      </Section>
    </div>
  )
}
