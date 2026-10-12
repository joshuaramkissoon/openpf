import { cn } from "@/lib/utils"

/**
 * Route-driven underline tabs for a page (e.g. Activity). Purely
 * presentational: the caller maps `value` to the route and handles `onChange`.
 */
export function TabNav<T extends string>({
  value,
  tabs,
  onChange,
  className,
}: {
  value: T
  tabs: { value: T; label: string; count?: number }[]
  onChange: (value: T) => void
  className?: string
}) {
  return (
    <div role="tablist" className={cn("flex gap-5 overflow-x-auto border-b border-border [scrollbar-width:none]", className)}>
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cn(
            "-mb-px flex shrink-0 items-center gap-1.5 border-b-2 pb-2.5 text-sm transition-colors",
            value === t.value
              ? "border-foreground font-medium text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          {t.label}
          {t.count ? (
            <span className="rounded-full bg-primary/15 px-1.5 font-mono text-[10px] font-semibold tabular-nums text-primary">
              {t.count}
            </span>
          ) : null}
        </button>
      ))}
    </div>
  )
}
