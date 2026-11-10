"use client";

import { Ban, Copy, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { errorText, fileName } from "@/components/av1/use-encoder";
import { phaseOf, PhaseProgress } from "@/components/av1/workers";
import { DangerDialog } from "@/components/mg/domain";
import { Button, InlineState, StatusChip, type Tone } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api, mutation } from "@/lib/api";
import { coreRequest } from "@/lib/api/client";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { useLive } from "@/lib/ws";
import type { EventMessage, Job } from "@/types";

export const JOB_TONE: Record<string, Tone> = {
  wacht: "neutral", bezig: "accent", verifieren: "info", klaar: "success", mislukt: "danger", afgebroken: "neutral", overgeslagen: "neutral", wacht_op_commit: "info", gecommit: "success", geweigerd: "warning", gepauzeerd: "warning",
};
const ACTIVE = new Set(["bezig", "verifieren"]);

type JobDetail = Job & { pogingen?: number; fase?: string | null; fase_gestart_op?: string | null; resultaat_pad?: string | null };

/** Door de noodstop gepauzeerd: geen fout, hervat vanzelf na opheffen (rapport 6, N1). */
export const pausedByEstop = (job: { status: string; fout?: string | null; pauze_reden?: string | null; noodstop_gepauzeerd?: boolean | number | null }) =>
  Boolean(job.noodstop_gepauzeerd) || job.status === "gepauzeerd" || ((job.status === "wacht" || job.status === "gepauzeerd") && /noodstop/i.test(`${job.pauze_reden ?? ""} ${job.fout ?? ""}`));

export function JobStatusChip({ status, job }: { status: string; job?: { status: string; fout?: string | null; pauze_reden?: string | null; noodstop_gepauzeerd?: boolean | number | null } }) {
  const { t } = useI18n();
  if (job && pausedByEstop(job)) return <StatusChip tone="warning" icon={null}>{t("estop.pausedJob")}</StatusChip>;
  return <StatusChip tone={JOB_TONE[status] ?? "neutral"} pending={ACTIVE.has(status)}>{t(`av1.queue.s.${status}` as MessageKey)}</StatusChip>;
}

async function readWholeLog(id: number): Promise<string[]> {
  const lines: string[] = [];
  let from = 0;
  for (let i = 0; i < 30; i += 1) {
    const r = await api<{ regels: string[]; volgende: number }>(`/jobs/${id}/log?vanaf=${from}`);
    lines.push(...r.regels);
    if (r.regels.length < 1000) break;
    from = r.volgende;
  }
  return lines.slice(-3000);
}

const STATUS_ORDER = ["bezig", "verifieren", "wacht", "mislukt", "afgebroken", "klaar", "overgeslagen"];

/** Eén job rechtstreeks (GET /jobs/{id}); oude engines zonder dat endpoint: zoeken in de lijst. */
async function findJob(id: number, hint?: string): Promise<JobDetail | null> {
  try {
    const one = await api<JobDetail>(`/jobs/${id}`);
    if (one && typeof one === "object" && Number(one.id) === id) return one;
  } catch (e) {
    if (!/404|niet gevonden|not found/i.test(errorText(e))) throw e;
  }
  const statuses = hint ? [hint, ...STATUS_ORDER.filter((s) => s !== hint)] : STATUS_ORDER;
  for (const status of statuses) {
    const r = await api<{ items: JobDetail[]; totaal: number }>(`/jobs?status=${status}&pagina=1&per_pagina=250`);
    const hit = r.items.find((j) => j.id === id);
    if (hit) return hit;
  }
  return null;
}

/** Links uit Problemen kunnen een MediaGuard-nummer bevatten: vertaal naar het engine-nummer (R5). */
async function resolveJob(id: number, hint?: string): Promise<JobDetail | null> {
  const direct = await findJob(id, hint);
  if (direct) return direct;
  try {
    const r = await coreRequest<{ engine_id: number | null }>(`/encoding/jobs/resolve/${id}`);
    if (r.engine_id && r.engine_id !== id) return await findJob(r.engine_id);
  } catch { /* oudere core of echt onbekend */ }
  return null;
}

