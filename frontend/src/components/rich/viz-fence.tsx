import { Component, useMemo, type ReactNode } from 'react'

import { VizBlock, validateVizSpec } from '@/components/viz'
import { CodeBlock } from './code-block'

/** Keeps a misbehaving viz from taking the whole message down. */
class VizBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

/**
 * A ```viz fenced block from Archie. While the fence is still streaming it's a
 * calm skeleton (never half-parsed JSON); once closed it renders through the
 * viz library, or degrades to a neat code block if the JSON/spec is invalid.
 */
export function VizFence({ body, closed }: { body: string; closed: boolean }) {
  const parsed = useMemo(() => {
    if (!closed) return null
    try {
      const spec = JSON.parse(body) as unknown
      const v = validateVizSpec(spec)
      return v.ok ? { ok: true as const, spec } : { ok: false as const, error: v.error }
    } catch {
      return { ok: false as const, error: 'not valid JSON' }
    }
  }, [body, closed])

  if (!parsed) {
    const title = body.match(/"title"\s*:\s*"([^"]{1,80})"/)?.[1]
    return (
      <div className="rc-viz-skel" aria-busy="true" aria-label="Drawing a chart…">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">{title ?? 'Drawing a chart…'}</span>
        </div>
        <div className="rc-viz-skel-bars">
          {[62, 38, 80, 51, 27, 70].map((h, i) => (
            <span key={i} style={{ height: `${h}%`, animationDelay: `${i * 90}ms` }} />
          ))}
        </div>
      </div>
    )
  }
  if (!parsed.ok) return <CodeBlock lang="viz" body={body} note={`couldn't draw this (${parsed.error})`} />
  return (
    <div className="rc-viz">
      <VizBoundary fallback={<CodeBlock lang="viz" body={body} note="couldn't draw this" />}>
        <VizBlock spec={parsed.spec} />
      </VizBoundary>
    </div>
  )
}
