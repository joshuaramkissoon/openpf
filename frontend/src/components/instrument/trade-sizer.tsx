import { useMemo, useState } from "react"
import { ArrowRight, CheckCircle2, Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { navigate } from "@/lib/router"
import { accountLabel } from "@/utils/format"
import { toastApiError } from "@/lib/api-error"
import { proposeFromSheet, type InstrumentInsight, type SheetProposalResult } from "@/api/instruments"
import { fmtMoney, fmtPct, fmtPrice, num } from "./insight-format"
import { WeightMeter } from "./insight-sections"

const ACCOUNTS = ["stocks_isa", "invest"] as const

/** Size a buy or sell against your limits, then file it as a *proposed* trade.
 *  Nothing executes here: the proposal waits for approval in Decisions. */
export function TradeSizer({ insight, onDone }: { insight: InstrumentInsight; onDone?: () => void }) {
  const book = insight.book
  const pos = insight.position
  const ownerPlay = insight.plays.find((p) => p.relation === "owner") ?? null
  const target = ownerPlay?.target_weight_of_book ?? null
  const cap = book?.position_cap ?? null
  const total = book?.total_gbp ?? 0
  const priceGbp = insight.quote?.price_gbp ?? null

  const defaultAccount =
    pos && pos.accounts.length
      ? Object.entries(pos.value_by_account).sort((a, b) => b[1] - a[1])[0][0]
      : Object.entries(book?.free_cash_by_account ?? {}).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "stocks_isa"

  const [side, setSide] = useState<"buy" | "sell">("buy")
  const [account, setAccount] = useState<string>(defaultAccount)
  const startAmount = Math.max(50, Math.round(Math.min(book?.max_order_gbp ?? 500, total * 0.01 || 500) / 50) * 50)
  const [amount, setAmount] = useState<string>(String(startAmount))
  const [why, setWhy] = useState("")
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SheetProposalResult | null>(null)

  const value = pos?.value_gbp ?? 0
  const amt = Number(amount) || 0
  const nextValue = side === "buy" ? value + amt : Math.max(0, value - amt)
  const weightNow = total ? value / total : 0
  const weightNext = total ? nextValue / total : 0
  const shares = priceGbp ? amt / priceGbp : null
  const heldInAccount = pos?.value_by_account?.[account] ?? 0
  const cash = book?.free_cash_by_account?.[account] ?? null

  const checks = useMemo(() => {
    const out: { tone: "bad" | "warn"; text: string }[] = []
    if (book?.max_order_gbp != null && amt > book.max_order_gbp)
      out.push({ tone: "bad", text: `Above your single-order limit of ${fmtMoney(book.max_order_gbp)} — it would be refused.` })
    if (side === "buy" && cap != null && weightNext > cap)
      out.push({ tone: "bad", text: `Takes it to ${fmtPct(weightNext)} of your book, over your ${fmtPct(cap, 0)} position cap.` })
    if (side === "buy" && target != null && weightNext > target * 1.15 && !(cap != null && weightNext > cap))
      out.push({ tone: "warn", text: `Above the play's ${fmtPct(target)} target.` })
    if (side === "buy" && cash != null && amt > cash)
      out.push({ tone: "warn", text: `More than the ${fmtMoney(cash)} free cash in ${accountLabel(account)}.` })
    if (side === "sell" && amt > heldInAccount + 0.01)
      out.push({ tone: "bad", text: `You hold ${fmtMoney(heldInAccount)} in ${accountLabel(account)}.` })
    return out
  }, [amt, book, cap, target, cash, side, weightNext, heldInAccount, account])

  async function submit() {
    setBusy(true)
    try {
      const res = await proposeFromSheet(insight.display_ticker, {
        side,
        account_kind: account,
        notional_gbp: amt,
        rationale: why.trim() || undefined,
      })
      setResult(res)
    } catch (err) {
      toastApiError(err, "Couldn't create the proposal")
    } finally {
      setBusy(false)
    }
  }

  if (result?.ok) {
    return (
      <div className="flex flex-col gap-2 rounded-xl border border-border/70 bg-muted/20 p-4 text-sm">
        <div className="flex items-center gap-2 font-medium">
          <CheckCircle2 className="size-4 text-positive" />
          Proposed: {side} {fmtMoney(result.intent?.estimated_notional ?? amt)} of {insight.display_ticker}
        </div>
        <p className="text-muted-foreground">Nothing has been sent to your broker. It's waiting for your approval in Decisions.</p>
        {result.warnings?.length ? <p className="text-xs text-warning">{result.warnings.join(" · ")}</p> : null}
        <div className="flex gap-2">
          <Button size="sm" onClick={() => navigate("/home")}>
            Review in Decisions <ArrowRight className="size-3.5" />
          </Button>
          <Button size="sm" variant="ghost" onClick={() => (setResult(null), onDone?.())}>
            Done
          </Button>
        </div>
      </div>
    )
  }

  const blocked = checks.some((c) => c.tone === "bad") || amt <= 0 || !priceGbp

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-border/70 bg-muted/15 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Seg value={side} onChange={(v) => setSide(v as "buy" | "sell")} options={pos ? ["buy", "sell"] : ["buy"]} />
        <Seg value={account} onChange={setAccount} options={[...ACCOUNTS]} label={(a) => accountLabel(a)} />
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="text-xs text-muted-foreground" htmlFor="sizer-amount">
          Amount in GBP
        </label>
        <div className="flex items-center gap-2">
          <div className="relative w-36">
            <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-sm text-muted-foreground">£</span>
            <Input
              id="sizer-amount"
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))}
              className={cn("pl-6", num)}
            />
          </div>
          <div className="flex gap-1">
            {[250, 500, 1000].map((v) => (
              <Button key={v} variant="ghost" size="sm" className={cn("h-7 px-2 text-xs", num)} onClick={() => setAmount(String(v))}>
                {fmtMoney(v)}
              </Button>
            ))}
          </div>
        </div>
        {shares != null && insight.quote ? (
          <span className={cn("text-xs text-muted-foreground", num)}>
            ≈ {shares.toLocaleString("en-GB", { maximumFractionDigits: 3 })} shares at {fmtPrice(insight.quote.price, insight.quote.currency)}
          </span>
        ) : null}
      </div>

      {total > 0 ? (
        <div className="flex flex-col gap-2">
          <div className={cn("flex items-baseline gap-2 text-sm", num)}>
            <span className="text-muted-foreground">Weight</span>
            <span>{fmtPct(weightNow)}</span>
            <ArrowRight className="size-3 self-center text-muted-foreground" />
            <span className="font-semibold">{fmtPct(weightNext)}</span>
            <span className="ml-auto text-xs text-muted-foreground">
              {target != null ? `target ${fmtPct(target)}` : ""}
              {target != null && cap != null ? " · " : ""}
              {cap != null ? `cap ${fmtPct(cap, 0)}` : ""}
            </span>
          </div>
          <WeightMeter weight={weightNow} next={weightNext} target={target} cap={cap} />
        </div>
      ) : null}

      {checks.length ? (
        <ul className="flex flex-col gap-1 text-xs">
          {checks.map((c, i) => (
            <li key={i} className={c.tone === "bad" ? "text-negative" : "text-warning"}>
              {c.text}
            </li>
          ))}
        </ul>
      ) : null}
      {result && !result.ok ? (
        <p className="text-xs text-negative">{result.error || result.violations?.join(" · ") || "Proposal refused."}</p>
      ) : null}

      <Input placeholder="Why? (optional, saved with the proposal)" value={why} onChange={(e) => setWhy(e.target.value)} />

      <div className="flex items-center gap-3">
        <Button onClick={() => void submit()} disabled={busy || blocked}>
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
          Propose {side} {fmtMoney(amt)}
        </Button>
        <span className="text-xs text-muted-foreground">Creates a proposal for you to approve. Nothing executes.</span>
      </div>
    </div>
  )
}

function Seg({
  value,
  onChange,
  options,
  label = (s: string) => s[0].toUpperCase() + s.slice(1),
}: {
  value: string
  onChange: (v: string) => void
  options: string[]
  label?: (s: string) => string
}) {
  return (
    <div className="flex rounded-lg bg-muted/60 p-0.5">
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={cn(
            "rounded-md px-2.5 py-1 text-xs font-medium",
            value === o ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {label(o)}
        </button>
      ))}
    </div>
  )
}
