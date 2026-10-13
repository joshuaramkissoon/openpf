import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable } from '@dnd-kit/sortable'
import { Check, GripVertical, LayoutGrid, Plus, RotateCcw, X } from 'lucide-react'

import type { HomeWidget, HomeWidgetSize } from '@/api/home'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

import { AddWidgetSheet } from './add-widget-sheet'
import { SIZE_LABEL, SPAN, sizesFor } from './registry'
import { useHomeLayout } from './use-home-layout'
import { WidgetBody, useWidgetTitle, type HomeCtx } from './widget-content'
import './widgets.css'

const LONG_PRESS_MS = 550
// Tiles stay put while dragging (sizes differ, so shuffling them reads as chaos);
// the drop target lights up and a light ghost follows the pointer.
const noShuffle = () => null

/**
 * Home's customisable widgets: a 4/2/1-column grid of tiles (S/M/L). "Edit Home"
 * makes tiles wiggle; drag (or Space + arrows on the grip) to reorder, pick a
 * size, × to remove, "Add widget" for the gallery. Long-press a tile on touch
 * to start editing. Every change saves itself and can be undone from the toast.
 */
export function HomeWidgets({ ctx, editing, onEditingChange, beforeGrid }: { ctx: HomeCtx; editing: boolean; onEditingChange: (v: boolean) => void; beforeGrid?: ReactNode }) {
  const { widgets, error, reload, change, reset } = useHomeLayout()
  const [galleryOpen, setGalleryOpen] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [overId, setOverId] = useState<string | null>(null)
  const [fresh, setFresh] = useState<string | null>(null)
  const titles = useRef(new Map<string, string>())

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 180, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  // Esc leaves edit mode (unless a drag is in flight — then it cancels the drag).
  useEffect(() => {
    if (!editing) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !activeId && !galleryOpen) onEditingChange(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editing, activeId, galleryOpen, onEditingChange])

  const titleOf = useCallback((id: string | number) => titles.current.get(String(id)) ?? 'widget', [])
  const announcements = useMemo<Announcements>(
    () => ({
      onDragStart: ({ active }) => `Picked up ${titleOf(active.id)}. Use the arrow keys to move it, Space to drop, Escape to cancel.`,
      onDragOver: ({ active, over }) => (over ? `${titleOf(active.id)} is over ${titleOf(over.id)}.` : `${titleOf(active.id)} is no longer over a widget.`),
      onDragEnd: ({ active, over }) => (over ? `Dropped ${titleOf(active.id)} where ${titleOf(over.id)} was.` : `Dropped ${titleOf(active.id)}.`),
      onDragCancel: ({ active }) => `Moving ${titleOf(active.id)} was cancelled.`,
    }),
    [titleOf],
  )

  if (error && !widgets) {
    return (
      <p className="text-sm text-muted-foreground">
        Couldn’t load your Home layout.{' '}
        <button type="button" className="text-primary hover:underline" onClick={() => void reload()}>
          Try again
        </button>
      </p>
    )
  }
  if (!widgets) {
    return (
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-4">
        <Skeleton className="h-64 rounded-2xl md:col-span-2" />
        <Skeleton className="h-64 rounded-2xl md:col-span-2" />
      </div>
    )
  }

  const onDragStart = (e: DragStartEvent) => setActiveId(String(e.active.id))
  const onDragOver = (e: DragOverEvent) => setOverId(e.over ? String(e.over.id) : null)
  const onDragEnd = (e: DragEndEvent) => {
    setActiveId(null)
    setOverId(null)
    const { active, over } = e
    if (!over || active.id === over.id) return
    const from = widgets.findIndex((w) => w.id === active.id)
    const to = widgets.findIndex((w) => w.id === over.id)
    if (from < 0 || to < 0) return
    change(arrayMove(widgets, from, to), `Moved ${titleOf(active.id)}`)
  }
  const remove = (w: HomeWidget) => change(widgets.filter((x) => x.id !== w.id), `Removed ${titleOf(w.id)}`)
  const resize = (w: HomeWidget, size: HomeWidgetSize) => {
    if (w.size === size) return
    change(
      widgets.map((x) => (x.id === w.id ? { ...x, size } : x)),
      `${titleOf(w.id)} is now ${SIZE_LABEL[size].toLowerCase()}`,
    )
  }
  const add = (w: HomeWidget, title: string) => {
    titles.current.set(w.id, title)
    setFresh(w.id)
    change([...widgets, w], `Added ${title}`)
    window.setTimeout(() => document.getElementById(`home-widget-${w.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 120)
  }

  return (
    <section aria-label="Home widgets" className="space-y-5">
      <p className="sr-only" aria-live="polite">
        {editing ? 'Editing Home. Drag widgets or use the grip with Space and arrow keys to reorder; choose a size; remove with the cross. Press Escape or Done when finished.' : ''}
      </p>
      {editing ? (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>Drag to reorder · pick a size · × to remove</span>
        </div>
      ) : null}
      {beforeGrid}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDragEnd={onDragEnd}
        onDragCancel={() => {
          setActiveId(null)
          setOverId(null)
        }}
        accessibility={{
          announcements,
          screenReaderInstructions: { draggable: 'To move this widget, press Space or Enter, use the arrow keys, then press Space again to drop it. Escape cancels.' },
        }}
      >
        <SortableContext items={widgets.map((w) => w.id)} strategy={noShuffle} disabled={!editing}>
          <div className={cn('grid grid-flow-row-dense grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-4', editing && 'pb-24')}>
            {widgets.map((w, i) => (
              <SortableTile
                key={w.id}
                widget={w}
                index={i}
                ctx={ctx}
                editing={editing}
                dragging={activeId === w.id}
                target={!!activeId && overId === w.id && activeId !== w.id}
                fresh={fresh === w.id}
                onTitle={(t) => titles.current.set(w.id, t)}
                onRemove={() => remove(w)}
                onResize={(s) => resize(w, s)}
                onLongPress={() => onEditingChange(true)}
              />
            ))}
          </div>
        </SortableContext>
        <DragOverlay dropAnimation={{ duration: 180, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' }}>
          {activeId ? (
            <div className="inline-flex cursor-grabbing items-center gap-2 rounded-full border border-border bg-popover px-3.5 py-2 text-sm font-medium shadow-xl shadow-black/25">
              <GripVertical className="size-4 text-muted-foreground" />
              {titleOf(activeId)}
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>

      {widgets.length === 0 && !editing ? (
        <div className="rounded-2xl border border-dashed border-border px-6 py-10 text-center">
          <p className="text-sm font-medium">Your Home is empty</p>
          <p className="mt-1 text-xs text-muted-foreground">Add the widgets you want to see first thing.</p>
          <Button size="sm" variant="outline" className="mt-4" onClick={() => setGalleryOpen(true)}>
            <Plus className="size-3.5" /> Add widget
          </Button>
        </div>
      ) : null}

      {editing ? (
        <EditBar onAdd={() => setGalleryOpen(true)} onReset={() => void reset()} onDone={() => onEditingChange(false)} />
      ) : (
        <div className="flex justify-center pt-1">
          <button
            type="button"
            onClick={() => onEditingChange(true)}
            className="inline-flex h-8 items-center gap-1.5 rounded-full px-3.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <LayoutGrid className="size-3.5" />
            Edit Home
          </button>
        </div>
      )}

      <AddWidgetSheet open={galleryOpen} onOpenChange={setGalleryOpen} ctx={ctx} current={widgets} onAdd={add} />
    </section>
  )
}

function EditBar({ onAdd, onReset, onDone }: { onAdd: () => void; onReset: () => void; onDone: () => void }) {
  const [confirmReset, setConfirmReset] = useState(false)
  useEffect(() => {
    if (!confirmReset) return
    const t = window.setTimeout(() => setConfirmReset(false), 3500)
    return () => window.clearTimeout(t)
  }, [confirmReset])
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center px-4 pb-[calc(env(safe-area-inset-bottom,0px)+1rem)] md:pl-[var(--sidebar-width,0px)]">
      <div role="toolbar" aria-label="Edit Home" className="home-pop pointer-events-auto flex items-center gap-1 rounded-full border border-border bg-popover/95 p-1.5 shadow-lg shadow-black/15 backdrop-blur">
        <Button size="sm" variant="ghost" className="h-8 rounded-full px-3" onClick={onAdd}>
          <Plus className="size-4" /> Add widget
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className={cn('h-8 rounded-full px-3 text-muted-foreground', confirmReset && 'text-foreground')}
          onClick={() => (confirmReset ? (setConfirmReset(false), onReset()) : setConfirmReset(true))}
        >
          <RotateCcw className="size-3.5" /> {confirmReset ? 'Reset to default?' : 'Reset'}
        </Button>
        <Button size="sm" className="h-8 rounded-full px-4" onClick={onDone}>
          <Check className="size-4" /> Done
        </Button>
      </div>
    </div>
  )
}

function SortableTile({
  widget,
  index,
  ctx,
  editing,
  dragging,
  target,
  fresh,
  onTitle,
  onRemove,
  onResize,
  onLongPress,
}: {
  widget: HomeWidget
  index: number
  ctx: HomeCtx
  editing: boolean
  dragging: boolean
  target: boolean
  fresh: boolean
  onTitle: (t: string) => void
  onRemove: () => void
  onResize: (s: HomeWidgetSize) => void
  onLongPress: () => void
}) {
  const title = useWidgetTitle(widget)
  useEffect(() => {
    onTitle(title)
  }, [title]) // eslint-disable-line react-hooks/exhaustive-deps
  const { attributes, listeners, setNodeRef, setActivatorNodeRef } = useSortable({ id: widget.id, disabled: !editing })
  const sizes = sizesFor(widget as { kind: HomeWidget['kind']; ref?: string })
  const size = sizes.includes(widget.size) ? widget.size : sizes[sizes.length - 1]
  const inner = useRef<HTMLDivElement>(null)

  // While editing, the widget's own links and buttons are out of reach (mouse, touch and keyboard).
  useEffect(() => {
    const el = inner.current
    if (!el) return
    if (editing) el.setAttribute('inert', '')
    else el.removeAttribute('inert')
  }, [editing])

  // Long-press (touch) on a tile enters edit mode, like the iOS home screen.
  const press = useRef<{ t: number; x: number; y: number } | null>(null)
  const cancelPress = () => {
    if (press.current) window.clearTimeout(press.current.t)
    press.current = null
  }
  const pressHandlers = editing
    ? {}
    : {
        onPointerDown: (e: React.PointerEvent) => {
          if (e.pointerType !== 'touch') return
          const t = window.setTimeout(() => {
            press.current = null
            navigator.vibrate?.(8)
            onLongPress()
          }, LONG_PRESS_MS)
          press.current = { t, x: e.clientX, y: e.clientY }
        },
        onPointerMove: (e: React.PointerEvent) => {
          const p = press.current
          if (p && Math.hypot(e.clientX - p.x, e.clientY - p.y) > 8) cancelPress()
        },
        onPointerUp: cancelPress,
        onPointerCancel: cancelPress,
      }

  // Pointer/touch drags start anywhere on the tile; the keyboard uses the grip.
  const { onKeyDown, ...pointerListeners } = (listeners ?? {}) as Record<string, (e: unknown) => void>

  return (
    <div
      ref={setNodeRef}
      id={`home-widget-${widget.id}`}
      // Viz tiles and slim lines keep their own height; a card with nothing to
      // render (e.g. no permissions used today) takes no grid cell at all.
      className={cn(
        'relative min-w-0 transition-opacity has-[[data-slim]]:self-start has-[[data-viz-tile]]:self-start [&:has(>div>div:empty)]:hidden',
        SPAN[size],
      )}
      data-editing={editing ? '' : undefined}
      {...(editing ? pointerListeners : {})}
      {...pressHandlers}
    >
      <div
        className={cn(
          'relative h-full rounded-2xl',
          fresh && 'home-pop',
          editing && 'home-wiggle cursor-grab touch-manipulation select-none outline-1 outline-offset-4 outline-border outline-dashed',
          dragging && 'opacity-35 [animation:none]',
          target && 'outline-2 outline-solid outline-primary [animation:none]',
        )}
        data-size={size}
        data-alt={index % 2 ? '' : undefined}
      >
        <div ref={inner} className={cn('h-full', editing && 'pointer-events-none')}>
          <WidgetBody widget={widget} size={size} ctx={ctx} editing={editing} />
        </div>

        {editing ? (
          <>
            <button
              type="button"
              onClick={onRemove}
              onMouseDown={(e) => e.stopPropagation()}
              onTouchStart={(e) => e.stopPropagation()}
              className="absolute -left-2.5 -top-2.5 z-10 inline-flex size-6 items-center justify-center rounded-full bg-foreground text-background shadow-md ring-2 ring-background transition-transform hover:scale-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              aria-label={`Remove ${title}`}
              title={`Remove ${title}`}
            >
              <X className="size-3.5" strokeWidth={2.75} />
            </button>
            <div className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-full border border-border bg-popover/95 p-0.5 shadow-sm backdrop-blur">
              {sizes.length > 1 ? (
                <div role="radiogroup" aria-label={`Size of ${title}`} className="flex items-center">
                  {sizes.map((s) => (
                    <button
                      key={s}
                      type="button"
                      role="radio"
                      aria-checked={s === size}
                      aria-label={SIZE_LABEL[s]}
                      title={SIZE_LABEL[s]}
                      onClick={() => onResize(s)}
                      onMouseDown={(e) => e.stopPropagation()}
                      onTouchStart={(e) => e.stopPropagation()}
                      className={cn(
                        'inline-flex h-6 min-w-6 items-center justify-center rounded-full px-1.5 font-mono text-[11px] font-medium transition-colors',
                        s === size ? 'bg-foreground text-background' : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                      )}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              ) : null}
              <button
                type="button"
                ref={setActivatorNodeRef}
                {...attributes}
                onKeyDown={onKeyDown as React.KeyboardEventHandler}
                aria-label={`Move ${title}`}
                aria-roledescription="draggable widget"
                className="inline-flex size-6 cursor-grab items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-primary"
              >
                <GripVertical className="size-3.5" />
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}
