"use client";

import { Activity, Ban, History, RotateCw, Radio, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AuditTable } from "@/components/mg/audit-table";
import { Button, Card, StatusChip, Tabs, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { CoreEvent, CoreJob, CoreJobDetail, Instance } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents, useLiveStatus } from "@/lib/events";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { taskLabel } from "@/lib/tasks";
import { actorLabel, eventLabel, instanceLabel } from "@/lib/labels";
import { parseServerDate } from "@/lib/i18n";

const STATUSES = ["queued", "running", "retry_scheduled", "failed", "succeeded", "cancelled"] as const;
const PAGE = 25;
const WEEK_MS = 7 * 86_400_000;

type Row = { kind: "job"; job: CoreJob } | { kind: "group"; job: CoreJob; count: number };

/** Routinetaken die elke paar minuten slagen samenvoegen tot één regel (M7). */
function groupRoutine(jobs: CoreJob[]): Row[] {
  const out: Row[] = [];
  for (const j of jobs) {
    const last = out[out.length - 1];
    if (j.status === "succeeded" && last && last.job.status === "succeeded" && last.job.job_type === j.job_type) {
      if (last.kind === "group") last.count += 1; else out[out.length - 1] = { kind: "group", job: last.job, count: 2 };
      continue;
    }
    out.push({ kind: "job", job: j });
  }
  return out;
}
const TONE: Record<string, Tone> = { queued: "neutral", running: "accent", retry_scheduled: "warning", failed: "danger", succeeded: "success", cancelled: "neutral" };
type Tab = "jobs" | "events" | "audit";

/** Geschiedenis van MediaGuard-taken (vroeger de pagina 'Jobs & logs'); nu een tabblad van Activiteit. */
export function JobsHistory({ basePath = "/activiteit" }: { basePath?: string }) {
  return <Suspense><Jobs basePath={basePath} /></Suspense>;
}

