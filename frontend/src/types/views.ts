export type ViewVersion = { version: number; created_at: string; note: string; bytes: number }
export type ViewHistoryItem = { kind: 'create' | 'edit' | 'fix'; prompt: string; at: string }

export type ViewMeta = {
  id: string
  title: string
  description: string
  icon: string
  pinned: boolean
  order: number
  created_at: string | null
  updated_at: string | null
  current_version: number
  versions: ViewVersion[]
  history: ViewHistoryItem[]
  starter: string | null
  active_job?: ViewJob | null
}

export type ViewJob = {
  id: string
  view_id: string
  kind: 'create' | 'edit' | 'fix'
  prompt: string
  status: 'queued' | 'running' | 'done' | 'error'
  progress: { at: number; text: string }[]
  error: string | null
  version: number | null
  elapsed_s: number
  cost_usd: number | null
}

export type ViewDocument = { view_id: string; version: number; html: string; problems: string[] }

export type ViewStarter = { slug: string; title: string; description: string; icon: string; installed: boolean }
