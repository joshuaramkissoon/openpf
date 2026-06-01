import { useEffect, useState } from "react"
import { Check, Scale } from "lucide-react"

import { getRebalancePreview, proposeRebalance, type RebalancePlan } from "@/api/client"
import { SectionCard } from "@/components/kit"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

/**
 * Autopilot rebalancer surface. Archie owns the policy (tunable by chat); this
 * just previews the concentration-drift trim and lets you send it to Execution
 * for one-tap approval. No knobs — proposals route to the normal approve flow.
 */
export function RebalanceCard({ accountView }: { accountView: "all" | "invest" | "stocks_isa" }) {
  const [plan, setPlan] = useState<RebalancePlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    setPlan(null)
    setSent(0)
    getRebalancePreview(accountView)
      .then((p) => active && setPlan(p))
      .catch(() => active && setError("Couldn't compute a rebalance right now."))
    return () => {
      active = false
    }
  }, [accountView])

  if (error) return null
  if (!plan) return null

  const trades = plan.trades ?? []
  const inBalance = trades.length === 0
  const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`)

  async function send() {
    setBusy(true)
    try {
      const res = await proposeRebalance(accountView)
      setSent(res.proposed_count ?? res.trades.length)
    } catch {
      setError("Couldn't queue the proposal.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <SectionCard
      title="Rebalance"
      description="Autopilot core-book risk check — Archie proposes, you approve."
      action={
        inBalance ? (
          <Badge variant="outline" className="text-positive">In balance</Badge>
        ) : sent > 0 ? (
          <Badge variant="outline" className="text-positive">
            <Check className="mr-1 size-3" /> {sent} sent to Execution
          </Badge>
        ) : (
          <Button size="sm" onClick={() => void send()} disabled={busy}>
            <Scale className="mr-1.5 size-3.5" />
            Send {trades.length} to Execution
          </Button>
        )
      }
    >
      <p className="text-sm leading-relaxed text-muted-foreground">{plan.rationale}</p>

      {!inBalance ? (
        <>
          <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
            <span>
              Top weight <span className="font-medium text-foreground">{pct(plan.before.top_position_weight)}</span> →{" "}
              <span className="font-medium text-foreground">{pct(plan.after.top_position_weight)}</span>
            </span>
            <span>
              HHI <span className="font-medium text-foreground">{plan.before.concentration_hhi}</span> →{" "}
              <span className="font-medium text-foreground">{plan.after.concentration_hhi}</span>
            </span>
          </div>
          <div className="mt-3 flex flex-col gap-1.5">
            {trades.map((t) => (
              <div
                key={`${t.ticker}-${t.account_kind}`}
                className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/15 px-3 py-2 text-xs"
              >
                <Badge variant="outline" className={t.side === "sell" ? "text-rose-500" : "text-emerald-500"}>
                  {t.side}
                </Badge>
                <span className="font-medium">{t.ticker}</span>
                <span className="text-muted-foreground">{t.account_kind}</span>
                <span className="ml-auto font-mono tabular-nums">£{t.est_notional.toLocaleString()}</span>
                {t.target_weight != null ? (
                  <span className="text-muted-foreground">{pct(t.current_weight)} → {pct(t.target_weight)}</span>
                ) : null}
              </div>
            ))}
          </div>
          <p className="mt-2.5 text-[11px] text-muted-foreground">
            Want different limits? Just tell Archie in chat (e.g. "keep PLTR under 22%") — it updates the policy.
          </p>
        </>
      ) : null}
    </SectionCard>
  )
}
