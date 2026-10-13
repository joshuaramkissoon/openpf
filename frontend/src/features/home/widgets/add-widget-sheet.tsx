import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Plus, Sparkles } from 'lucide-react'

import { listPresets, listViews } from '@/api/views'
import type { HomeWidget, HomeWidgetSize } from '@/api/home'
import { WIDGET_PRESETS } from '@/components/viz'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import type { ViewMeta, ViewPreset } from '@/types/views'

import { BUILTINS, SIZE_LABEL, SUGGESTION_MAP, headlineBlock, presetSpec, type BuiltinId } from './registry'
import { BuiltinBody, type HomeCtx } from './widget-content'
import { VizTile, loadViewSpec, oneBlock, useViewSpec } from './viz-widget'

type Item = {
  key: string
  title: string
  why: string
  sizes: HomeWidgetSize[]
  defaultSize: HomeWidgetSize
  make: (size: HomeWidgetSize) => Promise<HomeWidget>
  preview: (size: HomeWidgetSize) => ReactNode
}

const newId = () => `w_${Math.random().toString(16).slice(2, 10)}`

function isFromChat(v: ViewMeta) {
  return v.versions.some((x) => x.note === 'Pinned from chat')
}

function useSmallScreen() {
  const [small, setSmall] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 639px)')
    const on = () => setSmall(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return small
}

/** Render children only once scrolled near (live previews fetch data). */
function WhenVisible({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || seen) return
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && setSeen(true), { rootMargin: '200px' })
    io.observe(el)
    return () => io.disconnect()
  }, [seen])
  return (
    <div ref={ref} className={className}>
      {seen ? children : <div className="h-full w-full animate-pulse rounded-2xl bg-muted/40" />}
    </div>
  )
}

/** Previews are look-only: nothing inside is focusable or clickable. */
function Inert({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => ref.current?.setAttribute('inert', ''), [])
  return (
    <div ref={ref} className={className}>
      {children}
    </div>
  )
}

function ViewPreview({ id, size }: { id: string; size: HomeWidgetSize }) {
  const { spec, loading } = useViewSpec(id)
  if (loading) return <div className="h-full w-full animate-pulse rounded-2xl bg-muted/40" />
  if (!spec) return <p className="p-4 text-xs text-muted-foreground">Preview unavailable.</p>
  return <VizTile spec={oneBlock(spec, headlineBlock(spec))} size={size} title={spec.title} live={false} />
}

/**
 * "Add widget": a gallery of live previews at their real size, grouped as
 * Suggested for you (ranked for this portfolio, with why), Your views, Pinned
 * from chat and Built-in. Anything already on Home is left out.
 */
