"use client";

import { Ban, ChevronLeft, ChevronRight, Cpu, MonitorPlay, Moon, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { JobDrawer, JobStatusChip } from "@/components/av1/job-drawer";
import { errorText, fileName, folderOf, useEncoder } from "@/components/av1/use-encoder";
import { phaseOf, phaseLabel } from "@/components/av1/workers";
import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, InlineState, Progress, Segmented, StatusChip } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { mutation } from "@/lib/api";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useResource } from "@/lib/api/hooks";
import { useSession } from "@/lib/session";
import { useLive } from "@/lib/ws";
import type { ErrorGroup, Job, JobsResponse, Lane } from "@/types";

const PER_PAGE = 50;

interface EngineState { engine_id: number; core_id: number; core_status: string; status_nl: string | null; rolled_back: boolean; superseded_by: number | null; resolved: boolean }
const STATUSES = ["", "wacht", "bezig", "verifieren", "mislukt", "klaar", "afgebroken"] as const;
type StatusFilter = (typeof STATUSES)[number];

function QueuePage() {
  const { t, bytes, num, relative, duration } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const router = useRouter();
  const params = useSearchParams();
  const { tick } = useLive();
  const status = (STATUSES as readonly string[]).includes(params.get("status") ?? "") ? (params.get("status") ?? "") as StatusFilter : "";
  const jobParam = Number(params.get("job"));
  const [lane, setLane] = useState("");
  const [encoder, setEncoder] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState<number | null>(Number.isFinite(jobParam) && jobParam > 0 ? jobParam : null);
  const [abortMany, setAbortMany] = useState(false);
  const [pending, setPending] = useState<string | null>(null);

  // de engine sorteert zelf: lopend en wachtend eerst, daarna de nieuwste (rapport 4, S6)
  const serverPage = () => page;
  const queryFor = (st: string, perPage = PER_PAGE, fixedPage?: number) => {
    const q = new URLSearchParams({ pagina: String(fixedPage ?? serverPage()), per_pagina: String(perPage) });
    if (st) q.set("status", st);
    if (lane) q.set("baan", lane);
    if (encoder) q.set("encoder", encoder);
    return `/jobs?${q}`;
  };
  const jobs = useEncoder<JobsResponse>(queryFor(status), 10_000);
  // op de eerste pagina van 'Alle' staan lopende en wachtende jobs altijd bovenaan
  const pinActive = status === "" && page === 1;
  const activeJobs = useEncoder<JobsResponse>(pinActive ? queryFor("bezig", 20, 1) : null, 10_000);
  const verifyingJobs = useEncoder<JobsResponse>(pinActive ? queryFor("verifieren", 20, 1) : null, 10_000);
  const waitingJobs = useEncoder<JobsResponse>(pinActive ? queryFor("wacht", 30, 1) : null, 10_000);
  // 'Mislukt' toont ook wat niet geactiveerd werd (status geweigerd): dat is voor jou ook mislukt (N3)
  const refused = useEncoder<JobsResponse>(status === "mislukt" ? queryFor("geweigerd") : null, 10_000);
  const errors = useEncoder<{ groepen: ErrorGroup[] }>(status === "mislukt" ? "/fouten" : null);
  // MediaGuard weet meer dan de engine: opgevolgd door een latere poging, of teruggezet (R4/R21)
  const engineStates = useResource<{ items: EngineState[] }>("/encoding/engine-states", undefined, 30_000, { liveSlowdown: true });
  const stateOf = new Map((engineStates.data?.items ?? []).map((st) => [st.engine_id, st]));
  const [showSuperseded, setShowSuperseded] = useState(false);
  const pinned = [...(activeJobs.data?.items ?? []), ...(verifyingJobs.data?.items ?? []), ...(waitingJobs.data?.items ?? [])];
  const pinnedIds = new Set(pinned.map((j) => j.id));
  const rank = (j: Job) => (j.status === "bezig" ? 0 : j.status === "verifieren" ? 1 : j.status === "wacht" ? 2 : 3);
  const allRows = [...pinned.sort((a, b) => rank(a) - rank(b) || b.id - a.id),
    ...[...(jobs.data?.items ?? []), ...(refused.data?.items ?? [])].filter((j) => !pinnedIds.has(j.id)).sort((a, b) => b.id - a.id)];
  const supersededCount = allRows.filter((j) => stateOf.get(j.id)?.superseded_by != null).length;
  const rows = showSuperseded ? allRows : allRows.filter((j) => stateOf.get(j.id)?.superseded_by == null);
  const total = (jobs.data?.totaal ?? 0) + (refused.data?.totaal ?? 0);
  const pages = Math.max(1, Math.ceil(total / PER_PAGE));

  useEffect(() => { setSelected(new Set()); setPage(1); }, [status, lane, encoder]);

  const setStatus = (value: string) => {
    const q = new URLSearchParams(params.toString());
    if (value) q.set("status", value); else q.delete("status");
    q.delete("job");
    router.replace(`/av1/wachtrij${q.toString() ? `?${q}` : ""}`);
  };
  const openJob = (id: number | null) => {
    setOpen(id);
    const q = new URLSearchParams(params.toString());
    if (id) q.set("job", String(id)); else q.delete("job");
    router.replace(`/av1/wachtrij${q.toString() ? `?${q}` : ""}`, { scroll: false });
  };

  const counts = tick?.wachtrij;
  // nachtvenster en grens voor 'lange' jobs uit de uitvoeringsafstelling (M9)
  const tuning = useEncoder<{ items: Array<{ sleutel: string; waarde: number | boolean }> }>("/afstelling");
  const knob = (k: string) => tuning.data?.items?.find((i) => i.sleutel === k)?.waarde;
  const night = { on: knob("lange_jobs_alleen_nacht") !== false && tuning.data !== null, limit: Number(knob("lange_job_uren") ?? 6) * 3600, start: Number(knob("nacht_start") ?? 1), end: Number(knob("nacht_einde") ?? 7) };
  const live = new Map((tick?.workers ?? []).filter((w) => w.job_id !== null).map((w) => [w.job_id!, w]));
  const chosen = rows.filter((j) => selected.has(j.id));
  const allOnPage = rows.length > 0 && rows.every((j) => selected.has(j.id));

  const bulk = async (key: string, action: (job: Job) => Promise<unknown> | null, done: string) => {
    setPending(key);
    try {
      const work = chosen.map(action).filter((p): p is Promise<unknown> => p !== null);
      await Promise.all(work);
      toast.push("success", done.replace("{count}", String(work.length)));
      setSelected(new Set());
      setAbortMany(false);
      await jobs.reload();
    } catch (e) {
      toast.push("danger", t("av1.failed", { message: errorText(e) }));
    } finally {
      setPending(null);
    }
  };
  const moveTo = (target: Lane) => bulk(`lane-${target}`, (j) => (j.status === "wacht" ? mutation(`/jobs/${j.id}`, "PATCH", { baan: target }) : null),
    t("av1.queue.laneMoved", { count: "{count}", lane: target.toUpperCase() }));

  const statusOptions = STATUSES.map((s) => ({
    value: s,
    label: t((s ? `av1.queue.s.${s}` : "av1.queue.s.all") as MessageKey),
    count: s === "wacht" ? counts?.wachtend : s === "bezig" ? (tick ? Math.max(counts?.bezig ?? 0, live.size) : counts?.bezig) : s === "mislukt" ? (status === "mislukt" ? total : counts?.fouten) : undefined,
  }));

  return (
    <div className="stack">
      <InlineState tone="info">{t("av1.queue.decidedBy")} <Link className="link-more" href="/kwaliteit?tab=optimaliseren#av1-items">{t("av1.queue.plans")}</Link></InlineState>
      <div className="av1-filters">
        <Segmented label={t("av1.queue.status")} value={status} options={statusOptions} onChange={setStatus} />
        <select className="select input--sm" style={{ width: "auto" }} aria-label={t("av1.queue.lane")} value={lane} onChange={(e) => setLane(e.target.value)}>
          <option value="">{t("av1.queue.allLanes")}</option><option value="gpu">GPU</option><option value="cpu">CPU</option>
        </select>
        <select className="select input--sm" style={{ width: "auto" }} aria-label={t("av1.queue.encoder")} value={encoder} onChange={(e) => setEncoder(e.target.value)}>
          <option value="">{t("av1.queue.allEncoders")}</option><option value="av1_qsv">av1_qsv</option><option value="libsvtav1">libsvtav1</option>
        </select>
        <span className="t-3" style={{ fontSize: 14 }}>{t("av1.queue.count", { count: num(total) })}</span>
      </div>

      {status === "mislukt" && errors.data?.groepen.length ? (
        <Card title={t("av1.queue.errorGroups")} sub={t("av1.queue.errorGroupsSub")} flush>
          <div className="list">
            {errors.data.groepen.map((g) => (
              <div className="list-row" key={`${g.foutsoort}:${g.fout}`}>
                <span className="list-icon list-icon--danger"><Ban className="ico" aria-hidden="true" /></span>
                <div className="list-main"><div className="list-title">{g.foutsoort}</div><div className="list-sub" title={g.fout ?? ""}>{g.fout}</div></div>
                <div className="list-aside"><StatusChip tone="danger" icon={null}>{t("av1.queue.errorCount", { count: g.aantal })}</StatusChip><span className="t-3 hide-md" style={{ fontSize: 14 }}>{t("av1.queue.lastSeen", { when: relative(g.laatst) })}</span></div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      <Card title={t("av1.queue.title")} flush
        actions={supersededCount ? <Button size="sm" variant="ghost" onClick={() => setShowSuperseded((v) => !v)}>{showSuperseded ? t("av1.queue.hideSuperseded") : t("av1.queue.showSuperseded")}</Button> : undefined}>
        {supersededCount && !showSuperseded ? <div className="card-body t-3" style={{ fontSize: 14, paddingBottom: 0 }} data-testid="superseded-note">{t("av1.queue.superseded", { count: num(supersededCount) })}</div> : null}
        {chosen.length && can("operator") ? (
          <div className="bulkbar" role="toolbar" aria-label={t("av1.queue.selected", { count: chosen.length })}>
            <strong>{t("av1.queue.selected", { count: chosen.length })}</strong>
            <Button size="sm" icon={MonitorPlay} pending={pending === "lane-gpu"} onClick={() => void moveTo("gpu")}>{t("av1.queue.toGpu")}</Button>
            <Button size="sm" icon={Cpu} pending={pending === "lane-cpu"} onClick={() => void moveTo("cpu")}>{t("av1.queue.toCpu")}</Button>
            {chosen.some((j) => j.status === "mislukt" || j.status === "afgebroken") ? (
              <Button size="sm" icon={RotateCcw} pending={pending === "retry"} onClick={() => void bulk("retry", (j) => (j.status === "mislukt" || j.status === "afgebroken" ? mutation(`/jobs/${j.id}/opnieuw`, "POST") : null), t("av1.job.retried"))}>{t("av1.job.retry")}</Button>
            ) : null}
            <Button size="sm" variant="danger" icon={Ban} onClick={() => setAbortMany(true)}>{t("av1.queue.abortSelected")}</Button>
          </div>
        ) : null}
        {!jobs.data && jobs.error ? <div className="card-body"><StateView kind="offline" compact title={t("av1.engineDownTitle")} body={<>{jobs.error ? <>{jobs.error} </> : null}{t("av1.engineDownBody")}</>} /></div>
          : !jobs.data ? <div className="card-body"><StateView kind="loading" /></div>
            : rows.length === 0 ? <div className="card-body"><StateView kind="empty" compact title={t("av1.queue.empty")} body={t("av1.queue.emptyBody")} /></div>
              : (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th style={{ width: 52 }}>{can("operator") ? <label className="check-hit"><input type="checkbox" className="check" aria-label={t("av1.queue.selectAll")} checked={allOnPage} onChange={() => setSelected(allOnPage ? new Set() : new Set(rows.map((j) => j.id)))} /></label> : null}</th>
                        <th>{t("av1.queue.file")}</th>
                        <th className="hide-md">{t("av1.queue.status")}</th>
                        <th className="hide-md">{t("av1.queue.lane")}</th>
                        <th className="hide-md">{t("av1.queue.reason")}</th>
                        <th className="num hide-md">{t("av1.queue.size")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((job) => {
                        const w = live.get(job.id);
                        const running = job.status === "bezig" || job.status === "verifieren";
                        const pct = w?.percent ?? (running ? null : job.percent);
                        const statusCell = (
                          <div className="stack-sm" style={{ gap: 4 }}>
                            {stateOf.get(job.id)?.rolled_back ? <StatusChip tone="warning" icon={null}>{t("av1.queue.s.teruggezet")}</StatusChip>
                              : stateOf.get(job.id)?.superseded_by != null ? <StatusChip tone="neutral" icon={null}>{t("av1.queue.supersededChip")}</StatusChip>
                                : <JobStatusChip status={job.status} job={job as typeof job & { pauze_reden?: string | null }} />}
                            {job.status === "wacht" && typeof job.verwachte_duur_s === "number" ? (
                              <span className="row wrap" style={{ gap: 4 }}>
                                <span className="t-3" style={{ fontSize: 14 }} title={t("av1.queue.estimateTitle")}>{t("av1.queue.estimate", { duration: job.verwachte_duur_nl ?? duration(job.verwachte_duur_s) })}</span>
                                {night.on && job.verwachte_duur_s > night.limit ? <StatusChip tone="neutral" icon={Moon} title={t("av1.queue.nightOnlyTitle", { hours: num(night.limit / 3600, 0), start: night.start, end: night.end })}>{t("av1.queue.nightOnly")}</StatusChip> : null}
                              </span>
                            ) : null}
                            {running ? <>
                              <span className="t-3" style={{ fontSize: 14 }}>{phaseLabel(t, phaseOf(w ?? { fase: (job as Job & { fase?: string }).fase, status: job.status }))}{typeof pct === "number" ? ` · ${num(pct, 0)}%` : ""}</span>
                              <Progress thin value={pct} indeterminate={typeof pct !== "number"} tone={job.baan === "cpu" ? "cpu" : undefined} label={fileName(job.pad)} />
                            </> : null}
                          </div>
                        );
                        return (
                          <tr key={job.id} className="row-click" aria-selected={selected.has(job.id)} onClick={() => openJob(job.id)}>
                            <td onClick={(e) => e.stopPropagation()}>{can("operator") ? <label className="check-hit"><input type="checkbox" className="check" aria-label={t("av1.queue.select", { id: job.id })} checked={selected.has(job.id)} onChange={() => setSelected((old) => { const n = new Set(old); if (n.has(job.id)) n.delete(job.id); else n.add(job.id); return n; })} /></label> : null}</td>
                            <td><div className="cell-file"><strong title={job.pad}>{fileName(job.pad)}</strong><span title={folderOf(job.pad)}>#{job.id} · {folderOf(job.pad)}</span><div className="show-md" style={{ marginTop: 6 }}>{statusCell}<div className="t-3" style={{ fontSize: 14, marginTop: 4 }}>{bytes(job.bron_grootte)}</div></div></div></td>
                            <td className="hide-md" style={{ minWidth: 150 }}>{statusCell}</td>
                            <td className="hide-md"><span className={`job-lane job-lane--${job.baan}`}>{job.baan.toUpperCase()}</span><div className="t-3" style={{ fontSize: 14 }}>{job.encoder}</div></td>
                            <td className="hide-md" style={{ maxWidth: 360 }}><span className="t-2" style={{ fontSize: 14 }}>{stateOf.get(job.id)?.status_nl ?? job.fout ?? job.reden}</span></td>
                            <td className="num hide-md">{bytes(job.bron_grootte)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
        {total > PER_PAGE ? (
          <div className="pager">
            <span>{t("av1.queue.page", { page, pages })}</span>
            <span className="row">
              <Button size="sm" icon={ChevronLeft} disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>{t("av1.queue.prev")}</Button>
              <Button size="sm" icon={ChevronRight} disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>{t("av1.queue.next")}</Button>
            </span>
          </div>
        ) : null}
      </Card>

      <DangerDialog open={abortMany} onClose={() => setAbortMany(false)} onConfirm={() => void bulk("abort", (j) => mutation(`/jobs/${j.id}/afbreken`, "POST"), t("av1.queue.abortedMany", { count: "{count}" }))}
        pending={pending === "abort"} icon={Ban} title={t("av1.queue.abortManyTitle", { count: chosen.length })} description={t("av1.queue.abortManyBody")} confirmLabel={t("av1.queue.abortSelected")} />
      {open !== null ? <JobDrawer jobId={open} initial={rows.find((j) => j.id === open)} onClose={() => openJob(null)} onChanged={() => void jobs.reload()} /> : null}
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<StateView kind="loading" />}><QueuePage /></Suspense>;
}
