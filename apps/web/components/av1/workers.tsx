"use client";

import { Ban, Pause, Play, ScrollText, Settings2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { errorText, fileName, useEncoder } from "@/components/av1/use-encoder";
import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, InlineState, LinkButton, StatusChip, type Tone } from "@/components/mg/primitives";
import { useToast } from "@/components/mg/toast";
import { mutation } from "@/lib/api";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { plainText } from "@/lib/labels";
import { cn } from "@/lib/utils";
import type { JobsResponse, Lane, LiveWorker } from "@/types";
import { parseServerDate } from "@/lib/i18n";

/** Ordered pipeline steps; side steps (DV prep, HDR restore) belong to the encode step. */
const STEP_OF: Record<string, number> = {
  analyse: 0, proefstuk: 1, dv_voorbewerking: 1, encode: 1, opschonen: 1, hdr_terugzetten: 1,
  verifieren: 2, kwaliteit_meten: 2, klaarzetten: 3, vervangen: 3,
};
const STEPS = 4;
const PHASE_KEYS = new Set(Object.keys(STEP_OF));

export function phaseOf(worker: Pick<LiveWorker, "fase" | "status">): string {
  if (worker.fase && PHASE_KEYS.has(worker.fase)) return worker.fase;
  if (worker.status === "verifieren") return "verifieren";
  return worker.status === "bezig" ? "encode" : "unknown";
}

export function phaseLabel(t: (k: MessageKey) => string, phase: string): string {
  return t((PHASE_KEYS.has(phase) ? `av1.phase.${phase}` : "av1.phase.unknown") as MessageKey);
}

/** Re-render every `ms` so elapsed times keep running between engine ticks. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

function secondsSince(value: string | null | undefined, now: number): number | null {
  if (!value) return null;
  const t = parseServerDate(value)?.valueOf() ?? Number.NaN;
  return Number.isNaN(t) ? null : Math.max(0, Math.round((now - t) / 1000));
}

/**
 * Progress bar for one phase of one job.
 * - never moves backwards within the same job+phase (engine readings can jitter);
 * - a new phase starts fresh (striped "working" bar until the first reading arrives);
 * - width animates linearly between ticks, so it glides instead of jumping.
 */
