"use client";

import { CircleCheck, CircleMinus, CircleX, Info, TriangleAlert } from "lucide-react";
import { createContext, useCallback, useContext, type ReactNode } from "react";

import { JobProgress } from "@/components/mg/job-progress";
import { StatusChip, type Tone } from "@/components/mg/primitives";
import { useAction } from "@/lib/api/actions";
import type { Instance } from "@/lib/api/contracts";
import { verdictTone, type MigCheck, type MigLaunch, type MigRun, type MigSource, type MigStep, type MigStepStatus } from "@/lib/api/migration";
import { useI18n, type MessageKey } from "@/lib/i18n";

export interface WizardCtx {
  run: MigRun;
  reload: () => Promise<void>;
  sources: Record<string, MigSource>;
  instances: Instance[];
  /** Targets with a confirmed mapping (the scope of steps 4–10). */
  targets: string[];
  goStep: (step: string) => void;
}
const Ctx = createContext<WizardCtx | null>(null);
export const WizardProvider = Ctx.Provider;
export function useWizard(): WizardCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWizard outside provider");
  return v;
}

const STEP_TONE: Record<MigStepStatus, Tone> = {
  pending: "neutral", queued: "neutral", running: "accent", succeeded: "success", warning: "warning", skipped: "neutral", failed: "danger", blocked: "danger",
};
export function StepStatusChip({ status }: { status: MigStepStatus | undefined }) {
  const { t } = useI18n();
  const s = status ?? "pending";
  return <StatusChip tone={STEP_TONE[s]} pending={s === "running" || s === "queued"} icon={s === "pending" ? null : undefined}>{t(`mig.stepStatus.${s}` as MessageKey)}</StatusChip>;
}

export function VerdictChip({ verdict, size }: { verdict: string | null | undefined; size?: "lg" }) {
  const { t } = useI18n();
  if (!verdict) return null;
  const key = (["go", "warn", "no_go", "info"].includes(verdict) ? `mig.verdict.${verdict}` : "mig.verdict.unknown") as MessageKey;
  return <StatusChip tone={verdictTone(verdict)} size={size}>{t(key)}</StatusChip>;
}

const CHECK_ICON = { ok: CircleCheck, warn: TriangleAlert, fail: CircleX, skipped: CircleMinus } as const;
const CHECK_CLASS = { ok: "list-icon--success", warn: "list-icon--warning", fail: "list-icon--danger", skipped: "" } as const;
export function CheckList({ checks, testId }: { checks: MigCheck[]; testId?: string }) {
  const { t } = useI18n();
  if (!checks.length) return <p className="t-3" style={{ fontSize: 14, margin: 0 }}>{t("mig.noChecks")}</p>;
  return (
    <ul className="list" data-testid={testId}>
      {checks.map((c, i) => {
        // info: alleen ter informatie (bv. versieverschil), neutraal weergeven
        const info = c.info === true;
        const Icon = info ? Info : CHECK_ICON[c.status] ?? CircleMinus;
        return (
          <li key={`${c.check}-${i}`} className="list-row" style={{ alignItems: "flex-start" }} data-check={c.check} data-check-status={info ? "info" : c.status}>
            <span className={`list-icon ${info ? "" : CHECK_CLASS[c.status] ?? ""}`}><Icon className="ico" aria-hidden="true" /><span className="sr-only">{info ? t("mig.check.info") : t(`mig.check.${c.status}` as MessageKey)}</span></span>
            <div className="list-main"><div className="list-title" style={{ whiteSpace: "normal" }}>{c.message_nl}</div><details className="tech-details"><summary>{t("state.techDetails")}</summary><span className="mono">{c.check}</span></details></div>
          </li>
        );
      })}
    </ul>
  );
}

/** Job of a step (current or last run): live progress + logs. */
export function StepJob({ step, compact }: { step: MigStep | undefined; compact?: boolean }) {
  const { reload } = useWizard();
  if (!step?.job_id) return null;
  return <JobProgress key={step.job_id} jobId={step.job_id} compact={compact ?? !(step.status === "running" || step.status === "queued")} onFinished={() => void reload()} />;
}

export function StepError({ step }: { step: MigStep | undefined }) {
  if (!step?.error || !(step.status === "failed" || step.status === "blocked")) return null;
  return <div className="inline-state inline-state--danger" role="alert"><CircleX className="ico" aria-hidden="true" /><div>{step.error}</div></div>;
}

/** POST a wizard action; reloads the run afterwards; synchronous results (wait=true) are logged in the toast. */
export function useLaunch() {
  const { run, pending } = useAction();
  const { reload } = useWizard();
  const launch = useCallback(async <T = MigLaunch,>(key: string, path: string, body: unknown, success?: string, method: "post" | "put" | "patch" = "post"): Promise<T | undefined> => {
    const r = await run<T>(key, method, path, body, success);
    await reload();
    return r;
  }, [reload, run]);
  return { launch, pending };
}

export function TargetName({ id }: { id: string | null | undefined }) {
  const { instances } = useWizard();
  if (!id) return <span className="t-3">—</span>;
  const inst = instances.find((i) => i.id === id);
  return <span className="row" style={{ gap: 6, display: "inline-flex" }}><span className="tag">{id}</span>{inst && inst.name !== id ? <span className="t-3" style={{ fontSize: 14 }}>{inst.name}</span> : null}</span>;
}

export function Section({ title, children, actions, testId }: { title: ReactNode; children: ReactNode; actions?: ReactNode; testId?: string }) {
  return (
    <section className="mig-section" data-testid={testId}>
      <div className="row-between wrap" style={{ marginBottom: 10 }}><h3 className="mig-section-title">{title}</h3>{actions ? <div className="row wrap">{actions}</div> : null}</div>
      {children}
    </section>
  );
}
