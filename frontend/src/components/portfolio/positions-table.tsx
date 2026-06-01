import { useMemo, useState } from "react"
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react"

import { ScrollArea } from "@/components/ui/scroll-area"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Money, MoneyDelta, Pct, PctDelta, SectionCard } from "@/components/kit"
import { accountTag } from "@/utils/format"
import { cn } from "@/lib/utils"
import type { PositionItem } from "@/types"

function costBasis(p: PositionItem): number {
  return Number.isFinite(p.total_cost) && p.total_cost > 0 ? p.total_cost : Math.max(p.value - p.ppl, 0)
}

function pctChange(p: PositionItem): number | null {
  const cb = costBasis(p)
  if (!Number.isFinite(cb) || Math.abs(cb) < 1e-9) return null
  return p.ppl / cb
}

type SortKey = "ticker" | "quantity" | "invested" | "value" | "weight" | "ppl" | "pnl_pct"

function sortValue(p: PositionItem, key: SortKey): number | string {
  switch (key) {
    case "ticker":
      return p.ticker
    case "quantity":
      return p.quantity
    case "invested":
      return costBasis(p)
    case "value":
      return p.value
    case "weight":
      return p.weight
    case "ppl":
      return p.ppl
    case "pnl_pct":
      return pctChange(p) ?? Number.NEGATIVE_INFINITY
  }
}

export function PositionsTable({
  positions,
  accountView,
  displayCurrency,
  onSelect,
}: {
  positions: PositionItem[]
  accountView: "all" | "invest" | "stocks_isa"
  displayCurrency: "GBP" | "USD"
  onSelect: (position: PositionItem) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>("value")
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc")

  const visible = useMemo(() => {
    const rows = [...positions]
    rows.sort((a, b) => {
      const av = sortValue(a, sortKey)
      const bv = sortValue(b, sortKey)
      const cmp =
        typeof av === "string" || typeof bv === "string"
          ? String(av).localeCompare(String(bv))
          : (av as number) - (bv as number)
      return sortDir === "asc" ? cmp : -cmp
    })
    return rows
  }, [positions, sortKey, sortDir])

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(key)
      setSortDir(key === "ticker" ? "asc" : "desc") // text A→Z, numbers high→low first
    }
  }

  function SortHead({ sortKey: key, label, align = "right" }: { sortKey: SortKey; label: string; align?: "left" | "right" }) {
    const active = sortKey === key
    const Icon = !active ? ChevronsUpDown : sortDir === "asc" ? ArrowUp : ArrowDown
    return (
      <TableHead className={cn("text-xs", align === "right" && "text-right")}>
        <button
          type="button"
          onClick={() => toggleSort(key)}
          className={cn(
            "inline-flex items-center gap-1 transition-colors hover:text-foreground",
            align === "right" && "flex-row-reverse",
            active ? "text-foreground" : "text-muted-foreground",
          )}
        >
          {label}
          <Icon className={cn("size-3", active ? "opacity-100" : "opacity-40")} strokeWidth={2} />
        </button>
      </TableHead>
    )
  }

  return (
    <SectionCard
      title="Positions"
      description={accountView === "all" ? "Aggregated across Invest + ISA — click a row for chart, signals & forecast" : "Selected account — click a row for chart, signals & forecast"}
      noPadding
      action={
        visible.length > 12 ? (
          <Button variant="ghost" size="sm" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Show top" : `Show all ${visible.length}`}
          </Button>
        ) : undefined
      }
    >
      {visible.length === 0 ? (
        <p className="p-10 text-center text-sm text-muted-foreground">No positions to show.</p>
      ) : (
        <ScrollArea className={cn(expanded ? "max-h-none" : "max-h-[560px]")}>
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <SortHead sortKey="ticker" label="Symbol" align="left" />
                <SortHead sortKey="quantity" label="Qty" />
                <SortHead sortKey="invested" label="Invested" />
                <SortHead sortKey="value" label="Value" />
                <SortHead sortKey="weight" label="Weight" />
                <SortHead sortKey="ppl" label="P/L" />
                <SortHead sortKey="pnl_pct" label="P/L %" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((p, i) => (
                <TableRow
                  key={`${p.account_kind}-${p.instrument_code}-${i}`}
                  onClick={() => onSelect(p)}
                  className="cursor-pointer"
                >
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{p.ticker}</span>
                      <Badge variant="outline" className="px-1.5 py-0 text-[10px] text-muted-foreground">
                        {accountTag(p.account_kind)}
                      </Badge>
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-mono text-muted-foreground tabular-nums">
                    {p.quantity.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Money value={costBasis(p)} currency={displayCurrency} />
                  </TableCell>
                  <TableCell className="text-right">
                    <Money value={p.value} currency={displayCurrency} />
                  </TableCell>
                  <TableCell className="text-right">
                    <Pct value={p.weight} />
                  </TableCell>
                  <TableCell className="text-right">
                    <MoneyDelta value={p.ppl} currency={displayCurrency} />
                  </TableCell>
                  <TableCell className="text-right">
                    <PctDelta value={pctChange(p)} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </ScrollArea>
      )}
    </SectionCard>
  )
}
