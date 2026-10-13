import { Section } from "@/components/kit"
import { Button } from "@/components/ui/button"
import { useAppActions } from "@/lib/app-actions"
import { useCryptoStatus } from "@/lib/crypto-status"

const pct = (v: number) => `${(v * 100).toFixed(2).replace(/\.?0+$/, "")}%`

/** Settings → Crypto: the cap and UK notes. Exchanges are connected under Settings → Connections. */
export function CryptoSettings() {
  const { navigate } = useAppActions()
  const status = useCryptoStatus()

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-border p-4">
        <div className="space-y-1">
          <p className="text-sm">
            {status?.connected
              ? `${status.label} · ${status.tradable === false ? "read-only" : `trades on approval in ${status.quote}`}`
              : "No crypto exchange connected."}
          </p>
          <p className="text-xs text-muted-foreground">
            Connect Kraken, Coinbase and Binance — several at once, read-only or with a trade key — under Connections.
            {status ? ` Crypto is capped at ${pct(status.max_crypto_pct_of_book)} of your book (Risk rails).` : ""}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => navigate("/settings/connections")}>
          Open Connections →
        </Button>
      </div>

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
