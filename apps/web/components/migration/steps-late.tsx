"use client";

import { Activity, ClipboardCheck, FlaskConical, GitCompare, Lock, Radar, RotateCcw, Route, ScanSearch, ShieldCheck, Zap } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { DistributionBar, Timeline } from "@/components/mg/domain";
import { JobProgress } from "@/components/mg/job-progress";
import { Button, Field, InlineState, Modal, StatusChip } from "@/components/mg/primitives";
import { CheckList, Section, StepError, StepJob, StepStatusChip, TargetName, VerdictChip, useLaunch, useWizard } from "@/components/migration/shared";
import { PathPreviewView, StepCard } from "@/components/migration/wizard";
import { api, ApiError } from "@/lib/api/client";
import type { CoreJob } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { latestReport, runStep, type ImportPreviewTarget, type MigCheck, type MigCutover, type MigMismatch, type MigReport, type MigRun } from "@/lib/api/migration";
import { useCoreEvents } from "@/lib/events";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { parseServerDate } from "@/lib/i18n";

function NoTargets() {
  const { t } = useI18n();
  const { goStep } = useWizard();
  return <InlineState tone="warning">{t("mig.needMapping")} <button type="button" className="link-more" onClick={() => goStep("mapping")}>{t("mig.toMapping")}</button></InlineState>;
}

const isActive = (status?: string) => status === "running" || status === "queued";

// ---------------------------------------------------------------- 4 shadow import
export function ShadowImportStep() {
  const { t, relative } = useI18n();
  const { run, targets } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const prev = latestReport(run, "import_preview");
  const previews = ((prev?.summary as { targets?: ImportPreviewTarget[] } | undefined)?.targets ?? []);
  return (
    <StepCard step="shadow_import" icon={Radar} actions={targets.length ? <Button size="sm" icon={ScanSearch} disabled={!can("operator")} pending={pending === "ip"} onClick={() => void launch("ip", "/migration/import-preview", { run_id: run.id }, t("mig.import.previewed"))}>{prev ? t("mig.import.repreview") : t("mig.import.preview")}</Button> : null}>
      {targets.length === 0 ? <NoTargets /> : (
        <>
          {prev ? <span className="t-3" style={{ fontSize: 14 }}>{t("mig.import.previewMeta", { age: relative(prev.created_at) })}</span> : <InlineState tone="info">{t("mig.import.previewFirst")}</InlineState>}
          {targets.map((target) => <ShadowTarget key={target} target={target} preview={previews.find((p) => p.target_instance_id === target)} />)}
          {targets.some((tg) => ["succeeded", "warning"].includes(runStep(run, "shadow_import", tg)?.status ?? "")) ? (
            <InlineState tone="info">{t("mig.toProvisioning")} <Link className="link-more" href="/kwaliteit?tab=instellingen">{t("mig.openProvisioning")}</Link></InlineState>
          ) : null}
        </>
      )}
    </StepCard>
  );
}

