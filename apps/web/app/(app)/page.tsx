"use client";

import { LibraryLockBanner } from "@/components/mg/library-lock";
import {
  Activity, ArrowRight, CalendarDays, Check, ListChecks, OctagonX, SearchX, Zap,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { KpiCard, Poster } from "@/components/mg/domain";
import { OVERVIEW_MAX_AGE_MS, OVERVIEW_REFRESH_MS, OverviewStatus } from "@/components/mg/overview-status";
import { StorageLedgerLine } from "@/components/mg/storage-ledger";
import { Card, LinkButton, PageHeader, Progress, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StaleMark, StateView } from "@/components/mg/states";
import { useEncoder } from "@/components/av1/use-encoder";
import type { CalendarResponse, CoreJob, EncodingJob, LibraryKpis, SystemStatus, WantedKpis, WantedReasons } from "@/lib/api/contracts";
import { useResource, type Resource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { EPISODE_STATE } from "@/lib/status";
import { taskLabel } from "@/lib/tasks";
import { useHealth, type Health } from "@/lib/health";
import { type ToDoRow, useTodoRows } from "@/lib/todo";
import { cn } from "@/lib/utils";
import { codeLabel, instanceLabel, readableRelease } from "@/lib/labels";

interface EncoderStats { totaal: { bespaard_bytes: number; gemiddeld: number; jobs: number }; per_dag: Array<{ dag: string; bespaard_bytes: number }>; wacht_op_activatie?: { jobs: number; besparing_bytes: number } }

const iso = (d: Date) => d.toISOString().slice(0, 10);

export default function OverviewPage() {
  const { t, num, bytes, pct, date, time } = useI18n();
  const core = useResource<SystemStatus>("/status", undefined, OVERVIEW_REFRESH_MS);
  const health = useHealth(OVERVIEW_REFRESH_MS);
  const rows = useTodoRows(OVERVIEW_REFRESH_MS);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 5_000);
    return () => window.clearInterval(timer);
  }, []);
  const lib = useResource<LibraryKpis>("/library/kpis", undefined, OVERVIEW_REFRESH_MS);
  const wanted = useResource<WantedKpis>("/wanted/kpis", undefined, OVERVIEW_REFRESH_MS);
  // dezelfde bron (en laatst bekende waarde) als Ruimte, zodat beide hetzelfde tonen (R13/R14)
  const encRes = useEncoder<EncoderStats>("/statistieken", 60_000);
  useCoreEvents("library", () => void lib.reload());
  useCoreEvents("wanted", () => void wanted.reload());

  const scoped = lib.data?.totals ?? null;
  const monitored = scoped ? scoped.total - scoped.unmonitored - scoped.upcoming : 0;
  const encOk = encRes.data;
  const week = encOk ? encOk.per_dag.slice(-7).reduce((s, d) => s + d.bespaard_bytes, 0) : 0;
  const errors = health.measurement.errors;
  const missingCount = wanted.data?.missing_total ?? wanted.data?.missing;

  const saved = encOk?.totaal.bespaard_bytes ?? 0;
  const expired = (at: number | null, maxAge = OVERVIEW_MAX_AGE_MS) => at !== null && now - at > maxAge;
  const staleBadge = (stale: boolean, at: number | null) => stale && at !== null
    ? <StatusChip tone="stale">{t("overview.readingStale", { when: time(at) })}</StatusChip> : null;
  const errorsStale = health.measurement.stale || expired(health.measurement.at);
  const wantedStale = wanted.stale || expired(wanted.fetchedAt);
  const engineStale = encRes.stale || expired(encRes.fetchedAt, 120_000);
  return (
    <>
      <LibraryLockBanner />
      <PageHeader title={t("overview.title")} sub={t("overview.sub", { date: date(now), version: core.data?.version ?? "—" })} />

      <div className="mb-4 inline-grid w-full items-start gap-4 min-[1280px]:grid-cols-2">
        <OverviewStatus now={now} core={core} health={health} todo={rows} />
        <ToDo rows={rows} />
      </div>

      <div className="two-col-wide">
        <div className="stack" style={{ marginBottom: 16 }}>
          <DownloadEncode now={now} core={core} health={health} />
        </div>
        <div className="stack" style={{ marginBottom: 16 }}>
          <section className="kpi-grid" aria-label={t("overview.kpis")}>
            <KpiCard label={t("overview.kpi.available")} icon={Check} unavailable={!scoped} unknown={!scoped && lib.error ? t("overview.readingUnknown") : null} value={num(scoped?.available)}
              badge={staleBadge(lib.stale || expired(lib.fetchedAt), lib.fetchedAt)}
              meta={scoped ? t("overview.kpi.availableMeta", { pct: monitored ? pct((scoped.available / monitored) * 100, 1) : "—", monitored: num(monitored) }) : null}
              source={t("kpi.source.library")} updatedAt={lib.fetchedAt} href="/bibliotheek" drill={t("nav.library")} />
            <KpiCard label={t("overview.kpi.missing")} icon={SearchX} unavailable={missingCount == null} unknown={!wanted.data && wanted.error ? t("overview.readingUnknown") : null} value={num(missingCount)}
              badge={staleBadge(wantedStale, wanted.fetchedAt)}
              meta={wanted.data ? t("overview.kpi.missingMeta2", { week: num(wanted.data.missing_aired_7d), resolved: num(wanted.data.resolved_7d) }) : null}
              source={t("kpi.source.wanted")} updatedAt={wanted.fetchedAt} href="/ontbrekend" drill={t("nav.wanted")} />
            <KpiCard label={t("overview.kpi.errors")} icon={OctagonX} unavailable={errors === null}
              unknown={errors === null && (health.measurement.failed || health.measurement.at) ? t("overview.readingUnknown") : null} value={num(errors)}
              badge={staleBadge(errorsStale, health.measurement.at) ?? (errors ? <StatusChip tone="danger">{t("status.failed")}</StatusChip> : null)}
              meta={errorsStale ? null : errors ? t("overview.kpi.errorsMetaHealth") : t("overview.kpi.errorsNone")}
              source={t("kpi.source.health")} updatedAt={health.measurement.at} href="/activiteit?tab=problemen" drill={t("overview.kpi.errorsDrill")} />
            <KpiCard label={t("overview.kpi.av1")} icon={Zap} unavailable={!encOk} unknown={!encOk && encRes.error ? t("kpi.engineUnknown") : null}
              badge={staleBadge(engineStale, encRes.fetchedAt)}
              value={encOk ? (saved > 0 ? bytes(saved).split(" ")[0] : "0") : "—"} unit={encOk && saved > 0 ? bytes(saved).split(" ")[1] : undefined}
              meta={encOk ? (encOk.wacht_op_activatie?.jobs
                ? t("av1.kpi.savedPending", { n: num(encOk.wacht_op_activatie.jobs), bytes: bytes(encOk.wacht_op_activatie.besparing_bytes) })
                : saved > 0 ? t("overview.kpi.av1Meta", { pct: `−${num(encOk.totaal.gemiddeld, 1)}%`, week: bytes(week) }) : t("overview.kpi.av1Nothing")) : null}
              spark={!engineStale && saved > 0 ? encOk?.per_dag.slice(-14).map((d) => d.bespaard_bytes) : undefined}
              source={t("kpi.source.engine")} updatedAt={encRes.fetchedAt} href="/av1" drill={t("nav.space")} />
          </section>
          <WantedLine resource={wanted} stale={wantedStale} />
          <StorageLedgerLine />
          <NewThisWeek now={now} />
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Wat actie nodig heeft
function ToDo({ rows }: { rows: ToDoRow[] }) {
  const { t } = useI18n();
  if (!rows.length) return null;
  return (
    <div data-testid="todo-card" id="todo" className="min-w-0 scroll-mt-20">
    <Card title={<>{t("todo.waitingForYou")} <span className="chip">{rows.length}</span></>} icon={ListChecks} flush>
      <div className="list">
        {rows.slice(0, 5).map((r) => (
          <div className="list-row list-row--wrap" key={r.key} data-kind={r.kind}>
            <span className={cn("list-icon", r.kind === "fault" && "list-icon--danger", r.kind === "warning" && "list-icon--warning")}><r.icon className="ico" aria-hidden="true" /></span>
            <div className="list-main"><div className="list-title">{r.title}</div><div className="list-sub whitespace-normal">{r.sub}</div>
              {r.stale ? <StatusChip tone="stale">{t("state.staleShort")}</StatusChip> : null}
            </div>
            <div className="list-aside"><LinkButton href={r.href} size="sm" variant={r.kind === "fault" ? "primary" : "secondary"}>{r.action}</LinkButton></div>
          </div>
        ))}
        {rows.length > 5 ? (
          <div className="list-row"><Link className="list-sub" href="/activiteit?tab=problemen">{t("todo.more", { n: rows.length - 5 })}</Link></div>
        ) : null}
      </div>
    </Card>
    </div>
  );
}

// ------------------------------------------------------------------ Download & encode
const ACTIVE = new Set(["submitted", "queued", "running", "verifying", "awaiting_commit", "awaiting_activation"]);
function DownloadEncode({ now, core, health }: { now: number; core: Resource<SystemStatus>; health: Health }) {
  const { t, num, locale, time } = useI18n();
  const statusKnown = !core.error && core.fetchedAt !== null && now - core.fetchedAt <= OVERVIEW_MAX_AGE_MS;
  const estop = statusKnown ? core.data?.emergency_stop?.active : undefined;
  const jobs = useResource<EncodingJob[]>("/encoding/jobs", undefined, OVERVIEW_REFRESH_MS);
  const running = useResource<CoreJob[]>("/jobs", { status: "running", limit: 5 }, OVERVIEW_REFRESH_MS);
  useCoreEvents("encoding", () => void jobs.reload());
  useCoreEvents("job", () => void running.reload());
  const jobsOld = jobs.stale || (jobs.fetchedAt !== null && now - jobs.fetchedAt > OVERVIEW_MAX_AGE_MS);
  const tasksOld = running.stale || (running.fetchedAt !== null && now - running.fetchedAt > OVERVIEW_MAX_AGE_MS);
  const reading = health.measurement;
  const activityKnown = reading.complete && !reading.stale && reading.at !== null && now - reading.at <= OVERVIEW_MAX_AGE_MS;
  const active = (jobs.data ?? []).filter((job) => ACTIVE.has(job.status));
  const encodes = active.filter((job) => job.status === "running" || job.status === "verifying");
  const queued = active.length - encodes.length;
  const shown = 8;
  const parts = activityKnown ? [
    health.activeParts.downloads ? t("health.part.downloads", { count: num(health.activeParts.downloads) }) : null,
    health.activeParts.searches ? t("health.part.searches", { count: num(health.activeParts.searches) }) : null,
  ].filter(Boolean) : [];
  const isIdle = jobs.data !== null && !jobsOld && !tasksOld && activityKnown
    && !active.length && running.data?.length === 0 && reading.active === 0;
  return (
    <Card className="span-5" title={t("overview.nowBusy")} icon={Activity} flush
      actions={<Link className="link-more" href="/activiteit">{t("nav.activity")} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>}>
      <div className="list">
        <ResourceView resource={jobs} endpoint="GET /api/v1/encoding/jobs" compact>
          {() => (
            <>
              {jobsOld && !jobs.stale ? <div className="px-4 pb-2"><StaleMark>{t("overview.readingStale", { when: time(jobs.fetchedAt) })}</StaleMark></div> : null}
              {encodes.slice(0, shown).map((job) => {
                const title = readableRelease(job.source_path);
                const progress = !jobsOld && estop === false && Number.isFinite(job.progress)
                  && job.progress >= 0 && job.progress <= 1 ? job.progress * 100 : null;
                return (
                  <div className="list-row list-row--wrap" key={job.id}>
                    <span className="list-icon list-icon--accent"><Zap className="ico" aria-hidden="true" /></span>
                    <div className="list-main">
                      <div className="list-title">{title}</div>
                      {progress !== null ? <div className="mt-1.5"><Progress value={progress} thin label={title} /></div> : null}
                    </div>
                    {estop ? <StatusChip tone="paused">{t("estop.pausedJob")}</StatusChip>
                      : <div className="list-aside num t-2 text-sm">{progress === null ? t("overview.progressUnknown")
                        : `${num(progress)}% · ${codeLabel(job.status, locale)}${job.trial_only ? ` · ${t("overview.trial")}` : ""}`}</div>}
                  </div>
                );
              })}
              {!jobsOld && encodes.length > shown ? (
                <Link className="list-row link-more" href="/av1/wachtrij?status=bezig">{t("overview.moreEncodes", { count: num(encodes.length - shown) })}</Link>
              ) : null}
              {!jobsOld && queued > 0 ? (
                <Link className="list-row link-more" href="/av1/wachtrij?status=wacht" data-testid="queued-encodes">{t("overview.queuedEncodes", { count: num(queued) })}</Link>
              ) : null}
            </>
          )}
        </ResourceView>
        <ResourceView resource={running} endpoint="GET /api/v1/jobs" compact>
          {(tasks) => tasks.slice(0, 5).map((job) => (
            <div className="list-row" key={job.id}>
              <span className="list-icon"><Activity className="ico" aria-hidden="true" /></span>
              <div className="list-main">
                <div className="list-title">{taskLabel(t, job.job_type)}</div>
                <div className="list-sub">{job.instance_id ? instanceLabel(job.instance_id, null, locale) : ""}</div>
              </div>
              {tasksOld ? <StatusChip tone="stale">{t("overview.readingStale", { when: time(running.fetchedAt) })}</StatusChip>
                : <StatusChip tone="accent" pending>{t("status.running")}</StatusChip>}
            </div>
          ))}
        </ResourceView>
        {parts.length ? (
          <Link className="list-row" href="/activiteit" data-testid="busy-parts">
            <span className="list-icon"><Activity className="ico" aria-hidden="true" /></span>
            <div className="list-main"><div className="list-title">{t("overview.busyParts", { parts: parts.join(" · ") })}</div></div>
          </Link>
        ) : null}
        {!activityKnown && (reading.at !== null || reading.failed) ? <div className="px-4 py-3 text-sm text-muted-foreground">{t("overview.activityUnknown")}</div> : null}
        {isIdle ? <StateView kind="empty" compact title={t("overview.noActiveJobs")} body={t("overview.noActiveJobsBody")} /> : null}
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ New this week
/** Eén korte regel over ontbrekend: hoeveel, wat jou iets vraagt, wat nog niet uit is. */
function WantedLine({ resource: k, stale }: { resource: Resource<WantedKpis>; stale: boolean }) {
  const { t, num, time } = useI18n();
  const reasons = useResource<WantedReasons>("/wanted/reasons", undefined, OVERVIEW_REFRESH_MS);
  if (!k.data) return null;
  const needs = k.data.needs_user ? k.data.needs_user.reasons.filter((r) => r.total > 0).length
    : Object.entries(k.data.reason_distribution ?? {}).filter(([code, n]) => n > 0 && !reasons.stale && reasons.data?.[code]?.needs_user).length;
  const parts = [t("overview.wantedLine", { missing: num(k.data.missing_total ?? k.data.missing) }), needs ? t("overview.wantedNeeds", { count: num(needs) }) : null,
    k.data.upcoming?.total ? t("overview.wantedUpcoming", { count: num(k.data.upcoming.total) }) : null].filter(Boolean);
  return (
    <p className="t-2" style={{ fontSize: 14, margin: "0 0 16px" }} data-testid="overview-wanted-line">
      <Link className="link-more" href="/ontbrekend">{parts.join(" · ")}</Link>
      {stale ? <span className="ml-2"><StaleMark>{t("overview.readingStale", { when: time(k.fetchedAt) })}</StaleMark></span> : null}
    </p>
  );
}

function NewThisWeek({ now }: { now: number }) {
  const { t, date } = useI18n();
  const start = new Date(now);
  const end = new Date(now);
  start.setDate(start.getDate() - 1);
  end.setDate(end.getDate() + 7);
  const range = { start: iso(start), end: iso(end) };
  const cal = useResource<CalendarResponse>("/calendar", range, OVERVIEW_REFRESH_MS);
  return (
    <Card className="span-4" title={t("overview.newThisWeek")} icon={CalendarDays} flush actions={<Link className="link-more" href="/kalender">{t("nav.calendar")}</Link>}>
      <ResourceView resource={cal} endpoint="GET /api/v1/calendar" compact isEmpty={(r) => r.items.length === 0} empty={<StateView kind="empty" compact title={t("overview.noNew")} />}>
        {(r) => (
          <div className="list">
            {r.items.slice(0, 5).map((e, i) => {
              const view = EPISODE_STATE[e.state] ?? EPISODE_STATE.missing;
              return (
                <Link className="list-row" key={`${e.lib_item_id}-${i}`} href={`/bibliotheek/${e.instance_id}/${e.lib_item_id}`}>
                  <Poster title={e.title} />
                  <div className="list-main"><div className="list-title">{e.title}</div><div className="list-sub">{e.type === "episode" ? `S${String(e.season_number).padStart(2, "0")}E${String(e.episode_number).padStart(2, "0")}` : e.release_label_nl} · {date(e.date)}</div></div>
                  <StatusChip tone={view.tone} icon={view.icon}>{t(view.label)}</StatusChip>
                </Link>
              );
            })}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
