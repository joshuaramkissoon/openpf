import { type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ArrowUp, ChevronDown, ChevronRight, PanelLeft, Sparkles, Square, Wrench } from 'lucide-react'

import { getChatMessages, stopChat, streamChatMessage, type StreamHandle } from '../api/client'
import type { ChatMessage, ChatSession, ToolCallEntry } from '../types'
import { cn } from '../lib/utils'
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from './ui/input-group'
import { RichMarkdown } from './RichMarkdown'

type SegmentGroup =
  | { type: 'tools'; segments: StreamSegment[] }
  | { type: 'text'; text: string }

function groupSegments(segments: StreamSegment[]): SegmentGroup[] {
  const groups: SegmentGroup[] = []
  for (const seg of segments) {
    if (seg.kind === 'text') {
      const last = groups[groups.length - 1]
      if (last?.type === 'text') {
        last.text += seg.text
      } else {
        groups.push({ type: 'text', text: seg.text })
      }
    } else {
      const last = groups[groups.length - 1]
      if (last?.type === 'tools') {
        last.segments.push(seg)
      } else {
        groups.push({ type: 'tools', segments: [seg] })
      }
    }
  }
  return groups
}

// Phase → dot tint. Quiet, theme-token only.
function dotColor(phase: string): string {
  if (phase === 'tool_result') return 'bg-positive'
  if (phase === 'tool_start') return 'bg-warning'
  if (phase === 'subagent_start') return 'bg-primary'
  if (phase === 'error') return 'bg-negative'
  return 'bg-muted-foreground'
}

function ToolArgs({ input }: { input: Record<string, unknown> }) {
  const entries = Object.entries(input).slice(0, 4)
  if (entries.length === 0) return null
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {entries.map(([k, v]) => (
        <span
          key={k}
          className="inline-flex items-center gap-1 rounded bg-muted/40 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
        >
          <span className="text-foreground/70">{k}</span>
          <span className="opacity-70">{String(v).slice(0, 40)}</span>
        </span>
      ))}
    </div>
  )
}

function ToolStep({
  phase,
  message,
  input,
  children,
}: {
  phase: string
  message: string
  input?: Record<string, unknown>
  children?: React.ReactNode
}) {
  return (
    <div className="flex items-start gap-2 text-xs text-muted-foreground">
      <span className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', dotColor(phase))} />
      <div className="min-w-0 flex-1">
        <span className="leading-relaxed">{message}</span>
        {input ? <ToolArgs input={input} /> : null}
        {children}
      </div>
    </div>
  )
}

function ToolCallsSummary({ toolCalls, expanded, onToggle }: {
  toolCalls: ToolCallEntry[]
  expanded: boolean
  onToggle: () => void
}) {
  const toolCount = toolCalls.filter(tc => tc.phase === 'tool_start' || tc.phase === 'subagent_start').length
  if (toolCount === 0) return null

  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={onToggle}
        className="inline-flex items-center gap-1.5 rounded-md border border-border/60 bg-muted/30 px-2 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <Wrench className="size-3.5" />
        <span>
          {toolCount} tool{toolCount !== 1 ? 's' : ''} used
        </span>
      </button>
      {expanded && (
        <div className="mt-2 space-y-2 border-l border-border/60 pl-3">
          {toolCalls.map((tc, i) => {
            // Subagent entries carry nested_calls; regular entries carry tool_input.
            // Narrow via `in` because the regular `phase` widens to string.
            if ('nested_calls' in tc) {
              return (
                <ToolStep key={i} phase="subagent_start" message={tc.message}>
                  {tc.nested_calls.length > 0 && (
                    <div className="mt-2 space-y-2 border-l border-border/60 pl-3">
                      {tc.nested_calls.map((nc, j) => (
                        <ToolStep key={j} phase={nc.phase} message={nc.message} input={nc.tool_input} />
                      ))}
                    </div>
                  )}
                </ToolStep>
              )
            }
            return <ToolStep key={i} phase={tc.phase} message={tc.message} input={tc.tool_input} />
          })}
        </div>
      )}
    </div>
  )
}

const SUGGESTED_PROMPTS = [
  "How's my portfolio doing?",
  'What are my biggest positions?',
  'Any risk concerns?',
  'Summarise my allocation',
]

