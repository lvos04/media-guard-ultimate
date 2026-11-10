"use client";

import { ScrollText } from "lucide-react";
import { useEffect, useRef } from "react";

import { Progress, StatusChip, type Tone } from "@/components/mg/primitives";
import type { CoreJobDetail } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents, useLiveStatus } from "@/lib/events";
import { useI18n, type MessageKey } from "@/lib/i18n";

const ACTIVE = new Set(["queued", "running", "retry_scheduled"]);
const TONE: Record<string, Tone> = { queued: "neutral", running: "accent", retry_scheduled: "warning", failed: "danger", succeeded: "success", cancelled: "neutral" };

/**
 * Live view of one core job (`GET /api/v1/jobs/{id}`): status, progress and logs. Polls every
 * 1.5 s while active and also refreshes on SSE `job.*` events; calls `onFinished` once when the
 * job leaves the active states (so the caller can reload the state that the job wrote).
 */
export function JobProgress({ jobId, onFinished, compact }: { jobId: number; onFinished?: (job: CoreJobDetail) => void; compact?: boolean }) {
  const { t, pct, time } = useI18n();
  const job = useResource<CoreJobDetail>(`/jobs/${jobId}`);
  const active = job.data ? ACTIVE.has(job.data.status) : true;
  const { reload } = job;
  useCoreEvents("job", (e) => { if ((e.payload as { id?: number } | undefined)?.id === jobId) void reload(); });
  // SSE 'job.*' stuurt de verversing; zonder live-verbinding valt dit terug op 3 s, met op 10 s
  const live = useLiveStatus() === "live";
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => { if (!document.hidden) void reload(); }, live ? 10_000 : 3_000);
    return () => window.clearInterval(id);
  }, [active, live, reload]);
  const reported = useRef(false);
  const finishedRef = useRef(onFinished);
  useEffect(() => { finishedRef.current = onFinished; });
  useEffect(() => {
    if (job.data && !ACTIVE.has(job.data.status) && !reported.current) {
      reported.current = true;
      finishedRef.current?.(job.data);
    }
  }, [job.data]);

  const d = job.data;
  const progress = typeof d?.progress === "number" ? d.progress * 100 : null;
  const statusKey = `status.${d?.status ?? "queued"}` as MessageKey;
  return (
    <div className="job-progress stack-sm" data-testid={`job-${jobId}`}>
      <div className="row-between wrap">
        <span className="row" style={{ gap: 8 }}>
          <StatusChip tone={TONE[d?.status ?? "queued"] ?? "neutral"} pending={active}>{t(statusKey)}</StatusChip>
          <span className="t-3 mono" style={{ fontSize: 14 }}>{t("job.label", { id: jobId, type: d?.job_type ?? "…" })}</span>
        </span>
        <span className="num t-2" style={{ fontSize: 14 }}>{progress === null ? "" : pct(progress)}</span>
      </div>
      <Progress value={progress} indeterminate={active && (progress === null || progress < 6)} thin label={t("job.progress", { id: jobId })} tone={d?.status === "failed" ? "danger" : d?.status === "succeeded" ? "success" : undefined} />
      {d?.status === "queued" && !compact ? <p className="hint" style={{ margin: 0 }}>{t("job.queuedHint")}</p> : null}
      {d?.error ? <p className="t-danger" style={{ fontSize: 14, margin: 0 }}>{d.error}</p> : null}
      {d?.logs?.length ? (
        <details className="raw" open={!compact && active}>
          <summary><ScrollText className="ico ico-sm" aria-hidden="true" />{t("job.logs", { count: d.logs.length })}</summary>
          <pre>{d.logs.slice(-80).map((l) => `${time(l.ts)}  ${l.level.toUpperCase().padEnd(5)} ${l.message}`).join("\n")}</pre>
        </details>
      ) : null}
    </div>
  );
}
