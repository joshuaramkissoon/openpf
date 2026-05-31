import type { ReactNode } from "react"

import { Card } from "@/components/ui/card"
import { cn } from "@/lib/utils"

/**
 * A headline metric tile. Label is quiet + uppercase; value is large, mono,
 * tabular. Optional `hint` (one quiet line) and `footer` (stacked breakdown).
 */
export function StatCard({
  label,
  value,
  hint,
  footer,
  className,
}: {
  label: ReactNode
  value: ReactNode
  hint?: ReactNode
  footer?: ReactNode
  className?: string
}) {
  return (
    <Card className={cn("flex flex-col gap-1 rounded-xl border p-4 shadow-none", className)}>
      <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="font-mono text-2xl font-semibold leading-tight tracking-tight tabular-nums">{value}</span>
      {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
      {footer ? <div className="mt-1 flex flex-col gap-0.5 text-xs text-muted-foreground">{footer}</div> : null}
    </Card>
  )
}
