import { useState } from 'react'
import { ArrowRight, Bitcoin, Loader2 } from 'lucide-react'

import { createStarterCryptoPlay } from '@/api/crypto'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { toastApiError } from '@/lib/api-error'
import { useAppActions } from '@/lib/app-actions'
import { useCryptoStatus } from '@/lib/crypto-status'
import { Eyebrow, Segmented } from './ui'
import type { Conviction } from './types'

const CONVICTION_PCT: Record<Conviction, string> = { low: '1%', medium: '2%', high: '3%' }

/**
 * The built-in "Starter crypto" template: a small, staged way in for someone
 * who's nervous. Creates a DRAFT play — every weekly buy is still a proposal.
 */
export function StarterCryptoCard() {
  const { navigate } = useAppActions()
  const status = useCryptoStatus()
  const [open, setOpen] = useState(false)
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
    <div className="rounded-xl border border-border p-4">
      <div className="flex items-start gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
          <Bitcoin className="size-4 text-foreground/80" />
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-sm font-medium">Nervous about crypto? Start small.</p>
          <p className="text-sm leading-relaxed text-muted-foreground">
            The Starter crypto plan buys a little Bitcoin{eth ? ' (and some Ether)' : ''} every week on {exchange}, sized as a small
            slice of your book and never above your crypto cap. It comes with rules for when to pause, and spells out the risks
            and fees in plain English. Each weekly buy waits for your approval.
          </p>
        </div>
        {!open ? (
          <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
            Use template
          </Button>
        ) : null}
      </div>

      {open ? (
        <div className="mt-4 space-y-4 border-t border-border pt-4">
          {status && !status.connected ? (
            <p className="text-xs text-warning">
              No crypto exchange is connected yet — you can draft the plan now, but connect a trade-only key in Settings → Crypto
              before the first buy.
            </p>
          ) : null}
          <div className="grid gap-x-8 gap-y-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Eyebrow>Size (of your book)</Eyebrow>
              <Segmented<Conviction>
                value={conviction}
                onChange={(v) => setConviction(v ?? 'low')}
                options={(['low', 'medium', 'high'] as Conviction[]).map((c) => ({ value: c, label: CONVICTION_PCT[c] }))}
              />
            </div>
            <div className="space-y-1.5">
              <Eyebrow>Spread over</Eyebrow>
              <Segmented<string>
                value={weeks}
                onChange={(v) => setWeeks(v ?? '10')}
                options={[
                  { value: '8', label: '8 wks' },
                  { value: '10', label: '10 wks' },
                  { value: '12', label: '12 wks' },
                ]}
              />
            </div>
            <label className="flex items-center justify-between gap-3 space-y-0 self-end text-sm">
              <span>Include Ether (20%)</span>
              <Switch checked={eth} onCheckedChange={(v) => setEth(Boolean(v))} />
            </label>
          </div>
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">Creates a draft — nothing is bought until you approve each week's buy.</p>
            <Button size="sm" onClick={() => void create()} disabled={working}>
              {working ? <Loader2 className="size-4 animate-spin" /> : null}
              Draft the plan
              <ArrowRight className="size-4" />
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