export function AddWidgetSheet({
  open,
  onOpenChange,
  ctx,
  current,
  onAdd,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  ctx: HomeCtx
  current: HomeWidget[]
  onAdd: (w: HomeWidget, title: string) => void
}) {
  const small = useSmallScreen()
  const [ranked, setRanked] = useState<ViewPreset[] | null>(null)
  const [views, setViews] = useState<ViewMeta[] | null>(null)

  useEffect(() => {
    if (!open) return
    let alive = true
    listPresets()
      .then((p) => alive && setRanked(p))
      .catch(() => alive && setRanked([]))
    listViews()
      .then((v) => alive && setViews(v))
      .catch(() => alive && setViews([]))
    return () => {
      alive = false
    }
  }, [open])

  const groups = useMemo(() => {
    const onHome = new Set(current.map((w) => (w.kind === 'spec' ? null : `${w.kind}:${w.ref}`)).filter(Boolean) as string[])

    const presetItem = (id: string, why?: string): Item | null => {
      const p = WIDGET_PRESETS.find((x) => x.id === id)
      if (!p || onHome.has(`preset:${p.id}`)) return null
      return {
        key: `preset:${p.id}`,
        title: p.title,
        why: why ?? p.description,
        sizes: p.sizes,
        defaultSize: p.defaultSize,
        make: async (size) => ({ id: newId(), kind: 'preset', ref: p.id, size }),
        preview: (size) => <VizTile spec={presetSpec(p, size)} size={size} title={p.title} live={false} />,
      }
    }

    // Suggested: the server ranks view presets for this book; each maps to widget presets.
    const suggested: Item[] = []
    const used = new Set<string>()
    for (const r of ranked ?? []) {
      for (const id of SUGGESTION_MAP[r.slug] ?? []) {
        if (suggested.length >= 3 || used.has(id)) continue
        const item = presetItem(id, r.why ?? undefined)
        if (item) {
          suggested.push(item)
          used.add(id)
          break // one widget per reason keeps the suggestions varied
        }
      }
    }

    const viewItem = (v: ViewMeta): Item | null => {
      if (v.kind !== 'spec' || !v.current_version || onHome.has(`view:${v.id}`)) return null
      return {
        key: `view:${v.id}`,
        title: v.title,
        why: v.description || (isFromChat(v) ? 'Pinned from a chat with Archie' : 'One of your saved views'),
        sizes: ['S', 'M', 'L'],
        defaultSize: 'M',
        make: async (size) => {
          const spec = await loadViewSpec(v.id)
          return { id: newId(), kind: 'view', ref: v.id, block: spec ? headlineBlock(spec) : 0, size }
        },
        preview: (size) => <ViewPreview id={v.id} size={size} />,
      }
    }
    const specViews = (views ?? []).filter((v) => v.kind === 'spec')
    const yours = specViews.filter((v) => !isFromChat(v)).map(viewItem).filter(Boolean) as Item[]
    const chat = specViews.filter(isFromChat).map(viewItem).filter(Boolean) as Item[]

    const builtins = (Object.keys(BUILTINS) as BuiltinId[])
      .filter((id) => !onHome.has(`builtin:${id}`))
      .map<Item>((id) => {
        const b = BUILTINS[id]
        return {
          key: `builtin:${id}`,
          title: b.title,
          why: b.description,
          sizes: b.sizes,
          defaultSize: b.defaultSize,
          make: async (size) => ({ id: newId(), kind: 'builtin', ref: id, size }),
          preview: () => (
            <div className="relative h-full overflow-hidden rounded-2xl [mask-image:linear-gradient(to_bottom,black_70%,transparent)]">
              <BuiltinBody id={id} ctx={ctx} editing />
            </div>
          ),
        }
      })
    const presets = WIDGET_PRESETS.filter((p) => !used.has(p.id))
      .map((p) => presetItem(p.id))
      .filter(Boolean) as Item[]

    return [
      { id: 'suggested', title: 'Suggested for you', hint: 'Ranked for your portfolio right now', items: suggested },
      { id: 'views', title: 'Your views', hint: 'A chart from each saved view', items: yours },
      { id: 'chat', title: 'Pinned from chat', hint: 'Charts you pinned while talking to Archie', items: chat },
      { id: 'builtin', title: 'Built-in', hint: 'Home’s own cards and starter charts', items: [...builtins, ...presets] },
    ].filter((g) => g.items.length > 0)
  }, [ranked, views, current, ctx])

  const loading = open && (ranked === null || views === null)

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side={small ? 'bottom' : 'right'}
        className={cn('gap-0 p-0', small ? 'max-h-[88dvh] rounded-t-2xl' : 'w-full data-[side=right]:sm:max-w-[600px]')}
      >
        <SheetHeader className="border-b border-border/70 px-5 pb-4 pt-5">
          <SheetTitle className="text-base font-semibold">Add a widget</SheetTitle>
          <SheetDescription className="text-xs">Live previews of your own data. Pick a size, then add it to Home.</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-8 pt-5">
          {loading ? (
            <div className="space-y-4">
              <div className="h-56 animate-pulse rounded-2xl bg-muted/40" />
              <div className="h-56 animate-pulse rounded-2xl bg-muted/40" />
            </div>
          ) : groups.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted-foreground">Everything is already on your Home.</p>
          ) : (
            <div className="space-y-9">
              {groups.map((g) => (
                <section key={g.id} aria-labelledby={`gallery-${g.id}`}>
                  <div className="mb-3 flex items-baseline gap-2">
                    {g.id === 'suggested' ? <Sparkles className="size-3.5 self-center text-primary" aria-hidden /> : null}
                    <h3 id={`gallery-${g.id}`} className="text-sm font-semibold tracking-tight">
                      {g.title}
                    </h3>
                    <span className="text-xs text-muted-foreground">{g.hint}</span>
                  </div>
                  <ul className="space-y-4">
                    {g.items.map((it) => (
                      <GalleryCard
                        key={it.key}
                        item={it}
                        onAdd={async (size) => {
                          onAdd(await it.make(size), it.title)
                          onOpenChange(false)
                        }}
                      />
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

const PREVIEW_BOX: Record<HomeWidgetSize, string> = {
  S: 'h-[204px] w-[204px]',
  M: 'h-[204px] w-full max-w-[420px]',
  L: 'h-[400px] w-full',
}

function GalleryCard({ item, onAdd }: { item: Item; onAdd: (size: HomeWidgetSize) => Promise<void> }) {
  const [size, setSize] = useState<HomeWidgetSize>(item.defaultSize)
  const [busy, setBusy] = useState(false)
  return (
    <li className="rounded-2xl border border-border/70 bg-muted/20 p-3">
      <div className="flex justify-center rounded-xl bg-background/60 p-3" aria-hidden>
        <WhenVisible className={cn('pointer-events-none select-none transition-[width,height] duration-200', PREVIEW_BOX[size])}>
          <Inert className="h-full">{item.preview(size)}</Inert>
        </WhenVisible>
      </div>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-3 px-1">
        <div className="min-w-0 flex-1 basis-56">
          <p className="text-sm font-medium">{item.title}</p>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{item.why}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {item.sizes.length > 1 ? (
            <div role="radiogroup" aria-label={`Size for ${item.title}`} className="flex rounded-full bg-muted p-0.5">
              {item.sizes.map((s) => (
                <button
                  key={s}
                  type="button"
                  role="radio"
                  aria-checked={s === size}
                  aria-label={SIZE_LABEL[s]}
                  title={SIZE_LABEL[s]}
                  onClick={() => setSize(s)}
                  className={cn(
                    'h-7 min-w-7 rounded-full px-2 font-mono text-[11px] font-medium transition-colors',
                    s === size ? 'bg-card text-foreground shadow-sm ring-1 ring-border' : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
          ) : null}
          <Button
            size="sm"
            className="h-8 rounded-full px-3.5"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await onAdd(size)
              } finally {
                setBusy(false)
              }
            }}
            aria-label={`Add ${item.title} (${SIZE_LABEL[size].toLowerCase()})`}
          >
            <Plus className="size-3.5" /> Add
          </Button>
        </div>
      </div>
    </li>
  )
}
