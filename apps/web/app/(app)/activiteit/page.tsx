"use client";

import { WhatsHappened } from "@/components/notify/whats-happened";
import { AttentionList, DismissedNote, useAttention } from "@/components/mg/attention";
import { Wrench,
  ArrowDownToLine, Bot, CircleAlert, Clapperboard, Download, FileCheck2, History, ListChecks, ListTodo, OctagonX, RefreshCw,
  Pause, Route, Search, ShieldCheck, Sparkles, Trash2, TriangleAlert, Undo2, type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type ReactNode } from "react";

import { DownloadHealth } from "@/components/mg/download-health";
import { DownloadRecovery } from "@/components/mg/download-recovery";
import { KpiCard } from "@/components/mg/domain";
import { JobsHistory } from "@/components/mg/jobs-history";
import { Button, Card, InlineState, PageHeader, Progress, StatusChip, Tabs, type Tone } from "@/components/mg/primitives";
import { ErrorState, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { useAction } from "@/lib/api/actions";
import type { CoreJob, Instance } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useSession } from "@/lib/session";
import { taskLabel } from "@/lib/tasks";
import { RowMenu } from "@/components/mg/row-menu";
import { arrMessage } from "@/lib/arr-messages";
import { useHealth } from "@/lib/health";
import { instanceLabel, plainText, readableRelease } from "@/lib/labels";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { parseServerDate } from "@/lib/i18n";

interface Download {
  id: number; instance_id: string; instance_name: string; title: string; release: string | null;
  protocol: string | null; client: string | null; status: string; state: string;
  progress: number | null; size: number; size_left: number; eta: string | null; time_left: string | null;
  quality: string | null; warning: boolean; messages: string[]; link: string | null;
  /** Acties die core aanbiedt, bv. 'Importeren' bij een eenduidige ID-match (N9). */
  actions?: Array<{ label_nl: string; path: string; method?: string; body?: Record<string, unknown> }>;
  messages_nl?: string[]; can_import?: boolean; download_id?: string | null; awaiting_import?: boolean;
  /** rapport 7, A2: alleen in de client / van de oude installatie tijdens de migratie */
  only_in_client?: boolean; old_install?: boolean; status_nl?: string | null; category?: string | null;
}
interface Command {
  id: number; instance_id: string; instance_name: string; name: string; label: string; search: boolean;
  status: string; queued_at: string | null; started_at: string | null; target?: string | null;
}
interface EncJob { id: number; instance_id: string | null; instance_name?: string | null; title: string; status: string; label: string; progress: number; link: string; paused_estop?: boolean }
interface Task { id: number; job_type: string; label: string; status: string; instance_id: string | null; instance_name?: string | null; progress: number; started_at: string | null; created_at: string; error: string | null; link: string }
interface SearchAttempt { id: number; instance_id: string; instance_name?: string | null; ts: string; status: string; title: string | null; series_title: string | null; season_number: number | null; episode_number: number | null }
interface RecentItem { kind: string; instance_id: string | null; instance_name?: string | null; at: string; title: string | null; detail: string | null; link: string | null; actor_nl?: string | null }
interface Activity {
  generated_at: string;
  summary: { downloads: number; queued_downloads: number; searches: number; commands: number; av1: number; tasks: number; problems: number };
  downloads: Download[]; commands: Command[]; jobs: Task[]; searches: SearchAttempt[]; recent: RecentItem[];
  encoding: { jobs: EncJob[]; autopilot: { enabled?: boolean; blocked?: string | null; queue_depth?: number } };
  errors: Array<{ instance_id: string | null; name: string; error: string }>;
  /** rapport 7, A2: status van de downloadclients zelf (SABnzbd/qBittorrent) */
  download_clients?: DownloadClientState[]; clients?: DownloadClientState[];
}
interface DownloadClientState { name: string; kind?: string | null; ok: boolean; error?: string | null; error_nl?: string | null; items?: number | null }

function clientStates(a: Activity): DownloadClientState[] {
  const explicit = a.download_clients ?? a.clients ?? [];
  // core meldt een onbereikbare client als fout zonder instance_id (rapport 7, A2)
  const fromErrors = (a.errors ?? []).filter((e) => !e.instance_id && !explicit.some((c) => c.name === e.name))
    .map((e) => ({ name: e.name, ok: false, error: e.error }));
  return [...explicit, ...fromErrors];
}

