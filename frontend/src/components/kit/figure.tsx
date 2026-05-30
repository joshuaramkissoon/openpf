import { cn } from "@/lib/utils"
import {
  formatMoney,
  formatCompactMoney,
  formatSignedMoney,
  formatPercent,
  formatSignedPercent,
} from "@/utils/format"

/** A monetary figure. Always mono + tabular so columns align. */
export function Money({
  value,
  currency = "GBP",
  decimals = 2,
  compact = false,
  className,
}: {
  value: number
  currency?: string
  decimals?: number
  compact?: boolean
  className?: string
}) {
  const text = compact ? formatCompactMoney(value, currency) : formatMoney(value, currency, decimals)
  return <span className={cn("font-mono tabular-nums", className)}>{text}</span>
}

/** Signed money, coloured by sign (positive = gain, negative = loss). */
export function MoneyDelta({
  value,
  currency = "GBP",
  decimals = 2,
  className,
}: {
  value: number
  currency?: string
  decimals?: number
  className?: string
}) {
  return (
    <span
      className={cn(
        "font-mono tabular-nums",
        value >= 0 ? "text-positive" : "text-negative",
        className,
      )}
    >
      {formatSignedMoney(value, currency, decimals)}
    </span>
  )
}

/** A fraction rendered as a percentage. */
export function Pct({
  value,
  decimals = 1,
  className,
}: {
  value?: number | null
  decimals?: number
  className?: string
}) {
  return <span className={cn("font-mono tabular-nums", className)}>{formatPercent(value, decimals)}</span>
}

/** Signed percentage, coloured by sign. */
export function PctDelta({
  value,
  decimals = 1,
  className,
}: {
  value?: number | null
  decimals?: number
  className?: string
}) {
  const positive = (value ?? 0) >= 0
  return (
    <span
      className={cn(
        "font-mono tabular-nums",
        positive ? "text-positive" : "text-negative",
        className,
      )}
    >
      {formatSignedPercent(value, decimals)}
    </span>
  )
}
