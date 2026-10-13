import { useEffect, useMemo, useState } from "react"
import dayjs from "dayjs"
import calendar from "dayjs/plugin/calendar"
import { ArrowRight, CalendarClock, Check, FileText } from "lucide-react"

import { getArtifact, getSchedulerTimeline, listArtifacts } from "@/api/client"
import { Section, SectionLink } from "@/components/kit"
import { Inline, RichProvider } from "@/components/rich/inline"
import { ReportReader } from "@/components/rich/report-reader"
import { reportTypeLabel } from "@/components/scheduled-jobs/labels"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useAppActions } from "@/lib/app-actions"
import { cn } from "@/lib/utils"
import type { ArtifactDetail, ArtifactItem, ReportSummary, TimelineUpcoming } from "@/types"

import { ago } from "./decision-rows"

dayjs.extend(calendar)

/** Reports older than this don't belong on "Today". */
const MAX_AGE_DAYS = 7

/** The newest report worth showing: `signal: none` never appears on Home. */
function pickReport(items: ArtifactItem[]): ArtifactItem | null {
  const cutoff = dayjs().subtract(MAX_AGE_DAYS, "day")
  return (
    items
      .slice()
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .find((a) => a.summary?.signal !== "none" && Boolean(a.summary?.headline) && dayjs(a.created_at).isAfter(cutoff)) ?? null
  )
}

type ActionTarget = { label: string; run: () => void }

