"use client";

import { CheckCheck, CircleCheck, LockKeyhole, Undo2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button, Card, InlineState } from "@/components/mg/primitives";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export interface ActivationJob {
  id: number;
  source_path: string;
  status: string;
  error?: string | null;
  source_snapshot?: { size?: number } | null;
  staged_output?: { size?: number } | null;
  /** waarom de activatie wacht en wanneer MediaGuard het zelf opnieuw probeert (rapport 6, N4) */
  retry_at?: string | null;
  next_attempt_at?: string | null;
  deferred?: { reason_nl?: string | null; retry_at?: string | null; blocked_by?: { title?: string | null; client?: string | null; since?: string | null; href?: string | null } | null } | null;
  blocked_by?: { title?: string | null; client?: string | null; since?: string | null; href?: string | null } | null;
}

const isDeferred = (j: ActivationJob) => Boolean(j.deferred || j.retry_at || j.next_attempt_at || /uitgesteld|deferred|automatisch opnieuw/i.test(j.error ?? ""));

interface Readiness { ready?: boolean; library_locked?: boolean; reasons?: string[] }

export function useAwaitingActivation() {
  const jobs = useResource<ActivationJob[]>("/encoding/jobs", { status: "awaiting_activation", limit: 200 }, 30_000);
  useCoreEvents("encoding.", () => void jobs.reload());
  return jobs;
}

/**
 * Geverifieerde AV1-encodes die nog niet in de bibliotheek staan. Normaal activeert MediaGuard
 * ze zelf (Full Auto); deze kaart laat zien waarom ze wachten en biedt één knop.
 */
export function AwaitingActivation() {
  const { t, bytes, num } = useI18n();
  const { can } = useSession();
  const jobs = useAwaitingActivation();
  const readiness = useResource<Readiness>("/recovery/readiness", undefined, 60_000);
  const { run, pending } = useAction();
  const [done, setDone] = useState<Set<number>>(new Set());
  const rows = (jobs.data ?? []).filter((j) => !done.has(j.id));
  if (!rows.length) return null;
  const saving = (j: ActivationJob) => Math.max(0, (j.source_snapshot?.size ?? 0) - (j.staged_output?.size ?? 0));
  const total = rows.reduce((s, j) => s + saving(j), 0);
  const blocked = readiness.data && !readiness.data.ready;
  const activate = async (j: ActivationJob) => {
    const r = await run(`a-${j.id}`, "post", `/encoding/jobs/${j.id}/activate`, { confirm: true }, t("act.activated"));
    if (r !== undefined) setDone((s) => new Set(s).add(j.id));
    void jobs.reload();
  };
  const reject = async (j: ActivationJob) => {
    const r = await run(`r-${j.id}`, "post", `/encoding/jobs/${j.id}/cancel`, {}, t("act.kept"));
    if (r !== undefined) setDone((s) => new Set(s).add(j.id));
    void jobs.reload();
  };
  const activateAll = async () => {
    for (const j of rows) await activate(j);
  };
  return (
    <div style={{ marginBottom: 16 }} data-testid="awaiting-activation">
      <Card title={<>{t("act.title")} <span className="chip">{num(rows.length)}</span></>} icon={CircleCheck} flush
        actions={can("operator") && !blocked && rows.length > 1
          ? <Button size="sm" variant="primary" icon={CheckCheck} pending={Boolean(pending?.startsWith("a-"))} onClick={() => void activateAll()}>{t("act.all")}</Button>
          : undefined}>
        <div className="list">
          <div className="list-row"><div className="list-main">
            <div className="list-sub">{t("act.explain", { bytes: bytes(total) })}</div>
          </div></div>
          {blocked ? (
            <div className="list-row"><div className="list-main">
              <InlineState tone="info" icon={LockKeyhole} role="status">
                <strong>{t("act.blocked")}</strong> {(readiness.data?.reasons ?? []).join(" · ")}{" "}
                {readiness.data?.library_locked ? <Link className="btn btn--sm" href="/">{t("act.unlock")}</Link> : null}
              </InlineState>
            </div></div>
          ) : null}
          {rows.map((j) => (
            <div className="list-row" key={j.id}>
              <div className="list-main">
                <div className="list-title" style={{ wordBreak: "break-word" }}>{j.source_path.split("/").pop()}</div>
                <div className="list-sub">
                  {t("act.rowSaving", { bytes: bytes(saving(j)), from: bytes(j.source_snapshot?.size ?? 0), to: bytes(j.staged_output?.size ?? 0) })}
                  {j.deferred?.reason_nl ? ` · ${j.deferred.reason_nl}` : j.error ? ` · ${j.error}` : ""}
                </div>
                {isDeferred(j) ? <DeferredInfo job={j} /> : null}
              </div>
              {can("operator") ? (
                <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
                  {/* uitgesteld = MediaGuard probeert het zelf opnieuw; de knop is dan alleen 'Nu proberen' */}
                  <Button size="sm" variant={isDeferred(j) ? "ghost" : "primary"} disabled={Boolean(blocked)} pending={pending === `a-${j.id}`} onClick={() => void activate(j)}>{isDeferred(j) ? t("activation.tryNow") : t("act.one")}</Button>
                  <Button size="sm" variant="ghost" icon={Undo2} pending={pending === `r-${j.id}`} onClick={() => void reject(j)}>{t("act.keep")}</Button>
                </span>
              ) : null}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

/** Welke download/import blokkeert en wanneer het automatisch opnieuw gaat (N4). */
function DeferredInfo({ job }: { job: ActivationJob }) {
  const { t, time, dateTime } = useI18n();
  const by = job.deferred?.blocked_by ?? job.blocked_by ?? null;
  const at = job.deferred?.retry_at ?? job.retry_at ?? job.next_attempt_at ?? null;
  return (
    <div className="list-sub" data-testid={`deferred-${job.id}`} style={{ whiteSpace: "normal" }}>
      {by?.title ? <>{by.href ? <Link className="link-more" href={by.href}>{t("activation.deferredBy", { title: by.title })}</Link> : t("activation.deferredBy", { title: by.title })}{by.client ? ` (${by.client})` : ""}{by.since ? ` · ${t("activation.deferredSince", { when: dateTime(by.since) })}` : ""} · </> : null}
      {at ? t("activation.retryAt", { time: time(at) }) : t("activation.retryAuto")}
    </div>
  );
}