function Jobs({ basePath }: { basePath: string }) {
  const { t, relative, dateTime, locale } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  // standaard alleen 'Mislukt / Let op' van de laatste 7 dagen; 'all' = alles (M7)
  const status = params.get("status") ?? "problems";
  const [limit, setLimit] = useState(PAGE);
  const [since] = useState(() => Date.now() - WEEK_MS);
  const instance = params.get("instance") ?? "";
  const q = params.get("q") ?? "";
  const view = params.get("view");
  const [tab, setTab] = useState<Tab>(view === "audit" || view === "events" ? view : "jobs");
  const [openId, setOpenId] = useState<number | null>(params.get("job") ? Number(params.get("job")) : null);
  const apiStatus = status === "problems" || status === "all" ? undefined : status;
  const jobs = useResource<CoreJob[]>("/jobs", { status: apiStatus, instance_id: instance || undefined, limit: 500 });
  useCoreEvents("job", () => void jobs.reload());
  const set = (key: string, value: string) => {
    const qs = new URLSearchParams(params.toString());
    if (value) qs.set(key, value); else qs.delete(key);
    router.replace(`${basePath}?${qs.toString()}`);
  };
  const instances = useResource<Instance[]>("/instances");
  const names = Object.fromEntries((instances.data ?? []).map((i) => [i.id, i.name]));
  const filtered = (jobs.data ?? []).filter((j) => (!q || JSON.stringify(j).toLowerCase().includes(q.toLowerCase()))
    && (status !== "problems" || ((j.status === "failed" || j.status === "retry_scheduled") && (parseServerDate(String(j.finished_at ?? j.created_at))?.valueOf() ?? 0) >= since)));
  const grouped = groupRoutine(filtered);
  const rows = grouped.slice(0, limit);

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Tabs label={t("nav.jobs")} value={tab} onSelect={setTab} items={[{ value: "jobs", label: t("jobs.tab.jobs") }, { value: "events", label: t("jobs.tab.events") }, { value: "audit", label: t("jobs.tab.audit") }]} />
      </div>
      {tab === "jobs" ? (
        <div className="grid">
          <div className={openId ? "span-7" : "span-12"}>
            <Card title={t("jobs.tab.jobs")} icon={Activity} flush actions={<>
              <select className="select" style={{ width: "auto" }} aria-label={t("library.col.status")} value={status} onChange={(e) => { setLimit(PAGE); set("status", e.target.value === "problems" ? "" : e.target.value); }} data-testid="history-status">
                <option value="problems">{t("history.problems7d")}</option>
                <option value="all">{t("jobs.allStatuses")}</option>
                {STATUSES.map((s) => <option key={s} value={s}>{t(`status.${s}` as MessageKey)}</option>)}
              </select>
              <InstanceSelect value={instance} onChange={(v) => set("instance", v)} />
            </>}>
              <ResourceView resource={jobs} endpoint="GET /api/v1/jobs" isEmpty={() => rows.length === 0} empty={<StateView kind="empty" compact title={status === "problems" ? t("history.noProblems") : t("jobs.empty")} body={status === "problems" ? t("history.noProblemsBody") : undefined} action={status === "problems" ? <Button size="sm" onClick={() => set("status", "all")}>{t("history.showAll")}</Button> : undefined} />}>
                {() => (
                  <>
                  <div className="table-wrap">
                    <table className="table table--compact table--stack">
                      <thead><tr><th>{t("jobs.type")}</th><th>{t("library.col.status")}</th><th>{t("library.instance")}</th><th>{t("history.when")}</th><th>{t("jobs.error")}</th></tr></thead>
                      <tbody>{rows.map((r) => {
                        const j = r.job;
                        return (
                          <tr key={j.id} aria-selected={openId === j.id} onClick={() => setOpenId(j.id)} style={{ cursor: "pointer" }} data-testid={r.kind === "group" ? "history-group" : undefined}>
                            <td className="cell-primary"><button type="button" className="link-more" style={{ border: 0, background: "none", padding: 0, fontSize: 14, textAlign: "left" }} onClick={() => setOpenId(j.id)}>{taskLabel(t, j.job_type)}</button>
                              {r.kind === "group" ? <div className="cell-sub">{t("history.grouped", { count: r.count, ago: relative(j.finished_at ?? j.created_at) })}</div> : null}
                              {(() => { const by = (j as { created_by?: string | null }).created_by; return by && (by.startsWith("apikey:") || by.startsWith("ai:")) ? <div className="cell-sub" data-testid="job-by">{t("history.by", { who: actorLabel(by, locale) })}</div> : null; })()}</td>
                            <td data-label={t("library.col.status")}><StatusChip tone={TONE[j.status] ?? "neutral"} pending={j.status === "running"}>{t(`status.${j.status}` as MessageKey)}</StatusChip></td>
                            <td data-label={t("library.instance")}>{j.instance_id ? instanceLabel(j.instance_id, names[j.instance_id], locale) : "—"}</td>
                            <td data-label={t("history.when")} title={dateTime(j.created_at)}>{relative(j.finished_at ?? j.created_at)}</td>
                            <td data-label={t("jobs.error")} className="t-danger" style={{ maxWidth: 320, whiteSpace: "normal" }}>{j.error ?? ""}</td>
                          </tr>
                        );
                      })}</tbody>
                    </table>
                  </div>
                  <div className="card-footer">
                    <span>{t("history.showing", { shown: rows.length, total: grouped.length })}</span>
                    {grouped.length > rows.length ? <Button size="sm" onClick={() => setLimit((l) => l + PAGE)} data-testid="history-more">{t("history.more")}</Button> : null}
                  </div>
                  </>
                )}
              </ResourceView>
            </Card>
          </div>
          {openId ? <div className="span-5"><JobDetail id={openId} onClose={() => setOpenId(null)} onChanged={() => void jobs.reload()} /></div> : null}
        </div>
      ) : null}
      {tab === "events" ? <EventsFeed /> : null}
      {tab === "audit" ? <Card title={t("audit.title")} sub={t("audit.sub")} icon={History} flush><AuditTable /></Card> : null}
    </>
  );
}

