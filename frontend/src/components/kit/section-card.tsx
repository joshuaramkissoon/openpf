import type { ReactNode } from "react"

import { Card } from "@/components/ui/card"
import { cn } from "@/lib/utils"

/**
 * A titled panel: header row (title + optional description + right-aligned
 * action) over a bordered body. The single container pattern for every
 * dashboard section — do not hand-roll card chrome elsewhere.
 */
export function SectionCard({
  title,
  description,
  action,
  className,
  contentClassName,
  noPadding = false,
  children,
}: {
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
  contentClassName?: string
  noPadding?: boolean
  children: ReactNode
}) {
  return (
    <Card className={cn("flex flex-col gap-0 overflow-hidden rounded-xl border py-0 shadow-none", className)}>
      {(title || action) && (
        <div className="flex items-center justify-between gap-3 border-b border-border/60 px-5 py-3.5">
          <div className="min-w-0">
            {title ? <h2 className="truncate text-sm font-semibold tracking-tight">{title}</h2> : null}
            {description ? <p className="truncate text-xs text-muted-foreground">{description}</p> : null}
          </div>
          {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
        </div>
      )}
      <div className={cn(noPadding ? "" : "p-5", contentClassName)}>{children}</div>
    </Card>
  )
}
