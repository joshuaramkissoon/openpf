import { useEffect, useState } from 'react'

import { UNAUTHORIZED_EVENT, getApiToken, setApiToken } from '../../api/auth'

/** Minimal unlock prompt shown when the API answers 401 (OPENPF_API_TOKEN set). */
export function TokenGate() {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')

  useEffect(() => {
    const onUnauthorized = () => {
      setValue(getApiToken())
      setOpen(true)
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4">
      <form
        className="w-full max-w-sm space-y-3 rounded-lg border border-border bg-background p-5 shadow-xl"
        onSubmit={(e) => {
          e.preventDefault()
          setApiToken(value.trim())
          window.location.reload()
        }}
      >
        <h2 className="text-sm font-semibold">Unlock OpenPF</h2>
        <p className="text-xs text-muted-foreground">
          This server requires an API token (OPENPF_API_TOKEN). It is stored in this browser only.
        </p>
        <input
          type="password"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="API token"
          className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
        />
        <button
          type="submit"
          disabled={!value.trim()}
          className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          Unlock
        </button>
      </form>
    </div>
  )
}
