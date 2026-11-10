"use client";

import { LibraryLockBanner } from "@/components/mg/library-lock";
import {
  Activity, ArrowRight, CalendarDays, Check, CircleCheck, CirclePause, CircleHelp, ListChecks, OctagonX, SearchX, TriangleAlert, Zap,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { KpiCard, Poster } from "@/components/mg/domain";
import { StorageLedgerLine } from "@/components/mg/storage-ledger";
import { Card, LinkButton, PageHeader, Progress, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useShell } from "@/components/shell/app-shell";
import { useEncoder } from "@/components/av1/use-encoder";
import type { CalendarResponse, CoreJob, EncodingJob, LibraryKpis, WantedKpis, WantedReasons } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { EPISODE_STATE } from "@/lib/status";
import { taskLabel } from "@/lib/tasks";
import { useHealth } from "@/lib/health";
import { totalStatus, useTodoRows } from "@/lib/todo";
import { cn } from "@/lib/utils";
import { codeLabel, instanceLabel, readableRelease } from "@/lib/labels";

interface EncoderStats { totaal: { bespaard_bytes: number; gemiddeld: number; jobs: number }; per_dag: Array<{ dag: string; bespaard_bytes: number }>; wacht_op_activatie?: { jobs: number; besparing_bytes: number } }

const iso = (d: Date) => d.toISOString().slice(0, 10);

export default function OverviewPage() {
  const { t, num, bytes, pct, date, relative } = useI18n();
  const { status } = useShell();
  const health = useHealth();
  const [today] = useState(() => Date.now());
  const lib = useResource<LibraryKpis>("/library/kpis");
  const wanted = useResource<WantedKpis>("/wanted/kpis");
  // dezelfde bron (en laatst bekende waarde) als Ruimte, zodat beide hetzelfde tonen (R13/R14)
  const encRes = useEncoder<EncoderStats>("/statistieken", 60_000);
  const enc: EncoderStats | null | "offline" = encRes.data ?? (encRes.error ? "offline" : null);
  useCoreEvents("library", () => void lib.reload());
  useCoreEvents("wanted", () => void wanted.reload());

  const scoped = lib.data?.totals ?? null;
  const monitored = scoped ? scoped.total - scoped.unmonitored - scoped.upcoming : 0;
  // alleen recente problemen tellen (core levert failed_recent; oudere versies alleen failed)
  const failedJobs = status ? (status.jobs.failed_recent ?? status.jobs.failed ?? 0) : 0;
  const encOk = enc && enc !== "offline" ? enc : null;
  const week = encOk ? encOk.per_dag.slice(-7).reduce((s, d) => s + d.bespaard_bytes, 0) : 0;
  // één bron (B2): het gezondheidsmodel telt ook mislukte AV1-plaatsingen en vrijgavecontroles mee
  // Fouten = precies wat het gezondheidsmodel telt (N13); importfouten horen bij Ontbrekend
  const errors = health.loaded ? health.errors : failedJobs;
  // Ontbrekend: hetzelfde getal als de pagina Ontbrekend (wanted/kpis), anders bibliotheek
  const missingCount = wanted.data ? ((wanted.data as { missing_total?: number }).missing_total ?? wanted.data.missing) : scoped?.missing;

  const saved = encOk?.totaal.bespaard_bytes ?? 0;
  // Peil §4.4 / §9 (offline): antwoordt de core niet en is er nog nooit een waarde geweest, dan is de kaart Onbekend met
  // de reden, geen skelet of "Laden…" dat nooit eindigt.
  const coreDown = health.level === "offline" ? t("health.offline") : null;
  return (
    <>
      <LibraryLockBanner />
      <PageHeader title={t("overview.title")} sub={t("overview.sub", { date: date(today), version: status?.version ?? "—" })} />

      <StatusHero />

      <div className="two-col-wide">
        <div className="stack" style={{ marginBottom: 16 }}>
          <ToDo />
          <DownloadEncode />
        </div>
        <div className="stack" style={{ marginBottom: 16 }}>
          <section className="kpi-grid" aria-label={t("overview.kpis")}>
            <KpiCard label={t("overview.kpi.available")} icon={Check} unavailable={!scoped} unknown={!scoped ? coreDown : null} value={num(scoped?.available)}
              meta={scoped ? t("overview.kpi.availableMeta", { pct: monitored ? pct((scoped.available / monitored) * 100, 1) : "—", monitored: num(monitored) }) : null}
              source={t("kpi.source.library")} updatedAt={lib.fetchedAt} href="/bibliotheek" drill={t("nav.library")} />
            <KpiCard label={t("overview.kpi.missing")} icon={SearchX} unavailable={!scoped && !wanted.data} unknown={!scoped && !wanted.data ? coreDown : null} value={num(missingCount)}
              meta={wanted.data ? t("overview.kpi.missingMeta2", { week: num(wanted.data.missing_aired_7d), resolved: num(wanted.data.resolved_7d) }) : null}
              source={t("kpi.source.wanted")} updatedAt={wanted.fetchedAt ?? lib.fetchedAt} href="/ontbrekend" drill={t("nav.wanted")} />
            <KpiCard label={t("overview.kpi.errors")} icon={OctagonX} unavailable={!health.loaded} unknown={!health.loaded ? coreDown : null} value={num(errors)}
              badge={errors ? <StatusChip tone="danger">{t("status.failed")}</StatusChip> : null}
              meta={errors ? t("overview.kpi.errorsMetaHealth") : t("overview.kpi.errorsNone")}
              source={t("kpi.source.health")} href="/activiteit?tab=problemen" drill={t("overview.kpi.errorsDrill")} />
            <KpiCard label={t("overview.kpi.av1")} icon={Zap} unavailable={!encOk && enc !== "offline"} unknown={enc === "offline" && !encOk ? t("kpi.engineUnknown") : null}
              staleNote={encRes.stale && encRes.fetchedAt ? t("state.updatedAgo", { when: relative(encRes.fetchedAt) }) : null}
              value={encOk ? (saved > 0 ? bytes(saved).split(" ")[0] : "0") : "—"} unit={encOk && saved > 0 ? bytes(saved).split(" ")[1] : undefined}
              meta={encOk ? (encOk.wacht_op_activatie?.jobs
                ? t("av1.kpi.savedPending", { n: num(encOk.wacht_op_activatie.jobs), bytes: bytes(encOk.wacht_op_activatie.besparing_bytes) })
                : saved > 0 ? t("overview.kpi.av1Meta", { pct: `−${num(encOk.totaal.gemiddeld, 1)}%`, week: bytes(week) }) : t("overview.kpi.av1Nothing")) : null}
              spark={saved > 0 ? encOk?.per_dag.slice(-14).map((d) => d.bespaard_bytes) : undefined}
              source={t("kpi.source.engine")} updatedAt={encRes.fetchedAt} href="/av1" drill={t("nav.space")} />
          </section>
          <WantedLine />
          <StorageLedgerLine />
          <NewThisWeek />
        </div>
      </div>
    </>
  );
}

/**
 * SysteemStatusPaneel (Peil, PEIL_MIGRATIE §5.1/§6.6): de totaalstatus staat los van "Wat actie nodig heeft".
 * Totaal = het slechtste dat GEMETEN is: core onbereikbaar → Onbekend; een storing of waarschuwing in de lijst of fouten
 * in het gezondheidsmodel → Beperkt; anders Alles gezond. Inrichtingstaken en info maken de status nooit rood of
 * amber; ze staan alleen in de lijst (met hun aantal). Zelfde telling als kopbalk en zijbalk (N13).
 */
function StatusHero() {
  const { t, num } = useI18n();
  const h = useHealth();
  const todo = useTodoRows();
  if (!h.loaded) return null;
  const count = todo.length;
  const worst = totalStatus(h, todo);
  const word = worst === "unknown" ? t("health.offline") : worst === "warning" ? t("status.degraded") : t("health.allGood");
  const Icon = worst === "unknown" ? CircleHelp : worst === "warning" ? TriangleAlert : count ? ListChecks : CircleCheck;
  return (
    <div className={cn("status-hero mb-4 flex flex-wrap items-center gap-x-3.5 gap-y-3 px-5 py-4", `status-hero--${worst}`)} data-testid="status-hero" role="status" data-status={worst === "warning" ? "degraded" : worst}>
      <Icon className="ico" aria-hidden="true" />
      <div className="min-w-0 flex-[1_1_16rem]">
        <div className="status-hero-title">{count ? t("hero.waiting", { count: num(count) }) : t("hero.ok")}</div>
        {/* noodstop: niets is 'bezig'; lopende encodes staan bevroren (punt 39) */}
        <div className="status-hero-sub" data-testid="hero-sub">{h.paused.estop ? (h.paused.encodes ? t("hero.estopPaused", { count: num(h.paused.encodes) }) : t("hero.estop"))
          : h.active ? t("hero.busy", { count: num(h.active) }) : t("hero.quiet")}</div>
      </div>
      <span className="status-hero-total">
        <span className="sr-only">{t("hero.total")}: </span>
        <StatusChip tone={worst} size="lg" icon={h.paused.estop && worst === "healthy" ? CirclePause : undefined}>{word}</StatusChip>
      </span>
      {count ? <a className="btn" href="#todo">{t("hero.toList")}</a> : null}
    </div>
  );
}

// ------------------------------------------------------------------ Wat actie nodig heeft
function ToDo() {
  const { t } = useI18n();
  const rows = useTodoRows();
  if (!rows.length) return null;
  return (
    <div data-testid="todo-card" id="todo" style={{ marginBottom: 16, scrollMarginTop: 80 }}>
    <Card title={<>{t("todo.waitingForYou")} <span className="chip">{rows.length}</span></>} icon={ListChecks} flush>
      <div className="list">
        {rows.slice(0, 5).map((r) => (
          <div className="list-row list-row--wrap" key={r.key} data-kind={r.kind}>
            <span className={cn("list-icon", r.kind === "fault" && "list-icon--danger", r.kind === "warning" && "list-icon--warning")}><r.icon className="ico" aria-hidden="true" /></span>
            <div className="list-main"><div className="list-title">{r.title}</div><div className="list-sub" style={{ whiteSpace: "normal" }}>{r.sub}</div></div>
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
function DownloadEncode() {
  const { t, num, locale } = useI18n();
  const health = useHealth();
  const estop = health.paused.estop;
  const jobs = useResource<EncodingJob[]>("/encoding/jobs", undefined, 30_000, { liveSlowdown: true });
  const running = useResource<CoreJob[]>("/jobs", { status: "running", limit: 5 });
  useCoreEvents("encoding", () => void jobs.reload());
  useCoreEvents("job", () => void running.reload());
  // eerst wat echt draait (CPU én GPU), daarna wat wacht; alles wat bezig is wordt getoond (N13)
  const rank = (s: string) => (s === "running" || s === "verifying" ? 0 : 1);
  const active = (jobs.data ?? []).filter((j) => ACTIVE.has(j.status)).sort((a, b) => rank(a.status) - rank(b.status));
  const runningEnc = active.filter((j) => rank(j.status) === 0).length;
  // 'Nu bezig' = wat echt draait; wachtende encodes worden één regel (S11)
  const queuedEnc = active.length - runningEnc;
  const shown = Math.min(8, runningEnc);
  return (
    <Card className="span-5" title={t("overview.nowBusy")} icon={Activity} flush
      actions={<Link className="link-more" href="/activiteit">{t("nav.activity")} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>}>
      <ResourceView resource={jobs} endpoint="GET /api/v1/encoding/jobs" compact isEmpty={() => active.length === 0 && !(running.data?.length) && !health.active} empty={<StateView kind="empty" compact title={t("overview.noActiveJobs")} body={t("overview.noActiveJobsBody")} />}>
        {() => (
          <div className="list">
            {active.filter((j) => rank(j.status) === 0).slice(0, shown).map((j) => (
              <div className="list-row" key={`e-${j.id}`}>
                <span className="list-icon list-icon--accent"><Zap className="ico" aria-hidden="true" /></span>
                <div className="list-main">
                  <div className="list-title">{readableRelease(j.source_path)}</div>
                  <div style={{ marginTop: 6 }}><Progress value={(j.progress ?? 0) * 100} thin label={j.source_path} /></div>
                </div>
                {estop
                  ? <StatusChip tone="paused">{t("estop.pausedJob")}</StatusChip>
                  : <div className="list-aside num t-2" style={{ fontSize: 14 }}>{num((j.progress ?? 0) * 100)}% · {codeLabel(j.status, locale)}{j.trial_only ? ` · ${t("overview.trial")}` : ""}</div>}
              </div>
            ))}
            {(running.data ?? []).slice(0, 5).map((j) => (
              <div className="list-row" key={`c-${j.id}`}>
                <span className="list-icon"><Activity className="ico" aria-hidden="true" /></span>
                <div className="list-main"><div className="list-title">{taskLabel(t, j.job_type)}</div><div className="list-sub">{j.instance_id ? instanceLabel(j.instance_id, null, locale) : ""}</div></div>
                <StatusChip tone="accent" pending>{t("status.running")}</StatusChip>
              </div>
            ))}
            {/* wat het gezondheidsmodel als 'bezig' telt maar hierboven niet staat (downloads,
                zoekacties): zelfde getal als de statusregel (R13) */}
            {(() => {
              const p = health.activeParts;
              const parts = [
                p.downloads ? t("health.part.downloads", { count: num(p.downloads) }) : null,
                p.searches ? t("health.part.searches", { count: num(p.searches) }) : null,
                p.tasks && !(running.data?.length) ? t("health.part.tasks", { count: num(p.tasks) }) : null,
                p.encodes && !active.length ? t("health.part.encodes", { count: num(p.encodes) }) : null,
              ].filter(Boolean);
              return parts.length ? (
                <Link className="list-row" href="/activiteit" data-testid="busy-parts">
                  <span className="list-icon"><Activity className="ico" aria-hidden="true" /></span>
                  <div className="list-main"><div className="list-title">{t("overview.busyParts", { parts: parts.join(" · ") })}</div></div>
                </Link>
              ) : null;
            })()}
            {runningEnc > shown ? (
              <Link className="list-row link-more" href="/av1/wachtrij?status=bezig" style={{ fontSize: 14 }}>{t("overview.moreEncodes", { count: num(runningEnc - shown) })}</Link>
            ) : null}
            {queuedEnc > 0 ? (
              <Link className="list-row link-more" href="/av1/wachtrij?status=wacht" style={{ fontSize: 14 }} data-testid="queued-encodes">{t("overview.queuedEncodes", { count: num(queuedEnc) })}</Link>
            ) : null}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}

// ------------------------------------------------------------------ New this week
/** Eén korte regel over ontbrekend: hoeveel, wat jou iets vraagt, wat nog niet uit is. */
function WantedLine() {
  const { t, num } = useI18n();
  const k = useResource<WantedKpis>("/wanted/kpis", undefined, 120_000);
  const reasons = useResource<WantedReasons>("/wanted/reasons");
  if (!k.data) return null;
  const needs = k.data.needs_user ? k.data.needs_user.reasons.filter((r) => r.total > 0).length
    : Object.entries(k.data.reason_distribution ?? {}).filter(([code, n]) => n > 0 && reasons.data?.[code]?.needs_user).length;
  const parts = [t("overview.wantedLine", { missing: num((k.data as { missing_total?: number }).missing_total ?? k.data.missing) }), needs ? t("overview.wantedNeeds", { count: num(needs) }) : t("overview.wantedAuto"),
    k.data.upcoming?.total ? t("overview.wantedUpcoming", { count: num(k.data.upcoming.total) }) : null].filter(Boolean);
  return (
    <p className="t-2" style={{ fontSize: 14, margin: "0 0 16px" }} data-testid="overview-wanted-line">
      <Link className="link-more" href="/ontbrekend">{parts.join(" · ")}</Link>
    </p>
  );
}

function NewThisWeek() {
  const { t, date } = useI18n();
  const [range] = useState(() => { const s = new Date(); const e = new Date(); e.setDate(e.getDate() + 7); s.setDate(s.getDate() - 1); return { start: iso(s), end: iso(e) }; });
  const cal = useResource<CalendarResponse>("/calendar", range);
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
