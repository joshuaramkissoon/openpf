import { useState } from 'react'
import { Check, Copy } from 'lucide-react'

import { copyToClipboard } from '@/lib/clipboard'

/* ── Code ──────────────────────────────────────────────────────────────── */

export function CodeBlock({ lang, body, note }: { lang: string; body: string; note?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="rc-codeblock">
      <div className="rc-codeblock-head">
        <span>{lang || 'text'}</span>
        {note ? <span className="text-muted-foreground/80">· {note}</span> : null}
        <button
          type="button"
          className="ml-auto inline-flex items-center gap-1 hover:text-foreground"
          onClick={() => {
            void copyToClipboard(body).then(() => {
              setCopied(true)
              window.setTimeout(() => setCopied(false), 1200)
            })
          }}
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre>
        <code>{body}</code>
      </pre>
    </div>
  )
}

