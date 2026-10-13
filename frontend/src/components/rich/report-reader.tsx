import { useMemo, useRef, useState, type ReactNode } from 'react'
import { ChevronDown, FileText } from 'lucide-react'

import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { Blocks, Callout, CLAMP_WEIGHT, Clamp, calloutToneFor } from './blocks'
import { Inline, RichProvider } from './inline'
import { blockWeight, buildReport, parseMarkdown, plainText, type KeyNumber, type ReportDoc, type Section } from './parse'
import { ProposalGroupContext, proposalIdsIn } from './proposal'
import './rich.css'

/* ── Pieces ────────────────────────────────────────────────────────────── */

export function KeyNumbers({ items }: { items: KeyNumber[] }) {
  if (!items.length) return null
  return (
    <dl className={cn('rc-keynums', items.length >= 4 ? 'sm:grid-cols-4' : items.length === 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-2')}>
      {items.slice(0, 4).map((k, i) => (
        <div key={i} className="rc-keynum">
          <dt>{k.label}</dt>
          <dd>
            <Inline text={k.value} />
          </dd>
        </div>
      ))}
    </dl>
  )
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

/** TL;DR + contents + sections. Use inside any scroll container. */
export function ReportBody({ doc, markdown, showKeyNumbers = false }: { doc: ReportDoc; markdown: string; showKeyNumbers?: boolean }) {
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  const group = useMemo(() => proposalIdsIn(markdown), [markdown])
  return (
    <RichProvider>
      <ProposalGroupContext.Provider value={group}>
        <div className="rc rc-report">
          {showKeyNumbers ? <KeyNumbers items={doc.keyNumbers} /> : null}
          {doc.tldr.length ? (
            <div className="rc-tldr">
              <p className="rc-tldr-label">TL;DR</p>
              <Blocks blocks={doc.tldr} />
            </div>
          ) : null}
          {doc.sections.length >= 3 ? (
            <nav className="rc-toc" aria-label="Contents">
              <p className="rc-toc-label">Contents</p>
              <ol>
                {doc.sections.map((s, i) => (
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
          {doc.preamble.length ? <Blocks blocks={doc.preamble} /> : null}
          {doc.sections.map((s) => (
            <ReportSection key={s.heading.id} section={s} />
          ))}
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
          {doc && doc.keyNumbers.length ? (
            <RichProvider>
              <KeyNumbers items={doc.keyNumbers} />
            </RichProvider>
          ) : null}
        </SheetHeader>

        <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5 sm:px-7">
          <div className="mx-auto max-w-[68ch]">
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
              <ReportBody doc={doc} markdown={markdown} />
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
