import { useState } from 'react'
import { ArrowRight, Bitcoin, Loader2, TriangleAlert } from 'lucide-react'

import { createStarterCryptoPlay } from '@/api/crypto'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { toastApiError } from '@/lib/api-error'
import { useAppActions } from '@/lib/app-actions'
import { useCryptoStatus } from '@/lib/crypto-status'
import { Segmented } from './ui'
import type { Conviction } from './types'

const CONVICTION_PCT: Record<Conviction, string> = { low: '1%', medium: '2%', high: '3%' }

/**
 * The built-in "Starter crypto" template: a small, staged way in for someone
 * who's nervous. Creates a DRAFT play — every weekly buy is still a proposal.
 */
export function StarterCryptoDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { navigate } = useAppActions()
  const status = useCryptoStatus()
  const [conviction, setConviction] = useState<Conviction>('low')
  const [weeks, setWeeks] = useState('10')
  const [eth, setEth] = useState(true)
  const [working, setWorking] = useState(false)
  const exchange = status?.label?.replace(/ \(sandbox\)$/, '') || 'your exchange'

  async function create() {
    setWorking(true)
    try {
      const play = await createStarterCryptoPlay({ conviction, include_eth: eth, weeks: Number(weeks) })
      navigate(`/plays/${play.id}`)
    } catch (e) {
      toastApiError(e, 'Could not create the starter plan')
      setWorking(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-5 p-5 sm:max-w-md">
        <DialogHeader className="gap-3">
          <span
            className="flex size-10 items-center justify-center rounded-xl"
            style={{ background: 'color-mix(in oklab, var(--chart-7) 16%, transparent)', color: 'var(--chart-7)' }}
          >
            <Bitcoin className="size-5" />
          </span>
          <DialogTitle className="text-base">Starter crypto</DialogTitle>
          <DialogDescription className="leading-relaxed">
            A little Bitcoin{eth ? ' (and some Ether)' : ''} each week on {exchange}, kept to a small slice of your book and under
            your crypto cap. It comes with clear rules for when to pause.
          </DialogDescription>
        </DialogHeader>

        {status && !status.connected ? (
          <p className="flex gap-2 rounded-lg bg-warning/10 px-3 py-2 text-xs leading-relaxed text-warning">
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
            No exchange connected yet. You can draft the plan now and connect a trade-only key in Settings before the first buy.
          </p>
        ) : null}

        <div className="divide-y divide-border rounded-xl border border-border">
          <Row label="Size" hint="Share of your book, in total">
            <Segmented<Conviction>
              value={conviction}
              onChange={(v) => setConviction(v ?? 'low')}
              options={(['low', 'medium', 'high'] as Conviction[]).map((c) => ({ value: c, label: CONVICTION_PCT[c] }))}
            />
          </Row>
          <Row label="Spread over" hint="One small buy a week">
            <Segmented<string>
              value={weeks}
              onChange={(v) => setWeeks(v ?? '10')}
              options={[
                { value: '8', label: '8 wks' },
                { value: '10', label: '10 wks' },
                { value: '12', label: '12 wks' },
              ]}
            />
          </Row>
          <Row label="Include Ether" hint="20% of each buy">
            <Switch checked={eth} onCheckedChange={(v) => setEth(Boolean(v))} />
          </Row>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">Each weekly buy waits for your approval.</p>
          <Button onClick={() => void create()} disabled={working}>
            {working ? <Loader2 className="size-4 animate-spin" /> : null}
            Draft the plan
            <ArrowRight className="size-4" />
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function Row({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 px-3.5 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}
