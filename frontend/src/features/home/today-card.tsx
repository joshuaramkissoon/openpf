import { useEffect, useState } from "react"
import dayjs from "dayjs"
import calendar from "dayjs/plugin/calendar"
import { CalendarClock, FileText } from "lucide-react"

import { getArtifact, getSchedulerTimeline, listArtifacts } from "@/api/client"
import { Section, SectionLink } from "@/components/kit"
import { ReportReader } from "@/components/rich/report-reader"
import { humanizeTaskName } from "@/components/scheduled-jobs/labels"
import { Skeleton } from "@/components/ui/skeleton"
import { useAppActions } from "@/lib/app-actions"
import type { ArtifactDetail, ArtifactItem, TimelineUpcoming } from "@/types"

dayjs.extend(calendar)

/** First readable paragraph(s) of a markdown report, flattened for an excerpt. */
function excerpt(md: string, max = 320): string {
  const body = md
    .replace(/^---[\s\S]*?---\s*/, "") // front matter
    .split("\n")
    .filter((l) => l.trim() && !/^\s*(#|\||---|```)/.test(l))
    .map((l) => l.replace(/^\s*([-*]|\d+\.)\s+/, ""))
    .join(" ")
    .replace(/\*\*|__|`|\[([^\]]+)\]\([^)]+\)/g, (m, link) => link ?? "")
    .replace(/\s+/g, " ")
    .trim()
  return body.length > max ? `${body.slice(0, max).replace(/\s+\S*$/, "")}…` : body
}

/**
 * "Today": the latest automation output (morning brief, weekly review…) as a
 * short excerpt with "read more", plus when the next routine runs.
 */
export function TodayCard() {
  const { navigate } = useAppActions()
  const [latest, setLatest] = useState<ArtifactItem | null>(null)
  const [detail, setDetail] = useState<ArtifactDetail | null>(null)
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
      if (arts.status === "fulfilled" && arts.value.length) {
        const newest = arts.value.slice().sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
        setLatest(newest)
        try {
          const d = await getArtifact(newest.path)
          if (active) setDetail(d)
        } catch {
          /* excerpt is optional */
        }
      }
      if (active) setLoading(false)
    })()
    return () => {
      active = false
    }
  }, [])

  const nextLine = next ? (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <CalendarClock className="size-3.5" />
      Next: {next.name} · {dayjs(next.next_fire_at).calendar(null, { sameDay: "[today] HH:mm", nextDay: "[tomorrow] HH:mm", nextWeek: "ddd HH:mm", sameElse: "MMM D HH:mm" })}
    </p>
  ) : null

  return (
    <Section
      title="Today"
      action={<SectionLink onClick={() => navigate("/activity/automations")}>Automations →</SectionLink>}
    >
      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : latest ? (
        <div className="space-y-3">
          <div className="space-y-1">
            <p className="text-sm font-medium">{latest.title || latest.task_name || "Latest report"}</p>
            <p className="text-xs text-muted-foreground">
              {latest.task_name && latest.title !== latest.task_name ? `${latest.task_name} · ` : ""}
              {dayjs(latest.created_at).format("ddd D MMM, HH:mm")}
            </p>
          </div>
          {detail ? <p className="text-sm leading-relaxed text-muted-foreground">{excerpt(detail.content)}</p> : null}
          <div className="flex items-center justify-between gap-3">
            {nextLine ?? <span />}
            {detail ? (
              <button type="button" onClick={() => setOpen(true)} className="text-xs text-primary hover:underline">
                Read more →
              </button>
            ) : null}
          </div>
          <ReportReader
            open={open}
            onOpenChange={setOpen}
            title={latest.title || "Report"}
            kind={latest.task_name ? humanizeTaskName(latest.task_name) : latest.type || null}
            date={dayjs(latest.created_at).format("dddd D MMMM, HH:mm")}
            markdown={detail?.content ?? null}
          />
        </div>
      ) : (
        <div className="space-y-2">
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <FileText className="size-4" />
            No briefings yet.
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
