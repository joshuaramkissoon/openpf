import { useMemo, useState } from "react"
import { ArrowLeft, ArrowRight, CircleCheck, CircleX, Gauge, Loader2, TriangleAlert } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { cn } from "@/lib/utils"
import { accountLabel } from "@/utils/format"
import { parseApiError } from "@/lib/api-error"
import { placeTrade, previewTrade, type InstrumentInsight, type TradePreview } from "@/api/instruments"
import { fmtMoney, fmtPct, fmtPrice, num } from "./insight-format"
import { CapMeter } from "./insight-sections"

const STOCK_ACCOUNTS = ["stocks_isa", "invest"]

/** Where a trade should start: e.g. a held-back ask the user places themselves. */
export type TradePrefill = { side?: "buy" | "sell"; amount_gbp?: number; account_kind?: string | null }

/** A coin on the connected exchange, rather than a Trading 212 instrument. */
export function isCryptoInsight(insight: InstrumentInsight, prefill?: TradePrefill | null) {
  return (
    prefill?.account_kind === "crypto" ||
    Boolean(insight.instrument_code?.includes("/")) ||
    Boolean(insight.position?.accounts?.includes("crypto") && insight.position.accounts.length === 1)
  )
}

/**
 * The user's own trade, sized, then a review step: amount, account, the
 * post-trade weight against the cap, every rail, fees, and (live only) a final
 * confirm. Not a proposal: pressing Buy/Sell places it, through the same
 * execution gate. The limits that bind Archie's proposals (per order, per day,
 * the caps) are amber warnings here — the user can place it anyway; cash,
 * holdings and duplicates still block.
 */
