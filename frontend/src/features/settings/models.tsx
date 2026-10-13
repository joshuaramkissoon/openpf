import { useEffect, useState } from "react"
import axios from "axios"
import { Check, KeyRound, Loader2, RotateCcw } from "lucide-react"
import { toast } from "sonner"

import { withAuth } from "@/api/auth"
import { Section } from "@/components/kit"
import { Disclosure } from "./ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { toastApiError } from "@/lib/api-error"
import { cn } from "@/lib/utils"

const api = withAuth(axios.create({ baseURL: import.meta.env.VITE_API_BASE ?? "/api", timeout: 30000 }))

type Provider = "together" | "anthropic" | "gemini" | "claude_sdk"

interface JobRow {
  key: string
  kind: "agent" | "fast"
  group: string
  label: string
  explain: string
  provider: Provider
  model: string
  default_provider: Provider
  default_model: string
  customised: boolean
  fallbacks: string[]
}

interface ModelsInfo {
  jobs: JobRow[]
  providers: Record<Provider, boolean>
  default_models: Record<Provider, string>
  claude_models: string[]
  prices: { together: [number, number] }
}

type KeyField = "together_api_key" | "finnhub_api_key" | "fred_api_key"
type ProvidersInfo = Record<KeyField, { label: string; configured: boolean; source: "env" | "settings" | null }>

const PROVIDER_LABEL: Record<Provider, string> = {
  together: "Together AI",
  anthropic: "Claude API",
  gemini: "Gemini",
  claude_sdk: "Claude (sign-in)",
}

const selectClass =
  "h-8 rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 dark:bg-input/30"

/** Settings → Models: which model runs which job, with plain-English reasons. */
export function ModelsSection() {
  const [info, setInfo] = useState<ModelsInfo | null>(null)
  const [saving, setSaving] = useState<string | null>(null)

  useEffect(() => {
    api.get<ModelsInfo>("/config/models").then(({ data }) => setInfo(data)).catch((e) => toastApiError(e, "Couldn't load models"))
  }, [])

  async function save(key: string, patch: { provider?: string; model?: string }) {
    setSaving(key)
    try {
      const { data } = await api.put<ModelsInfo>("/config/models", { jobs: { [key]: patch } })
      setInfo(data)
    } catch (e) {
      toastApiError(e, "Couldn't save that")
    } finally {
      setSaving(null)
    }
  }

  async function savePrice(input: number, output: number) {
    try {
      const { data } = await api.put<ModelsInfo>("/config/models", { together_price: [input, output] })
      setInfo(data)
      toast.success("Together price saved")
    } catch (e) {
      toastApiError(e, "Couldn't save the price")
    }
  }

  if (!info) return <Skeleton className="h-64 w-full rounded-xl" />
  const agent = info.jobs.filter((j) => j.kind === "agent")
  const fast = info.jobs.filter((j) => j.kind === "fast")

  const agentSelect = (j: JobRow) => (
    <select
      aria-label={`${j.label} model`}
      className={selectClass}
      value={j.model}
      onChange={(e) => void save(j.key, { model: e.target.value === j.default_model ? "" : e.target.value })}
    >
      {[...new Set([j.model, ...info.claude_models])].map((m) => (
        <option key={m} value={m}>
          {m}
          {m === j.default_model ? " (default)" : ""}
        </option>
      ))}
    </select>
  )
  // Lead with the one job everyone cares about; the rest are expert knobs.
  const [primary, ...others] = agent
  const changed = others.filter((j) => j.model !== j.default_model).length

  return (
    <div className="space-y-8">
      <Section
        title="Models"
        description="Archie runs on Claude through your sign-in. Defaults balance quality and cost: Sonnet for most work, Opus only for deep analysis, Haiku for small checks."
      >
        {primary ? (
          <div className="divide-y divide-border rounded-xl border border-border">
            <JobLine job={primary} busy={saving === primary.key}>
              {agentSelect(primary)}
            </JobLine>
          </div>
        ) : null}
      </Section>

      <Disclosure label="Per-job models" hint={`${others.length} jobs${changed ? `, ${changed} changed` : ", all default"}`}>
        <div className="divide-y divide-border rounded-xl border border-border">
          {others.map((j) => (
            <JobLine key={j.key} job={j} busy={saving === j.key}>
              {agentSelect(j)}
            </JobLine>
          ))}
        </div>
      </Disclosure>

      <Disclosure label="Quick one-shot jobs and pricing" hint="defaults are tuned for cost and speed">
      <div className="space-y-8">
      <Section
        title="Quick one-shot jobs"
        description="Single calls that return structured data in a few seconds. If the chosen provider fails, the next configured one takes over."
      >
        <div className="divide-y divide-border rounded-xl border border-border">
          {fast.map((j) => (
            <JobLine key={j.key} job={j} busy={saving === j.key}>
              <select
                aria-label={`${j.label} provider`}
                className={selectClass}
                value={j.provider}
                onChange={(e) => void save(j.key, { provider: e.target.value, model: "" })}
              >
                {(Object.keys(PROVIDER_LABEL) as Provider[]).map((p) => (
                  <option key={p} value={p}>
                    {PROVIDER_LABEL[p]}
                    {info.providers[p] ? "" : " (not set up)"}
                  </option>
                ))}
              </select>
              <ModelInput job={j} onSave={(model) => void save(j.key, { model })} />
            </JobLine>
          ))}
        </div>
      </Section>

      <Section title="Together pricing" description="Used to estimate the cost of Together calls in Usage. Set it to your account's rates.">
        <PriceEditor value={info.prices.together} onSave={savePrice} />
      </Section>
      </div>
      </Disclosure>
    </div>
  )
}