const REFRESH_MS = 10_000;

function useVisible(): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    update();
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

/** "00:05:12" (Sonarr timeleft) → seconden. */
function parseTimeLeft(value: string | null): number | null {
  if (!value) return null;
  const m = /^(?:(\d+)\.)?(\d+):(\d+):(\d+)/.exec(value);
  if (!m) return null;
  return (Number(m[1] ?? 0) * 86400) + Number(m[2]) * 3600 + Number(m[3]) * 60 + Number(m[4]);
}

function Section({ title, icon, count, children }: { title: string; icon: LucideIcon; count?: number; children: ReactNode }) {
  return <Card title={title} icon={icon} sub={count ? String(count) : undefined} flush>{children}</Card>;
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="list-row"><span className="list-sub" style={{ whiteSpace: "normal" }}>{children}</span></div>;
}

function Row({ href, icon: Icon, tone, title, sub, aside, children, wrapSub }: {
  href?: string | null; icon: LucideIcon; tone?: "danger" | "warning" | "success" | "accent" | "info";
  title: ReactNode; sub?: ReactNode; aside?: ReactNode; children?: ReactNode; wrapSub?: boolean;
}) {
  const body = (
    <>
      <span className={`list-icon${tone ? ` list-icon--${tone}` : ""}`}><Icon className="ico" aria-hidden="true" /></span>
      <div className="list-main stack-sm">
        <div className="list-title">{title}</div>
        {sub ? <div className="list-sub" style={wrapSub ? { whiteSpace: "normal" } : undefined}>{sub}</div> : null}
        {children}
      </div>
      {aside ? <div className="list-aside">{aside}</div> : null}
    </>
  );
  return href ? <Link className="list-row" href={href}>{body}</Link> : <div className="list-row">{body}</div>;
}

const DL_STATE: Record<string, { key: MessageKey; tone: Tone }> = {
  queued: { key: "activity.dl.queued", tone: "neutral" },
  delay: { key: "activity.dl.queued", tone: "neutral" },
  paused: { key: "activity.dl.paused", tone: "warning" },
  importpending: { key: "activity.dl.completed", tone: "info" },
  importing: { key: "activity.dl.importing", tone: "accent" },
  completed: { key: "activity.dl.completed", tone: "info" },
  failed: { key: "activity.dl.failed", tone: "danger" },
  failedpending: { key: "activity.dl.failed", tone: "danger" },
};

function DownloadRow({ d, showSize }: { d: Download; showSize?: boolean }) {
  const { t, num, duration, locale, bytes } = useI18n();
  const left = parseTimeLeft(d.time_left);
  const downloading = d.status === "downloading";
  const st = DL_STATE[d.state] ?? DL_STATE[d.status];
  const meta = [downloading && left !== null ? t("activity.dl.left", { time: duration(left) }) : null,
    showSize && d.size ? t("dl37.size", { left: bytes(d.size_left ?? 0), size: bytes(d.size) }) : null,
    instanceLabel(d.instance_id, d.instance_name, locale), d.client, d.quality]
    .filter(Boolean).join(" · ");
  return (
    <Row href={d.link} icon={d.warning ? TriangleAlert : ArrowDownToLine} tone={d.warning ? "warning" : downloading ? "accent" : undefined}
      title={readableRelease(d.title) === "—" ? d.title : readableRelease(d.title)} sub={meta} wrapSub
      aside={d.progress !== null ? <span className="num" style={{ fontWeight: 600 }}>{num(d.progress, 0)}%</span> : null}>
      {d.progress !== null ? <Progress value={d.progress} thin label={t("activity.dl.progress", { title: d.title })} tone={d.warning ? "warning" : undefined} /> : null}
      {d.only_in_client || d.old_install ? (
        <div className="row wrap" style={{ gap: 6 }}>
          {d.old_install ? <StatusChip tone="neutral" outline>{t("dl37.oldInstall")}</StatusChip> : null}
          {d.only_in_client ? <StatusChip tone="neutral" outline>{t("dl37.onlyClient", { category: d.category ?? "—" })}</StatusChip> : null}
        </div>
      ) : null}
      {d.warning || (!downloading && (st || d.status_nl)) ? (
        <div className="row wrap" style={{ gap: 6 }}>
          {!downloading && st ? <StatusChip tone={st.tone}>{t(st.key)}</StatusChip> : !downloading && d.status_nl ? <StatusChip tone="neutral">{d.status_nl}</StatusChip> : null}
          {d.warning ? <StatusChip tone="warning">{t("activity.dl.warning")}</StatusChip> : null}
        </div>
      ) : null}
      {(d.messages_nl ?? d.messages).length ? <div className="list-sub" style={{ whiteSpace: "normal" }}>{(d.messages_nl ?? d.messages).slice(0, 2).map((m) => arrMessage(m, locale)).join(" · ")}</div> : null}
      {d.actions?.length || (d.can_import && d.download_id) ? <DownloadActions d={d} /> : null}
    </Row>
  );
}

