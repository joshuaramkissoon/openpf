import { useMemo, useState, type ReactNode } from 'react'

import { useAppActions } from '@/lib/app-actions'
import { useDisplayPrefs } from '@/lib/display-prefs'
import { usePrivacyMode } from '@/lib/privacy'
import { cn } from '@/lib/utils'
import { BlockView } from './BlockView'
import type { EngineCtx } from './engine'
import { VizEnvContext, type VizEnv, type WidgetSize } from './shared'
import { packRows, tidyBlocks } from './validate'
import type { Account, Block, VizSpec } from './types'
import './viz.css'

// Literal classes so Tailwind sees them.
const LG_SPAN: Record<number, string> = {
  3: 'lg:col-span-3',
  4: 'lg:col-span-4',
  5: 'lg:col-span-5',
  6: 'lg:col-span-6',
  7: 'lg:col-span-7',
  8: 'lg:col-span-8',
  9: 'lg:col-span-9',
  10: 'lg:col-span-10',
  12: 'lg:col-span-12',
}

const ACCOUNTS: { id: Account; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'invest', label: 'Invest' },
  { id: 'stocks_isa', label: 'ISA' },
]

export function VizProvider({ compact = false, size = null, onEditBlock, children }: { compact?: boolean; size?: WidgetSize | null; onEditBlock?: (block: Block) => void; children: ReactNode }) {
  const actions = useAppActions()
  const { currency } = useDisplayPrefs()
  const privacy = usePrivacyMode()
  const env = useMemo<VizEnv>(
    () => ({ currency, privacy, openTicker: actions.openTicker, navigate: actions.navigate, compact: compact || !!size, size, onEditBlock }),
    [currency, privacy, actions.openTicker, actions.navigate, compact, size, onEditBlock],
  )
  return <VizEnvContext.Provider value={env}>{children}</VizEnvContext.Provider>
}

/**
 * Renders a whole (already validated) viz spec as an editorial page: content
 * sits on the canvas (no card per block), rows are packed to a full 12-column
 * width, rows are separated by hairline rules and columns by hairline dividers.
 * Narrative order is the spec's order: the answer, then why, then the detail.
 */
export function ViewSpecRenderer({ spec, compact = false, className, onEditBlock }: { spec: VizSpec; compact?: boolean; className?: string; onEditBlock?: (block: Block) => void }) {
  const { currency } = useDisplayPrefs()
  const [account, setAccount] = useState<Account>('all')
  const ctx: EngineCtx = { currency, account }
  const rows = useMemo(() => packRows(tidyBlocks(spec.blocks)), [spec.blocks])
  return (
    <VizProvider compact={compact} onEditBlock={onEditBlock}>
      <div className={cn('viz-page', className)}>
        {spec.controls?.account ? (
          <div className="mb-8 flex items-center gap-2" role="group" aria-label="Account">
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
        {rows.map((row, ri) => (
          <div
            key={row[0].index}
            className={cn('grid grid-cols-1', !compact && 'lg:grid-cols-12', ri > 0 && (compact ? 'mt-6 border-t border-border/70 pt-6' : 'mt-9 border-t border-border/70 pt-9'))}
          >
            {row.map((cell, ci) => (
              <div
                key={cell.index}
                className={cn(
                  'min-w-0',
                  !compact && LG_SPAN[cell.span],
                  ci > 0 && (compact ? 'mt-6 border-t border-border/70 pt-6' : 'max-lg:mt-8 max-lg:border-t max-lg:border-border/70 max-lg:pt-8 lg:border-l lg:border-border/70 lg:pl-9'),
                  !compact && ci < row.length - 1 && 'lg:pr-9',
                )}
              >
                <BlockView spec={spec} block={cell.block} ctx={ctx} span={compact ? 12 : cell.span} />
              </div>
            ))}
          </div>
        ))}
      </div>
    </VizProvider>
  )
}