/** A summary's `action` → one button: a proposal / play / page link, or "ask Archie". */
function useAction(summary: ReportSummary | null | undefined, kind: string): ActionTarget | null {
  const { navigate, askArchie } = useAppActions()
  return useMemo(() => {
    const action = summary?.action?.trim()
    if (!action) return null
    const link = action.match(/\[([^\]]+)\]\(([^)\s]+)\)/)
    if (link) {
      const [, label, href] = link
      if (href.startsWith("proposal:")) return { label, run: () => navigate("/activity/proposals") }
      if (href.startsWith("#/")) return { label, run: () => navigate(href.slice(1)) }
      if (href.startsWith("/")) return { label, run: () => navigate(href) }
      if (/^https?:/.test(href)) return { label, run: () => window.open(href, "_blank", "noreferrer") }
    }
    const plain = action.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/[*_`]/g, "")
    return { label: plain, run: () => askArchie(`From the ${kind.toLowerCase()}: "${plain}". Help me do this.`) }
  }, [summary, kind, navigate, askArchie])
}

/**
 * "Today": the latest report as a high-signal digest from its summary block:
 * type + when, the headline, up to three points and one action. Low-signal
 * reports collapse to a quiet line; `signal: none` stays off Home entirely.
 * The full text lives in the reader.
 */
export function TodayCard() {
  const { navigate } = useAppActions()
  const [latest, setLatest] = useState<ArtifactItem | null>(null)
  const [quietCount, setQuietCount] = useState(0)
  const [detail, setDetail] = useState<ArtifactDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [next, setNext] = useState<TimelineUpcoming | null>(null)
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let active = true
    ;(async () => {
      const [arts, timeline] = await Promise.allSettled([listArtifacts(), getSchedulerTimeline()])
      if (!active) return
      if (timeline.status === "fulfilled") {
        const up = timeline.value.upcoming.slice().sort((a, b) => a.next_fire_at.localeCompare(b.next_fire_at))
        setNext(up[0] ?? null)
      }
      if (arts.status === "fulfilled") {
        setLatest(pickReport(arts.value))
        const since = dayjs().subtract(1, "day")
        setQuietCount(arts.value.filter((a) => a.summary?.signal === "none" && dayjs(a.created_at).isAfter(since)).length)
      }
      setLoading(false)
    })()
    return () => {
      active = false
    }
  }, [])

  // The full report loads only when the reader opens.
  useEffect(() => {
    if (!open || !latest || detail?.path === latest.path) return
    let active = true
    setDetailError(null)
    getArtifact(latest.path)
      .then((d) => active && setDetail(d))
      .catch((e) => active && setDetailError(e instanceof Error ? e.message : "Couldn't load the report"))
    return () => {
      active = false
    }
  }, [open, latest, detail?.path])

  const summary = latest?.summary ?? null
  const kind = reportTypeLabel(latest?.task_name, latest?.type === "chat" ? "Chat report" : "Report")
  const action = useAction(summary, kind)
  const quiet = summary?.signal === "low"

  const nextLine = next ? (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <CalendarClock className="size-3.5" />
      Next: {reportTypeLabel(next.name)} ·{" "}
      {dayjs(next.next_fire_at).calendar(null, { sameDay: "[today] HH:mm", nextDay: "[tomorrow] HH:mm", nextWeek: "ddd HH:mm", sameElse: "MMM D HH:mm" })}
    </p>
  ) : null

  const reader = latest ? (
    <ReportReader
      open={open}
      onOpenChange={setOpen}
      title={latest.title || kind}
      kind={kind}
      date={dayjs(latest.created_at).format("dddd D MMMM, HH:mm")}
      markdown={detail?.path === latest.path ? detail.content : null}
      summary={summary}
      loading={open && !detailError && detail?.path !== latest.path}
      error={detailError}
    />
  ) : null

  return (
    <Section
      title="Today"
      action={<SectionLink onClick={() => navigate("/activity/automations")}>All reports →</SectionLink>}
    >
      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-3 w-1/3" />
          <Skeleton className="h-5 w-4/5" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ) : latest && summary && quiet ? (
        // Low signal: one quiet line.
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="group flex w-full items-center gap-2 text-left text-sm text-muted-foreground hover:text-foreground"
          >
            <Check className="size-3.5 text-positive" />
            <span className="min-w-0 truncate">
              <span className="text-foreground/80">{kind}</span> — nothing needs you
            </span>
            <span className="ml-auto shrink-0 text-xs">{ago(latest.created_at)} ago</span>
          </button>
          {nextLine}
          {reader}
        </div>
      ) : latest && summary ? (
        <div className="space-y-3">
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {summary.signal === "high" ? <span className="size-1.5 rounded-full bg-primary" aria-label="Worth a look" /> : null}
            <span className="font-medium text-foreground/80">{kind}</span>
            <span>· {ago(latest.created_at)} ago</span>
          </p>
          <RichProvider>
            <p className="text-[15px] font-medium leading-snug tracking-tight text-foreground">
              <Inline text={summary.headline} />
            </p>
            {summary.points.length ? (
              <ul className="space-y-1">
                {summary.points.slice(0, 3).map((p, i) => (
                  <li key={i} className="flex gap-2 text-sm leading-relaxed text-muted-foreground">
                    <span className="mt-[9px] size-1 shrink-0 rounded-full bg-muted-foreground/60" />
                    <span className="min-w-0">
                      <Inline text={p} />
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </RichProvider>
          <div className="flex flex-wrap items-center gap-2 pt-0.5">
            {action ? (
              <Button size="sm" className="h-7 max-w-full gap-1.5 px-3 text-xs" onClick={action.run}>
                <span className="truncate">{action.label}</span>
                <ArrowRight className="size-3.5 shrink-0" />
              </Button>
            ) : null}
            <Button
              size="sm"
              variant={action ? "ghost" : "outline"}
              className={cn("h-7 px-2.5 text-xs", action && "text-muted-foreground")}
              onClick={() => setOpen(true)}
            >
              Read report
            </Button>
          </div>
          {nextLine}
          {reader}
        </div>
      ) : (
        <div className="space-y-2">
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            {quietCount ? <Check className="size-4 text-positive" /> : <FileText className="size-4" />}
            {quietCount ? "Nothing in today's reports needs you." : "No briefings yet."}
          </p>
          {nextLine ?? (
            <p className="text-xs text-muted-foreground">
              Turn on the morning brief in{" "}
              <button type="button" className="text-primary hover:underline" onClick={() => navigate("/activity/automations")}>
                Automations
              </button>{" "}
              and Archie will summarise your book here each day.
            </p>
          )}
        </div>
      )}
    </Section>
  )
}