export function PhaseProgress({ jobId, lane, phase, percent, paused, stale, label, pass = 1 }: {
  jobId: number; lane: Lane; phase: string; percent: number | null | undefined; paused?: boolean; stale?: boolean; label: string;
  /** pass van een tweepass-encode: pass 2 begint een eigen balk (T2) */
  pass?: number;
}) {
  const { t, num } = useI18n();
  // "Storing information from previous renders" (React docs): the highest value seen for this
  // job+phase, so a jittering reading never moves the bar backwards.
  const key = `${jobId}:${phase}:${pass}`;
  const [best, setBest] = useState<{ key: string; value: number }>({ key, value: 0 });
  const known = typeof percent === "number" && Number.isFinite(percent);
  const floor = best.key === key ? best.value : 0;
  const shown = known ? Math.max(floor, Math.min(100, Math.max(0, percent))) : null;
  if (best.key !== key || (shown !== null && shown !== best.value)) setBest({ key, value: shown ?? 0 });
  const indeterminate = shown === null;
  const name = phaseLabel(t, phase);
  const step = STEP_OF[phase];
  const valueText = indeterminate ? t("av1.worker.progressUnknown", { phase: name }) : t("av1.worker.progressText", { phase: name, pct: num(shown, 1) });
  return (
    <div className="phase">
      <div className="phase-head">
        <span className="phase-label">
          {name}
          {step !== undefined ? <small>· {t("av1.phase.step", { n: step + 1, total: STEPS })}</small> : null}
        </span>
        <span className="phase-pct" aria-hidden="true">{indeterminate ? t("av1.worker.measuring") : `${num(shown, shown! < 10 ? 1 : 0)}%`}</span>
      </div>
      <div
        className={cn("phase-bar", lane === "cpu" && "phase-bar--cpu", phase === "verifieren" && "phase-bar--verify",
          paused && "phase-bar--paused", stale && "phase-bar--stale", indeterminate && "phase-bar--indeterminate")}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={indeterminate ? undefined : Math.round(shown!)}
        aria-valuetext={valueText}
      >
        <span style={{ width: `${indeterminate ? 100 : shown}%` }} />
      </div>
      {step !== undefined ? (
        <div className="phase-steps" aria-hidden="true">
          {Array.from({ length: STEPS }, (_, i) => <span key={i} data-done={i < step} data-current={i === step} />)}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Verwachte besparing, alleen als die betrouwbaar is (T2): niet tijdens de eerste pass van een
 * tweepass-encode (die schrijft nog niets), niet onder 10 % voortgang en geen onmogelijke
 * waarden (≥ 98 % besparing betekent dat de uitvoer nog niet echt geschreven wordt).
 */
function projectedSaving(worker: LiveWorker, phase: string, pass: { index: number; total: number }): number | null {
  const source = worker.grootte_bron ?? 0;
  const now = worker.grootte_nu ?? 0;
  const pct = worker.percent ?? 0;
  if (phase !== "encode" || source <= 0 || now <= 0 || pct < 10) return null;
  if (pass.total > 1 && pass.index < pass.total) return null;
  const projected = now / (pct / 100);
  const saving = (1 - projected / source) * 100;
  if (saving >= 98) return null;
  return Math.max(-100, Math.min(100, saving));
}

/** Twee-pass-encoders melden per pass 0–100 %: een flinke terugval binnen dezelfde job is pass 2 (T2). */
const PASS_SEEN = new Map<number, { max: number; index: number }>();
const TWO_PASS = /aom|vp9|two.?pass/i;
export function passOf(worker: LiveWorker): { index: number; total: number } {
  if (typeof worker.pas === "number" && worker.pas > 0) return { index: worker.pas, total: Math.max(worker.pas, worker.passes ?? worker.pas) };
  const id = worker.job_id ?? -1;
  const twoPass = TWO_PASS.test(worker.encoder ?? "");
  const pct = worker.percent ?? null;
  const seen = PASS_SEEN.get(id) ?? { max: 0, index: 1 };
  if (pct !== null && worker.fase === "encode") {
    if (twoPass && seen.index === 1 && seen.max >= 40 && pct < seen.max - 30) seen.index = 2;
    seen.max = Math.max(seen.index === 2 && seen.max > 100 ? 0 : seen.max, pct);
    PASS_SEEN.set(id, seen);
  }
  return { index: seen.index, total: twoPass ? 2 : 1 };
}

/**
 * Voortgang binnen proefstuk/meten (A4): de engine meldt een deelstap (n van m) en soms een
 * percentage. Zonder percentage leiden we er een af uit de deelstap, zodat de balk niet 10–15
 * minuten op '—' blijft staan.
 */
function detailStep(worker: LiveWorker): { n: number; total: number } | null {
  if (typeof worker.fase_stap === "number" && typeof worker.fase_stappen === "number") return { n: worker.fase_stap, total: worker.fase_stappen };
  const m = /(\d+)\s*\/\s*(\d+)/.exec(worker.fase_detail ?? "");
  return m ? { n: Number(m[1]), total: Number(m[2]) } : null;
}

export function phaseStepPercent(worker: LiveWorker, phase: string): number | null | undefined {
  if (phase !== "proefstuk" && phase !== "kwaliteit_meten") return worker.percent;
  if (typeof worker.fase_percent === "number") return worker.fase_percent;
  if (typeof worker.percent === "number") return worker.percent;
  const step = detailStep(worker);
  if (step && step.total > 0) return Math.max(0, Math.min(100, ((step.n - 1) / step.total) * 100));
  return worker.percent;
}

export function phaseStepText(t: (k: MessageKey, v?: Record<string, string | number>) => string, worker: LiveWorker, phase: string): string | null {
  if (worker.fase_tekst) return worker.fase_tekst;
  if (worker.fase_detail) return worker.fase_detail;
  const step = detailStep(worker);
  if (!step || step.total <= 0) return null;
  const { n, total } = step;
  if (phase === "proefstuk") return t("phase37.sample", { n, total });
  if (phase === "kwaliteit_meten") return t("phase37.measure", { n, total });
  return t("phase37.step", { label: phaseLabel(t, phase), n, total });
}

export function WorkerSlot({ worker, onOpen }: { worker: LiveWorker; onOpen?: (jobId: number) => void }) {
  const { t, num, duration, bytes } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const now = useNow();
  const [confirm, setConfirm] = useState(false);
  const [pending, setPending] = useState(false);
  const lane = worker.baan;
  const slot = worker.slot ?? 1;
  const title = t("av1.worker.title", { n: slot });

  if (worker.job_id === null) {
    const disabled = worker.uitgeschakeld;
    const paused = worker.gepauzeerd;
    return (
      <div className={cn("worker", lane === "cpu" && "worker--cpu", "worker--idle", paused && "worker--paused", disabled && "worker--disabled")} data-testid={`worker-${lane}-${slot}`}>
        <div className="worker-top">
          <span className="worker-name">{title}</span>
          {disabled ? <StatusChip tone="neutral">{t("av1.lane.disabled")}</StatusChip>
            : paused ? <StatusChip tone="paused">{t("av1.lane.paused")}</StatusChip>
              : <StatusChip tone="neutral" icon={null}>{t("av1.worker.idle")}</StatusChip>}
        </div>
        <div className="worker-idle-text">
          {disabled ? t("av1.lane.disabledBody") : paused ? t("av1.worker.pausedShort") : t("av1.worker.idleBody")}
        </div>
      </div>
    );
  }

  const phase = phaseOf(worker);
  const elapsed = secondsSince(worker.fase_gestart_op ?? worker.gestart_op, now);
  const totalElapsed = secondsSince(worker.gestart_op, now);
  const pass = passOf(worker);
  const saving = projectedSaving(worker, phase, pass);
  // ETA voor het geheel: in pass 1 van 2 komt er nog ongeveer een volledige pass bij
  const passElapsed = secondsSince(worker.fase_gestart_op ?? worker.gestart_op, now);
  const etaTotal = worker.eta_seconden == null ? null
    : pass.total > 1 && pass.index < pass.total && passElapsed !== null ? worker.eta_seconden + passElapsed + worker.eta_seconden
      : worker.eta_seconden;
  const tone: Tone = phase === "verifieren" ? "success" : "accent";
  const speed = worker.speed ?? null;
  const name = fileName(worker.bestand);

  const abort = async () => {
    setPending(true);
    try {
      await mutation(`/jobs/${worker.job_id}/afbreken`, "POST");
      toast.push("success", t("av1.worker.aborted"));
      setConfirm(false);
    } catch (e) {
      toast.push("danger", t("av1.failed", { message: errorText(e) }));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className={cn("worker", lane === "cpu" && "worker--cpu")} data-testid={`worker-${lane}-${slot}`}>
      <div className="worker-top">
        <span className="worker-name">{title}</span>
        <span className="row wrap" style={{ gap: 6, justifyContent: "flex-end" }}>
          {worker.verouderd ? <StatusChip tone="neutral" outline title={t("av1.worker.staleHint")}>{t("av1.worker.stale")}</StatusChip> : null}
          {worker.gepauzeerd ? <StatusChip tone="paused" title={t("av1.worker.pausedBody", { reason: worker.pauze_reden ?? "—" })}>{t("av1.worker.lanePaused")}</StatusChip> : null}
          {(worker as LiveWorker & { noodstop_gepauzeerd?: boolean }).noodstop_gepauzeerd
            ? <StatusChip tone="warning" icon={Pause}>{t("estop.pausedJob")}</StatusChip>
            : <StatusChip tone={tone} pending>{phaseLabel(t, phase)}</StatusChip>}
        </span>
      </div>
      <div className="worker-file">
        <strong title={worker.bestand}>{name}</strong>
        <span>{t("av1.worker.job", { id: worker.job_id, encoder: worker.encoder ?? (lane === "gpu" ? "av1_qsv" : "libsvtav1") })}{pass.total > 1 && phase === "encode" ? ` · ${t("av1.worker.pass", { index: pass.index, total: pass.total })}` : ""}</span>
      </div>
      <PhaseProgress jobId={worker.job_id} lane={lane} phase={phase} pass={pass.index} percent={phaseStepPercent(worker, phase)} stale={worker.verouderd} label={`${title}: ${name}`} />
      {phaseStepText(t, worker, phase) ? <div className="t-2" style={{ fontSize: 14 }} data-testid="phase-step">{phaseStepText(t, worker, phase)}</div> : null}
      <div className="worker-stats">
        <div><span>{t("av1.worker.speed")}</span><strong title={speed ? t("av1.worker.fps", { fps: num(worker.fps, 1), speed: num(speed, 2) }) : undefined}>{speed ? `${num(speed, 2)}×` : worker.fps ? `${num(worker.fps, 1)} fps` : "—"}</strong></div>
        <div><span>{t("av1.worker.elapsed")}</span><strong title={elapsed !== null ? duration(elapsed) : undefined}>{duration(totalElapsed ?? elapsed)}</strong></div>
        <div><span>{t("av1.worker.eta")}</span><strong title={pass.total > 1 ? t("av1.worker.etaAllPasses") : undefined}>{phase === "encode" || phase === "opschonen" ? duration(etaTotal) : "—"}</strong></div>
        <div><span>{t("av1.worker.saving")}</span><strong className={saving === null ? undefined : saving > 0 ? "t-success" : "t-warning"} title={worker.grootte_bron ? bytes(worker.grootte_bron) : undefined}>{saving === null ? "—" : saving >= 0 ? `≈ ${num(saving)}%` : `≈ +${num(Math.abs(saving))}%`}</strong></div>
      </div>
      <div className="worker-actions">
        {onOpen ? <Button size="sm" variant="ghost" icon={ScrollText} onClick={() => onOpen(worker.job_id!)}>{t("av1.details")}</Button>
          : <LinkButton size="sm" variant="ghost" icon={ScrollText} href={`/av1/wachtrij?job=${worker.job_id}`}>{t("av1.details")}</LinkButton>}
        {can("operator") ? <Button size="sm" variant="danger" icon={Ban} onClick={() => setConfirm(true)}>{t("av1.worker.abort")}</Button> : null}
      </div>
      <DangerDialog open={confirm} onClose={() => setConfirm(false)} onConfirm={() => void abort()} pending={pending} icon={Ban}
        title={t("av1.worker.abortTitle")} description={t("av1.worker.abortBody", { file: name })} confirmLabel={t("av1.worker.abort")} />
    </div>
  );
}

/** All workers of one lane, each with its own bar. */
export function LaneSection({ lane, workers, onOpen, onChanged, status }: {
  lane: Lane; workers: LiveWorker[]; onOpen?: (jobId: number) => void; onChanged?: () => void;
  /** één statusregel voor de hele baan (engine: tick.banen[].status) */
  status?: string | null;
}) {
  const { t, locale } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const [pending, setPending] = useState(false);
  const active = workers.filter((w) => w.job_id !== null).length;
  const total = Math.max(workers[0]?.aantal ?? workers.length, active);
  const paused = workers.some((w) => w.gepauzeerd);
  const disabled = workers.length > 0 && workers.every((w) => w.uitgeschakeld);
  // vrije plek(ken) maar er wacht werk voor deze baan: laat zien wáárom er niets start (S1)
  const waiting = useEncoder<JobsResponse>(!disabled && active < total ? `/jobs?status=wacht&baan=${lane}&per_pagina=3&pagina=1` : null, 15_000);
  const blocked = (waiting.data?.items ?? []).find((j) => j.fout)?.fout ?? null;
  const waitingCount = waiting.data?.totaal ?? 0;
  const toggle = async () => {
    setPending(true);
    try {
      await mutation(`/workers/${lane}/pauzeer`, "POST");
      onChanged?.();
    } catch (e) {
      toast.push("danger", t("av1.failed", { message: errorText(e) }));
    } finally {
      setPending(false);
    }
  };
  return (
    <Card
      className="span-12"
      title={<span className="lane-title"><span className={cn("lane-dot", `lane-dot--${lane}`)} aria-hidden="true" />{t(lane === "gpu" ? "av1.lane.gpu" : "av1.lane.cpu")}<small>{t(lane === "gpu" ? "av1.lane.gpuEngine" : "av1.lane.cpuEngine")}</small></span>}
      sub={disabled ? t("av1.lane.disabled") : t("av1.lane.activeOf", { active, total })}
      actions={<>
        <Link className="link-more" href="/av1/instellingen#workers"><Settings2 className="ico ico-sm" aria-hidden="true" /> {t("av1.lane.configure")}</Link>
        {can("operator") && !disabled ? <Button size="sm" icon={paused ? Play : Pause} pending={pending} onClick={() => void toggle()}>{paused ? t("av1.lane.resume") : t("av1.lane.pause")}</Button> : null}
      </>}
    >
      {status || (paused && workers[0]?.pauze_reden) ? <div style={{ padding: "0 16px 10px" }}><InlineState tone={paused ? "warning" : "info"} role="status">{status ?? workers[0]?.pauze_reden}</InlineState></div> : null}
      {!paused && waitingCount > 0 && active < total ? (
        <div style={{ padding: "0 16px 10px" }} data-testid={`lane-blocked-${lane}`}>
          <InlineState tone="warning" role="status"><strong>{t("av1.lane.notStarting", { count: waitingCount })}</strong>{" "}{blocked ? plainText(blocked, locale) : t("av1.lane.notStartingUnknown")}</InlineState>
        </div>
      ) : null}
      <StallInfo lane={lane} workers={workers} />
      <div className="worker-grid" aria-label={t("av1.lane.aria", { lane: lane.toUpperCase() })}>
        {workers.map((w) => <WorkerSlot key={`${lane}-${w.slot ?? 0}-${w.job_id ?? "idle"}`} worker={w} onOpen={onOpen} />)}
      </div>
    </Card>
  );
}

/** Plekken die steeds vastlopen (engine-statistieken: vastlopers_12u) — rapport 6, N2. */
interface StallSummary { vastlopers_12u?: { per_plek?: Array<{ baan: string; plek?: string | null; aantal: number }>; teruggeschaald?: string[] } }
function StallInfo({ lane }: { lane: Lane; workers: LiveWorker[] }) {
  const { t, num } = useI18n();
  const stats = useEncoder<StallSummary>("/statistieken", 60_000);
  const v = stats.data?.vastlopers_12u;
  const rows = (v?.per_plek ?? []).filter((r) => r.baan === lane && r.aantal > 0);
  const reduced = (v?.teruggeschaald ?? []).includes(lane);
  if (!rows.length && !reduced) return null;
  return (
    <div style={{ padding: "0 16px 10px" }} data-testid={`lane-stalls-${lane}`}>
      <InlineState tone="warning" role="status">
        {rows.map((r, i) => (
          <span key={`${r.plek ?? i}`} style={{ display: "block" }}>{t("stall.slot", { slot: r.plek ?? `${lane}-${i + 1}`, count: num(r.aantal) })}</span>
        ))}
        {reduced ? <span style={{ display: "block" }}>{t("stall.reducedLane", { lane: lane.toUpperCase() })}</span> : null}
      </InlineState>
    </div>
  );
}
