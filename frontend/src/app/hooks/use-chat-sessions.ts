import { useCallback, useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'
import { toast } from 'sonner'

import { createChatSession, deleteChatSession, getChatSessions } from '@/api/client'
import type { ChatSession } from '@/types'

import { parseApiError } from '../portfolio-utils'

/** Archie conversations: list, active selection, create/delete. */
export function useChatSessions() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeId, setActiveId] = useState('')
  const [busy, setBusy] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setBusy(true)
    ;(async () => {
      try {
        const rows = await getChatSessions()
        if (cancelled) return
        if (rows.length > 0) {
          setSessions(rows)
          setActiveId((prev) => (prev && rows.some((r) => r.id === prev) ? prev : rows[0].id))
          return
        }
        const created = await createChatSession('New chat')
        if (cancelled) return
        setSessions([created])
        setActiveId(created.id)
      } catch (err) {
        toast.error('Could not load conversations', { description: parseApiError(err) })
      } finally {
        if (!cancelled) setBusy(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const active = useMemo(() => sessions.find((s) => s.id === activeId) || null, [sessions, activeId])

  const create = useCallback(async (): Promise<ChatSession | null> => {
    if (busy || deletingId) return null
    setBusy(true)
    try {
      const created = await createChatSession(`Chat ${dayjs().format('MMM D HH:mm')}`)
      setSessions((prev) => [created, ...prev])
      setActiveId(created.id)
      return created
    } catch (err) {
      toast.error('Could not start a chat', { description: parseApiError(err) })
      return null
    } finally {
      setBusy(false)
    }
  }, [busy, deletingId])

  const remove = useCallback(
    async (sessionId: string) => {
      if (busy || deletingId) return
      setDeletingId(sessionId)
      try {
        await deleteChatSession(sessionId)
        const remaining = sessions.filter((s) => s.id !== sessionId)
        setSessions(remaining)
        if (activeId === sessionId) {
          if (remaining.length > 0) {
            setActiveId(remaining[0].id)
          } else {
            const created = await createChatSession('New chat')
            setSessions([created])
            setActiveId(created.id)
          }
        }
      } catch (err) {
        toast.error('Could not delete chat', { description: parseApiError(err) })
      } finally {
        setDeletingId(null)
      }
    },
    [busy, deletingId, sessions, activeId],
  )

  /** A session got a new message — float it to the top and select it. */
  const touched = useCallback((session: ChatSession) => {
    setSessions((prev) => [session, ...prev.filter((s) => s.id !== session.id)])
    setActiveId(session.id)
  }, [])

  return { sessions, active, activeId, setActiveId, busy, deletingId, create, remove, touched }
}

export type ChatSessionsState = ReturnType<typeof useChatSessions>