function ShadowTarget({ target, preview }: { target: string; preview?: ImportPreviewTarget }) {
  const { t, num } = useI18n();
  const { run } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const st = runStep(run, "shadow_import", target);
  const [open, setOpen] = useState(false);
  const [accept, setAccept] = useState(Boolean(run.options.accept_unresolved_paths));
  const result = (st?.result ?? {}) as { mode?: string; mode_reason?: string; link?: { expected_with_file: number; linked: number; missing: number }; credentials_needed?: unknown[]; shadow_enforced?: string[]; message_nl?: string };
  const blocking = Boolean(preview?.path_preview?.blocking);
  return (
    <Section title={<span className="row" style={{ gap: 8 }}><TargetName id={target} /> <StepStatusChip status={st?.status} /></span>} testId={`shadow-${target}`}
      actions={<Button size="sm" variant="primary" icon={Radar} disabled={!can("admin") || isActive(st?.status)} onClick={() => setOpen(true)}>{st?.status && st.status !== "pending" ? t("mig.import.rerun") : t("mig.import.start")}</Button>}>
      <div className="stack-sm">
        {preview ? (
          <>
            <div className="impact" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))", margin: 0 }}>
              <div><span>{t("mig.import.mode")}</span><strong>{t(`mig.mode.${preview.mode}` as MessageKey)}</strong></div>
              <div><span>{t("mig.import.items")}</span><strong>{num(preview.items)}</strong></div>
              <div><span>{t("mig.import.indexers")}</span><strong>{num(preview.indexers)} / {num(preview.download_clients)}</strong></div>
              <div><span>{t("mig.import.existing")}</span><strong>{preview.target_existing_items === null ? "—" : num(preview.target_existing_items)}</strong></div>
            </div>
            <p className="t-2" style={{ fontSize: 14, margin: 0 }}>{preview.writes_nl} <span className="t-3">({preview.mode_reason})</span></p>
            {preview.credentials_needed.length ? <InlineState tone="warning">{t("mig.import.credentials", { names: preview.credentials_needed.map((c) => c.name).join(", ") })}</InlineState> : null}
            {preview.path_preview ? <details className="raw" style={{ border: "1px solid var(--mg-border-soft)", borderRadius: "var(--mg-r)" }}><summary>{t("mig.paths.title")}</summary><div style={{ padding: 12 }}><PathPreviewView p={preview.path_preview} /></div></details> : null}
          </>
        ) : null}
        {result.link ? (
          <div className="impact" style={{ margin: 0 }}>
            <div><span>{t("mig.link.expected")}</span><strong>{num(result.link.expected_with_file)}</strong></div>
            <div><span>{t("mig.link.linked")}</span><strong className="t-success">{num(result.link.linked)}</strong></div>
            <div><span>{t("mig.link.missing")}</span><strong className={result.link.missing ? "t-danger" : undefined}>{num(result.link.missing)}</strong></div>
          </div>
        ) : null}
        {result.shadow_enforced?.length ? <InlineState tone="warning">{t("mig.import.enforced", { list: result.shadow_enforced.join(", ") })}</InlineState> : null}
        {result.message_nl ? <InlineState tone="info">{result.message_nl}</InlineState> : null}
        <StepJob step={st} />
        <StepError step={st} />
      </div>
      <Modal open={open} onClose={() => setOpen(false)} icon={Radar} title={t("mig.import.confirmTitle", { target })} description={t("mig.import.confirmBody")}
        footer={<><Button onClick={() => setOpen(false)} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" disabled={blocking && !accept} pending={pending === `si-${target}`}
          onClick={() => void launch(`si-${target}`, `/migration/runs/${run.id}/shadow-import`, { target_instance_id: target, accept_unresolved_paths: accept }, t("mig.queued")).then((r) => { if (r !== undefined) setOpen(false); })}>{t("mig.import.start")}</Button></>}>
        <dl className="kv">
          <dt>{t("mig.import.writes")}</dt><dd>{preview?.writes_nl ?? t("mig.import.writesUnknown")}</dd>
          <dt>{t("mig.import.sourceWrites")}</dt><dd>{t("mig.import.sourceUntouched")}</dd>
          <dt>{t("mig.import.shadow")}</dt><dd>{t("mig.import.shadowBody")}</dd>
        </dl>
        {blocking ? (
          <div style={{ marginTop: 12 }} className="stack-sm">
            <InlineState tone="danger">{t("mig.import.pathsBlocking", { count: preview?.path_preview?.unresolved_count ?? 0 })}</InlineState>
            <label className="choice"><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} /><span>{t("mig.opt.acceptPaths")}</span></label>
          </div>
        ) : null}
      </Modal>
    </Section>
  );
}

// ---------------------------------------------------------------- 5 verify
function liveVerdict(mismatches: MigMismatch[], accepted: Set<string>): string {
  const live = mismatches.filter((m) => !(m.acceptable && accepted.has(m.id)));
  if (live.some((m) => m.severity === "no_go")) return "no_go";
  if (live.some((m) => m.severity === "warning")) return "warn";
  return "go";
}

export function VerifyStep() {
  const { run, targets } = useWizard();
  return (
    <StepCard step="verify" icon={GitCompare}>
      {targets.length === 0 ? <NoTargets /> : targets.map((target) => <VerifyTarget key={target} target={target} run={run} />)}
    </StepCard>
  );
}

