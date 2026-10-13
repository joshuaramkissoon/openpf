import { useEffect, useState } from 'react'
import { LockKeyhole } from 'lucide-react'

import { UNAUTHORIZED_EVENT, getApiToken, setApiToken } from '../../api/auth'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

/** Unlock screen shown when the API answers 401 (OPENPF_API_TOKEN set). */
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
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-background/95 p-4 backdrop-blur-sm">
      <form
        className="w-full max-w-sm space-y-5"
        onSubmit={(e) => {
          e.preventDefault()
          setApiToken(value.trim())
          window.location.reload()
        }}
      >
        <div className="space-y-3">
          <span className="flex size-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <LockKeyhole className="size-4" />
          </span>
          <div className="space-y-1">
            <h2 className="text-xl font-semibold tracking-tight">Unlock OpenPF</h2>
            <p className="text-sm text-muted-foreground">
              This server needs its API token (<span className="font-mono text-xs">OPENPF_API_TOKEN</span>). It's kept in
              this browser only.
            </p>
          </div>
        </div>
        <Input
          type="password"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="API token"
          aria-label="API token"
        />
        <Button type="submit" className="w-full" disabled={!value.trim()}>
          Unlock
        </Button>
      </form>
    </div>
  )
}
