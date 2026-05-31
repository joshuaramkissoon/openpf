import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts"

import { SectionCard } from "@/components/kit"
import type { PositionItem } from "@/types"

const CHART_COLORS = [
  "var(--color-chart-1)",
  "var(--color-chart-2)",
  "var(--color-chart-3)",
  "var(--color-chart-4)",
  "var(--color-chart-5)",
  "var(--color-chart-6)",
  "var(--color-chart-7)",
  "var(--color-chart-8)",
]

type Slice = { name: string; value: number; fill: string }

/** High-contrast tooltip: solid popover surface, foreground text, colour dot. */
function AllocationTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: Array<{ payload: Slice }>
}) {
  if (!active || !payload?.length) return null
  const slice = payload[0].payload
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 shadow-lg">
      <div className="flex items-center gap-2">
        <i className="size-2 rounded-[3px]" style={{ background: slice.fill }} />
        <span className="text-xs font-medium text-popover-foreground">{slice.name}</span>
        <span className="ml-3 font-mono text-xs font-semibold tabular-nums text-popover-foreground">
          {slice.value.toFixed(2)}%
        </span>
      </div>
    </div>
  )
}

export function AllocationCard({ positions }: { positions: PositionItem[] }) {
  const rows: Slice[] = positions
    .slice()
    .sort((a, b) => b.value - a.value)
    .slice(0, 8)
    .map((p, i) => ({
      name: p.ticker,
      value: Number((p.weight * 100).toFixed(2)),
      fill: CHART_COLORS[i % CHART_COLORS.length],
    }))

  return (
    <SectionCard title="Allocation" description="Top weights by position">
      {rows.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted-foreground">No positions to allocate.</p>
      ) : (
        <div className="flex flex-col gap-4">
          <ResponsiveContainer width="100%" height={232}>
            <PieChart>
              <Pie
                data={rows}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                outerRadius={94}
                innerRadius={58}
                paddingAngle={2}
                stroke="var(--color-card)"
                strokeWidth={2}
              >
                {rows.map((entry) => (
                  <Cell key={entry.name} fill={entry.fill} />
                ))}
              </Pie>
              <Tooltip cursor={false} content={<AllocationTooltip />} />
            </PieChart>
          </ResponsiveContainer>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {rows.map((r) => (
              <span key={r.name} className="flex items-center gap-1.5 text-xs">
                <i className="size-2 rounded-[3px]" style={{ background: r.fill }} />
                <span className="font-medium text-foreground">{r.name}</span>
                <span className="font-mono text-muted-foreground tabular-nums">{r.value.toFixed(1)}%</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </SectionCard>
  )
}