function JobDetail({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const { t, dateTime, locale } = useI18n();
  const { can } = useSession();
  const job = useResource<CoreJobDetail>(`/jobs/${id}`);
  useCoreEvents("job", () => void job.reload());
  const { run, pending } = useAction();
  return (
    <Card title={t("history.taskN", { id })} icon={Activity} actions={<Button size="sm" variant="ghost" iconOnly icon={X} onClick={onClose}>{t("action.close")}</Button>}>
      <ResourceView resource={job} endpoint={`GET /api/v1/jobs/${id}`} compact>
        {(j) => (
          <div className="stack">
            <dl className="kv">
              <dt>{t("jobs.type")}</dt><dd>{taskLabel(t, j.job_type)}</dd>
              <dt>{t("library.col.status")}</dt><dd><StatusChip tone={TONE[j.status] ?? "neutral"}>{t(`status.${j.status}` as MessageKey)}</StatusChip></dd>
              <dt>{t("library.instance")}</dt><dd>{j.instance_id ? instanceLabel(j.instance_id, null, locale) : "—"}</dd>
              <dt>{t("jobs.created")}</dt><dd>{dateTime(j.created_at)}</dd>
              <dt>{t("jobs.attempts")}</dt><dd>{j.attempts}</dd>
              {j.error ? <><dt>{t("jobs.error")}</dt><dd className="t-danger">{j.error}</dd></> : null}
            </dl>
            <div className="row">
              <Button size="sm" icon={RotateCw} disabled={!can("operator") || !["failed", "cancelled", "retry_scheduled"].includes(j.status)} pending={pending === "retry"} onClick={async () => { await run("retry", "post", `/jobs/${id}/retry`, {}, t("jobs.retried")); onChanged(); void job.reload(); }}>{t("jobs.retry")}</Button>
              <Button size="sm" variant="danger" icon={Ban} disabled={!can("operator") || !["queued", "running", "retry_scheduled"].includes(j.status)} pending={pending === "cancel"} onClick={async () => { await run("cancel", "post", `/jobs/${id}/cancel`, {}, t("jobs.cancelled")); onChanged(); void job.reload(); }}>{t("action.cancel")}</Button>
            </div>
            <details className="tech-details"><summary>{t("state.techDetails")}</summary>
              <div className="stack-sm" style={{ marginTop: 6 }}>
                <span className="mono t-3">{j.job_type} · #{j.id}</span>
                <details className="raw"><summary>{t("jobs.payload")}</summary><pre>{JSON.stringify(j.payload, null, 2)}</pre></details>
                {j.result !== null && j.result !== undefined ? <details className="raw"><summary>{t("jobs.result")}</summary><pre>{JSON.stringify(j.result, null, 2)}</pre></details> : null}
              </div>
            </details>
            <div>
              <span className="eyebrow">{t("action.logs")}</span>
              {j.logs_truncated ? <p className="t-3" style={{ fontSize: 14 }}>{t("jobs.logsTruncated")}</p> : null}
              {j.logs.length ? <pre className="payload-preview" style={{ marginTop: 6 }}>{j.logs.map((l) => `${l.ts}  ${l.level.toUpperCase().padEnd(7)} ${l.message}`).join("\n")}</pre> : <p className="t-3" style={{ fontSize: 14 }}>{t("jobs.noLogs")}</p>}
            </div>
          </div>
        )}
      </ResourceView>
    </Card>
  );
}

function EventsFeed() {
  const { t, dateTime, locale } = useI18n();
  const live = useLiveStatus();
  const recent = useResource<Array<{ id: number; topic: string; instance_id: string | null; payload: unknown; ts: string }>>("/events/recent", { limit: 100 });
  const [liveRows, setLiveRows] = useState<CoreEvent[]>([]);
  useCoreEvents("", (e) => setLiveRows((rows) => [e, ...rows].slice(0, 100)));
  useEffect(() => setLiveRows([]), [recent.data]);
  const all = [...liveRows.map((e, i) => ({ id: e.id ?? -i, topic: e.topic, instance_id: e.instance_id ?? null, payload: e.payload, ts: e.ts ?? new Date().toISOString() })), ...(recent.data ?? [])];
  return (
    <Card title={t("jobs.tab.events")} icon={Radio} sub={t(`live.${live}`)} flush>
      <ResourceView resource={recent} endpoint="GET /api/v1/events/recent" isEmpty={() => all.length === 0} empty={<StateView kind="empty" compact />}>
        {() => (
          <div className="table-wrap" style={{ maxHeight: 640 }}>
            <table className="table table--compact table--stack">
              <thead><tr><th>{t("audit.when")}</th><th>{t("jobs.topic")}</th><th>{t("library.instance")}</th><th>{t("state.techDetails")}</th></tr></thead>
              <tbody>{all.map((e) => (
                <tr key={`${e.id}-${e.ts}`}><td className="cell-primary num">{dateTime(e.ts)}</td><td data-label={t("jobs.topic")}>{eventLabel(e.topic, locale)}</td><td data-label={t("library.instance")}>{e.instance_id ? instanceLabel(e.instance_id, null, locale) : "—"}</td><td data-label={t("state.techDetails")} style={{ maxWidth: 520 }}><details className="tech-details"><summary>{t("state.techDetails")}</summary><span className="mono t-2" style={{ overflowWrap: "anywhere", fontSize: 14 }}>{JSON.stringify(e.payload)}</span></details></td></tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </ResourceView>
    </Card>
  );
}

function InstanceSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useI18n();
  const instances = useResource<Instance[]>("/instances");
  return (
    <select className="select" style={{ width: "auto" }} aria-label={t("library.instance")} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{t("media.all")}</option>
      {(instances.data ?? []).map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
    </select>
  );
}
