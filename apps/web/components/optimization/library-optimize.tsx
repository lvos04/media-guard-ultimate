"use client";

import { Cpu, FlaskConical, Zap } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { DangerDialog, DistributionBar } from "@/components/mg/domain";
import { Button, Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { OptimizationPlan } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import type { MessageKey } from "@/lib/i18n";
import { codeLabel } from "@/lib/labels";
import { useSession } from "@/lib/session";

interface PlanDisplay {
  status_nl?: string; can_start?: boolean; savings_nl?: string; eta_nl?: string; eta_weeks_nl?: string; progress_nl?: Record<string, number>;
  downloads?: { bytes: number; searches: number; days: number | null; nl: string | null; confirmed: boolean; needs_confirmation: boolean };
}
const PLAN_STATUS: Record<string, [string, string]> = { dry_run: ["proefplan", "dry run"], running: ["loopt", "running"], paused: ["gepauzeerd", "paused"], completed: ["klaar", "completed"], cancelled: ["gestopt", "stopped"], failed: ["mislukt", "failed"] };
function planStatus(s: string, locale: string): string {
  const hit = PLAN_STATUS[s];
  return hit ? (locale === "en-US" ? hit[1] : hit[0]) : codeLabel(s, locale as "nl-NL");
}

/** Bibliotheek optimaliseren (vervangen/zoeken/AV1): één plek, onder Instellingen → Kwaliteit. */
export function LibraryOptimize({ params }: { params: URLSearchParams }) {
  const { t, bytes, num, dateTime, locale } = useI18n();
  const { can } = useSession();
  const plan = useResource<OptimizationPlan>("/optimization/plan");
  // downloaden (TB's, weken) is een apart besluit: standaard alleen lokaal naar AV1 omzetten (N16)
  const [withDownloads, setWithDownloads] = useState(false);
  const plans = useResource<OptimizationPlan[]>("/optimization/plans");
  useCoreEvents("optimization", () => { void plan.reload(); void plans.reload(); });
  const { run, pending } = useAction();
  const [confirm, setConfirm] = useState(false);
  const filters = {
    instance_ids: params.get("instance") ? [params.get("instance") as string] : [],
    media_types: params.get("type") ? [params.get("type") as string] : [],
    series_ids: params.get("series") ? [Number(params.get("series"))] : [],
    movie_ids: params.get("movie") ? [Number(params.get("movie"))] : [],
    season: params.get("season") ? Number(params.get("season")) : null,
  };
  const scoped = filters.instance_ids.length + filters.media_types.length + filters.series_ids.length + filters.movie_ids.length > 0;
  const p = plan.data;
  const disp = (p as (OptimizationPlan & { display?: PlanDisplay }) | null)?.display;
  const actionable = p ? (p.summary.vervangen ?? 0) + (p.summary.zoeken ?? 0) + (p.summary.AV1 ?? 0) : 0;
  const dryRun = async () => { await run("dry", "post", "/optimization/dry-run", { filters }, t("overview.dryRunDone")); void plan.reload(); void plans.reload(); };
  return (
    <div className="stack">
      <Card title={t("migration.optimizeTitle")} icon={FlaskConical} actions={<><StatusChip tone="accent">{t("migration.simulationOnly")}</StatusChip><Button size="sm" icon={FlaskConical} disabled={!can("operator")} pending={pending === "dry"} onClick={() => void dryRun()}>{scoped ? t("migration.dryRunScoped") : t("migration.dryRunAll")}</Button></>}
        footer={p ? <><span>{t("migration.planMeta", { id: p.id, status: disp?.status_nl ?? planStatus(p.status, locale), at: dateTime(p.created_at) })}</span><span className="row">
          {p.status === "running" ? <Button size="sm" pending={pending === "pause"} onClick={() => void run("pause", "post", `/optimization/plans/${p.id}/pause`, {}, t("toast.saved")).then(() => plan.reload())}>{t("action.pause")}</Button> : null}
          {p.status === "paused" ? <Button size="sm" pending={pending === "resume"} onClick={() => void run("resume", "post", `/optimization/plans/${p.id}/resume`, {}, t("toast.saved")).then(() => plan.reload())}>{t("action.resume")}</Button> : null}
          <Button size="sm" variant="primary" icon={Zap} disabled={!can("operator") || !(disp?.can_start ?? p.status === "dry_run") || actionable === 0} onClick={() => setConfirm(true)}>{t("migration.startOptimize")}</Button>
        </span></> : undefined}>
        <ResourceView resource={plan} endpoint="GET /api/v1/optimization/plan" empty={null}>
          {(data) => (
            <div className="stack">
              <DistributionBar distribution={data.summary} />
              <div className="impact" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))", margin: 0 }}>
                <div><span>{disp?.savings_nl && /^kost/i.test(disp.savings_nl) ? t("plan.costs") : t("plan.saves")}</span><strong>{disp?.savings_nl ? disp.savings_nl.replace(/^(Bespaart|Kost)\s*/i, "") : `± ${bytes(Math.abs(data.forecast.estimated_savings_bytes ?? 0))}`}</strong></div>
                <div><span>{t("migration.peakCache")}</span><strong>{bytes(data.forecast.staging_total_needed_bytes)}</strong></div>
                <div><span>{t("migration.downloadEstimate")}</span><strong>{bytes(data.forecast.download_bytes_estimate)}</strong></div>
                <div><span>{t("migration.downloadDays")}</span><strong>{data.forecast.download_days_estimate === null ? "—" : num(data.forecast.download_days_estimate, 1)}</strong></div>
              </div>
              {data.forecast.note_nl ? <p className="hint">{data.forecast.note_nl}</p> : null}
              {disp?.eta_weeks_nl ?? disp?.eta_nl ? <p className="t-2" style={{ fontSize: 14 }} data-testid="plan-eta">{disp?.eta_weeks_nl ?? disp?.eta_nl}</p> : null}
              {disp?.downloads?.needs_confirmation && p && p.status !== "dry_run" ? (
                <InlineState tone="warning" data-testid="plan-downloads-confirm">
                  <strong>{disp.downloads.nl ?? t("plan.withDownloads", { count: num(disp.downloads.searches) })}</strong> {t("plan.downloadsWaiting")}{" "}
                  <Button size="sm" variant="primary" disabled={!can("operator")} pending={pending === "dl"} onClick={() => void run("dl", "post", `/optimization/plans/${p.id}/confirm-downloads`, { confirm: true }, t("plan.downloadsConfirmed")).then(() => plan.reload())}>{t("plan.confirmDownloads")}</Button>
                </InlineState>
              ) : null}
              {Object.keys(disp?.progress_nl ?? data.progress).length ? <p className="t-2" style={{ fontSize: 14 }}>{t("migration.progress")}: {Object.entries(disp?.progress_nl ?? data.progress).map(([k, v]) => `${disp?.progress_nl ? k : codeLabel(k, locale)} ${num(v)}`).join(" · ")}</p> : null}
            </div>
          )}
        </ResourceView>
        {plan.error?.kind === "not_found" ? <StateView kind="empty" compact title={t("overview.noPlan")} body={plan.error.message} /> : null}
      </Card>
      {p ? <Av1Items planId={p.id} /> : null}
      <Card title={t("migration.plans")} flush>
        <ResourceView resource={plans} endpoint="GET /api/v1/optimization/plans" compact isEmpty={(r) => r.length === 0}>
          {(rows) => (
            <table className="table table--compact table--stack">
              <thead><tr><th>#</th><th>{t("library.col.status")}</th><th className="num">{t("dialog.impactCount")}</th><th>{t("jobs.created")}</th><th /></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.id}><td className="cell-primary num">#{r.id}</td><td data-label={t("library.col.status")}><StatusChip tone={r.status === "running" ? "accent" : r.status === "completed" ? "success" : "neutral"}>{planStatus(r.status, locale)}</StatusChip></td><td data-label={t("dialog.impactCount")} className="num">{num(r.item_count)}</td><td data-label={t("jobs.created")}>{dateTime(r.created_at)}</td>
                  <td><a className="link-more" href={`/api/v1/optimization/plans/${r.id}/export`}>{t("migration.export")}</a></td></tr>
              ))}</tbody>
            </table>
          )}
        </ResourceView>
      </Card>
      <DangerDialog open={confirm} onClose={() => setConfirm(false)} icon={Zap} pending={pending === "run"} title={t("migration.optimizeConfirmTitle", { count: num(actionable) })} description={t("migration.optimizeConfirmBody")}
        impact={[{ label: t("plan.av1Count"), value: num(p?.summary.AV1 ?? 0) }, { label: t("plan.saves"), value: p ? `± ${bytes(Math.abs(p.forecast.estimated_savings_bytes ?? 0))}` : "—" }, { label: t("migration.peakCache"), value: p ? bytes(p.forecast.staging_total_needed_bytes) : "—" }]}
        facts={[{ label: t("dialog.rollback"), value: t("migration.optimizeRollback") }]}
        confirmWord={actionable > 100 ? "OPTIMALISEER" : undefined} confirmLabel={t("migration.startOptimize")}
        onConfirm={async () => { if (!p) return; const r = await run("run", "post", "/optimization/run", { plan_id: p.id, confirm: true }, t("migration.optimizeStarted")); if (r !== undefined) { if (withDownloads) await run("dl", "post", `/optimization/plans/${p.id}/confirm-downloads`, { confirm: true }); setConfirm(false); void plan.reload(); } }}>
        {p && ((p.summary.vervangen ?? 0) + (p.summary.zoeken ?? 0)) > 0 ? (
          <label className="check-row" style={{ alignItems: "flex-start" }} data-testid="plan-with-downloads">
            <span><strong>{t("plan.withDownloads", { count: num((p.summary.vervangen ?? 0) + (p.summary.zoeken ?? 0)) })}</strong>
              <span className="t-2" style={{ display: "block", fontSize: 14 }}>{t("plan.withDownloadsHint", { size: bytes(p.forecast.download_bytes_estimate), days: p.forecast.download_days_estimate === null ? "—" : num(p.forecast.download_days_estimate, 0) })}</span></span>
            <input type="checkbox" className="check" checked={withDownloads} onChange={(e) => setWithDownloads(e.target.checked)} />
          </label>
        ) : null}
      </DangerDialog>
    </div>
  );
}

