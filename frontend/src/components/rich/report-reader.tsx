import { useMemo, useRef, useState, type ReactNode } from 'react'
import { ChevronDown, FileText } from 'lucide-react'

import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import type { ReportSummary } from '@/types'
import { Blocks, Callout, CLAMP_WEIGHT, Clamp, calloutToneFor } from './blocks'
import { cardStats, gapsFromNotes, restates, splitHoldingSections, statsFromKeyNumbers } from './card-model'
import { Inline, RichProvider } from './inline'
import { blockWeight, buildReport, parseMarkdown, plainText, type Block, type KeyNumber, type ReportDoc, type Section } from './parse'
import { ProposalGroupContext, proposalIdsIn } from './proposal'
import { GapsLine, HoldingCards, ResultCard, StatGrid } from './result-card'
import './rich.css'

/* ── Pieces ────────────────────────────────────────────────────────────── */

/** Key numbers as stat tiles: short values (long ones split into value + sublabel), no empty cells. */
export function KeyNumbers({ items }: { items: KeyNumber[] }) {
  if (!items.length) return null
  return <StatGrid stats={statsFromKeyNumbers(items)} />
}

function ReportSection({ section }: { section: Section }) {
  const [open, setOpen] = useState(true)
  const tone = calloutToneFor(section.heading.text)
  const long = blockWeight(section.blocks) > CLAMP_WEIGHT * 1.2
  if (tone) {
    return (
      <section id={section.heading.id} className="rc-section scroll-mt-4">
        <Callout tone={tone} title={<Inline text={section.heading.text} />}>
          <Blocks blocks={section.blocks} />
        </Callout>
      </section>
    )
  }
  return (
    <section id={section.heading.id} className="rc-section scroll-mt-4">
      <button type="button" className="rc-section-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="rc-h rc-h2 m-0">
          <Inline text={section.heading.text} />
        </span>
        <ChevronDown className={cn('rc-section-chevron', !open && '-rotate-90')} />
      </button>
      {open ? (
        <Clamp active={long}>
          <Blocks blocks={section.blocks} />
        </Clamp>
      ) : null}
    </section>
  )
}

