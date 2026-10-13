import { useMemo, useState, type ReactNode } from 'react'

import { useAppActions } from '@/lib/app-actions'
import { useDisplayPrefs } from '@/lib/display-prefs'
import { usePrivacyMode } from '@/lib/privacy'
import { cn } from '@/lib/utils'
import { BlockView } from './BlockView'
import type { EngineCtx } from './engine'
import { VizEnvContext, type VizEnv, type WidgetSize } from './shared'
import type { Account, Block, VizSpec } from './types'
import './viz.css'

const WIDTH: Record<NonNullable<Block['width']>, string> = {
  full: 'lg:col-span-12',
  half: 'lg:col-span-6',
  third: 'lg:col-span-4',
  'two-thirds': 'lg:col-span-8',
}

const ACCOUNTS: { id: Account; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'invest', label: 'Invest' },
  { id: 'stocks_isa', label: 'ISA' },
]

export function VizProvider({ compact = false, size = null, children }: { compact?: boolean; size?: WidgetSize | null; children: ReactNode }) {
  const actions = useAppActions()
  const { currency } = useDisplayPrefs()
  const privacy = usePrivacyMode()
  const env = useMemo<VizEnv>(
    () => ({ currency, privacy, openTicker: actions.openTicker, navigate: actions.navigate, compact: compact || !!size, size }),
    [currency, privacy, actions.openTicker, actions.navigate, compact, size],
  )
  return <VizEnvContext.Provider value={env}>{children}</VizEnvContext.Provider>
}

/** Renders a whole (already validated) viz spec as a page: controls row, then a 12-column block grid. */
export function ViewSpecRenderer({ spec, compact = false, className }: { spec: VizSpec; compact?: boolean; className?: string }) {
  const { currency } = useDisplayPrefs()
  const [account, setAccount] = useState<Account>('all')
  const ctx: EngineCtx = { currency, account }
  return (
    <VizProvider compact={compact}>
      <div className={cn('space-y-4', className)}>
        {spec.controls?.account ? (
          <div className="flex items-center gap-2" role="group" aria-label="Account">
            <div className="inline-flex rounded-lg bg-muted p-0.5">
              {ACCOUNTS.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  aria-pressed={account === a.id}
                  onClick={() => setAccount(a.id)}
                  className={cn(
                    'h-7 rounded-md px-3 text-xs font-medium transition-colors',
                    account === a.id ? 'bg-card text-foreground shadow-sm ring-1 ring-border' : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  {a.label}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <div className={cn('grid grid-cols-1 gap-4', !compact && 'lg:grid-cols-12')}>
          {spec.blocks.map((block, i) => (
            <div key={i} className={cn('min-w-0', !compact && WIDTH[block.width ?? 'full'])}>
              <BlockView spec={spec} block={block} ctx={ctx} />
            </div>
          ))}
        </div>
      </div>
    </VizProvider>
  )
}