interface PlanItem { id: number; title: string | null; path: string | null; status: string; encode: { id: number; status: string; encoder_job_id: string | null } | null }

const ITEM_TONE: Record<string, "neutral" | "accent" | "success" | "warning" | "danger"> = {
  planned: "neutral", encode_submitted: "accent", encode_skipped: "neutral", blocked: "warning", failed: "danger", done: "success",
};
const ENC_TONE: Record<string, "neutral" | "accent" | "success" | "warning" | "danger"> = {
  committed: "success", rejected: "danger", failed: "danger", running: "accent", verifying: "accent", awaiting_commit: "warning",
};

/** Welke items van dit plan naar AV1 gaan, en hoe ver elk is (met link naar de wachtrij). */
function Av1Items({ planId }: { planId: number }) {
  const { t, locale } = useI18n();
  const res = useResource<{ items: PlanItem[] }>("/optimization/plan", { plan_id: planId, bucket: "AV1", limit: 500 });
  useCoreEvents("encoding.", () => void res.reload());
  const itemLabel = (s: string) => {
    const key = `av1.plan.st.${s}` as MessageKey;
    const v = t(key);
    return v === key ? codeLabel(s, locale) : v;
  };
  return (
    <div id="av1-items" data-testid="plan-av1-items">
      <Card title={t("av1.plan.items")} icon={Cpu} flush>
        <ResourceView resource={res} endpoint="GET /api/v1/optimization/plan?bucket=AV1" compact isEmpty={(r) => r.items.length === 0}
          empty={<StateView kind="empty" compact title={t("av1.plan.itemsEmpty")} />}>
          {(data) => (
            <table className="table table--compact table--stack">
              <thead><tr><th>{t("av1.plan.col.title")}</th><th>{t("av1.plan.col.status")}</th><th /></tr></thead>
              <tbody>{data.items.map((it) => (
                <tr key={it.id}>
                  <td className="cell-primary" style={{ wordBreak: "break-word" }}>{it.title || (it.path ?? "").split("/").pop() || `#${it.id}`}</td>
                  <td data-label={t("av1.plan.col.status")}>
                    {it.encode
                      ? <StatusChip tone={ENC_TONE[it.encode.status] ?? "accent"}>{codeLabel(it.encode.status, locale)}</StatusChip>
                      : <StatusChip tone={ITEM_TONE[it.status] ?? "neutral"}>{itemLabel(it.status)}</StatusChip>}
                  </td>
                  <td>{it.encode?.encoder_job_id ? <Link className="link-more" href={`/av1/wachtrij?job=${it.encode.encoder_job_id}`}>{t("av1.plan.toQueue")}</Link> : null}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </ResourceView>
      </Card>
    </div>
  );
}