/** Machine-readable ```json blocks are data for code — never part of the read. */
function readable(blocks: Block[]): Block[] {
  return blocks.filter((b) => !(b.kind === 'code' && /^json$/i.test(b.lang) && /^\s*[[{]/.test(b.body)))
}

/** Callout bullets that only restate the headline go; an emptied callout goes too. */
function withoutRepeats(section: Section, headline: string | null): Section | null {
  if (!headline || !calloutToneFor(section.heading.text)) return section
  const blocks: Block[] = []
  for (const b of section.blocks) {
    if (b.kind === 'list') {
      const items = b.items.filter((it) => !restates(it.text, headline))
      if (items.length) blocks.push({ ...b, items })
    } else if (b.kind === 'paragraph') {
      if (!restates(b.text, headline)) blocks.push(b)
    } else blocks.push(b)
  }
  return blocks.length ? { ...section, blocks } : null
}

type Piece = { kind: 'section'; section: Section } | { kind: 'holdings' }

/**
 * The report under its card: per-name sections become holding cards, a Notes
 * section about missing data becomes one muted line, callouts drop bullets
 * that repeat the headline, and machine JSON never renders.
 */
function tidy(doc: ReportDoc, summary: ReportSummary | null | undefined) {
  const headline = summary && !summary.derived ? summary.headline : null
  let gaps: string[] | null = summary?.gaps?.length ? summary.gaps : null
  const sections: Section[] = []
  for (const s of doc.sections) {
    const fromNotes = gapsFromNotes(s)
    if (fromNotes) {
      gaps = gaps ?? fromNotes
      continue
    }
    const clean = withoutRepeats({ ...s, blocks: readable(s.blocks) }, headline)
    if (clean && clean.blocks.length) sections.push(clean)
  }
  const { holdings, rest, at } = splitHoldingSections(sections, summary)
  const pieces: Piece[] = rest.map((section) => ({ kind: 'section', section }))
  if (holdings.length) pieces.splice(Math.max(0, Math.min(at, pieces.length)), 0, { kind: 'holdings' })
  return { pieces, holdings, gaps, preamble: readable(doc.preamble), toc: rest }
}

/** The card, contents and sections. Use inside any scroll container. */
export function ReportBody({
  doc,
  markdown,
  summary,
  title = 'Report',
}: {
  doc: ReportDoc
  markdown: string
  /** The report's summary block: when present it *is* the lead card. */
  summary?: ReportSummary | null
  /** What produced the report (for the card's action prompt). */
  title?: string
}) {
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  const group = useMemo(() => proposalIdsIn(markdown), [markdown])
  const fromSummary = Boolean(summary && !summary.derived && summary.headline)
  const { pieces, holdings, gaps, preamble, toc } = useMemo(() => tidy(doc, summary), [doc, summary])
  const stats = cardStats(fromSummary ? summary : null, doc.keyNumbers)
  return (
    <RichProvider>
      <ProposalGroupContext.Provider value={group}>
        <div className="rc rc-report">
          {fromSummary && summary ? (
            <ResultCard summary={{ ...summary, gaps: undefined }} stats={stats} title={title} variant="reader" className="mb-6" />
          ) : (
            <>
              {stats.length ? (
                <div className="mb-5">
                  <StatGrid stats={stats} />
                </div>
              ) : null}
              {doc.tldr.length ? (
                <div className="rc-tldr">
                  <p className="rc-tldr-label">TL;DR</p>
                  <Blocks blocks={doc.tldr} />
                </div>
              ) : null}
            </>
          )}
          {toc.length >= 3 ? (
            <nav className="rc-toc" aria-label="Contents">
              <p className="rc-toc-label">Contents</p>
              <ol>
                {toc.map((s, i) => (
                  <li key={s.heading.id}>
                    <button type="button" onClick={() => scrollTo(s.heading.id)}>
                      <span className="rc-toc-n">{i + 1}</span>
                      {plainText(s.heading.text)}
                    </button>
                  </li>
                ))}
              </ol>
            </nav>
          ) : null}
          {preamble.length ? <Blocks blocks={preamble} /> : null}
          {pieces.map((p, i) =>
            p.kind === 'holdings' ? <HoldingCards key={`holdings-${i}`} holdings={holdings} /> : <ReportSection key={p.section.heading.id} section={p.section} />,
          )}
          {gaps ? (
            <div className="mt-6 border-t border-border/60 pt-3">
              <GapsLine gaps={gaps} />
            </div>
          ) : null}
        </div>
      </ProposalGroupContext.Provider>
    </RichProvider>
  )
}

export function useReport(markdown: string | null | undefined) {
  return useMemo(() => (markdown ? buildReport(parseMarkdown(markdown)) : null), [markdown])
}

/* ── The drawer ────────────────────────────────────────────────────────── */

/**
 * The report reader: a right-side drawer with the report's type and date, a
 * key-numbers strip, a TL;DR card, contents for long reports, and the body at
 * a comfortable reading width. Used by Home "Today" and Automations.
 */
export function ReportReader({
  open,
  onOpenChange,
  title,
  kind,
  date,
  markdown,
  loading = false,
  error = null,
  empty,
  titleAdornment,
  footer,
  summary,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  /** e.g. "Weekly review", "Morning brief". */
  kind?: string | null
  date?: ReactNode
  markdown?: string | null
  loading?: boolean
  error?: string | null
  /** Shown when there's no markdown and no error. */
  empty?: ReactNode
  titleAdornment?: ReactNode
  footer?: ReactNode
  /** The report's summary block (becomes the TL;DR card). */
  summary?: ReportSummary | null
}) {
  const doc = useReport(markdown)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const shownTitle = doc?.title && (!title || /^report$/i.test(title)) ? doc.title : title
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="!w-full gap-0 sm:!max-w-[44rem]">
        <SheetHeader className="gap-3 border-b border-border/60 px-5 pb-4 pt-5 sm:px-7">
          <div className="flex items-center gap-2 pr-8 text-xs text-muted-foreground">
            <span className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full bg-muted px-2 py-0.5 font-medium text-foreground/80">
              <FileText className="size-3" />
              {kind || 'Report'}
            </span>
            {date ? <SheetDescription className="text-xs">{date}</SheetDescription> : null}
          </div>
          <SheetTitle className="flex items-center gap-2 text-xl font-semibold leading-tight tracking-tight">
            {titleAdornment}
            <span className="min-w-0">{shownTitle}</span>
          </SheetTitle>
        </SheetHeader>

        <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5 sm:px-7">
          <div className="mx-auto max-w-[72ch]">
            {loading ? (
              <div className="space-y-3">
                <Skeleton className="h-20 w-full rounded-xl" />
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-5/6" />
              </div>
            ) : error ? (
              <p className="text-sm text-negative">{error}</p>
            ) : doc && markdown ? (
              <ReportBody doc={doc} markdown={markdown} summary={summary} title={title || kind || 'Report'} />
            ) : (
              <div className="text-sm text-muted-foreground">{empty ?? 'Nothing to show.'}</div>
            )}
          </div>
        </div>

        {footer ? <SheetFooter className="border-t border-border/60 px-5 sm:px-7">{footer}</SheetFooter> : null}
      </SheetContent>
    </Sheet>
  )
}