export function TradeSizer({
  insight,
  prefill,
  onDone,
}: {
  insight: InstrumentInsight
  prefill?: TradePrefill | null
  onDone?: (placed: boolean) => void
}) {
  const book = insight.book
  const pos = insight.position
  const ownerPlay = insight.plays.find((p) => p.relation === "owner") ?? null
  const target = ownerPlay?.target_weight_of_book ?? null
  const cap = book?.position_cap ?? null
  const total = book?.total_gbp ?? 0
  const priceGbp = insight.quote?.price_gbp ?? null

  const crypto = isCryptoInsight(insight, prefill)
  const accounts = crypto ? ["crypto"] : STOCK_ACCOUNTS
  const unit = crypto ? "coins" : "shares"
  const fallbackAccount =
    pos && pos.accounts.length
      ? Object.entries(pos.value_by_account).sort((a, b) => b[1] - a[1])[0][0]
      : Object.entries(book?.free_cash_by_account ?? {})
          .filter(([a]) => STOCK_ACCOUNTS.includes(a))
          .sort((a, b) => b[1] - a[1])[0]?.[0] ?? "stocks_isa"
  const defaultAccount = crypto
    ? "crypto"
    : prefill?.account_kind && STOCK_ACCOUNTS.includes(prefill.account_kind)
      ? prefill.account_kind
      : STOCK_ACCOUNTS.includes(fallbackAccount)
        ? fallbackAccount
        : "stocks_isa"

  const [side, setSide] = useState<"buy" | "sell">(prefill?.side ?? "buy")
  const [account, setAccount] = useState<string>(defaultAccount)
  const startAmount = prefill?.amount_gbp
    ? Math.round(prefill.amount_gbp)
    : Math.max(50, Math.round(Math.min(book?.max_order_gbp ?? 500, total * 0.01 || 500) / 50) * 50)
  const [amount, setAmount] = useState<string>(String(startAmount))
  const [review, setReview] = useState<TradePreview | null>(null)
  const [busy, setBusy] = useState<null | "review" | "place">(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmLive, setConfirmLive] = useState(false)

  const value = pos?.value_gbp ?? 0
  const amt = Number(amount) || 0
  const nextValue = side === "buy" ? value + amt : Math.max(0, value - amt)
  const weightNow = total ? value / total : 0
  const weightNext = total ? nextValue / total : 0
  const shares = priceGbp ? amt / priceGbp : null
  const heldInAccount = pos?.value_by_account?.[account] ?? 0
  const cash = book?.free_cash_by_account?.[account] ?? null
  const verb = side === "buy" ? "Buy" : "Sell"

  // Instant feedback while sizing; the review step asks the server (the authority).
  // Limits are Archie's guard rails, so on your own trade they warn, never block.
  const checks = useMemo(() => {
    const out: { tone: "bad" | "warn"; text: string }[] = []
    if (book?.max_order_gbp != null && amt > book.max_order_gbp)
      out.push({ tone: "warn", text: `Above your ${fmtMoney(book.max_order_gbp)} single-order limit. You can still place it.` })
    if (side === "buy" && cap != null && weightNext > cap)
      out.push({ tone: "warn", text: `Takes it to ${fmtPct(weightNext)} of your book, over your ${fmtPct(cap, 0)} position cap.` })
    if (side === "buy" && target != null && weightNext > target * 1.15 && !(cap != null && weightNext > cap))
      out.push({ tone: "warn", text: `Above the play's ${fmtPct(target)} target.` })
    if (side === "buy" && cash != null && amt > cash) out.push({ tone: "bad", text: `More than the ${fmtMoney(cash)} free cash in ${accountLabel(account)}.` })
    if (side === "sell" && amt > heldInAccount + 0.01) out.push({ tone: "bad", text: `You hold ${fmtMoney(heldInAccount)} in ${accountLabel(account)}.` })
    return out
  }, [amt, book, cap, target, cash, side, weightNext, heldInAccount, account])

  const blocked = checks.some((c) => c.tone === "bad") || amt <= 0 || !priceGbp

  function edit(fn: () => void) {
    fn()
    setReview(null)
    setError(null)
  }

  async function toReview() {
    setBusy("review")
    setError(null)
    try {
      setReview(await previewTrade(insight.display_ticker, { side, account_kind: account, notional_gbp: amt }))
    } catch (err) {
      setError(parseApiError(err).message)
    } finally {
      setBusy(null)
    }
  }

  async function place(liveConfirmed = false) {
    setBusy("place")
    setError(null)
    try {
      const res = await placeTrade(insight.display_ticker, { side, account_kind: account, notional_gbp: amt, live_confirmed: liveConfirmed })
      const ccy = insight.quote?.currency ?? "USD"
      const filled = res.intent.quantity
      // A broker that doesn't report its fill price yet: show the price it was sized at, marked as approximate.
      const price = res.fill_price || review?.sizing?.reference_price
      toast.success(`${side === "buy" ? "Bought" : "Sold"} ${fmtMoney(res.intent.estimated_notional_gbp)} of ${insight.display_ticker}`, {
        description: `${filled.toLocaleString("en-GB", { maximumFractionDigits: crypto ? 8 : 4 })} ${unit}${price ? ` ${res.fill_price ? "at" : "at ≈"} ${fmtPrice(price, ccy)}` : ""} · ${accountLabel(account)}${
          res.broker_mode === "paper" ? " · paper" : ""
        }${res.rail_warnings?.length ? " · over your limits, as you chose" : ""}`,
      })
      setReview(null)
      onDone?.(true)
    } catch (err) {
      const e = parseApiError(err)
      const detail = (err as { response?: { data?: { detail?: { violations?: string[] } } } })?.response?.data?.detail
      setError(detail?.violations?.length ? detail.violations.join(" · ") : e.message)
    } finally {
      setBusy(null)
    }
  }

  if (review) {
    const s = review.sizing
    const fees = review.fees
    const live = review.broker_mode === "live"
    const ok = review.ok && !review.violations.length
    const over = review.over_limits ?? []
    const otherWarnings = review.warnings.filter((w) => !over.includes(w))
    return (
      <div className="flex flex-col gap-4 rounded-xl border border-border/70 bg-muted/15 p-4" aria-label="Review your trade">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" className="-ml-1.5 size-7" onClick={() => edit(() => {})} aria-label="Back to sizing">
            <ArrowLeft className="size-4" />
          </Button>
          <p className="text-sm font-semibold">
            {verb} {fmtMoney(s?.estimated_notional_gbp ?? amt)} of {insight.display_ticker}
          </p>
          <span
            className={cn(
              "ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider",
              live ? "bg-negative/15 text-negative" : "bg-muted text-muted-foreground",
            )}
          >
            {live ? "Live" : review.broker_mode}
          </span>
        </div>

        <dl className={cn("grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2 text-sm", num)}>
          <dt className="text-muted-foreground">Amount</dt>
          <dd>
            {fmtMoney(s?.estimated_notional_gbp ?? amt)}
            {s ? (
              <span className="text-muted-foreground">
                {" "}
                · {s.quantity.toLocaleString("en-GB", { maximumFractionDigits: crypto ? 8 : 4 })} {unit} at {fmtPrice(s.reference_price, s.currency)}
              </span>
            ) : null}
          </dd>
          <dt className="text-muted-foreground">Account</dt>
          <dd>{accountLabel(account)}</dd>
          <dt className="text-muted-foreground">Fees</dt>
          <dd>
            {fees?.exchange_fee_gbp != null ? (
              <>Exchange fee ≈ {fmtMoney(fees.exchange_fee_gbp)}</>
            ) : fees && fees.fx_fee_gbp > 0 ? (
              <>
                No commission · FX fee {fmtPct(fees.fx_fee_rate, 2)} ≈ {fmtMoney(fees.fx_fee_gbp)}
              </>
            ) : (
              "No commission"
            )}
          </dd>
        </dl>

        {total > 0 ? <CapMeter weight={weightNow} next={weightNext} cap={cap} target={target} /> : null}

        <div className="flex flex-col gap-1.5 text-xs" aria-live="polite">
          {review.error ? (
            <p className="flex items-start gap-1.5 text-negative">
              <CircleX className="mt-px size-3.5 shrink-0" /> {review.error}
            </p>
          ) : null}
          {review.violations.map((v) => (
            <p key={v} className="flex items-start gap-1.5 text-negative">
              <CircleX className="mt-px size-3.5 shrink-0" /> {railWords(v)}
            </p>
          ))}
          {otherWarnings.map((v) => (
            <p key={v} className="flex items-start gap-1.5 text-warning">
              <TriangleAlert className="mt-px size-3.5 shrink-0" /> {railWords(v)}
            </p>
          ))}
          {ok && !over.length ? (
            <p className="flex items-center gap-1.5 text-muted-foreground">
              <CircleCheck className="size-3.5 text-positive" /> Within your limits: order size, daily limit, cash and duplicates.
            </p>
          ) : null}
          {error ? (
            <p className="flex items-start gap-1.5 text-negative">
              <CircleX className="mt-px size-3.5 shrink-0" /> {error}
            </p>
          ) : null}
        </div>

        {ok && over.length ? (
          <div className="flex flex-col gap-1.5 rounded-lg border border-warning/35 bg-warning/[0.07] px-3 py-2.5 text-xs" role="note">
            <p className="flex items-center gap-1.5 font-medium text-warning">
              <Gauge className="size-3.5 shrink-0" /> Over your limits
            </p>
            <ul className="flex flex-col gap-0.5 pl-5 text-foreground/85">
              {over.map((v) => (
                <li key={v} className="list-disc marker:text-warning/70">
                  {railWords(v, review)}
                </li>
              ))}
            </ul>
            <p className="pl-5 text-muted-foreground">
              Your limits keep Archie’s proposals in check. This is your own trade, so it’s your call. It still counts toward today’s total.
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => (live ? setConfirmLive(true) : void place())}
            disabled={!ok || busy !== null}
            className={cn(
              side === "sell" && "bg-negative text-white hover:bg-negative/90",
              over.length && side === "buy" && "bg-warning text-warning-foreground hover:bg-warning/85",
            )}
          >
            {busy === "place" ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {over.length ? "Place anyway · " : ""}
            {verb} {fmtMoney(s?.estimated_notional_gbp ?? amt)}
          </Button>
          <span className="text-xs text-muted-foreground">
            {live
              ? crypto
                ? "Sends a real market order to your exchange."
                : "Sends a real market order to Trading 212."
              : review.broker_mode === "paper"
                ? "Paper trading: a simulated fill."
                : "Sandbox: fake money, topped up as needed."}
          </span>
        </div>

        <AlertDialog open={confirmLive} onOpenChange={setConfirmLive}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                {verb} {fmtMoney(s?.estimated_notional_gbp ?? amt)} of {insight.display_ticker}?
              </AlertDialogTitle>
              <AlertDialogDescription>
                This sends a real order to your {crypto ? "" : "Trading 212 "}
                {accountLabel(account)} account.
                {over.length ? " It’s over your limits; you’ve chosen to place it anyway." : ""}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => {
                  setConfirmLive(false)
                  void place(true)
                }}
              >
                Place order
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-border/70 bg-muted/15 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Seg value={side} onChange={(v) => edit(() => setSide(v as "buy" | "sell"))} options={pos ? ["buy", "sell"] : ["buy"]} />
        <Seg value={account} onChange={(v) => edit(() => setAccount(v))} options={accounts} label={(a) => accountLabel(a)} />
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="text-xs text-muted-foreground" htmlFor="sizer-amount">
          Amount in GBP
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-36">
            <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-sm text-muted-foreground">£</span>
            <Input
              id="sizer-amount"
              inputMode="decimal"
              value={amount}
              onChange={(e) => edit(() => setAmount(e.target.value.replace(/[^\d.]/g, "")))}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !blocked) void toReview()
              }}
              className={cn("pl-6", num)}
            />
          </div>
          <div className="flex gap-1">
            {[250, 500, 1000].map((v) => (
              <Button key={v} variant="ghost" size="sm" className={cn("h-7 px-2 text-xs", num)} onClick={() => edit(() => setAmount(String(v)))}>
                {fmtMoney(v)}
              </Button>
            ))}
          </div>
        </div>
        {shares != null && insight.quote ? (
          <span className={cn("text-xs text-muted-foreground", num)}>
            ≈ {shares.toLocaleString("en-GB", { maximumFractionDigits: crypto ? 6 : 3 })} {unit} at {fmtPrice(insight.quote.price, insight.quote.currency)}
          </span>
        ) : null}
      </div>

      {total > 0 ? <CapMeter weight={weightNow} next={weightNext} cap={cap} target={target} /> : null}

      {checks.length ? (
        <ul className="flex flex-col gap-1 text-xs">
          {checks.map((c, i) => (
            <li key={i} className={cn("flex items-start gap-1.5", c.tone === "bad" ? "text-negative" : "text-warning")}>
              {c.tone === "bad" ? <CircleX className="mt-px size-3.5 shrink-0" /> : <TriangleAlert className="mt-px size-3.5 shrink-0" />}
              {c.text}
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <p className="text-xs text-negative">{error}</p> : null}

      <div className="flex items-center gap-3">
        <Button onClick={() => void toReview()} disabled={busy !== null || blocked}>
          {busy === "review" ? <Loader2 className="size-3.5 animate-spin" /> : null}
          Review {side} {fmtMoney(amt)}
          <ArrowRight className="size-3.5" />
        </Button>
        <span className="text-xs text-muted-foreground">You’ll see fees and every check before anything is placed.</span>
      </div>
    </div>
  )
}

/** Rail messages from the gate, in plain words (with the limit's size when the preview carries it). */
function railWords(v: string, review?: TradePreview): string {
  const s = v.replace(/^(risk|crypto|duplicate-order|position)-guard:\s*/i, "")
  const caps = review?.caps
  if (/max single-order notional/i.test(s))
    return caps ? `Above your ${fmtMoney(caps.max_single_order_notional_gbp)} single-order limit.` : "Above your single-order limit."
  if (/max daily notional/i.test(s))
    return caps ? `Takes today past your ${fmtMoney(caps.max_daily_notional_gbp)} daily limit.` : "Over your daily trading limit."
  if (/insufficient available cash/i.test(s)) return "Not enough free cash in this account."
  if (/similar order already exists/i.test(s)) return "A similar order was placed moments ago."
  return s.charAt(0).toUpperCase() + s.slice(1)
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
