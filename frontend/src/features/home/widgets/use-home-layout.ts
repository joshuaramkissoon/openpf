import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { HOME_CHANGED, getHomeLayout, putHomeLayout, resetHomeLayout, type HomeWidget } from '@/api/home'
import { toastApiError } from '@/lib/api-error'

const SAVE_DELAY_MS = 350
const UNDO_TOAST = 'home-layout-undo'

/**
 * The Home layout: loaded once, changed optimistically, saved after a short
 * debounce. Every change offers a one-step Undo in a single (replaced) toast.
 */
export function useHomeLayout() {
  const [widgets, setWidgets] = useState<HomeWidget[] | null>(null)
  const [error, setError] = useState(false)
  const current = useRef<HomeWidget[] | null>(null)
  const timer = useRef<number | null>(null)

  const load = useCallback(async () => {
    try {
      const layout = await getHomeLayout()
      current.current = layout.widgets
      setWidgets(layout.widgets)
      setError(false)
    } catch {
      setError(true)
    }
  }, [])

  useEffect(() => {
    void load()
    const onChange = () => void load()
    window.addEventListener(HOME_CHANGED, onChange)
    return () => window.removeEventListener(HOME_CHANGED, onChange)
  }, [load])

  const save = useCallback((next: HomeWidget[]) => {
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(async () => {
      timer.current = null
      try {
        const saved = await putHomeLayout({ widgets: next })
        // Server may assign ids or drop duplicates; adopt its answer if it differs.
        if (JSON.stringify(saved.widgets) !== JSON.stringify(current.current)) {
          current.current = saved.widgets
          setWidgets(saved.widgets)
        }
      } catch (err) {
        toastApiError(err, 'Couldn’t save your Home layout')
        void load()
      }
    }, SAVE_DELAY_MS)
  }, [load])

  /** Apply a change; `label` names it in the undo toast ("Removed Top movers"). */
  const change = useCallback(
    (next: HomeWidget[], label: string) => {
      const prev = current.current ?? []
      current.current = next
      setWidgets(next)
      save(next)
      toast(label, {
        id: UNDO_TOAST,
        duration: 5000,
        action: {
          label: 'Undo',
          onClick: () => {
            current.current = prev
            setWidgets(prev)
            save(prev)
          },
        },
      })
    },
    [save],
  )

  const reset = useCallback(async () => {
    const prev = current.current ?? []
    try {
      const layout = await resetHomeLayout()
      current.current = layout.widgets
      setWidgets(layout.widgets)
      toast('Home reset to the default', {
        id: UNDO_TOAST,
        duration: 5000,
        action: { label: 'Undo', onClick: () => change(prev, 'Restored your layout') },
      })
    } catch (err) {
      toastApiError(err, 'Couldn’t reset Home')
    }
  }, [change])

  useEffect(
    () => () => {
      // Flush a pending save if Home unmounts mid-debounce.
      if (timer.current && current.current) {
        window.clearTimeout(timer.current)
        void putHomeLayout({ widgets: current.current }).catch(() => {})
      }
    },
    [],
  )

  return { widgets, error, reload: load, change, reset }
}
