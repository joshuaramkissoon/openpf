import { ScrollArea } from "@/components/ui/scroll-area"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Money, MoneyDelta, Pct, PctDelta, SectionCard } from "@/components/kit"
import { accountTag } from "@/utils/format"
import type { PositionItem } from "@/types"

function costBasis(p: PositionItem): number {
  return Number.isFinite(p.total_cost) && p.total_cost > 0 ? p.total_cost : Math.max(p.value - p.ppl, 0)
}

function pctChange(p: PositionItem): number | null {
  const cb = costBasis(p)
  if (!Number.isFinite(cb) || Math.abs(cb) < 1e-9) return null
  return p.ppl / cb
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
  const visible = positions

  return (
    <SectionCard
      title="Positions"
      description={accountView === "all" ? "Aggregated across Invest + ISA — click a row for chart, signals & forecast" : "Selected account — click a row for chart, signals & forecast"}
      noPadding
    >
      {visible.length === 0 ? (
        <p className="p-10 text-center text-sm text-muted-foreground">No positions to show.</p>
      ) : (
        <ScrollArea className="max-h-[560px]">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="text-xs">Symbol</TableHead>
                <TableHead className="text-right text-xs">Qty</TableHead>
                <TableHead className="text-right text-xs">Invested</TableHead>
                <TableHead className="text-right text-xs">Value</TableHead>
                <TableHead className="text-right text-xs">Weight</TableHead>
                <TableHead className="text-right text-xs">P/L</TableHead>
                <TableHead className="text-right text-xs">P/L %</TableHead>
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
