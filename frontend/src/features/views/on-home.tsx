import { useEffect, useState } from 'react'
import { Check, LayoutGrid } from 'lucide-react'

import { HOME_CHANGED, addToHome, getHomeLayout } from '@/api/home'
import { Button } from '@/components/ui/button'
import { PRESET_VIEW } from '@/features/home/widgets/links'

/**
 * A view's place on Home: "Add to Home", or a quiet "On Home" (which opens
 * Home) once a widget for it is there. Every view can be a widget: a chart view
 * by its headline block, a code view as a card that opens it.
 */
export function HomeToggle({ viewId, starter, title, block, navigate }: { viewId: string; starter: string | null; title: string; block: number; navigate: (path: string) => void }) {
  const [onHome, setOnHome] = useState<boolean | null>(null)
  useEffect(() => {
    let alive = true
    const check = () =>
      getHomeLayout()
        .then(
          (l) =>
            alive &&
            setOnHome(
              l.widgets.some(
                (w) =>
                  (w.kind === 'view' && w.ref === viewId) ||
                  // A preset widget whose full page is this view counts too.
                  (w.kind === 'preset' && !!starter && (PRESET_VIEW[w.ref] ?? `widget:${w.ref}`) === starter),
              ),
            ),
        )
        .catch(() => alive && setOnHome(false))
    void check()
    window.addEventListener(HOME_CHANGED, check)
    return () => {
      alive = false
      window.removeEventListener(HOME_CHANGED, check)
    }
  }, [viewId, starter])
  if (onHome === null) return null
  if (onHome) {
    return (
      <Button variant="ghost" size="sm" className="gap-1.5 text-muted-foreground" onClick={() => navigate('/home')} title="This view is a widget on your Home — open Home">
        <Check className="size-3.5" />
        On Home
      </Button>
    )
  }
  return (
    <Button
      variant="ghost"
      size="icon"
      title="Add to Home"
      aria-label="Add to Home"
      onClick={async () => {
        if (await addToHome({ kind: 'view', ref: viewId, block, size: 'M' }, title, navigate)) setOnHome(true)
      }}
    >
      <LayoutGrid className="text-muted-foreground" />
    </Button>
  )
}
