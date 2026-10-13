import { useMemo } from 'react'

import { cn } from '@/lib/utils'
import { Blocks, ChatBlocks } from './blocks'
import { RichProvider } from './inline'
import { parseMarkdown } from './parse'
import { ProposalGroupContext, proposalIdsIn } from './proposal'
import './rich.css'

export type RichVariant = 'chat' | 'report' | 'compact'

/**
 * The one rich renderer for Archie's chat replies, reports and briefs:
 * typographic scale + measure, real data tables, ticker chips, consistent
 * £/% figures, callouts, viz blocks, proposal cards, collapsible long
 * sections. Streaming-safe: pass `streaming` while text is still arriving.
 */
export function RichMarkdown({
  markdown,
  streaming = false,
  variant = 'chat',
  className,
  proposalGroup,
  tickers,
}: {
  markdown: string
  streaming?: boolean
  variant?: RichVariant
  className?: string
  /** All proposal ids in the surrounding message (so they render as one group). */
  proposalGroup?: string[]
  /** Extra symbols to render as ticker chips (e.g. a play's legs), beyond what's held. */
  tickers?: string[]
}) {
  const blocks = useMemo(() => parseMarkdown(markdown, { streaming }), [markdown, streaming])
  const group = useMemo(() => proposalGroup ?? proposalIdsIn(markdown), [proposalGroup, markdown])
  return (
    <RichProvider extra={tickers}>
      <ProposalGroupContext.Provider value={group}>
        <div className={cn('rc', `rc-${variant}`, className)}>
          {variant === 'chat' ? <ChatBlocks blocks={blocks} streaming={streaming} /> : <Blocks blocks={blocks} />}
        </div>
      </ProposalGroupContext.Provider>
    </RichProvider>
  )
}
