import { useState } from "react"
import dayjs from "dayjs"
import { Loader2, Plus, X } from "lucide-react"

import { AgentChatPanel } from "@/components/AgentChatPanel"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { cn } from "@/lib/utils"
import type { ChatSession } from "@/types"
import type { ChatSessionsState } from "@/app/hooks/use-chat-sessions"
import type { AccountView, DisplayCurrency } from "@/app/portfolio-utils"

function dayLabel(iso: string) {
  const d = dayjs(iso)
  if (d.isSame(dayjs(), "day")) return "Today"
  if (d.isSame(dayjs().subtract(1, "day"), "day")) return "Yesterday"
  if (d.isAfter(dayjs().subtract(7, "day"))) return "This week"
  return "Older"
}

function ConversationRail({ chat, onPicked }: { chat: ChatSessionsState; onPicked?: () => void }) {
  const [deleteTarget, setDeleteTarget] = useState<ChatSession | null>(null)
  const groups: { label: string; rows: ChatSession[] }[] = []
  for (const s of chat.sessions) {
    const label = dayLabel(s.updated_at)
    const g = groups.find((x) => x.label === label)
    if (g) g.rows.push(s)
    else groups.push({ label, rows: [s] })
  }

  return (
    <>
      <div className="p-2.5">
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-start gap-2"
          onClick={() => {
            void chat.create()
            onPicked?.()
          }}
          disabled={chat.busy}
        >
          <Plus className="size-4" />
          New chat
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-3 [scrollbar-width:none]">
        {groups.map((g) => (
          <div key={g.label} className="mb-3">
            <p className="px-2 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">{g.label}</p>
            {g.rows.map((s) => (
              <div
                key={s.id}
                className={cn(
                  "group flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm transition-colors",
                  s.id === chat.activeId ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                )}
              >
                <button
                  type="button"
                  className="min-w-0 flex-1 truncate text-left"
                  title={s.title}
                  onClick={() => {
                    chat.setActiveId(s.id)
                    onPicked?.()
                  }}
                  disabled={Boolean(chat.deletingId)}
                >
                  {s.title}
                </button>
                <button
                  type="button"
                  className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition hover:text-foreground group-hover:opacity-100 focus-visible:opacity-100"
                  onClick={() => setDeleteTarget(s)}
                  disabled={chat.deletingId === s.id}
                  title="Delete chat"
                  aria-label={`Delete ${s.title}`}
                >
                  {chat.deletingId === s.id ? <Loader2 className="size-3.5 animate-spin" /> : <X className="size-3.5" />}
                </button>
              </div>
            ))}
          </div>
        ))}
      </div>

      <AlertDialog open={deleteTarget !== null} onOpenChange={(o) => !o && !chat.deletingId && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete chat?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget ? <>“{deleteTarget.title}” will be permanently deleted.</> : null}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (deleteTarget) void chat.remove(deleteTarget.id).then(() => setDeleteTarget(null))
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

/**
 * `/archie` — conversation rail + chat. The shell keeps this mounted (hidden)
 * across navigation so an in-flight stream is never lost.
 */
export function ArchiePage({
  chat,
  accountView,
  currency,
  presentationMask,
  pendingPrompt,
  pendingPromptSend,
  onPendingPromptConsumed,
  onError,
}: {
  chat: ChatSessionsState
  accountView: AccountView
  currency: DisplayCurrency
  presentationMask: boolean
  pendingPrompt: string | null
  pendingPromptSend: boolean
  onPendingPromptConsumed: () => void
  onError: (m: string | null) => void
}) {
  const [railOpen, setRailOpen] = useState(false)
  return (
    <div className="flex h-full min-h-0">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border md:flex">
        <ConversationRail chat={chat} />
      </aside>
      <Sheet open={railOpen} onOpenChange={setRailOpen}>
        <SheetContent side="left" showCloseButton={false} className="flex w-72 max-w-[85vw] flex-col gap-0 p-0">
          <SheetTitle className="sr-only">Conversations</SheetTitle>
          <ConversationRail chat={chat} onPicked={() => setRailOpen(false)} />
        </SheetContent>
      </Sheet>
      <div className="min-w-0 flex-1">
        <AgentChatPanel
          activeSessionId={chat.activeId}
          activeSessionTitle={chat.active?.title || null}
          accountView={accountView}
          displayCurrency={currency}
          presentationMask={presentationMask}
          onSessionTouched={chat.touched}
          onError={onError}
          deletingSessionId={chat.deletingId}
          onOpenSessions={() => setRailOpen(true)}
          pendingPrompt={pendingPrompt}
          pendingPromptSend={pendingPromptSend}
          onPendingPromptConsumed={onPendingPromptConsumed}
        />
      </div>
    </div>
  )
}
