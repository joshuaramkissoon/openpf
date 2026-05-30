import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const RISK_STYLES: Record<string, string> = {
  ok: "border-positive/30 bg-positive/10 text-positive",
  oversold: "border-warning/30 bg-warning/10 text-warning",
  overbought: "border-warning/30 bg-warning/10 text-warning",
  warning: "border-warning/30 bg-warning/10 text-warning",
  critical: "border-negative/30 bg-negative/10 text-negative",
}

/** Risk flag pill with semantic colour. Falls back to a quiet "ok". */
export function RiskBadge({ flag, className }: { flag?: string | null; className?: string }) {
  const key = (flag || "ok").toLowerCase()
  return (
    <Badge
      variant="outline"
      className={cn("font-medium capitalize", RISK_STYLES[key] ?? RISK_STYLES.ok, className)}
    >
      {key}
    </Badge>
  )
}