interface ImportResult { imported?: boolean; pending?: boolean; reason_nl?: string }

function DownloadActions({ d }: { d: Download }) {
  const { t } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const toast = useToast();
  // 'Import bevestigd' alleen als Sonarr/Radarr echt importeerden (verbeterlijst 12)
  const importNow = async () => {
    const r = await run<ImportResult>(`imp-${d.download_id}`, "post", `/activity/downloads/${encodeURIComponent(d.instance_id)}/import`, { download_id: d.download_id, confirm: true });
    if (!r) return;
    if (r.imported) toast.push("success", t("activity.dl.imported"));
    else if (r.pending) toast.push("info", t("activity.dl.importPending"));
    else toast.push("danger", t("activity.dl.importNot"), r.reason_nl);
  };
  return (
    <div className="row wrap" style={{ gap: 6 }} onClick={(e) => e.preventDefault()}>
      {d.can_import && d.download_id ? (
        <Button size="sm" variant="primary" disabled={!can("operator")} pending={pending === `imp-${d.download_id}`}
          onClick={(e) => { e.preventDefault(); void importNow(); }}>{t("activity.dl.import")}</Button>
      ) : null}
      {(d.actions ?? []).map((a) => (
        <Button key={a.path} size="sm" variant="primary" disabled={!can("operator")} pending={pending === a.path}
          onClick={(e) => { e.preventDefault(); void run(a.path, (a.method ?? "post").toLowerCase() as "post", a.path.replace(/^\/api\/v1/, ""), a.body ?? {}, t("wanted.actionDone")); }}>{a.label_nl}</Button>
      ))}
    </div>
  );
}

/** Downloadclients die niet uit te lezen zijn: zeg dat, in plaats van stil '0' te tonen (A2). */
function ClientStatus({ clients }: { clients: DownloadClientState[] }) {
  const { t } = useI18n();
  const down = clients.filter((c) => !c.ok);
  if (!down.length) return null;
  return (
    <div style={{ marginBottom: 8 }} data-testid="download-clients-down">
      {down.map((c) => (
        <InlineState key={c.name} tone="warning" icon={TriangleAlert} role="status">
          {t("dl37.clientDown", { name: c.name, error: c.error_nl ?? c.error ?? "—" })}
        </InlineState>
      ))}
    </div>
  );
}

/** Activiteit → Downloads: wat er nú in SABnzbd/qBittorrent staat, met voortgang, grootte en ETA (A2). */
function LiveDownloads() {
  const { t } = useI18n();
  const visible = useVisible();
  const res = useResource<Activity>("/activity", undefined, visible ? REFRESH_MS : undefined);
  const a = res.data;
  return (
    <Section title={t("dl37.live")} icon={ArrowDownToLine} count={a?.downloads.length ?? 0}>
      {!a ? (res.error ? <ErrorState error={res.error} compact onRetry={() => void res.reload()} /> : <StateView kind="loading" compact />) : (
        <>
          <ClientStatus clients={clientStates(a)} />
          {a.errors.some((e) => e.instance_id) ? <InlineState tone="warning" icon={CircleAlert}>{t("activity.errors", { names: a.errors.filter((e) => e.instance_id).map((e) => e.name).join(", ") })}</InlineState> : null}
          <div className="list" data-testid="live-downloads">
            {a.downloads.length ? a.downloads.map((d) => <DownloadRow key={`${d.instance_id}:${d.id}`} d={d} showSize />) : <Empty>{t("dl37.liveEmpty")}</Empty>}
          </div>
        </>
      )}
    </Section>
  );
}