function useJob(id: number, initial?: Job) {
  const [data, setData] = useState<JobDetail | null>(initial ?? null);
  const [error, setError] = useState<string | null>(null);
  const status = useRef<string | undefined>(initial?.status);
  const reload = useCallback(async () => {
    try {
      const job = await resolveJob(id, status.current);
      if (job) { status.current = job.status; setData(job); setError(null); } else setError("404");
    } catch (e) {
      setError(errorText(e));
    }
  }, [id]);
  useEffect(() => {
    void reload();
    // live voortgang komt via de websocket-tick; dit vangt alleen statuswissels op
    const t = window.setInterval(() => { if (!document.hidden) void reload(); }, 15_000);
    return () => window.clearInterval(t);
  }, [reload]);
  return { data, error, reload };
}

export function JobDrawer({ jobId, initial, onClose, onChanged }: { jobId: number; initial?: Job; onClose: () => void; onChanged?: () => void }) {
  const { t, bytes, dateTime, num } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const { tick, events, volg, stopVolgen } = useLive();
  const job = useJob(jobId, initial);
  const [lines, setLines] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [abort, setAbort] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const seen = useRef(0);
  const logRef = useRef<HTMLPreElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let stop = false;
    readWholeLog(jobId).then((l) => { if (!stop) setLines(l); }).catch(() => undefined);
    seen.current = events.length;
    volg(jobId);
    return () => { stop = true; stopVolgen(jobId); };
  }, [jobId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const fresh = events.slice(seen.current).filter((e): e is Extract<EventMessage, { type: "log" }> => e.type === "log" && e.job_id === jobId);
    seen.current = events.length;
    if (fresh.length) setLines((old) => [...old, ...fresh.map((e) => e.tekst)].slice(-3000));
  }, [events, jobId]);

  useEffect(() => {
    const el = logRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, [lines]);

  // focus alleen bij openen; een nieuwe onClose (inline) mag de focus niet uit het filterveld halen
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });
  useEffect(() => {
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeRef.current(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? lines.filter((l) => l.toLowerCase().includes(q)) : lines;
  }, [filter, lines]);

  const d = job.data;
  const live = tick?.workers.find((w) => w.job_id === jobId);
  const running = d ? ACTIVE.has(d.status) : false;

  const run = async (key: string, path: string, ok: string) => {
    setPending(key);
    try {
      await mutation(path, "POST");
      toast.push("success", ok);
      setAbort(false);
      await job.reload();
      onChanged?.();
    } catch (e) {
      toast.push("danger", t("av1.failed", { message: errorText(e) }));
    } finally {
      setPending(null);
    }
  };

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} aria-hidden="true" />
      <div className="drawer drawer--right" role="dialog" aria-modal="true" aria-label={t("av1.job.title", { id: jobId })} ref={panel} tabIndex={-1} data-testid="job-drawer">
        <div className="drawer-head">
          <div style={{ minWidth: 0 }}>
            <div className="t-3" style={{ fontSize: 14 }}>{t("av1.job.title", { id: jobId })}</div>
            <div className="media-title" style={{ fontSize: 15 }} title={d?.pad}>{fileName(d?.pad)}</div>
          </div>
          <Button size="sm" variant="ghost" iconOnly icon={X} onClick={onClose}>{t("av1.close")}</Button>
        </div>
        <div className="drawer-body">
          {!d && job.error ? <StateView kind="not_found" compact title={job.error === "404" ? t("av1.job.notFoundTitle") : t("av1.job.notFound")} body={job.error === "404" ? t("av1.job.notFoundBody", { id: jobId }) : job.error} />
            : !d ? <StateView kind="loading" compact />
              : (
                <>
                  <div className="row wrap" style={{ gap: 6 }}>
                    <JobStatusChip status={d.status} job={d} />
                    <StatusChip tone="neutral" outline>{d.baan.toUpperCase()} · {d.encoder}</StatusChip>
                  </div>
                  {running ? (
                    <PhaseProgress jobId={jobId} lane={d.baan} phase={phaseOf(live ?? d)} percent={live ? live.percent : d.percent} stale={live?.verouderd} label={fileName(d.pad)} />
                  ) : null}
                  <dl className="kv">
                    <dt>{t("av1.job.source")}</dt><dd>{bytes(d.bron_grootte)}</dd>
                    {d.resultaat_grootte ? <><dt>{t("av1.job.result")}</dt><dd>{bytes(d.resultaat_grootte)} ({signedPct(num, d.bron_grootte ? (d.resultaat_grootte / d.bron_grootte - 1) * 100 : 0)})</dd></> : null}
                    <dt>{t("av1.job.started")}</dt><dd>{dateTime(d.gestart_op)}</dd>
                    {d.klaar_op ? <><dt>{t("av1.job.finished")}</dt><dd>{dateTime(d.klaar_op)}</dd></> : null}
                    {d.pogingen ? <><dt>{t("av1.job.attempts")}</dt><dd>{d.pogingen}</dd></> : null}
                    <dt>{t("av1.queue.reason")}</dt><dd>{d.reden}</dd>
                    {d.verificatie_toelichting ? <><dt>{t("av1.job.verification")}</dt><dd>{d.verificatie_toelichting}</dd></> : null}
                  </dl>
                  {d.fout ? (pausedByEstop(d as typeof d & { pauze_reden?: string | null })
                    ? <InlineState tone="warning"><strong>{t("estop.pausedJob")}</strong><br />{d.fout}</InlineState>
                    : <InlineState tone="danger"><strong>{d.foutsoort ?? t("av1.job.error")}</strong><br /><span style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{d.fout}</span></InlineState>) : null}
                  {(() => {
                    // bij 'geen voortgang' stuurt de engine de laatste ffmpeg-regels mee (rapport 6, N2)
                    const x = d as typeof d & { ffmpeg_laatste_regels?: string[] | string | null; stderr_tail?: string[] | string | null };
                    const tail = x.ffmpeg_laatste_regels ?? x.stderr_tail;
                    const text = Array.isArray(tail) ? tail.join("\n") : tail;
                    return text ? (
                      <details className="tech-details" open data-testid="ffmpeg-tail"><summary>{t("stall.lastLines")}</summary>
                        <pre className="mono t-2" style={{ fontSize: 14, whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: 0 }}>{text}</pre>
                      </details>
                    ) : null;
                  })()}
                  {d.kwaliteitsadvies ? <InlineState tone="info"><strong>{t("av1.job.advice")}</strong><br />{d.kwaliteitsadvies}</InlineState> : null}
                  {can("operator") ? (
                    <div className="row wrap">
                      {running || d.status === "wacht" ? <Button variant="danger" icon={Ban} onClick={() => setAbort(true)}>{t("av1.worker.abort")}</Button> : null}
                      {d.status === "mislukt" || d.status === "afgebroken" ? <Button icon={RotateCcw} pending={pending === "retry"} onClick={() => void run("retry", `/jobs/${d?.id ?? jobId}/opnieuw`, t("av1.job.retried"))}>{t("av1.job.retry")}</Button> : null}
                    </div>
                  ) : null}
                  {d.commando ? (
                    <div className="stack-sm">
                      <div className="row-between"><span className="label" style={{ margin: 0 }}>{t("av1.job.command")}</span>
                        <Button size="sm" variant="ghost" icon={Copy} onClick={() => { void navigator.clipboard?.writeText(d.commando ?? ""); toast.push("success", t("av1.job.copied")); }}>{t("av1.job.copyCommand")}</Button></div>
                      <pre className="codeblock">{formatCommand(d.commando)}</pre>
                    </div>
                  ) : null}
                </>
              )}
          <div className="stack-sm">
            <div className="row-between wrap">
              <span className="label" style={{ margin: 0 }}>{t("av1.job.log")} {running ? <StatusChip tone="accent" pending>{t("av1.job.logLive")}</StatusChip> : null}</span>
              <input className="input input--sm" style={{ maxWidth: 200 }} value={filter} placeholder={t("av1.job.logFilter")} aria-label={t("av1.job.logFilter")} onChange={(e) => setFilter(e.target.value)} />
            </div>
            <pre className="logview" ref={logRef} aria-live="polite">{shown.length ? shown.join("\n") : t("av1.job.logEmpty")}</pre>
          </div>
        </div>
      </div>
      <DangerDialog open={abort} onClose={() => setAbort(false)} onConfirm={() => void run("abort", `/jobs/${d?.id ?? jobId}/afbreken`, t("av1.worker.aborted"))} pending={pending === "abort"} icon={Ban}
        title={t("av1.worker.abortTitle")} description={t("av1.worker.abortBody", { file: fileName(d?.pad) })} confirmLabel={t("av1.worker.abort")} />
    </>
  );
}

function signedPct(num: (v: number, d?: number) => string, value: number): string {
  return `${value < 0 ? "−" : "+"}${num(Math.abs(value))}%`;
}

function formatCommand(raw: string): string {
  try {
    const args = JSON.parse(raw) as unknown;
    if (Array.isArray(args)) return args.map((a) => (/[\s"']/.test(String(a)) ? JSON.stringify(String(a)) : String(a))).join(" ");
  } catch {
    // al leesbare tekst
  }
  return raw;
}
