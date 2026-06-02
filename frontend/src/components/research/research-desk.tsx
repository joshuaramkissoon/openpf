import { useEffect, useRef, useState } from "react"
import { FileText, FlaskConical, Loader2 } from "lucide-react"

import { SectionCard } from "@/components/kit"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { RichMarkdown } from "@/components/RichMarkdown"
import { runResearch, type ResearchRunResult } from "@/api/client"

const VERDICT_STYLES: Record<string, string> = {
  support: "border-positive/30 bg-positive/10 text-positive",
  refute: "border-negative/30 bg-negative/10 text-negative",
  mixed: "border-warning/30 bg-warning/10 text-warning",
}

function parseApiError(error: unknown): string {
  const candidate = error as { response?: { data?: { detail?: string } } }
  return candidate?.response?.data?.detail || (error instanceof Error ? error.message : "Analysis failed")
}

export function ResearchDesk({
  accountView,
  onError,
  seedSubject = null,
  onSeedConsumed,
}: {
  accountView: "all" | "invest" | "stocks_isa"
  onError: (msg: string | null) => void
  /** When set, pre-fills the analysis subject (e.g. from an Attention action). One-shot. */
  seedSubject?: string | null
  onSeedConsumed?: () => void
}) {
  const [subject, setSubject] = useState("")
  const [objective, setObjective] = useState("")
  const [hypothesis, setHypothesis] = useState("")
  const [horizon, setHorizon] = useState(30)
  const [createThesis, setCreateThesis] = useState(false)
  const [running, setRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [result, setResult] = useState<ResearchRunResult | null>(null)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    if (running) {
      setElapsed(0)
      timerRef.current = window.setInterval(() => setElapsed((e) => e + 1), 1000)
    } else if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [running])

  // Pre-fill the form when an Attention action deep-links a ticker here. Sets a
  // ready-to-run objective if one isn't already typed, then clears the seed.
  useEffect(() => {
    if (!seedSubject) return
    setSubject(seedSubject)
    setObjective((prev) => prev || `Quick read on ${seedSubject}: current setup, key risks, and whether I should act.`)
    onSeedConsumed?.()
  }, [seedSubject, onSeedConsumed])

  async function handleRun() {
    if (!objective.trim() || running) return
    setRunning(true)
    setResult(null)
    onError(null)
    try {
      const res = await runResearch({
        objective: objective.trim(),
        subject: subject.trim(),
        hypothesis: hypothesis.trim(),
        horizon_days: horizon,
        account_kind: accountView,
        create_thesis: createThesis,
      })
      setResult(res)
    } catch (err) {
      onError(parseApiError(err))
    } finally {
      setRunning(false)
    }
  }

  const verdict = (result?.verdict || "").toLowerCase()

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
      <div className="min-w-0 lg:col-span-2">
        <SectionCard
          title="Analysis Request"
          description="Archie runs the quant + research subagents over live data and a Kronos forecast, then returns an evidence-backed verdict."
        >
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="rd-subject">
                Subject <span className="font-normal text-muted-foreground">(ticker — optional)</span>
              </Label>
              <Input
                id="rd-subject"
                placeholder="e.g. NVDA — leave blank for a general question"
                value={subject}
                onChange={(e) => setSubject(e.target.value.toUpperCase())}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="rd-objective">Objective</Label>
              <Textarea
                id="rd-objective"
                rows={3}
                placeholder="e.g. Is this a fair entry on a 2-week horizon? Should I trim my position?"
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="rd-hypothesis">
                Hypothesis <span className="font-normal text-muted-foreground">(optional)</span>
              </Label>
              <Textarea
                id="rd-hypothesis"
                rows={2}
                placeholder="e.g. Oversold bounce after the -8% week."
                value={hypothesis}
                onChange={(e) => setHypothesis(e.target.value)}
              />
            </div>
            <div className="flex flex-wrap items-end gap-x-5 gap-y-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="rd-horizon">Horizon (days)</Label>
                <Input
                  id="rd-horizon"
                  type="number"
                  min={1}
                  max={365}
                  className="w-28"
                  value={horizon}
                  onChange={(e) => setHorizon(Math.max(1, Math.min(365, Number(e.target.value) || 30)))}
                />
              </div>
              <div className="flex items-center gap-2 pb-2.5">
                <Switch id="rd-thesis" checked={createThesis} onCheckedChange={setCreateThesis} />
                <Label htmlFor="rd-thesis" className="text-sm font-normal text-muted-foreground">
                  Save as thesis if supported
                </Label>
              </div>
            </div>
            <Button onClick={() => void handleRun()} disabled={running || !objective.trim()} className="mt-1">
              {running ? <Loader2 className="size-4 animate-spin" /> : <FlaskConical className="size-4" />}
              {running ? `Analyzing… ${elapsed}s` : "Run analysis"}
            </Button>
            {running ? (
              <p className="text-xs text-muted-foreground">
                Archie is gathering live data, forecasting, and weighing evidence. This usually takes 1–2 minutes.
              </p>
            ) : null}
          </div>
        </SectionCard>
      </div>

      <div className="min-w-0 lg:col-span-3">
        {result ? (
          <SectionCard title="Verdict" description={subject ? `${subject} · ${horizon}d horizon` : `${horizon}d horizon`}>
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-center gap-2.5">
                {verdict ? (
                  <Badge variant="outline" className={`font-medium capitalize ${VERDICT_STYLES[verdict] ?? ""}`}>
                    {verdict}
                  </Badge>
                ) : null}
                {result.confidence != null ? (
                  <span className="font-mono text-xs tabular-nums text-muted-foreground">
                    confidence {(result.confidence * 100).toFixed(0)}%
                  </span>
                ) : null}
                {result.thesis_id ? (
                  <Badge variant="outline" className="text-muted-foreground">
                    saved as thesis
                  </Badge>
                ) : null}
              </div>
              {result.summary ? <p className="text-sm font-medium leading-relaxed">{result.summary}</p> : null}
              {result.suggested_action ? (
                <div className="rounded-lg border border-border/60 bg-muted/20 px-3.5 py-2.5">
                  <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Suggested action</p>
                  <p className="mt-1 text-sm">{result.suggested_action}</p>
                </div>
              ) : null}
              {result.invalidation ? (
                <div className="rounded-lg border border-border/60 bg-muted/20 px-3.5 py-2.5">
                  <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Invalidation</p>
                  <p className="mt-1 text-sm">{result.invalidation}</p>
                </div>
              ) : null}
              {result.markdown ? (
                <div className="border-t border-border/50 pt-3">
                  <RichMarkdown markdown={result.markdown} />
                </div>
              ) : null}
              {result.artifact_path ? (
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <FileText className="size-3.5" /> saved to <span className="font-mono">{result.artifact_path}</span>
                </p>
              ) : null}
            </div>
          </SectionCard>
        ) : (
          <SectionCard title="Verdict" description="Run an analysis to see Archie's evidence-backed verdict here.">
            <div className="flex h-64 items-center justify-center px-6 text-center text-sm text-muted-foreground">
              {running ? (
                <span className="flex items-center gap-2">
                  <Loader2 className="size-4 animate-spin" /> Archie is working… {elapsed}s
                </span>
              ) : (
                <span>
                  Fill the request on the left and run an analysis. Archie will pull live prices, technicals, risk
                  metrics, a Kronos forecast, and news — then weigh the evidence.
                </span>
              )}
            </div>
          </SectionCard>
        )}
      </div>
    </div>
  )
}