const MARKDOWN_CLASS =
  'text-sm leading-relaxed [&_a]:text-primary [&_a]:underline [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs [&_ul]:list-disc [&_ul]:pl-5 [&_table]:w-full'

type StatusItem = { id: string; phase: string; message: string }
type StreamSegment = {
  id: string
  kind: 'thinking' | 'tool_start' | 'tool_result' | 'text'
  text: string
  toolInput?: Record<string, unknown>
}

interface SessionState {
  messages: ChatMessage[]
  sending: boolean
  stopping: boolean
  streamStatus: string | null
  hasStreamedText: boolean
  statusTrail: StatusItem[]
  streamSegments: StreamSegment[]
  streamAssistantId: number | null
  toolCallsExpanded: boolean
  pendingMessages: { content: string; optimisticId: number }[]
  stopNotice: string | null
}

function freshSession(): SessionState {
  return {
    messages: [],
    sending: false,
    stopping: false,
    streamStatus: null,
    hasStreamedText: false,
    statusTrail: [],
    streamSegments: [],
    streamAssistantId: null,
    toolCallsExpanded: false,
    pendingMessages: [],
    stopNotice: null,
  }
}

function stopReasonNotice(stopReason?: string | null, resultSubtype?: string | null): string | null {
  if (resultSubtype === 'error_max_turns') {
    return 'Archie reached the maximum number of steps for this response. You can send a follow-up to continue.'
  }
  if (resultSubtype === 'error_timeout') {
    return 'Response timed out. Try again or simplify your request.'
  }
  if (resultSubtype === 'error_max_budget_usd') {
    return 'Archie hit the cost budget for this response.'
  }
  if (resultSubtype === 'error_during_execution') {
    return 'An error occurred while processing. You can try again.'
  }
  if (stopReason === 'refusal') {
    return 'Archie declined this request. Try rephrasing.'
  }
  if (stopReason === 'max_tokens') {
    return 'Response was cut short due to length limits. Send a follow-up to continue.'
  }
  return null
}

interface Props {
  activeSessionId: string
  activeSessionTitle?: string | null
  accountView: 'all' | 'invest' | 'stocks_isa'
  displayCurrency: 'GBP' | 'USD'
  presentationMask?: boolean
  onSessionTouched?: (session: ChatSession) => void
  onError: (message: string | null) => void
  deletingSessionId?: string | null
  onOpenSessions?: () => void
}