function VerifyTarget({ target, run }: { target: string; run: MigRun }) {
  const { t, num, relative, dateTime } = useI18n();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const st = runStep(run, "verify", target);
  const rep = latestReport(run, "verify", target);
  const accepted = run.accepted_mismatches[target] ?? {};
  const acceptedIds = new Set(Object.keys(accepted));
  const [delta, setDelta] = useState(true);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("");
  const mm = rep?.mismatches ?? [];
  const hard = mm.filter((m) => m.severity === "no_go" && !m.acceptable);
  // al geaccepteerd (door jou of automatisch, bv. iets wat de nieuwe versie niet kent) apart
  const isAccepted = (m: MigMismatch) => Boolean(m.acceptable && (acceptedIds.has(m.id) || (m as { auto_accepted?: boolean }).auto_accepted));
  const acceptedRows = mm.filter(isAccepted);
  const acceptable = mm.filter((m) => m.acceptable && m.severity === "no_go" && !isAccepted(m));
  const warnings = mm.filter((m) => m.acceptable && m.severity !== "no_go" && !isAccepted(m));
  const live = rep ? liveVerdict(mm, acceptedIds) : null;
  const summary = rep?.summary as { source?: Record<string, number>; target?: Record<string, number>; compared_against?: string } | undefined;
  const toggle = (id: string) => setPicked((old) => { const n = new Set(old); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const accept = async () => {
    const r = await launch(`acc-${target}`, `/migration/runs/${run.id}/accept-mismatches`, { target_instance_id: target, mismatch_ids: [...picked], reason: reason.trim() }, t("mig.verify.acceptedToast"));
    if (r !== undefined) { setPicked(new Set()); setReason(""); }
  };
  const list = (rows: MigMismatch[], selectable: boolean) => (
    <ul className="list">{rows.map((m) => (
      <li key={m.id} className="list-row" style={{ alignItems: "flex-start" }} data-mismatch={m.id}>
        {selectable ? <input type="checkbox" aria-label={t("mig.verify.pick", { message: m.message_nl })} checked={picked.has(m.id) || acceptedIds.has(m.id)} disabled={acceptedIds.has(m.id) || !can("admin")} onChange={() => toggle(m.id)} style={{ marginTop: 3 }} /> : null}
        <div className="list-main">
          <div className="list-title" style={{ whiteSpace: "normal" }}>{m.message_nl}</div>
          <div className="list-sub mono" style={{ whiteSpace: "normal" }}>{m.category} · {m.field} · {t("mig.verify.srcTgt", { source: JSON.stringify(m.source), target: JSON.stringify(m.target) })}</div>
          {(m as { auto_accepted?: boolean }).auto_accepted ? <div className="t-success" style={{ fontSize: 14 }}>{t("mig.verify.autoAccepted")}</div>
            : accepted[m.id] ? <div className="t-success" style={{ fontSize: 14 }}>{t("mig.verify.acceptedBy", { by: accepted[m.id].by, at: dateTime(accepted[m.id].at), reason: accepted[m.id].reason })}</div> : null}
        </div>
      </li>
    ))}</ul>
  );
  return (
    <Section title={<span className="row" style={{ gap: 8 }}><TargetName id={target} /> <StepStatusChip status={st?.status} /></span>} testId={`verify-${target}`}
      actions={<>
        <label className="choice" style={{ fontSize: 14 }}><input type="checkbox" checked={delta} onChange={(e) => setDelta(e.target.checked)} /><span>{t("mig.verify.delta")}</span></label>
        <Button size="sm" variant="primary" icon={GitCompare} disabled={!can("operator") || isActive(st?.status)} pending={pending === `vf-${target}`} onClick={() => void launch(`vf-${target}`, "/migration/instance-mapping/verify", { run_id: run.id, target_instance_id: target, sync_delta: delta }, t("mig.queued"))}>{rep ? t("mig.rerun") : t("mig.verify.run")}</Button>
      </>}>
      <div className="stack-sm">
        <StepJob step={st} />
        <StepError step={st} />
        {rep ? (
          <>
            <div className="row wrap" style={{ gap: 8 }}>
              <VerdictChip verdict={live} size="lg" />
              {live !== rep.verdict ? <span className="t-3" style={{ fontSize: 14 }}>{t("mig.verify.rerunForVerdict", { verdict: t(`mig.verdict.${rep.verdict}` as MessageKey) })}</span> : null}
              <span className="t-3" style={{ fontSize: 14 }}>{t("mig.reportMeta", { id: rep.id, age: relative(rep.created_at) })}{summary?.compared_against ? ` · ${t("mig.verify.against", { what: summary.compared_against })}` : ""}</span>
            </div>
            {summary?.source && summary.target ? (
              <div className="table-wrap">
                <table className="table table--compact table--stack">
                  <thead><tr><th>{t("mig.verify.metric")}</th><th className="num">{t("mig.verify.source")}</th><th className="num">{t("mig.verify.target")}</th></tr></thead>
                  <tbody>{Object.keys(summary.source).map((k) => {
                    const a = summary.source?.[k], b = summary.target?.[k];
                    return <tr key={k}><td className="cell-primary">{t(`mig.metric.${k}` as MessageKey)}</td><td data-label={t("mig.verify.source")} className="num">{num(a)}</td><td data-label={t("mig.verify.target")} className={a !== b ? "num t-warning" : "num"}>{num(b)}</td></tr>;
                  })}</tbody>
                </table>
              </div>
            ) : null}
            {(rep.mismatch_count ?? mm.length) > mm.length ? <p className="hint">{t("mig.verify.truncated", { shown: num(mm.length), total: num(rep.mismatch_count ?? 0) })}</p> : null}
            {hard.length ? <div className="stack-sm" data-testid="nogo-list"><h4 className="mig-sub t-danger">{t("mig.verify.hard", { count: hard.length })}</h4>{list(hard, false)}</div> : null}
            {acceptable.length ? <div className="stack-sm"><h4 className="mig-sub t-warning">{t("mig.verify.acceptable", { count: acceptable.length })}</h4>{list(acceptable, true)}</div> : null}
            {warnings.length ? <div className="stack-sm"><h4 className="mig-sub">{t("mig.verify.warnings", { count: warnings.length })}</h4>{list(warnings, true)}</div> : null}
            {acceptedRows.length ? (
              <details className="stack-sm" data-testid="accepted-list"><summary className="mig-sub">{t("mig.verify.acceptedGroup", { count: acceptedRows.length })}</summary>{list(acceptedRows, false)}</details>
            ) : null}
            {mm.length === 0 ? <InlineState tone="success">{t("mig.verify.noDiff")}</InlineState> : null}
            {acceptable.length + warnings.length > 0 ? (
              <div className="stack-sm accept-box">
                <Field label={t("mig.verify.reason")} htmlFor={`reason-${target}`} hint={can("admin") ? t("mig.verify.reasonHint") : t("mig.adminOnly")}>
                  <input id={`reason-${target}`} className="input" value={reason} maxLength={500} disabled={!can("admin")} onChange={(e) => setReason(e.target.value)} />
                </Field>
                <div><Button size="sm" icon={ShieldCheck} disabled={!can("admin") || picked.size === 0 || reason.trim().length < 3} pending={pending === `acc-${target}`} onClick={() => void accept()}>{t("mig.verify.accept", { count: picked.size })}</Button></div>
              </div>
            ) : null}
          </>
        ) : !isActive(st?.status) ? <InlineState tone="info">{t("mig.verify.none")}</InlineState> : null}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------- 6 scan + link report
export function ScanStep() {
  const { t, num } = useI18n();
  const { run, targets } = useWizard();
  return (
    <StepCard step="scan" icon={ScanSearch}>
      {targets.length === 0 ? <NoTargets /> : (
        <>
          <div className="table-wrap" data-testid="link-report">
            <table className="table table--compact table--stack">
              <thead><tr><th>{t("mig.col.target")}</th><th className="num">{t("mig.link.expected")}</th><th className="num">{t("mig.link.linked")}</th><th className="num">{t("mig.link.missing")}</th><th className="num">{t("mig.scan.itemsMissing")}</th><th>{t("mig.col.verdict")}</th></tr></thead>
              <tbody>{targets.map((target) => {
                const rep = latestReport(run, "scan", target);
                const s = rep?.summary as ScanSummary | undefined;
                const link = (runStep(run, "shadow_import", target)?.result as { link?: { expected_with_file: number; linked: number; missing: number } } | undefined)?.link;
                const expected = s?.files_expected ?? link?.expected_with_file;
                const found = s?.files_found ?? link?.linked;
                const missing = s?.files_missing ?? link?.missing;
                return (
                  <tr key={target}>
                    <td className="cell-primary"><TargetName id={target} /></td>
                    <td data-label={t("mig.link.expected")} className="num">{num(expected)}</td>
                    <td data-label={t("mig.link.linked")} className="num">{num(found)}</td>
                    <td data-label={t("mig.link.missing")} className={missing ? "num t-danger" : "num"}>{num(missing)}</td>
                    <td data-label={t("mig.scan.itemsMissing")} className="num">{s ? num(s.missing_items.length) : "—"}</td>
                    <td data-label={t("mig.col.verdict")}>{rep ? <VerdictChip verdict={rep.verdict} /> : <span className="t-3">—</span>}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          {targets.map((target) => <ScanTarget key={target} target={target} />)}
        </>
      )}
    </StepCard>
  );
}

interface ScanSummary { expected_items: number; found_items: number; missing_items: string[]; unknown_items: string[]; duplicates_across_instances: Array<{ ext: string; instances: string }>; wrong_scope: string[]; files_expected: number; files_found: number; files_missing: number; monitored_missing: number }

function ScanTarget({ target }: { target: string }) {
  const { t, num, relative } = useI18n();
  const { run } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const st = runStep(run, "scan", target);
  const rep = latestReport(run, "scan", target);
  const s = rep?.summary as ScanSummary | undefined;
  const [now] = useState(() => Date.now());
  // velden die core sinds rapport 2 meestuurt (optioneel: oudere rapporten hebben ze niet)
  const ext = (rep?.summary ?? {}) as { extra_items_nl?: string; files_missing_nl?: string; files_missing_ratio?: number; missing_titles?: unknown[]; wrong_scope_nl?: string };
  return (
    <Section title={<span className="row" style={{ gap: 8 }}><TargetName id={target} /> <StepStatusChip status={st?.status} /></span>}
      actions={<Button size="sm" variant="primary" icon={ScanSearch} disabled={!can("operator") || isActive(st?.status)} pending={pending === `sc-${target}`} onClick={() => void launch(`sc-${target}`, `/migration/runs/${run.id}/scan`, { target_instance_id: target }, t("mig.queued"))}>{rep ? t("mig.rerun") : t("mig.scan.run")}</Button>}>
      <div className="stack-sm">
        <StepJob step={st} />
        <StepError step={st} />
        {s && rep ? (
          <>
            <span className="row wrap t-3" style={{ fontSize: 14, gap: 6 }}>{t("mig.reportMeta", { id: rep.id, age: relative(rep.created_at) })}
              {now - (parseServerDate(rep.created_at)?.valueOf() ?? now) > 12 * 3600_000 ? <StatusChip tone="warning" icon={null} data-testid="scan-stale">{t("mig.scan.stale")}</StatusChip> : null}</span>
            {ext.extra_items_nl ? <p className="hint" style={{ margin: 0 }}>{ext.extra_items_nl}</p>
              : s.found_items > s.expected_items || s.files_found > s.files_expected ? <p className="hint" style={{ margin: 0 }}>{t("mig.scan.moreThanExpected")}</p> : null}
            {ext.files_missing_nl ? <InlineState tone={(ext.files_missing_ratio ?? 0) > 0.5 ? "danger" : "warning"}>{ext.files_missing_nl}</InlineState> : null}
            <div className="impact" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))", margin: 0 }}>
              <div><span>{t("mig.scan.items")}</span><strong>{num(s.found_items)} / {num(s.expected_items)}</strong></div>
              <div><span>{t("mig.scan.files")}</span><strong>{num(s.files_found)} / {num(s.files_expected)}</strong></div>
              <div><span>{t("mig.scan.monitoredMissing")}</span><strong>{num(s.monitored_missing)}</strong></div>
              <div><span>{t("mig.scan.unknown")}</span><strong>{num(s.unknown_items.length)}</strong></div>
            </div>
            {(ext.missing_titles ?? s.missing_items).length ? <InlineState tone="danger">{t("mig.scan.missingList", { list: (ext.missing_titles ?? s.missing_items).slice(0, 30).map((x) => scanItemName(x, t)).join(", ") })}</InlineState> : null}
            {ext.wrong_scope_nl ? <InlineState tone="info">{ext.wrong_scope_nl}</InlineState>
              : s.wrong_scope.length ? <InlineState tone="warning">{t("mig.scan.wrongScope", { list: s.wrong_scope.slice(0, 20).map((x) => scanItemName(x, t)).join(", ") })}</InlineState> : null}
            {s.duplicates_across_instances.length ? <InlineState tone="warning">{t("mig.scan.duplicates", { count: s.duplicates_across_instances.length })}</InlineState> : null}
          </>
        ) : null}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------- 7 optimization dry-run
export function OptimizationStep() {
  const { t, num } = useI18n();
  const { run, targets, goStep } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const st = runStep(run, "optimization");
  const r = (st?.result ?? {}) as { plan_id?: number; summary?: Record<string, number>; item_count?: number; message_nl?: string };
  return (
    <StepCard step="optimization" icon={FlaskConical} actions={<><StatusChip tone="accent">{t("migration.simulationOnly")}</StatusChip><Button size="sm" variant="primary" icon={FlaskConical} disabled={!can("operator") || targets.length === 0 || isActive(st?.status)} pending={pending === "opt"} onClick={() => void launch("opt", "/migration/dry-run", { run_id: run.id }, t("mig.queued"))}>{r.plan_id ? t("mig.rerun") : t("mig.opt.run")}</Button></>}>
      {targets.length === 0 ? <NoTargets /> : null}
      <StepJob step={st} />
      <StepError step={st} />
      {r.message_nl ? <InlineState tone="info">{r.message_nl}</InlineState> : null}
      {r.summary ? (
        <div className="stack-sm">
          <DistributionBar distribution={r.summary} />
          <p className="t-2" style={{ fontSize: 14, margin: 0 }}>{t("mig.opt.planMeta", { id: r.plan_id ?? "—", count: num(r.item_count) })} <button type="button" className="link-more" onClick={() => goStep("optimize-tab")}>{t("mig.opt.openPlan")}</button></p>
        </div>
      ) : null}
    </StepCard>
  );
}

// ---------------------------------------------------------------- 8 readiness
export function ReadinessStep() {
  const { targets } = useWizard();
  return (
    <StepCard step="readiness" icon={ClipboardCheck}>
      {targets.length === 0 ? <NoTargets /> : targets.map((target) => <ReadinessTarget key={target} target={target} />)}
    </StepCard>
  );
}

function ReadinessTarget({ target }: { target: string }) {
  const { t, relative } = useI18n();
  const { run } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const [webhook, setWebhook] = useState(true);
  const st = runStep(run, "readiness", target);
  const rep = latestReport(run, "readiness", target);
  return (
    <Section title={<span className="row" style={{ gap: 8 }}><TargetName id={target} /> <StepStatusChip status={st?.status} /></span>} testId={`readiness-${target}`}
      actions={<>
        <label className="choice" style={{ fontSize: 14 }}><input type="checkbox" checked={webhook} onChange={(e) => setWebhook(e.target.checked)} /><span>{t("mig.ready.webhook")}</span></label>
        <Button size="sm" variant="primary" icon={ClipboardCheck} disabled={!can("operator") || isActive(st?.status)} pending={pending === `rd-${target}`} onClick={() => void launch(`rd-${target}`, `/migration/runs/${run.id}/readiness`, { target_instance_id: target, register_webhook: webhook }, t("mig.queued"))}>{rep ? t("mig.rerun") : t("mig.ready.run")}</Button>
      </>}>
      <div className="stack-sm">
        <StepJob step={st} />
        <StepError step={st} />
        {rep ? (
          <>
            <div className="row wrap" style={{ gap: 8 }}><VerdictChip verdict={rep.verdict} /><span className="t-3" style={{ fontSize: 14 }}>{t("mig.reportMeta", { id: rep.id, age: relative(rep.created_at) })}</span></div>
            <CheckList checks={((rep.summary as { checks?: MigCheck[] }).checks ?? [])} />
          </>
        ) : null}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------- 9 cutover / activate-search / rollback
const PHASES = ["requested", "frozen", "delta_synced", "lifted", "seerr_done", "completed"];

export function CutoverStep() {
  const { t } = useI18n();
  const { run, targets } = useWizard();
  const jobs = useResource<CoreJob[]>("/jobs", { limit: 30 });
  useCoreEvents("job", () => void jobs.reload());
  const runJobs = (jobs.data ?? []).filter((j) => (j.job_type === "migration.cutover" || j.job_type === "migration.rollback") && String(j.target_id ?? "") === String(run.id));
  const activeJobs = runJobs.filter((j) => ["queued", "running", "retry_scheduled"].includes(j.status));
  return (
    <StepCard step="cutover" icon={Route}>
      <InlineState tone="warning" icon={Lock}>{t("mig.cutover.rule")}</InlineState>
      {activeJobs.map((j) => <JobProgress key={j.id} jobId={j.id} onFinished={() => void jobs.reload()} />)}
      {targets.length === 0 ? <NoTargets /> : targets.map((target) => <CutoverTarget key={target} target={target} />)}
      {runJobs.length > activeJobs.length ? (
        <details className="raw" style={{ border: "1px solid var(--mg-border-soft)", borderRadius: "var(--mg-r)" }}>
          <summary>{t("mig.cutover.pastJobs", { count: runJobs.length - activeJobs.length })}</summary>
          <div className="stack-sm" style={{ padding: 12 }}>{runJobs.filter((j) => !activeJobs.includes(j)).slice(0, 6).map((j) => <JobProgress key={j.id} jobId={j.id} compact />)}</div>
        </details>
      ) : null}
    </StepCard>
  );
}

function cutoverOf(run: MigRun, target: string): MigCutover | undefined {
  return run.cutovers.filter((c) => c.target_instance_id === target).sort((a, b) => b.id - a.id)[0];
}

function CutoverTarget({ target }: { target: string }) {
  const { t, dateTime } = useI18n();
  const { run, sources } = useWizard();
  const { can } = useSession();
  const mapping = run.mappings.find((m) => m.target_instance_id === target);
  const source = mapping ? sources[mapping.source_id] : undefined;
  const co = cutoverOf(run, target);
  const verify = latestReport(run, "verify", target);
  const ready = latestReport(run, "readiness", target);
  const verifyLive = verify ? liveVerdict(verify.mismatches, new Set(Object.keys(run.accepted_mismatches[target] ?? {}))) : null;
  const [dialog, setDialog] = useState<"cutover" | "rollback" | null>(null);
  const backupOnly = source?.kind === "backup";
  const blocked = !verify || verifyLive === "no_go" || !ready || ready.verdict === "no_go";
  const done = co?.status === "completed";
  const phaseIdx = co ? PHASES.indexOf(co.phase) : -1;
  const vs = verify?.summary as { source?: { items?: number } } | undefined;
  return (
    <Section title={<span className="row" style={{ gap: 8 }}><TargetName id={target} />{co ? <StatusChip tone={co.status === "completed" ? "success" : co.status === "failed" ? "danger" : co.status === "rolled_back" ? "neutral" : "accent"} pending={co.status === "running" || co.status === "rolling_back"}>{t(`mig.cutover.status.${co.status}` as MessageKey)}</StatusChip> : null}</span>} testId={`cutover-${target}`}
      actions={<>
        {co ? <Button size="sm" variant="danger" icon={RotateCcw} disabled={!can("admin") || co.status === "rolled_back"} onClick={() => setDialog("rollback")}>{t("mig.rollback.button")}</Button> : null}
        <Button size="sm" variant="danger" icon={backupOnly ? Zap : Route} disabled={!can("admin") || blocked || done || run.status !== "active"} onClick={() => setDialog("cutover")}>{backupOnly ? t("mig.cutover.activate") : co?.status === "failed" ? t("mig.cutover.resume") : t("mig.cutover.start")}</Button>
      </>}>
      <div className="stack-sm">
        <dl className="kv">
          <dt>{t("mig.col.source")}</dt><dd>{source ? `${source.name} (${t(source.kind === "live" ? "mig.source.live" : "mig.source.backup")})` : mapping?.source_id ?? "—"}</dd>
          <dt>{t("mig.cutover.verify")}</dt><dd>{verifyLive ? <VerdictChip verdict={verifyLive} /> : t("mig.cutover.missingReport")}</dd>
          <dt>{t("mig.cutover.ready")}</dt><dd>{ready ? <VerdictChip verdict={ready.verdict} /> : t("mig.cutover.missingReport")}</dd>
        </dl>
        {blocked && !done ? <InlineState tone="warning">{t("mig.cutover.blocked")}</InlineState> : null}
        {co ? (
          <Timeline items={PHASES.map((p, i) => ({
            title: t(`mig.phase.${p}` as MessageKey),
            tone: i < phaseIdx || co.phase === "completed" ? "success" : i === phaseIdx ? (co.status === "failed" ? "danger" : "accent") : "neutral",
            meta: i === phaseIdx && co.error ? co.error : i === 0 ? t("mig.cutover.by", { by: co.confirmed_by, at: dateTime(co.started_at) }) : undefined,
          }))} />
        ) : null}
        {co?.rolled_back_at ? <InlineState tone="neutral">{t("mig.rollback.done", { at: dateTime(co.rolled_back_at) })}</InlineState> : null}
      </div>
      <CutoverDialog open={dialog === "cutover"} onClose={() => setDialog(null)} target={target} backupOnly={backupOnly} items={vs?.source?.items ?? null} sourceName={source?.name ?? mapping?.source_id ?? "?"} warnVerdict={verifyLive === "warn" || ready?.verdict === "warn"} />
      <RollbackDialog open={dialog === "rollback"} onClose={() => setDialog(null)} target={target} />
    </Section>
  );
}

function useDirectPost() {
  const { reload } = useWizard();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const post = async (path: string, body: unknown): Promise<boolean> => {
    setPending(true);
    setError(null);
    try {
      await api.post(path, body);
      await reload();
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      return false;
    } finally {
      setPending(false);
    }
  };
  return { post, pending, error, reset: () => setError(null) };
}

function CutoverDialog({ open, onClose, target, backupOnly, items, sourceName, warnVerdict }: { open: boolean; onClose: () => void; target: string; backupOnly: boolean; items: number | null; sourceName: string; warnVerdict: boolean }) {
  const { t, num } = useI18n();
  const { run } = useWizard();
  const [typed, setTyped] = useState("");
  const [acceptWarnings, setAcceptWarnings] = useState(false);
  const [offline, setOffline] = useState(false);
  const [seerr, setSeerr] = useState("");
  const { post, pending, error, reset } = useDirectPost();
  const close = () => { setTyped(""); setAcceptWarnings(false); setOffline(false); setSeerr(""); reset(); onClose(); };
  const ok = typed.trim() === target && (!backupOnly || offline) && (!warnVerdict || acceptWarnings);
  const submit = async () => {
    const body = { run_id: run.id, targets: [target], confirm_names: { [target]: typed.trim() }, accept_warnings: acceptWarnings, confirm_old_instance_offline: offline, seerr_server_id: seerr.trim() ? Number(seerr) : null };
    if (await post(backupOnly ? `/migration/runs/${run.id}/activate-search` : "/migration/cutover", body)) close();
  };
  return (
    <Modal open={open} onClose={close} danger role="alertdialog" icon={backupOnly ? Zap : Route}
      title={backupOnly ? t("mig.cutover.activateTitle", { target }) : t("mig.cutover.title", { target })}
      description={backupOnly ? t("mig.cutover.activateBody") : t("mig.cutover.body")}
      footer={<><Button onClick={close} data-autofocus>{t("action.cancel")}</Button><Button variant="danger-solid" disabled={!ok} pending={pending} onClick={() => void submit()}>{backupOnly ? t("mig.cutover.activate") : t("mig.cutover.start")}</Button></>}>
      <div className="impact">
        <div><span>{t("mig.col.source")}</span><strong style={{ fontSize: 14 }}>{sourceName}</strong></div>
        <div><span>{t("mig.col.target")}</span><strong style={{ fontSize: 14 }}>{target}</strong></div>
        <div><span>{t("mig.import.items")}</span><strong>{items === null ? "—" : num(items)}</strong></div>
      </div>
      <dl className="kv">
        <dt>{t("mig.cutover.old")}</dt><dd>{backupOnly ? t("mig.cutover.oldBackup") : t("mig.cutover.oldLive")}</dd>
        <dt>{t("mig.cutover.new")}</dt><dd>{t("mig.cutover.newBody")}</dd>
        <dt>{t("mig.cutover.seerr")}</dt><dd>{t("mig.cutover.seerrBody")}</dd>
        <dt>{t("dialog.rollback")}</dt><dd>{t("mig.cutover.rollbackBody")}</dd>
      </dl>
      <div className="stack-sm" style={{ marginTop: 14 }}>
        {warnVerdict ? <label className="choice"><input type="checkbox" checked={acceptWarnings} onChange={(e) => setAcceptWarnings(e.target.checked)} /><span>{t("mig.cutover.acceptWarnings")}</span></label> : null}
        {backupOnly ? <label className="choice"><input type="checkbox" checked={offline} onChange={(e) => setOffline(e.target.checked)} /><span>{t("mig.cutover.offline")}</span></label> : null}
        {!backupOnly ? <Field label={t("mig.cutover.seerrId")} htmlFor={`seerr-${target}`} hint={t("mig.cutover.seerrIdHint")}><input id={`seerr-${target}`} className="input input--sm" inputMode="numeric" value={seerr} onChange={(e) => setSeerr(e.target.value.replace(/\D/g, ""))} style={{ maxWidth: 140 }} /></Field> : null}
        <Field label={t("dialog.typeToConfirm", { word: target })} htmlFor={`confirm-${target}`}><input id={`confirm-${target}`} className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" /></Field>
      </div>
      {error ? <div className="field-error" role="alert" style={{ marginTop: 12 }}>{error}</div> : null}
    </Modal>
  );
}

function RollbackDialog({ open, onClose, target }: { open: boolean; onClose: () => void; target: string }) {
  const { t } = useI18n();
  const { run } = useWizard();
  const [typed, setTyped] = useState("");
  const { post, pending, error, reset } = useDirectPost();
  const close = () => { setTyped(""); reset(); onClose(); };
  return (
    <Modal open={open} onClose={close} danger role="alertdialog" icon={RotateCcw} title={t("mig.rollback.title", { target })} description={t("mig.rollback.body")}
      footer={<><Button onClick={close} data-autofocus>{t("action.cancel")}</Button><Button variant="danger-solid" disabled={typed.trim() !== target} pending={pending} onClick={() => void post("/migration/rollback", { run_id: run.id, target_instance_id: target, confirm_name: typed.trim() }).then((ok) => { if (ok) close(); })}>{t("mig.rollback.confirm")}</Button></>}>
      <ol className="t-2" style={{ fontSize: 14, margin: "0 0 12px", paddingLeft: 20, lineHeight: 1.7 }}>
        <li>{t("mig.rollback.step1")}</li><li>{t("mig.rollback.step2")}</li><li>{t("mig.rollback.step3")}</li>
      </ol>
      <Field label={t("dialog.typeToConfirm", { word: target })} htmlFor={`rb-${target}`}><input id={`rb-${target}`} className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" /></Field>
      {error ? <div className="field-error" role="alert" style={{ marginTop: 12 }}>{error}</div> : null}
    </Modal>
  );
}

// ---------------------------------------------------------------- 10 post-cutover audit
export function PostAuditStep() {
  const { t } = useI18n();
  const { run, targets } = useWizard();
  const completed = targets.filter((tg) => cutoverOf(run, tg)?.status === "completed");
  return (
    <StepCard step="post_cutover" icon={Activity}>
      {completed.length === 0 ? <InlineState tone="info">{t("mig.audit.none")}</InlineState> : completed.map((target) => <AuditTarget key={target} target={target} />)}
    </StepCard>
  );
}

interface AuditSummary { since: string; until: string; window_hours: number; elapsed_hours: number; window_complete: boolean; target: Record<string, number>; old: Record<string, number> | null; queue: number; issues: Array<{ severity: string; message_nl: string }> }

function AuditTarget({ target }: { target: string }) {
  const { t, num, dateTime } = useI18n();
  const { run } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const [hours, setHours] = useState(72);
  const rep: MigReport | undefined = latestReport(run, "post_cutover", target);
  const s = rep?.summary as unknown as AuditSummary | undefined;
  return (
    <Section title={<TargetName id={target} />} actions={<>
      <label className="row" style={{ gap: 6, fontSize: 14 }}>{t("mig.audit.hours")}<input className="input input--sm num" type="number" min={1} max={720} value={hours} onChange={(e) => setHours(Math.max(1, Math.min(720, Number(e.target.value) || 72)))} style={{ width: 80 }} /></label>
      <Button size="sm" variant="primary" icon={Activity} disabled={!can("operator")} pending={pending === `pa-${target}`} onClick={() => void launch(`pa-${target}`, `/migration/runs/${run.id}/post-audit`, { target_instance_id: target, hours }, t("mig.audit.done"))}>{t("mig.audit.run")}</Button>
    </>}>
      {rep && s ? (
        <div className="stack-sm">
          <div className="row wrap" style={{ gap: 8 }}><VerdictChip verdict={rep.verdict} /><span className="t-3" style={{ fontSize: 14 }}>{t("mig.audit.window", { since: dateTime(s.since), until: dateTime(s.until), elapsed: num(s.elapsed_hours, 1), hours: s.window_hours })}</span></div>
          <div className="impact" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))", margin: 0 }}>
            {Object.entries(s.target ?? {}).map(([k, v]) => <div key={k}><span>{t("mig.audit.target", { what: k })}</span><strong>{num(v)}</strong></div>)}
            <div><span>{t("mig.audit.queue")}</span><strong>{num(s.queue)}</strong></div>
            {s.old ? <div><span>{t("mig.audit.oldGrabs")}</span><strong className={s.old.grabbed ? "t-danger" : undefined}>{num(s.old.grabbed ?? 0)}</strong></div> : null}
          </div>
          {s.issues.map((i, n) => <InlineState key={n} tone={i.severity === "no_go" ? "danger" : "warning"}>{i.message_nl}</InlineState>)}
        </div>
      ) : null}
    </Section>
  );
}

/** Scan-item → naam voor mensen: titel als die er is, anders "titel met ID 123" in plaats van een kale ID (N12). */
function scanItemName(x: unknown, t: (k: MessageKey, v?: Record<string, string | number>) => string): string {
  if (x && typeof x === "object") {
    const o = x as { title?: string; name?: string; id?: string | number; tmdb_id?: number; tvdb_id?: number };
    return o.title ?? o.name ?? t("mig.scan.idItem", { id: String(o.tmdb_id ?? o.tvdb_id ?? o.id ?? "?") });
  }
  const v = String(x);
  return /^\d+$/.test(v) ? t("mig.scan.idItem", { id: v }) : v;
}
