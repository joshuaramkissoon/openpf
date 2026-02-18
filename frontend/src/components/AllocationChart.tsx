import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'

import type { PositionItem } from '../types'

interface Props {
  positions: PositionItem[]
}

const COLORS = ['#3ad98f', '#88a2ff', '#f8ad55', '#ef6f6c', '#57c7ff', '#ffd56a', '#b78cff', '#76efad']

export function AllocationChart({ positions }: Props) {
  const rows = positions
    .slice()
    .sort((a, b) => b.value - a.value)
    .slice(0, 8)
    .map((p) => ({
      name: p.ticker,
      value: Number((p.weight * 100).toFixed(2)),
    }))

  return (
    <section className="glass-card allocation-card">
      <div className="section-heading-row">
        <h2>Allocation Map</h2>
        <span className="hint">Top weights by position</span>
      </div>
      <div className="allocation-wrap">
        <ResponsiveContainer width="100%" height={280}>
          <PieChart>
            <Pie
              data={rows}
              dataKey="value"
              nameKey="name"
              cx="50%"
              cy="50%"
              outerRadius={100}
              innerRadius={50}
              paddingAngle={2}
            >
              {rows.map((entry, index) => (
                <Cell key={`cell-${entry.name}`} fill={COLORS[index % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip formatter={(v: number) => `${v.toFixed(2)}%`} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="legend-row">
        {rows.map((r, i) => (
          <span key={r.name} className="legend-item">
            <i style={{ background: COLORS[i % COLORS.length] }} />
            {r.name} {r.value.toFixed(1)}%
          </span>
        ))}
      </div>
    </section>
  )
}