function JobLine({ job, busy, children }: { job: JobRow; busy: boolean; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-1">
        <p className="flex items-center gap-2 text-sm font-medium">
          {job.label}
          {job.customised ? <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">custom</span> : null}
          {busy ? <Loader2 className="size-3 animate-spin text-muted-foreground" /> : null}
        </p>
        <p className="text-sm leading-relaxed text-muted-foreground">{job.explain}</p>
        {job.fallbacks.length ? (
          <p className="text-[11px] text-muted-foreground">Falls back to {job.fallbacks.map((f) => f.split(":")[0]).map((p) => PROVIDER_LABEL[p as Provider] ?? p).join(", then ")}.</p>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">{children}</div>
    </div>
  )
}

function ModelInput({ job, onSave }: { job: JobRow; onSave: (m: string) => void }) {
  const [value, setValue] = useState(job.model)
  useEffect(() => setValue(job.model), [job.model])
  const dirty = value.trim() !== job.model
  return (
    <form
      className="flex items-center gap-1"
      onSubmit={(e) => {
        e.preventDefault()
        onSave(value.trim())
      }}
    >
      <Input aria-label={`${job.label} model`} value={value} onChange={(e) => setValue(e.target.value)} className="h-8 w-64 font-mono text-xs" />
      {dirty ? (
        <Button type="submit" size="sm" variant="outline" className="h-8">
          Save
        </Button>
      ) : job.customised ? (
        <Button type="button" size="icon" variant="ghost" className="size-8" title="Reset to default" onClick={() => onSave("")}>
          <RotateCcw className="size-3.5" />
        </Button>
      ) : null}
    </form>
  )
}

function PriceEditor({ value, onSave }: { value: [number, number]; onSave: (i: number, o: number) => void }) {
  const [input, setInput] = useState(String(value[0]))
  const [output, setOutput] = useState(String(value[1]))
  return (
    <form
      className="flex flex-wrap items-end gap-3 rounded-xl border border-border p-4"
      onSubmit={(e) => {
        e.preventDefault()
        const i = Number(input)
        const o = Number(output)
        if (!Number.isFinite(i) || !Number.isFinite(o) || i < 0 || o < 0) return toast.error("Enter prices in USD per million tokens")
        onSave(i, o)
      }}
    >
      <label className="space-y-1 text-xs text-muted-foreground">
        Input $/M tokens
        <Input value={input} onChange={(e) => setInput(e.target.value)} inputMode="decimal" className="h-8 w-28 font-mono" />
      </label>
      <label className="space-y-1 text-xs text-muted-foreground">
        Output $/M tokens
        <Input value={output} onChange={(e) => setOutput(e.target.value)} inputMode="decimal" className="h-8 w-28 font-mono" />
      </label>
      <Button type="submit" size="sm" variant="outline">
        Save
      </Button>
    </form>
  )
}

const KEY_HELP: Record<KeyField, string> = {
  together_api_key: "Fast model for view building, chat titles, sorting holdings and report write-ups.",
  finnhub_api_key: "Company news and earnings dates.",
  fred_api_key: "Macro data (yields, rates).",
}

/** Settings → Data providers: write-only API keys. The app never shows a saved key. */
export function DataProvidersSection() {
  const [info, setInfo] = useState<ProvidersInfo | null>(null)

  useEffect(() => {
    api.get<ProvidersInfo>("/config/data-providers").then(({ data }) => setInfo(data)).catch((e) => toastApiError(e, "Couldn't load providers"))
  }, [])

  if (!info) return <Skeleton className="h-48 w-full rounded-xl" />
  return (
    <div className="divide-y divide-border rounded-xl border border-border">
      {(Object.keys(KEY_HELP) as KeyField[]).map((field) => (
        <KeyRow key={field} field={field} state={info[field]} onChange={setInfo} />
      ))}
    </div>
  )
}

function KeyRow({
  field,
  state,
  onChange,
}: {
  field: KeyField
  state: ProvidersInfo[KeyField]
  onChange: (p: ProvidersInfo) => void
}) {
  const [value, setValue] = useState("")
  const [busy, setBusy] = useState(false)

  async function put(body: Record<string, unknown>, done: string) {
    setBusy(true)
    try {
      const { data } = await api.put<ProvidersInfo>("/config/data-providers", body)
      onChange(data)
      setValue("")
      toast.success(done)
    } catch (e) {
      toastApiError(e, "Couldn't save the key")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 space-y-1">
        <p className="flex items-center gap-2 text-sm font-medium">
          <KeyRound className="size-3.5 text-muted-foreground" />
          {state.label}
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-normal",
              state.configured ? "bg-positive/10 text-positive" : "bg-muted text-muted-foreground",
            )}
          >
            {state.configured ? <Check className="size-3" /> : null}
            {state.configured ? (state.source === "env" ? "Set in backend/.env" : "Saved") : "Not set"}
          </span>
        </p>
        <p className="text-sm text-muted-foreground">{KEY_HELP[field]}</p>
      </div>
      <form
        className="flex shrink-0 items-center gap-1.5"
        onSubmit={(e) => {
          e.preventDefault()
          if (value.trim()) void put({ [field]: value.trim() }, `${state.label} key saved`)
        }}
      >
        <Input
          type="password"
          autoComplete="off"
          aria-label={`${state.label} API key`}
          placeholder={state.configured ? "••••••••  (replace)" : "Paste API key"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={state.source === "env"}
          className="h-8 w-52"
        />
        <Button type="submit" size="sm" variant="outline" disabled={busy || !value.trim()}>
          Save
        </Button>
        {state.source === "settings" ? (
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void put({ clear: [field] }, `${state.label} key removed`)}>
            Remove
          </Button>
        ) : null}
      </form>
    </div>
  )
}
