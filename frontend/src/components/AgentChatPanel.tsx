import { type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dayjs from 'dayjs'

import { getChatMessages, streamChatMessage, type StreamHandle } from '../api/client'
import type { ChatMessage, ChatSession, ToolCallEntry } from '../types'
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

function ToolCallsSummary({ toolCalls, expanded, onToggle }: {
  toolCalls: ToolCallEntry[]
  expanded: boolean
  onToggle: () => void
}) {
  const toolCount = toolCalls.filter(tc => tc.phase === 'tool_start').length
  if (toolCount === 0) return null

  return (
    <div className="chat-tool-summary">
      <button className="chat-tool-summary-toggle" onClick={onToggle}>
        <span className="chat-tool-summary-icon">{expanded ? '\u25BE' : '\u25B8'}</span>
        <span className="chat-tool-summary-count">
          {toolCount} tool{toolCount !== 1 ? 's' : ''} used
        </span>
      </button>
      {expanded && (
        <div className="chat-tool-timeline chat-tool-timeline-done">
          {toolCalls.map((tc, i) => (
            <div key={i} className={`chat-tool-step ${tc.phase}`}>
              <span className={`chat-tool-dot done ${tc.phase}`} />
              <div className="chat-tool-step-body">
                <span className="chat-tool-step-text">{tc.message}</span>
                {tc.tool_input && Object.keys(tc.tool_input).length > 0 && (
                  <div className="chat-tool-args">
                    {Object.entries(tc.tool_input).slice(0, 4).map(([k, v]) => (
                      <span key={k} className="chat-tool-arg">
                        <span className="chat-tool-arg-key">{k}</span>
                        <span className="chat-tool-arg-val">{String(v).slice(0, 40)}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
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
  streamStatus: string | null
  hasStreamedText: boolean
  statusTrail: StatusItem[]
  streamSegments: StreamSegment[]
  streamAssistantId: number | null
  toolCallsExpanded: boolean
}

function freshSession(): SessionState {
  return {
    messages: [],
    sending: false,
    streamStatus: null,
    hasStreamedText: false,
    statusTrail: [],
    streamSegments: [],
    streamAssistantId: null,
    toolCallsExpanded: false,
  }
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

  async function submitContent(content: string) {
    const trimmed = content.trim()
    const sessionId = activeSessionId
    if (!trimmed) return
    if (!sessionId) {
      onError('Create or select a conversation in the sidebar first.')
      return
    }

    const session = getSession(sessionId)
    if (session.sending) return

    setInput('')
    onError(null)
    isNearBottomRef.current = true

    const optimisticUserId = -Date.now()
    const optimisticAssistantId = optimisticUserId - 1
    const optimisticUser: ChatMessage = {
      id: optimisticUserId,
      session_id: sessionId,
      created_at: new Date().toISOString(),
      role: 'user',
      content: trimmed,
    }
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
      s.messages = [...s.messages, optimisticUser, optimisticAssistant]
    })

    const handle = streamChatMessage(
      sessionId,
      {
        content: trimmed,
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
        onDone: ({ session: touched, assistant_message }) => {
          activeStreamsRef.current.delete(sessionId)
          patchSession(sessionId, (s) => {
            s.messages = s.messages.map((row) => (row.id === optimisticAssistantId ? assistant_message : row))
            s.streamStatus = null
            s.streamSegments = []
            s.streamAssistantId = null
            s.sending = false
            s.hasStreamedText = false
          })
          onSessionTouched?.(touched)
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
      // (avoids a race condition flash where segments are cleared but sending is still true)
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

  function submit() {
    void submitContent(input)
  }

  function onPromptChipClick(prompt: string) {
    void submitContent(prompt)
  }

  function onInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (input.trim() && !current.sending) {
        submit()
      }
    }
  }

  return (
    <section className="glass-card chat-card chat-card-full">
      <div className="chat-head-actions">
        <span className="hint">Conversation: {activeSessionTitle || 'None selected'}</span>
        {presentationMask && <span className="hint">Demo-safe mode: numeric context is obfuscated</span>}
        <span className="hint">Enter to send, Shift+Enter newline</span>
      </div>

      <div className="chat-main-pane">
        <div className="chat-thread" ref={threadRef}>
          {visibleMessages.length === 0 && !current.sending && (
            <div className="chat-welcome">
              <p className="chat-welcome-greeting">Hey Josh! I'm Archie, your portfolio copilot.</p>
              <div className="chat-welcome-chips">
                {SUGGESTED_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    className="chat-prompt-chip"
                    onClick={() => onPromptChipClick(prompt)}
                    disabled={!activeSessionId}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          )}

          {visibleMessages.map((message) => {
            if (message.role === 'assistant') {
              const isError = message.content.startsWith('Message failed:')
              const isStreaming = current.sending && current.streamAssistantId === message.id
              if (isStreaming) {
                const groups = groupSegments(current.streamSegments)
                const expanded = current.toolCallsExpanded

                return (
                  <article key={`${message.id}-${message.role}`} className="chat-response">
                    <div className="chat-inline-stream">
                      {groups.map((group, gi) => {
                        if (group.type === 'text') {
                          return (
                            <div key={`text-${gi}`} className="chat-markdown">
                              <RichMarkdown markdown={group.text} />
                            </div>
                          )
                        }
                        const isLastToolGroup = !groups.slice(gi + 1).some(g => g.type === 'tools')
                        const collapsible = isLastToolGroup && !expanded && group.segments.length > 3
                        const visible = collapsible ? group.segments.slice(-3) : group.segments
                        const hiddenCount = group.segments.length - visible.length

                        return (
                          <div key={`tools-${gi}`} className="chat-tool-timeline">
                            {hiddenCount > 0 && (
                              <button
                                className="chat-tool-expand-btn"
                                onClick={() => patchSession(activeSessionId, s => { s.toolCallsExpanded = true })}
                              >
                                Show {hiddenCount} earlier step{hiddenCount !== 1 ? 's' : ''}
                              </button>
                            )}
                            {visible.map((segment) => (
                              <div key={segment.id} className={`chat-tool-step ${segment.kind}`}>
                                <span className={`chat-tool-dot ${segment.kind}`} />
                                <div className="chat-tool-step-body">
                                  <span className="chat-tool-step-text">{segment.text}</span>
                                  {segment.toolInput && Object.keys(segment.toolInput).length > 0 && (
                                    <div className="chat-tool-args">
                                      {Object.entries(segment.toolInput).slice(0, 4).map(([k, v]) => (
                                        <span key={k} className="chat-tool-arg">
                                          <span className="chat-tool-arg-key">{k}</span>
                                          <span className="chat-tool-arg-val">{String(v).slice(0, 40)}</span>
                                        </span>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              </div>
                            ))}
                            {expanded && isLastToolGroup && group.segments.length > 3 && (
                              <button
                                className="chat-tool-expand-btn"
                                onClick={() => patchSession(activeSessionId, s => { s.toolCallsExpanded = false })}
                              >
                                Collapse
                              </button>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </article>
                )
              }
              const hasToolCalls = message.tool_calls && message.tool_calls.length > 0
              return (
                <article key={`${message.id}-${message.role}`} className={`chat-response ${isError ? 'error' : ''}`}>
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
                  <div className="chat-markdown">
                    <RichMarkdown markdown={message.content} />
                  </div>
                </article>
              )
            }
            return (
              <article key={`${message.id}-${message.role}`} className="chat-bubble user">
                <div className="chat-meta">
                  <strong>You</strong>
                  <span>{dayjs(message.created_at).format('HH:mm:ss')}</span>
                </div>
                <p>{message.content}</p>
              </article>
            )
          })}
        </div>

        <div className="chat-composer">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder={
              activeSessionTitle
                ? `Message ${activeSessionTitle} (${accountView.toUpperCase()} / ${displayCurrency})`
                : 'Select a conversation from the sidebar'
            }
            rows={1}
            disabled={!activeSessionId}
          />
          <button
            className="chat-send-btn"
            onClick={submit}
            disabled={current.sending || !input.trim() || !activeSessionId}
            aria-label="Send message"
          >
            {current.sending ? '…' : 'Send'}
          </button>
        </div>
      </div>
    </section>
  )
}