const SEARCH_TONE: Record<string, { key: MessageKey; tone: Tone }> = {
  grabbed: { key: "activity.search.grabbed", tone: "success" },
  no_candidate: { key: "activity.search.none", tone: "neutral" },
  candidates: { key: "activity.search.none", tone: "neutral" },
  pending: { key: "activity.search.pending", tone: "accent" },
  error: { key: "activity.search.error", tone: "danger" },
};

const RECENT: Record<string, { key: MessageKey; icon: LucideIcon; tone?: "success" | "accent" | "info" | "warning" }> = {
  grab: { key: "activity.recent.grab", icon: Download, tone: "accent" },
  import: { key: "activity.recent.import", icon: FileCheck2, tone: "success" },
  upgrade: { key: "activity.recent.upgrade", icon: Sparkles, tone: "success" },
  replaced: { key: "activity.recent.replaced", icon: Clapperboard, tone: "info" },
  restored: { key: "activity.recent.restored", icon: Undo2, tone: "warning" },
  routed: { key: "activity.recent.routed", icon: Route, tone: "info" },
  stuck_fixed: { key: "activity.recent.stuckFixed", icon: Wrench, tone: "success" },
  stuck_failed: { key: "activity.recent.stuckFailed", icon: Wrench, tone: "warning" },
  identity_reevaluated: { key: "activity.recent.identityReevaluated", icon: ShieldCheck, tone: "info" },
  identity_ai: { key: "activity.recent.identityAi", icon: Bot, tone: "accent" },
  identity_replaced: { key: "activity.recent.identityReplaced", icon: Wrench, tone: "warning" },
  title_removed: { key: "activity.recent.titleRemoved", icon: Trash2, tone: "info" },
  // door een andere app gestart (API-sleutel, bv. Media Source Optimizer), S17
  external: { key: "activity.recent.external", icon: Route, tone: "info" },
  event: { key: "activity.recent.event", icon: History },
};

type ActTab = "nu" | "downloads" | "problemen" | "geschiedenis" | "gebeurd";

export default function ActivityPage() {
  return <Suspense><ActivityTabs /></Suspense>;
}

function ActivityTabs() {
  const { t } = useI18n();
  const params = useSearchParams();
  const router = useRouter();
  const raw = params.get("tab");
  // Aanvragen en Opslag hebben een eigen plek gekregen (Aanvragen, Ruimte): oude links doorsturen
  const moved = raw === "aanvragen" ? "/aanvragen"
    : raw === "opslag" ? `/av1${params.get("periode") ? `?periode=${encodeURIComponent(params.get("periode") ?? "")}` : ""}#opslag` : null;
  useEffect(() => { if (moved) router.replace(moved); }, [moved, router]);
  const tab: ActTab = raw === "problemen" || raw === "geschiedenis" || raw === "downloads" || raw === "gebeurd" ? raw : "nu";
  const go = (next: ActTab) => router.replace(next === "nu" ? "/activiteit" : `/activiteit?tab=${next}`, { scroll: false });
  if (moved) return <StateView kind="loading" />;
  return (
    <div className="stack" data-testid="activity-root">
      <PageHeader title={t("activity.title")} sub={t("activity.sub")} />
      <Tabs variant="chips" label={t("activity.title")} value={tab} onSelect={go} items={[
        { value: "nu", label: t("activity.tab.now") }, { value: "problemen", label: t("activity.tab.problems") }, { value: "downloads", label: t("dl.tab") }, { value: "gebeurd", label: t("happened.tab") }, { value: "geschiedenis", label: t("activity.tab.history") },
      ]} />
      {tab === "nu" ? <NowView /> : tab === "downloads" ? <div className="stack"><LiveDownloads /><DownloadRecovery /><DownloadHealth /></div> : tab === "problemen" ? <ProblemsView /> : tab === "gebeurd" ? <WhatsHappened /> : <JobsHistory basePath="/activiteit" />}
    </div>
  );
}

/** "Bijgewerkt 3 s geleden" als eigen klein onderdeel: de seconde-klok laat niet de hele pagina hertekenen. */
function UpdatedAgo({ at, visible }: { at: number | null; visible: boolean }) {
  const { t, relative } = useI18n();
  const now = useNow();
  // Peil §4.4: nog nooit opgehaald is geen "—" maar staat er gewoon
  if (visible && !at) return <span className="list-sub">{t("activity.updatedNever")}</span>;
  const ago = at ? relative(new Date(Math.min(at, now))) : "";
  return <span className="list-sub">{visible ? t("activity.updated", { ago }) : t("activity.paused")}</span>;
}

