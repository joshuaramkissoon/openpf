"use client"

import * as React from "react"
import { SearchIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { Dialog, DialogContent } from "@/components/ui/dialog"

/**
 * A lightweight, dependency-free command-palette primitive set in the shadcn
 * shape (Command / CommandInput / CommandList / CommandGroup / CommandItem …).
 *
 * We intentionally do NOT use `cmdk` here: this project's UI kit is built on
 * Base UI (not Radix), and cmdk would pull in a second, conflicting dialog/focus
 * stack. Keyboard navigation walks the rendered items in DOM order, so callers
 * can filter/group freely and arrow-key movement just works.
 */

type CommandContextValue = {
  active: string
  setActive: (value: string) => void
  /** id of the listbox, shared so the input can wire aria-controls/activedescendant. */
  listId: string
}

const CommandContext = React.createContext<CommandContextValue | null>(null)

function useCommand() {
  const ctx = React.useContext(CommandContext)
  if (!ctx) throw new Error("Command components must be used within <Command>")
  return ctx
}

function Command({
  className,
  value,
  resetKey,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  /** Current search text — active item resets to the first match when it changes. */
  value?: string
  /** Bump when async results arrive so the highlight snaps back to the top match. */
  resetKey?: string | number
}) {
  const [active, setActive] = React.useState("")
  const rootRef = React.useRef<HTMLDivElement>(null)
  const listId = React.useId()

  const getItems = () =>
    Array.from(rootRef.current?.querySelectorAll<HTMLElement>("[data-command-item]") ?? [])

  // Snap the highlight to the first item whenever the query or result set changes.
  React.useLayoutEffect(() => {
    setActive(getItems()[0]?.dataset.value ?? "")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, resetKey])

  const move = (dir: 1 | -1) => {
    const els = getItems()
    if (els.length === 0) return
    const idx = els.findIndex((el) => el.dataset.value === active)
    const next = els[(idx + dir + els.length) % els.length] ?? els[0]
    setActive(next.dataset.value ?? "")
    next.scrollIntoView({ block: "nearest" })
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      move(1)
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      move(-1)
    } else if (e.key === "Enter") {
      const el = getItems().find((it) => it.dataset.value === active)
      if (el) {
        e.preventDefault()
        el.click()
      }
    }
  }

  return (
    <CommandContext.Provider value={{ active, setActive, listId }}>
      <div
        ref={rootRef}
        data-slot="command"
        className={cn("flex h-full w-full flex-col overflow-hidden rounded-xl bg-popover text-popover-foreground", className)}
        onKeyDown={onKeyDown}
        {...props}
      >
        {children}
      </div>
    </CommandContext.Provider>
  )
}

function CommandInput({ className, ...props }: React.ComponentProps<"input">) {
  const { active, listId } = useCommand()
  return (
    <div data-slot="command-input-wrapper" className="flex items-center gap-2.5 border-b border-border/60 px-4">
      <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
      <input
        data-slot="command-input"
        role="combobox"
        aria-expanded={true}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={active ? `${listId}-opt-${active}` : undefined}
        // eslint-disable-next-line jsx-a11y/no-autofocus
        autoFocus
        className={cn(
          "flex h-12 w-full bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
        {...props}
      />
    </div>
  )
}

function CommandList({ className, ...props }: React.ComponentProps<"div">) {
  const { listId } = useCommand()
  return (
    <div
      data-slot="command-list"
      id={listId}
      role="listbox"
      className={cn("max-h-[min(60vh,420px)] overflow-y-auto overflow-x-hidden overscroll-contain p-1.5", className)}
      {...props}
    />
  )
}

function CommandEmpty({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="command-empty"
      className={cn("py-10 text-center text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

function CommandGroup({
  heading,
  className,
  children,
  ...props
}: React.ComponentProps<"div"> & { heading?: React.ReactNode }) {
  return (
    <div data-slot="command-group" className={cn("mb-1", className)} {...props}>
      {heading ? (
        <div className="px-2.5 pt-2.5 pb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          {heading}
        </div>
      ) : null}
      {children}
    </div>
  )
}

function CommandItem({
  value,
  onSelect,
  className,
  children,
  disabled,
  ...props
}: Omit<React.ComponentProps<"div">, "onSelect"> & {
  value: string
  onSelect?: () => void
  disabled?: boolean
}) {
  const { active, setActive, listId } = useCommand()
  const isActive = active === value

  return (
    <div
      data-slot="command-item"
      data-command-item={disabled ? undefined : ""}
      data-value={value}
      id={`${listId}-opt-${value}`}
      role="option"
      aria-selected={isActive}
      aria-disabled={disabled}
      onMouseMove={() => !disabled && setActive(value)}
      onClick={() => !disabled && onSelect?.()}
      className={cn(
        "flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm outline-none transition-colors",
        isActive ? "bg-accent text-accent-foreground" : "text-foreground",
        disabled && "pointer-events-none opacity-50",
        className
      )}
      {...props}
    >
      {children}
    </div>
  )
}

function CommandSeparator({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="command-separator" className={cn("-mx-1.5 my-1 h-px bg-border/60", className)} {...props} />
}

function CommandShortcut({ className, ...props }: React.ComponentProps<"kbd">) {
  return (
    <kbd
      data-slot="command-shortcut"
      className={cn(
        "ml-auto inline-flex h-5 items-center rounded border border-border/60 bg-muted/50 px-1.5 font-mono text-[10px] text-muted-foreground",
        className
      )}
      {...props}
    />
  )
}

function CommandDialog({
  open,
  onOpenChange,
  children,
  className,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: React.ReactNode
  className?: string
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className={cn(
          "top-[12vh] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-xl",
          className
        )}
      >
        {children}
      </DialogContent>
    </Dialog>
  )
}

export {
  Command,
  CommandDialog,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandSeparator,
  CommandShortcut,
}