export function AgentChatPanel({
  activeSessionId,
  activeSessionTitle = null,
  accountView,
  displayCurrency,
  presentationMask = false,
  onSessionTouched,
  onError,
  deletingSessionId = null,
  onOpenSessions,
}: Props) {
  const sessionsRef = useRef(new Map<string, SessionState>())
  const activeStreamsRef = useRef(new Map<string, StreamHandle>())
  const [, rerender] = useState(0)
  const activeIdRef = useRef(activeSessionId)
  activeIdRef.current = activeSessionId

  const [input, setInput] = useState('')
  const [expandedToolMsgIds, setExpandedToolMsgIds] = useState<Set<number>>(new Set())
  const threadRef = useRef<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const isNearBottomRef = useRef(true)

  function getSession(id: string): SessionState {
    let s = sessionsRef.current.get(id)
    if (!s) {
      s = freshSession()
      sessionsRef.current.set(id, s)
    }
    return s
  }

  function patchSession(id: string, updater: (s: SessionState) => void) {
    updater(getSession(id))
    if (id === activeIdRef.current) rerender((k) => k + 1)
  }

  const current = getSession(activeSessionId)

  const autoResize = useCallback(() => {
    const ta = textareaRef.current
    if (!ta) return
    // Skip when hidden (display:none ancestor) — scrollHeight is 0
    if (ta.offsetParent === null) {
      ta.style.removeProperty('height')
      return
    }
    // Temporarily hide overflow so scrollHeight reflects full content
    ta.style.overflow = 'hidden'
    ta.style.height = '0'
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'
    ta.style.overflow = ''
  }, [])

  useEffect(() => {
    autoResize()
  }, [input, autoResize])

  // Re-run autoResize when the textarea becomes visible (tab switch)
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) autoResize() },
      { threshold: 0 }
    )
    observer.observe(ta)
    return () => observer.disconnect()
  }, [autoResize])

  useEffect(() => {
    if (!activeSessionId) return
    const session = sessionsRef.current.get(activeSessionId)
    if (session?.sending) {
      rerender((k) => k + 1)
      return
    }

    let cancelled = false
    async function load() {
      try {
        const rows = await getChatMessages(activeSessionId)
        if (cancelled) return
        patchSession(activeSessionId, (s) => {
          s.messages = rows
          s.statusTrail = []
          s.streamStatus = null
          s.streamSegments = []
          s.streamAssistantId = null
        })
      } catch (err) {
        if (cancelled) return
        onError(err instanceof Error ? err.message : 'Failed to load chat messages')
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [activeSessionId, onError])

  useEffect(() => {
    const el = threadRef.current
    if (!el) return
    const handleScroll = () => {
      isNearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    }
    el.addEventListener('scroll', handleScroll, { passive: true })
    return () => el.removeEventListener('scroll', handleScroll)
  }, [])

  useEffect(() => {
    const el = threadRef.current
    if (!el || !isNearBottomRef.current) return
    el.scrollTop = el.scrollHeight
  })

  // Abort stream when a session is being deleted
  useEffect(() => {
    if (!deletingSessionId) return
    const handle = activeStreamsRef.current.get(deletingSessionId)
    if (handle) {
      handle.abort()
      activeStreamsRef.current.delete(deletingSessionId)
    }
  }, [deletingSessionId])

  // Safety net: abort all remaining streams on unmount (app teardown)
  useEffect(() => {
    const streams = activeStreamsRef.current
    return () => {
      for (const handle of streams.values()) {
        handle.abort()
      }
      streams.clear()
    }
  }, [])

  const visibleMessages = useMemo(
    () => current.messages.filter((msg) => msg.role !== 'assistant' || Boolean(msg.content.trim()) || current.sending),
    [current.messages, current.sending]
  )

  async function runStream(
    sessionId: string,
    content: string,
    optimisticUserId: number,
    optimisticAssistantId: number,
  ) {
    const handle = streamChatMessage(
      sessionId,
      {
        content,
        account_kind: accountView,
        display_currency: displayCurrency,
        redact_values: presentationMask,
      },
      {
        onAck: ({ user_message }) => {
          patchSession(sessionId, (s) => {
            s.messages = s.messages.map((row) => (row.id === optimisticUserId ? user_message : row))
            s.streamStatus = 'Thinking...'
          })
        },
        onStatus: ({ phase, message, toolInput }) => {
          patchSession(sessionId, (s) => {
            const next = message || 'Thinking...'
            const last = s.statusTrail[s.statusTrail.length - 1]
            if (!last || last.message !== next || last.phase !== phase) {
              s.statusTrail = [...s.statusTrail, { id: `st-${Date.now()}-${Math.random()}`, phase, message: next }].slice(-8)
            }
            s.streamStatus = next

            const kind: StreamSegment['kind'] =
              phase === 'tool_start'
                ? 'tool_start'
                : phase === 'tool_result'
                  ? 'tool_result'
                  : 'thinking'
            const prev = s.streamSegments[s.streamSegments.length - 1]
            if (!prev || prev.kind !== kind || prev.text !== next) {
              s.streamSegments = [...s.streamSegments, {
                id: `seg-${Date.now()}-${Math.random()}`,
                kind,
                text: next,
                toolInput: phase === 'tool_start' ? toolInput : undefined,
              }]
            }
          })
        },
        onDelta: ({ delta }) => {
          patchSession(sessionId, (s) => {
            s.hasStreamedText = true
            const last = s.streamSegments[s.streamSegments.length - 1]
            if (last && last.kind === 'text') {
              const nextLast: StreamSegment = { ...last, text: last.text + delta }
              s.streamSegments = [...s.streamSegments.slice(0, -1), nextLast]
            } else {
              s.streamSegments = [...s.streamSegments, { id: `seg-${Date.now()}-${Math.random()}`, kind: 'text', text: delta }]
            }
          })
        },
        onDone: ({ session: touched, assistant_message, stop_reason, result_subtype }) => {
          activeStreamsRef.current.delete(sessionId)
          const notice = stopReasonNotice(stop_reason, result_subtype)
          // Atomically set sending=false and dequeue next message to avoid race
          let nextQueued: { content: string; optimisticId: number } | undefined
          patchSession(sessionId, (s) => {
            s.messages = s.messages.map((row) => (row.id === optimisticAssistantId ? assistant_message : row))
            s.streamStatus = null
            s.streamSegments = []
            s.streamAssistantId = null
            s.sending = false
            s.hasStreamedText = false
            s.stopNotice = notice
            nextQueued = s.pendingMessages.shift()
          })
          onSessionTouched?.(touched)
          if (nextQueued) {
            void drainQueued(sessionId, nextQueued.content, nextQueued.optimisticId)
          }
        },
        onError: (message) => {
          activeStreamsRef.current.delete(sessionId)
          patchSession(sessionId, (s) => {
            s.streamStatus = `Failed: ${message}`
            s.statusTrail = [...s.statusTrail, { id: `err-${Date.now()}`, phase: 'error', message: `Failed: ${message}` }].slice(-8)
            s.messages = s.messages.map((row) =>
              row.id === optimisticAssistantId ? { ...row, content: `Message failed: ${message}` } : row
            )
            s.streamSegments = []
            s.streamAssistantId = null
            s.sending = false
            s.hasStreamedText = false
            s.pendingMessages = []
          })
        },
      }
    )
    activeStreamsRef.current.set(sessionId, handle)

    try {
      await handle.done
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to send chat message'
      onError(message)
      patchSession(sessionId, (s) => {
        s.messages = s.messages.map((row) =>
          row.id === optimisticAssistantId ? { ...row, content: row.content || `Message failed: ${message}` } : row
        )
      })
    } finally {
      activeStreamsRef.current.delete(sessionId)
      // Only clean up if onDone/onError didn't already handle it
      const session = getSession(sessionId)
      if (session.sending) {
        patchSession(sessionId, (s) => {
          s.streamStatus = null
          s.sending = false
          s.hasStreamedText = false
        })
      }
    }
  }

  function startStream(sessionId: string, content: string, optimisticUserId: number) {
    const optimisticAssistantId = optimisticUserId - 1
    const optimisticAssistant: ChatMessage = {
      id: optimisticAssistantId,
      session_id: sessionId,
      created_at: new Date().toISOString(),
      role: 'assistant',
      content: '',
    }

    patchSession(sessionId, (s) => {
      s.sending = true
      s.streamStatus = 'Thinking...'
      s.hasStreamedText = false
      s.statusTrail = [{ id: `st-${Date.now()}`, phase: 'thinking', message: 'Thinking...' }]
      s.streamAssistantId = optimisticAssistantId
      s.streamSegments = [{ id: `seg-${Date.now()}`, kind: 'thinking', text: 'Thinking...' }]
      s.messages = [...s.messages, optimisticAssistant]
      s.stopNotice = null
    })

    void runStream(sessionId, content, optimisticUserId, optimisticAssistantId)
  }

  function drainQueued(sessionId: string, content: string, existingOptimisticUserId: number) {
    onError(null)
    isNearBottomRef.current = true
    startStream(sessionId, content, existingOptimisticUserId)
  }

  async function submitContent(content: string) {
    const trimmed = content.trim()
    const sessionId = activeSessionId
    if (!trimmed) return
    if (!sessionId) {
      onError('Create or select a conversation in the sidebar first.')
      return
    }

    const session = getSession(sessionId)

    // Queue the message if Archie is currently responding
    if (session.sending) {
      setInput('')
      isNearBottomRef.current = true
      const optimisticId = -Date.now()
      patchSession(sessionId, (s) => {
        s.pendingMessages.push({ content: trimmed, optimisticId })
        s.messages = [...s.messages, {
          id: optimisticId,
          session_id: sessionId,
          created_at: new Date().toISOString(),
          role: 'user' as const,
          content: trimmed,
        }]
      })
      return
    }

    setInput('')
    onError(null)
    isNearBottomRef.current = true

    const optimisticUserId = -Date.now()
    const optimisticUser: ChatMessage = {
      id: optimisticUserId,
      session_id: sessionId,
      created_at: new Date().toISOString(),
      role: 'user',
      content: trimmed,
    }

    patchSession(sessionId, (s) => {
      s.messages = [...s.messages, optimisticUser]
    })

    startStream(sessionId, trimmed, optimisticUserId)
  }

  function submit() {
    void submitContent(input)
  }

  function partialTextFromSegments(segments: StreamSegment[]): string {
    return segments
      .filter((seg) => seg.kind === 'text')
      .map((seg) => seg.text)
      .join('')
      .trim()
  }

  async function stopStream(sessionId: string) {
    const session = getSession(sessionId)
    if (!session.sending || session.stopping) return

    patchSession(sessionId, (s) => {
      s.stopping = true
      s.streamStatus = 'Stopping…'
    })

    // Tear down the socket first so no further deltas arrive, then ask the
    // backend to interrupt the in-flight turn. The StreamHandle's abort()
    // marks itself settled before closing, so onError won't fire.
    const handle = activeStreamsRef.current.get(sessionId)
    handle?.abort()
    activeStreamsRef.current.delete(sessionId)

    try {
      await stopChat(sessionId)
    } catch {
      // Best-effort: even if the stop call fails, we still finalize the UI
      // locally so the user isn't stuck in a streaming state.
    }

    // Finalize the partial response: keep whatever streamed so far and drop
    // any queued follow-ups, returning the composer to idle.
    patchSession(sessionId, (s) => {
      const partial = partialTextFromSegments(s.streamSegments)
      const assistantId = s.streamAssistantId
      if (assistantId !== null) {
        s.messages = s.messages.map((row) =>
          row.id === assistantId
            ? { ...row, content: partial || row.content }
            : row
        )
        // Drop an empty optimistic assistant bubble if nothing streamed.
        if (!partial) {
          s.messages = s.messages.filter((row) => row.id !== assistantId)
        }
      }
      s.sending = false
      s.stopping = false
      s.hasStreamedText = false
      s.streamStatus = null
      s.streamSegments = []
      s.streamAssistantId = null
      s.pendingMessages = []
      s.stopNotice = 'Response stopped.'
    })
  }

  function onPromptChipClick(prompt: string) {
    void submitContent(prompt)
  }

  function onInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (input.trim()) {
        submit()
      }
    }
  }

  const sendDisabled = !input.trim() || !activeSessionId
  const placeholder = activeSessionId ? 'Write a message…' : 'Select a conversation from the sidebar'

  return (
    <section className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3 py-2.5 text-xs text-muted-foreground sm:px-4">
        {onOpenSessions ? (
          <button
            type="button"
            onClick={onOpenSessions}
            aria-label="Open conversations"
            className="-ml-1 shrink-0 rounded p-1 text-muted-foreground transition-colors hover:text-foreground md:hidden"
          >
            <PanelLeft className="size-4" />
          </button>
        ) : null}
        <span className="mr-auto min-w-0 truncate font-medium text-foreground">
          {activeSessionTitle || 'No conversation selected'}
        </span>
        {presentationMask && <span className="hidden sm:inline">Demo-safe mode: numeric context obfuscated</span>}
        <span className="shrink-0 font-mono uppercase tracking-wide">
          {accountView.toUpperCase()} · {displayCurrency}
        </span>
      </div>

      <div
        ref={threadRef}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-6"
      >
        {visibleMessages.length === 0 && !current.sending ? (
          <div className="flex h-full flex-col items-center justify-center gap-5 text-center">
            <div className="flex size-12 items-center justify-center rounded-full bg-muted/40 text-muted-foreground">
              <Sparkles className="size-5" />
            </div>
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">Hey Josh! I'm Archie, your portfolio copilot.</p>
              <p className="text-xs text-muted-foreground">Ask about performance, positions, allocation or risk.</p>
            </div>
            <div className="flex max-w-md flex-wrap justify-center gap-2">
              {SUGGESTED_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => onPromptChipClick(prompt)}
                  disabled={!activeSessionId}
                  className="rounded-full border border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
            {visibleMessages.map((message) => {
              if (message.role === 'assistant') {
                const isError = message.content.startsWith('Message failed:')
                const isStreaming = current.sending && current.streamAssistantId === message.id
                if (isStreaming) {
                  const groups = groupSegments(current.streamSegments)
                  const expanded = current.toolCallsExpanded

                  return (
                    <article key={`${message.id}-${message.role}`} className="space-y-3">
                      {groups.map((group, gi) => {
                        if (group.type === 'text') {
                          return (
                            <div key={`text-${gi}`} className={MARKDOWN_CLASS}>
                              <RichMarkdown markdown={group.text} />
                            </div>
                          )
                        }
                        const isLastToolGroup = !groups.slice(gi + 1).some(g => g.type === 'tools')
                        const collapsible = isLastToolGroup && !expanded && group.segments.length > 3
                        const visible = collapsible ? group.segments.slice(-3) : group.segments
                        const hiddenCount = group.segments.length - visible.length

                        return (
                          <div
                            key={`tools-${gi}`}
                            className="space-y-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2.5"
                          >
                            {hiddenCount > 0 && (
                              <button
                                type="button"
                                className="text-xs text-muted-foreground transition-colors hover:text-foreground"
                                onClick={() => patchSession(activeSessionId, s => { s.toolCallsExpanded = true })}
                              >
                                Show {hiddenCount} earlier step{hiddenCount !== 1 ? 's' : ''}
                              </button>
                            )}
                            {visible.map((segment) => (
                              <ToolStep
                                key={segment.id}
                                phase={segment.kind}
                                message={segment.text}
                                input={segment.toolInput}
                              />
                            ))}
                            {expanded && isLastToolGroup && group.segments.length > 3 && (
                              <button
                                type="button"
                                className="text-xs text-muted-foreground transition-colors hover:text-foreground"
                                onClick={() => patchSession(activeSessionId, s => { s.toolCallsExpanded = false })}
                              >
                                Collapse
                              </button>
                            )}
                          </div>
                        )
                      })}
                    </article>
                  )
                }
                const hasToolCalls = message.tool_calls && message.tool_calls.length > 0
                return (
                  <article key={`${message.id}-${message.role}`}>
                    {hasToolCalls && (
                      <ToolCallsSummary
                        toolCalls={message.tool_calls!}
                        expanded={expandedToolMsgIds.has(message.id)}
                        onToggle={() => {
                          setExpandedToolMsgIds(prev => {
                            const next = new Set(prev)
                            if (next.has(message.id)) {
                              next.delete(message.id)
                            } else {
                              next.add(message.id)
                            }
                            return next
                          })
                        }}
                      />
                    )}
                    <div className={cn(MARKDOWN_CLASS, isError && 'text-negative')}>
                      <RichMarkdown markdown={message.content} />
                    </div>
                  </article>
                )
              }
              return (
                <div key={`${message.id}-${message.role}`} className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl bg-secondary px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap">
                    {message.content}
                  </div>
                </div>
              )
            })}

            {current.stopNotice && !current.sending && (
              <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                {current.stopNotice}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="border-t border-border px-4 py-3">
        <div className="mx-auto w-full max-w-3xl">
          <InputGroup>
            <InputGroupTextarea
              ref={textareaRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={onInputKeyDown}
              placeholder={placeholder}
              rows={1}
              disabled={!activeSessionId}
            />
            <InputGroupAddon align="block-end">
              <span className="text-xs text-muted-foreground">
                {current.sending ? 'Stop to interrupt Archie' : 'Enter to send · Shift+Enter for newline'}
              </span>
              {current.sending ? (
                <InputGroupButton
                  size="icon-sm"
                  className="ml-auto rounded-full bg-muted text-foreground hover:bg-muted/80"
                  aria-label="Stop"
                  title="Stop response"
                  onClick={() => void stopStream(activeSessionId)}
                  disabled={current.stopping || !activeSessionId}
                >
                  <Square className="fill-current" />
                </InputGroupButton>
              ) : (
                <InputGroupButton
                  size="icon-sm"
                  className="ml-auto rounded-full bg-primary text-primary-foreground hover:bg-primary/90"
                  aria-label="Send"
                  onClick={submit}
                  disabled={sendDisabled}
                >
                  <ArrowUp />
                </InputGroupButton>
              )}
            </InputGroupAddon>
          </InputGroup>
        </div>
      </div>
    </section>
  )
}