function NowView() {
  const { t, num, relative, locale } = useI18n();
  const visible = useVisible();
  // 10 s is genoeg (Sonarr/Radarr-wachtrijen veranderen niet per seconde); verborgen = pauze
  const res = useResource<Activity>("/activity", undefined, visible ? REFRESH_MS : undefined);
  const { reload } = res;
  const a = res.data;

  const header = (
    <div className="row-between" style={{ gap: 8 }}>
      <UpdatedAgo at={res.fetchedAt} visible={visible} />
      <Button size="sm" icon={RefreshCw} onClick={() => void reload()} pending={res.loading}>{t("activity.refresh")}</Button>
    </div>
  );
  if (!a) {
    return <div className="stack">{header}{res.error ? <ErrorState error={res.error} onRetry={() => void reload()} /> : <StateView kind="loading" title={t("activity.title")} />}</div>;
  }
  const s = a.summary;
  const ap = a.encoding.autopilot ?? {};
  const recent = dedupeRecent(a.recent);
  const awaitingImport = a.downloads.filter((d) => d.awaiting_import || d.state === "importPending" || d.state === "importBlocked").length;
  return (
    <div className="stack" data-testid="activity-page">
      {header}
      {a.errors.length ? <InlineState tone="warning" icon={CircleAlert}>{t("activity.errors", { names: a.errors.map((e) => e.name).join(", ") })}</InlineState> : null}

      <section className="kpi-grid" aria-label={t("activity.title")}>
        <KpiCard icon={ArrowDownToLine} label={t("activity.kpi.downloads")} value={num(s.downloads)} href="/activiteit?tab=downloads"
          tone={clientStates(a).some((c) => !c.ok) ? "warning" : undefined}
          meta={clientStates(a).some((c) => !c.ok) ? t("dl37.kpiUnknown") : awaitingImport ? t("activity.kpi.awaitingImport", { count: num(awaitingImport), total: num(s.queued_downloads) }) : t("activity.kpi.downloadsMeta", { total: num(s.queued_downloads) })} />
        <KpiCard icon={Search} label={t("activity.kpi.searches")} value={num(s.searches)} meta={t("activity.kpi.searchesMeta", { count: num(s.commands) })} />
        <KpiCard icon={Clapperboard} label={t("activity.kpi.av1")} value={num(s.av1)} meta={t("activity.kpi.av1Meta")} href="/av1" />
        <KpiCard icon={ListTodo} label={t("activity.kpi.tasks")} value={num(s.tasks)} meta={t("activity.kpi.tasksMeta")} href="/activiteit?tab=geschiedenis" />
        <KpiCard icon={ListChecks} label={t("activity.kpi.problems")} value={num(s.problems)} meta={t("activity.kpi.problemsMeta")} tone={s.problems ? "warning" : undefined} href="/activiteit?tab=problemen" />
      </section>

      <Section title={t("activity.sec.downloads")} icon={ArrowDownToLine} count={a.downloads.length}>
        <ClientStatus clients={clientStates(a)} />
        <div className="list" data-testid="activity-downloads">
          {a.downloads.length ? a.downloads.map((d) => <DownloadRow key={`${d.instance_id}:${d.id}`} d={d} />) : <Empty>{t("activity.empty.downloads")}</Empty>}
        </div>
      </Section>

      <Section title={t("activity.sec.commands")} icon={Search} count={a.commands.length}>
        <div className="list" data-testid="activity-commands">
          {a.commands.length ? a.commands.map((c) => (
            <Row key={`${c.instance_id}:${c.id}`} icon={c.search ? Search : RefreshCw} tone={c.status === "started" ? "accent" : undefined}
              title={c.target ? `${c.label} · ${c.target}` : c.label}
              sub={[instanceLabel(c.instance_id, c.instance_name, locale), c.status === "started" ? t("activity.cmd.started", { ago: relative(c.started_at) }) : t("activity.cmd.queuedAt", { ago: relative(c.queued_at) })].join(" · ")}
              aside={<StatusChip tone={c.status === "started" ? "accent" : "neutral"} pending={c.status === "started"}>{t(c.status === "started" ? "activity.cmd.running" : "activity.cmd.queued")}</StatusChip>} />
          )) : <Empty>{t("activity.empty.commands")}</Empty>}
          {a.searches.length ? (
            <>
              <div className="list-row"><span className="list-sub" style={{ fontWeight: 600 }}>{t("activity.searches.title")}</span></div>
              {a.searches.slice(0, 8).map((x) => {
                const st = SEARCH_TONE[x.status] ?? SEARCH_TONE.pending;
                const code = x.season_number !== null && x.episode_number !== null
                  ? ` · S${String(x.season_number).padStart(2, "0")}E${String(x.episode_number).padStart(2, "0")}` : "";
                return (
                  <Row key={`s${x.id}`} icon={Bot} title={`${x.series_title ?? x.title ?? "—"}${code}`}
                    sub={`${instanceLabel(x.instance_id, x.instance_name, locale)} · ${t("activity.search.sent", { ago: relative(x.ts) })}`}
                    aside={<StatusChip tone={st.tone}>{t(st.key)}</StatusChip>} />
                );
              })}
            </>
          ) : null}
        </div>
      </Section>

      <Section title={t("activity.sec.av1")} icon={Clapperboard} count={a.encoding.jobs.length}>
        <div className="list" data-testid="activity-av1">
          <Row href="/av1" icon={Sparkles} tone={ap.enabled && !ap.blocked ? "success" : undefined} title={t("activity.av1.autopilot")}
            sub={!ap.enabled ? t("activity.av1.autopilotOff") : ap.blocked ? t("activity.av1.autopilotWaiting", { reason: ap.blocked }) : t("activity.av1.autopilotOn2", { running: num(a.encoding.jobs.filter((j) => j.status === "running" && !j.paused_estop).length), queued: num(ap.queue_depth ?? 0) })} />
          {a.encoding.jobs.length ? a.encoding.jobs.map((j) => (
            <Row key={j.id} href={j.link} icon={Clapperboard} tone="accent" title={readableRelease(j.title)} sub={[j.label, j.instance_id ? instanceLabel(j.instance_id, j.instance_name, locale) : null].filter(Boolean).join(" · ")}
              aside={j.paused_estop
                // noodstop: bevroren, niet 'bezig' (punt 39)
                ? <StatusChip tone="warning" icon={Pause}>{t("estop.pausedJob")}</StatusChip>
                : <span className="num" style={{ fontWeight: 600 }}>{num(j.progress, 0)}%</span>}>
              <Progress value={j.progress} thin label={t("activity.dl.progress", { title: j.title })} indeterminate={!j.paused_estop && (j.status === "queued" || j.status === "submitted")} />
            </Row>
          )) : <Empty>{t("activity.empty.av1")}</Empty>}
        </div>
      </Section>

      <Section title={t("activity.sec.tasks")} icon={ListTodo} count={a.jobs.length}>
        <div className="list" data-testid="activity-tasks">
          {a.jobs.length ? a.jobs.map((j) => (
            <Row key={j.id} href={j.link} icon={ListTodo} tone={j.status === "retry_scheduled" ? "warning" : j.status === "running" ? "accent" : undefined}
              title={j.label} sub={[j.instance_id ? instanceLabel(j.instance_id, j.instance_name, locale) : null, j.error && j.status === "retry_scheduled" ? j.error : relative(j.started_at ?? j.created_at)].filter(Boolean).join(" · ")}
              aside={<StatusChip tone={j.status === "running" ? "accent" : j.status === "retry_scheduled" ? "warning" : "neutral"} pending={j.status === "running"}>
                {t(j.status === "running" ? "activity.task.running" : j.status === "retry_scheduled" ? "activity.task.retry" : "activity.task.queued")}</StatusChip>}>
              {j.status === "running" && j.progress > 0 ? <Progress value={j.progress} thin label={t("activity.dl.progress", { title: j.label })} /> : null}
            </Row>
          )) : <Empty>{t("activity.empty.tasks")}</Empty>}
        </div>
      </Section>

      <Section title={t("activity.sec.recent")} icon={History} count={recent.length}>
        <div className="list" data-testid="activity-recent">
          {recent.length ? recent.map((r, i) => {
            const k = RECENT[r.kind] ?? RECENT.event;
            return (
              <Row key={`${r.kind}:${r.at}:${i}`} href={r.link} icon={k.icon} tone={k.tone} title={recentTitle(r.title)}
                sub={[t(k.key), r.instance_id ? instanceLabel(r.instance_id, r.instance_name, locale) : null, r.detail, r.kind === "external" ? null : r.actor_nl, relative(r.at)].filter(Boolean).join(" · ")}
                aside={r.kind === "external" && r.actor_nl ? <span className="chip chip--accent" data-testid="external-badge">{r.actor_nl}</span> : undefined} />
            );
          }) : <Empty>{t("activity.empty.recent")}</Empty>}
        </div>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------- Problemen
const RECENT_DAYS = 7;

const BREAKDOWN: Record<string, { label: MessageKey; sub: MessageKey; href: string }> = {
  av1_not_activated: { label: "problems.av1NotActivated", sub: "problems.av1NotActivatedSub", href: "/av1/wachtrij?status=mislukt" },
  av1_failed: { label: "problems.av1Failed", sub: "problems.av1FailedSub", href: "/av1/wachtrij?status=mislukt" },
  av1_conflicts: { label: "problems.av1Conflicts", sub: "problems.av1ConflictsSub", href: "/av1/wachtrij" },
  release_check_failed: { label: "problems.releaseCheck", sub: "problems.releaseCheckSub", href: "/quarantaine" },
  encoder_degraded: { label: "problems.encoderDegraded", sub: "problems.encoderDegradedSub", href: "/av1" },
};

/** "Jager.X.Jager.2011.S03E10.1080p…" → "Jager X Jager — S03E10" (M8/L6). */
/**
 * Dezelfde gebeurtenis kan via twee bronnen binnenkomen (bv. webhook én historie van Sonarr):
 * toon hem één keer — zelfde soort, titel en detail binnen dezelfde minuut (rapport 7, A4).
 */
function dedupeRecent(items: RecentItem[]): RecentItem[] {
  const seen = new Set<string>();
  return items.filter((r) => {
    const minute = (r.at ?? "").slice(0, 16);
    const key = `${r.kind}|${r.instance_id ?? ""}|${r.title ?? ""}|${r.detail ?? ""}|${minute}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function recentTitle(title: string | null): string {
  if (!title) return "—";
  return /[._].*[Ss]\d{1,2}[Ee]\d{1,3}|\.(mkv|mp4)$|\[.*\]/.test(title) ? readableRelease(title) : title;
}

function ProblemsView() {
  const { t, relative, locale } = useI18n();
  const { can } = useSession();
  const failed = useResource<CoreJob[]>("/jobs", { status: "failed", limit: 100 });
  const activity = useResource<Activity>("/activity");
  const instances = useResource<Instance[]>("/instances");
  const attention = useAttention();
  const { run, pending } = useAction();
  const [since] = useState(() => Date.now() - RECENT_DAYS * 86_400_000);
  const jobs = (failed.data ?? []).filter((j) => (parseServerDate(String((j as { finished_at?: string | null }).finished_at ?? j.created_at))?.valueOf() ?? 0) >= since);
  const stuck = (activity.data?.downloads ?? []).filter((d) => d.warning);
  const down = (instances.data ?? []).filter((i) => i.status === "offline" || i.status === "degraded");
  const waiting = attention.data?.items ?? [];
  const health = useHealth();
  // waar 'Fouten N' op het Overzicht uit bestaat, per onderdeel met de items zelf (N13)
  const errorGroups = useResource<{ total: number; groups: Array<{ key: string; label_nl: string; count: number; href: string; items: Array<{ id?: string | number; title?: string; reason_nl?: string; href?: string }> }> }>("/health/errors", undefined, 60_000);
  // fouten die het gezondheidsmodel telt maar die niet al hierboven in 'Wacht op jou' staan
  const extra = Object.entries(health.breakdown).filter(([k]) => BREAKDOWN[k]).map(([k, n]) => ({ key: k, n, ...BREAKDOWN[k] }));
  const total = jobs.length + stuck.length + down.length + waiting.length + extra.length + (errorGroups.data?.total ?? 0);
  if (failed.loading && activity.loading) return <StateView kind="loading" />;
  return (
    <div className="stack" data-testid="activity-problems">
      {total === 0 ? <StateView kind="empty" title={t("activity.problems.none")} body={t("activity.problems.noneBody")} /> : null}
      {waiting.length ? <h2 style={{ margin: "4px 0 0", fontSize: 15, fontWeight: 650 }}>{t("attention.title")}</h2> : null}
      <AttentionList items={waiting} onChange={() => void attention.reload()} />
      <DismissedNote count={attention.data?.dismissed} onChange={() => void attention.reload()} />
      {errorGroups.data?.groups?.length ? (
        <Section title={t("activity.problems.errors", { count: errorGroups.data.total })} icon={OctagonX} count={errorGroups.data.total}>
          <div className="stack-sm" data-testid="problems-errors">{errorGroups.data.groups.map((g) => (
            // de link staat naast de summary, niet erin (axe nested-interactive)
            <div key={g.key} className="problem-group">
            <details className="raw" open={errorGroups.data!.groups.length === 1}>
              <summary style={{ fontSize: 14 }}><strong>{g.label_nl}</strong> <span className="chip">{g.count}</span></summary>
              <div className="list">{(g.items ?? []).map((it, i) => (
                <Row key={String(it.id ?? i)} href={it.href ?? g.href} icon={OctagonX} tone="danger" title={it.title ?? "—"} sub={plainText(it.reason_nl ?? "", locale)} wrapSub />
              ))}</div>
            </details>
            <Link className="link-more problem-group-link" href={g.href}>{t("todo.look")}</Link>
            </div>
          ))}</div>
        </Section>
      ) : extra.length ? (
        <Section title={t("activity.problems.av1")} icon={Clapperboard} count={extra.reduce((s, e) => s + e.n, 0)}>
          <div className="list" data-testid="problems-av1">{extra.map((e) => (
            <Row key={e.key} href={e.href} icon={OctagonX} tone="danger" title={t(e.label, { count: e.n })} sub={t(e.sub)} wrapSub aside={<span className="btn btn--sm btn--primary">{t("todo.look")}</span>} />
          ))}</div>
        </Section>
      ) : null}
      {down.length ? (
        <Section title={t("activity.problems.instances")} icon={CircleAlert} count={down.length}>
          <div className="list">{down.map((i) => (
            <Row key={i.id} href="/instellingen/instanties" icon={CircleAlert} tone={i.status === "offline" ? "danger" : "warning"} title={i.name}
              sub={i.last_error ?? t(`status.${i.status}` as MessageKey)} wrapSub aside={<StatusChip tone={i.status === "offline" ? "danger" : "warning"}>{t(`status.${i.status}` as MessageKey)}</StatusChip>} />
          ))}</div>
        </Section>
      ) : null}
      {stuck.length ? (
        <Section title={t("activity.problems.downloads")} icon={Download} count={stuck.length}>
          <div className="list">{stuck.map((d) => <DownloadRow key={`${d.instance_id}:${d.id}`} d={d} />)}</div>
        </Section>
      ) : null}
      {jobs.length ? (
        <Section title={t("activity.problems.tasks", { days: RECENT_DAYS })} icon={ListTodo} count={jobs.length}>
          <div className="list">{jobs.slice(0, 30).map((j) => (
            <Row key={j.id} href={`/activiteit?tab=geschiedenis&job=${j.id}`} icon={ListTodo} tone="danger" title={taskLabel(t, j.job_type)}
              sub={[j.error, relative(String((j as { finished_at?: string | null }).finished_at ?? j.created_at))].filter(Boolean).join(" · ")} wrapSub
              aside={can("operator") ? <span style={{ display: "inline-flex", gap: 6 }} onClick={(e) => e.preventDefault()}>
                <Button size="sm" variant="primary" pending={pending === `r-${j.id}`} onClick={async (e) => { e.preventDefault(); await run(`r-${j.id}`, "post", `/jobs/${j.id}/retry`, {}, t("jobs.retried")); void failed.reload(); }}>{t("jobs.retry")}</Button>
                <RowMenu><Button size="sm" pending={pending === `i-${j.id}`} onClick={async (e) => { e.preventDefault(); await run(`i-${j.id}`, "post", `/jobs/${j.id}/ignore`, {}, t("jobs.ignored")); void failed.reload(); }}>{t("jobs.ignore")}</Button></RowMenu>
              </span> : undefined} />
          ))}</div>
        </Section>
      ) : null}
      <p className="hint">{t("activity.problems.hint")} <Link href="/activiteit?tab=geschiedenis&status=failed">{t("activity.problems.allFailed")}</Link></p>
    </div>
  );
}
