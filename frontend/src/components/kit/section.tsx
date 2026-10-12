import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * A borderless titled block — the calm alternative to `SectionCard` for
 * screens that want typographic hierarchy instead of a grid of boxes.
 */
export function Section({
  title,
  description,
  action,
  className,
  children,
}: {
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <section className={cn("min-w-0", className)}>
      {title || action ? (
        <div className="mb-3 flex items-end justify-between gap-3">
          <div className="min-w-0">
            {title ? <h2 className="text-sm font-semibold tracking-tight">{title}</h2> : null}
            {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
          </div>
          {action ? <div className="flex shrink-0 items-center gap-1">{action}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  )
}

/** A quiet text link-button used in section headers ("View all →"). */
export function SectionLink({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
    >
      {children}
    </button>
  )
}
