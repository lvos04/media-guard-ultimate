"use client";

import { ArrowLeft, ArrowRight, Ban, ClipboardCheck, DatabaseBackup, FolderTree, Lock, Plus, RefreshCcw, Route, Save, ShieldCheck, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Stepper } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Modal, StatusChip } from "@/components/mg/primitives";
import { AutopilotPanel } from "@/components/migration/autopilot";
import { SourceKindChip, sourceCounts } from "@/components/migration/overview";
import { CheckList, Section, StepError, StepJob, StepStatusChip, TargetName, VerdictChip, useLaunch, useWizard } from "@/components/migration/shared";
import { CutoverStep, OptimizationStep, PostAuditStep, ReadinessStep, ScanStep, ShadowImportStep, VerifyStep } from "@/components/migration/steps-late";
import { UploadZone } from "@/components/migration/upload";
import { MIG_STEPS, latestReport, runStep, stepTone, type MigBackup, type MigMapping, type MigStepId, type PathPreview, type PathRule, type PreflightSummary } from "@/lib/api/migration";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { instanceLabel } from "@/lib/labels";
import { useSession } from "@/lib/session";

export type WizardStep = "sources" | MigStepId;
export const WIZARD_STEPS: WizardStep[] = ["sources", ...MIG_STEPS];

export function WizardLayout({ step, onStep, onLeave }: { step: WizardStep; onStep: (s: WizardStep) => void; onLeave: () => void }) {
  const { t } = useI18n();
  const { run } = useWizard();
  const idx = WIZARD_STEPS.indexOf(step);
  return (
    <div className="grid">
      <AutopilotPanel />
      <Card className="span-3" title={t("mig.run.title", { id: run.id })} sub={run.name} actions={<Button size="sm" variant="ghost" icon={ArrowLeft} onClick={onLeave}>{t("mig.run.all")}</Button>}>
        <div className="stack-sm">
          {run.status !== "active" ? <InlineState tone="neutral">{t(`mig.run.status.${run.status}` as MessageKey)}</InlineState> : null}
          <Stepper steps={WIZARD_STEPS.map((s) => {
            const st = s === "sources" ? undefined : runStep(run, s);
            let tone = s === "sources" ? "done" : stepTone(st?.status);
            // Overschakelen krijgt pas een vinkje als de overstap echt gedaan is (N12)
            if (s === "cutover" && tone === "done" && !(run.cutovers ?? []).some((c) => c.status === "completed")) tone = "todo";
            return {
              label: t(`mig.step.${s}` as MessageKey),
              status: s === step ? "current" : tone === "current" ? "warning" : tone,
              meta: s === "cutover" && tone === "todo" ? <Lock className="ico ico-sm" aria-hidden="true" /> : st && (st.status === "running" || st.status === "queued") && s !== step ? <StatusChip tone="accent" pending icon={null}>{t(`mig.stepStatus.${st.status}` as MessageKey)}</StatusChip> : null,
              onClick: () => onStep(s),
            };
          })} />
          <p className="hint" style={{ margin: 0 }}>{t("mig.persistNote")}</p>
        </div>
      </Card>
      <div className="span-9 stack" data-testid={`mig-step-${step}`}>
        <StepBody step={step} />
        <div className="row-between">
          <Button size="sm" icon={ArrowLeft} disabled={idx <= 0} onClick={() => onStep(WIZARD_STEPS[idx - 1])}>{t("action.previous")}</Button>
          <Button size="sm" disabled={idx >= WIZARD_STEPS.length - 1} onClick={() => onStep(WIZARD_STEPS[idx + 1])}>{t("action.next")} <ArrowRight className="ico" aria-hidden="true" /></Button>
        </div>
      </div>
    </div>
  );
}

function StepBody({ step }: { step: WizardStep }) {
  switch (step) {
    case "sources": return <RunSourcesStep />;
    case "preflight": return <PreflightStep />;
    case "backup": return <BackupStep />;
    case "mapping": return <MappingStep />;
    case "shadow_import": return <ShadowImportStep />;
    case "verify": return <VerifyStep />;
    case "scan": return <ScanStep />;
    case "optimization": return <OptimizationStep />;
    case "readiness": return <ReadinessStep />;
    case "cutover": return <CutoverStep />;
    case "post_cutover": return <PostAuditStep />;
  }
}

/** Header card of a step: title, what it reads/writes, status chip, primary action. */
export function StepCard({ step, icon, actions, children, testId }: { step: MigStepId; icon?: typeof Route; actions?: React.ReactNode; children: React.ReactNode; testId?: string }) {
  const { t } = useI18n();
  const { run } = useWizard();
  const st = runStep(run, step);
  return (
    <Card title={t(`mig.step.${step}` as MessageKey)} icon={icon ?? Route} sub={<StepStatusChip status={st?.status} />} actions={actions}>
      <div className="stack" data-testid={testId}>
        <p className="t-2" style={{ fontSize: 14, margin: 0 }}>{t(`mig.body.${step}` as MessageKey)}</p>
        {children}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- 0 sources of this run
function RunSourcesStep() {
  const { t, num } = useI18n();
  const { run, sources } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const [follow, setFollow] = useState<"exact" | "follow">(run.options.follow_new_seasons ? "follow" : "exact");
  const [acceptPaths, setAcceptPaths] = useState(Boolean(run.options.accept_unresolved_paths));
  const [cancel, setCancel] = useState(false);
  const dirty = (follow === "follow") !== Boolean(run.options.follow_new_seasons) || acceptPaths !== Boolean(run.options.accept_unresolved_paths);
  return (
    <>
      <Card title={t("mig.step.sources")} icon={DatabaseBackup} flush>
        <div className="table-wrap">
          <table className="table table--compact table--stack">
            <thead><tr><th>{t("mig.col.source")}</th><th>{t("mig.col.kind")}</th><th>{t("mig.col.app")}</th><th>{t("mig.col.content")}</th><th>{t("mig.col.target")}</th></tr></thead>
            <tbody>{run.mappings.map((m) => {
              const s = sources[m.source_id];
              return (
                <tr key={m.source_id}>
                  <td className="cell-primary"><div className="cell-title">{s?.name ?? m.source_id}</div><div className="cell-sub mono">{m.source_id}</div></td>
                  <td data-label={t("mig.col.kind")}>{s ? <SourceKindChip source={s} /> : "—"}</td>
                  <td data-label={t("mig.col.app")} className="mono">{s ? `${s.app_name ?? s.app} ${s.version ?? ""}` : "—"}</td>
                  <td data-label={t("mig.col.content")}>{s ? sourceCounts(s, t, num) ?? "—" : "—"}</td>
                  <td data-label={t("mig.col.target")}><TargetName id={m.target_instance_id} /> {m.status === "confirmed" ? <StatusChip tone="success" icon={null}>{t("mig.mapping.status.confirmed")}</StatusChip> : null}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      </Card>
      <Card title={t("mig.opt.title")} icon={ShieldCheck}
        footer={<><span>{t("mig.opt.footer")}</span><span className="row">
          <Button size="sm" variant="danger" icon={Ban} disabled={!can("admin") || run.status !== "active"} onClick={() => setCancel(true)}>{t("mig.run.cancel")}</Button>
          <Button size="sm" variant="primary" icon={Save} disabled={!dirty || !can("operator") || run.status !== "active"} pending={pending === "opts"} onClick={() => void launch("opts", `/migration/runs/${run.id}/options`, { follow_new_seasons: follow === "follow" ? true : null, accept_unresolved_paths: acceptPaths }, t("toast.saved"), "patch")}>{t("action.save")}</Button>
        </span></>}>
        <div className="stack">
          <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="label">{t("mig.opt.newSeasons")}</legend>
            <label className="choice"><input type="radio" name="run-follow" checked={follow === "exact"} onChange={() => setFollow("exact")} /><span>{t("mig.opt.exact")}</span></label>
            <label className="choice"><input type="radio" name="run-follow" checked={follow === "follow"} onChange={() => setFollow("follow")} /><span>{t("mig.opt.follow")}</span></label>
          </fieldset>
          <label className="choice"><input type="checkbox" checked={acceptPaths} onChange={(e) => setAcceptPaths(e.target.checked)} /><span>{t("mig.opt.acceptPaths")}<span className="hint" style={{ display: "block" }}>{t("mig.opt.acceptPathsHint")}</span></span></label>
        </div>
      </Card>
      <Modal open={cancel} onClose={() => setCancel(false)} danger icon={Ban} title={t("mig.run.cancelTitle", { id: run.id })} description={t("mig.run.cancelBody")}
        footer={<><Button onClick={() => setCancel(false)} data-autofocus>{t("action.close")}</Button><Button variant="danger-solid" pending={pending === "cancel"} onClick={() => void launch("cancel", `/migration/runs/${run.id}/cancel`, {}, t("mig.run.cancelled")).then(() => setCancel(false))}>{t("mig.run.cancel")}</Button></>} />
    </>
  );
}

// ---------------------------------------------------------------- 1 preflight
function PreflightStep() {
  const { t, relative } = useI18n();
  const { run } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const st = runStep(run, "preflight");
  const rep = latestReport(run, "preflight");
  const s = rep?.summary as unknown as PreflightSummary | undefined;
  const active = st?.status === "running" || st?.status === "queued";
  return (
    <StepCard step="preflight" icon={ClipboardCheck} actions={<Button size="sm" variant="primary" icon={ClipboardCheck} disabled={!can("operator") || active} pending={pending === "pf"} onClick={() => void launch("pf", "/migration/preflight", { run_id: run.id }, t("mig.queued"))}>{rep ? t("mig.rerun") : t("mig.preflight.run")}</Button>}>
      <StepJob step={st} />
      <StepError step={st} />
      {rep && s ? (
        <div className="stack" data-testid="preflight-report">
          <div className="row wrap" style={{ gap: 8 }}><VerdictChip verdict={rep.verdict} size="lg" /><span className="t-3" style={{ fontSize: 14 }}>{t("mig.reportMeta", { id: rep.id, age: relative(rep.created_at) })}</span></div>
          {Object.entries(s.sources ?? {}).map(([sid, checks]) => <Section key={`s-${sid}`} title={t("mig.preflight.source", { id: sid })}><CheckList checks={checks} /></Section>)}
          {Object.entries(s.targets ?? {}).map(([tid, checks]) => <Section key={`t-${tid}`} title={<>{t("mig.preflight.target")} <TargetName id={tid} /></>}><CheckList checks={checks} /></Section>)}
          {s.local || s.seerr ? <Section title={t("mig.preflight.other")}><CheckList checks={[s.local, s.seerr].filter((c): c is NonNullable<typeof c> => Boolean(c))} /></Section> : null}
        </div>
      ) : !active ? <InlineState tone="info">{t("mig.preflight.none")}</InlineState> : null}
    </StepCard>
  );
}

// ---------------------------------------------------------------- 2 backup
function BackupStep() {
  const { t } = useI18n();
  const { run, sources } = useWizard();
  return (
    <StepCard step="backup" icon={DatabaseBackup}>
      {run.mappings.map((m) => {
        const s = sources[m.source_id];
        return s ? <BackupSource key={m.source_id} sourceId={m.source_id} /> : <InlineState key={m.source_id} tone="warning">{t("mig.backup.unknownSource", { id: m.source_id })}</InlineState>;
      })}
    </StepCard>
  );
}

function BackupSource({ sourceId }: { sourceId: string }) {
  const { t, bytes, dateTime } = useI18n();
  const { run, sources, reload } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const s = sources[sourceId];
  const st = runStep(run, "backup", `source:${sourceId}`);
  const [strategy, setStrategy] = useState<"existing" | "create">("existing");
  const [confirmCreate, setConfirmCreate] = useState(false);
  const active = st?.status === "running" || st?.status === "queued";
  const b: MigBackup | null = s.latest_backup;
  return (
    <Section title={<span className="row" style={{ gap: 8 }}>{s.name} <SourceKindChip source={s} /> <StepStatusChip status={st?.status} /></span>} testId={`backup-${sourceId}`}>
      <div className="stack-sm">
        {b ? (
          <dl className="kv">
            <dt>{t("mig.backup.file")}</dt><dd className="mono">{b.backup_name}</dd>
            <dt>{t("mig.backup.size")}</dt><dd>{bytes(b.size)}</dd>
            <dt>sha256</dt><dd className="mono" style={{ fontSize: 14 }}>{b.sha256}</dd>
            <dt>{t("mig.backup.check")}</dt><dd>{b.verified ? <StatusChip tone="success">{t("mig.backup.verifiedLong")}</StatusChip> : <StatusChip tone="warning">{t("mig.backup.unverified")}</StatusChip>}</dd>
            <dt>{t("mig.backup.origin")}</dt><dd>{t(`mig.backup.origin.${b.origin}` as MessageKey)} · {dateTime(b.created_at)}</dd>
          </dl>
        ) : <InlineState tone="warning">{t("mig.backup.none")}</InlineState>}
        {s.kind === "live" ? (
          <div className="stack-sm">
            <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
              <legend className="label">{t("mig.backup.strategy")}</legend>
              <label className="choice"><input type="radio" name={`strat-${sourceId}`} checked={strategy === "existing"} onChange={() => setStrategy("existing")} /><span>{t("mig.backup.existing")}</span></label>
              <label className="choice"><input type="radio" name={`strat-${sourceId}`} checked={strategy === "create"} onChange={() => setStrategy("create")} /><span>{t("mig.backup.create")}</span></label>
            </fieldset>
            {strategy === "create" ? <label className="choice"><input type="checkbox" checked={confirmCreate} onChange={(e) => setConfirmCreate(e.target.checked)} /><span>{t("mig.backup.confirmCreate")}</span></label> : null}
            <div><Button size="sm" variant="primary" icon={DatabaseBackup} disabled={!can("admin") || active || (strategy === "create" && !confirmCreate)} pending={pending === `bk-${sourceId}`}
              onClick={() => void launch(`bk-${sourceId}`, `/migration/runs/${run.id}/backup`, { source_id: sourceId, strategy, confirm_create: strategy === "create" && confirmCreate }, t("mig.queued"))}>{t("mig.backup.run")}</Button></div>
          </div>
        ) : (
          <UploadZone<{ id: number; sha256: string; size: number; backup_name: string; verified: boolean }>
            path={`/migration/sources/${encodeURIComponent(sourceId)}/backups/upload`} accept=".zip,application/zip" disabled={!can("admin")}
            label={t("mig.backup.uploadNewer")} hint={t("mig.backup.uploadNewerHint")}
            onUploaded={() => void reload()} describe={(r) => t("mig.backup.uploaded", { sha: r.sha256.slice(0, 12) })} />
        )}
        <StepJob step={st} />
        <StepError step={st} />
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------- 3 mapping + paths
interface Draft { target: string | null; excluded: boolean; mode: MigMapping["mode"]; path_map: PathRule[] }
const draftOf = (m: MigMapping): Draft => ({ target: m.target_instance_id, excluded: m.status === "excluded", mode: m.mode, path_map: m.path_map.map((r) => ({ ...r })) });

function MappingStep() {
  const { run } = useWizard();
  // re-key the editor when the server state changes so the draft starts from the saved mapping
  // ook het doel en de redenen: updated_at heeft secondenresolutie, een voorstel binnen dezelfde
  // seconde als het aanmaken van de run zou anders het oude (lege) concept laten staan
  const key = run.mappings.map((m) => `${m.source_id}:${m.updated_at}:${m.status}:${m.target_instance_id ?? ""}:${m.reasons.length}`).join("|");
  return <MappingEditor key={key} />;
}

function MappingEditor() {
  const { t, pct, locale } = useI18n();
  const { run, sources, instances } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const initial = useMemo(() => Object.fromEntries(run.mappings.map((m) => [m.source_id, draftOf(m)])), [run.mappings]);
  const [draft, setDraft] = useState<Record<string, Draft>>(initial);
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const targets = instances.filter((i) => !i.archived);
  const set = (sid: string, next: Partial<Draft>) => setDraft((d) => ({ ...d, [sid]: { ...d[sid], ...next } }));
  const used = Object.entries(draft).filter(([, d]) => !d.excluded && d.target).map(([, d]) => d.target);
  const duplicate = (target: string | null) => Boolean(target) && used.filter((x) => x === target).length > 1;
  const anyConfirmed = run.mappings.some((m) => m.status === "confirmed");
  const allConfirmed = run.mappings.every((m) => m.status !== "proposed");
  const previewed = run.mappings.some((m) => m.target_instance_id !== null || m.reasons.length > 0);
  const problems = Object.entries(draft).filter(([, d]) => !d.excluded && (!d.target || duplicate(d.target)));
  const st = runStep(run, "mapping");

  const entries = () => Object.entries(draft).map(([source_id, d]) => ({ source_id, target_instance_id: d.target, mode: d.mode, path_map: d.path_map.filter((r) => r.from.trim() || r.to.trim()), excluded: d.excluded }));
  const save = () => launch("map-save", `/migration/runs/${run.id}/mapping`, { entries: entries() }, t("mig.mapping.saved"), "put");
  const confirm = async () => {
    if (dirty && (await save()) === undefined) return;
    await launch("map-confirm", `/migration/runs/${run.id}/mapping/confirm`, {}, t("mig.mapping.confirmed"));
  };

  return (
    <>
      <StepCard step="mapping" icon={Route} testId="mapping-step" actions={<>
        <Button size="sm" variant={previewed ? "secondary" : "primary"} icon={RefreshCcw} disabled={!can("operator")} pending={pending === "map-prev"} onClick={() => void launch("map-prev", "/migration/instance-mapping/preview", { run_id: run.id, reset: false }, t("mig.mapping.previewed"))}>{previewed ? t("mig.mapping.repreview") : t("mig.mapping.preview")}</Button>
        {anyConfirmed ? <Button size="sm" variant="ghost" disabled={!can("operator")} pending={pending === "map-reset"} onClick={() => void launch("map-reset", "/migration/instance-mapping/preview", { run_id: run.id, reset: true }, t("mig.mapping.previewed"))}>{t("mig.mapping.reset")}</Button> : null}
      </>}>
        <InlineState tone="info">{t("mig.mapping.neverTitle")}</InlineState>
        <div className="table-wrap">
          <table className="table table--compact table--stack mig-matrix" data-testid="mapping-matrix">
            <thead><tr><th>{t("mig.col.source")}</th>{targets.map((i) => <th key={i.id} className="center"><span className="tag">{instanceLabel(i.id, i.name, locale)}</span></th>)}<th className="center">{t("mig.mapping.exclude")}</th><th>{t("mig.col.status")}</th></tr></thead>
            <tbody>{run.mappings.map((m) => {
              const s = sources[m.source_id];
              const d = draft[m.source_id];
              return (
                <tr key={m.source_id} data-mapping={m.source_id}>
                  <td className="cell-primary">
                    <div className="cell-title">{s?.name ?? m.source_id}</div>
                    <div className="cell-sub"><span className="mono">{s?.app ?? "?"}</span>{m.anime_score !== null ? ` · ${t("mig.sources.animeScoreShort", { score: Math.round(m.anime_score * 100) })}` : ""}{m.confidence !== null ? ` · ${t("mig.mapping.confidence", { value: pct(m.confidence * 100) })}` : ""}</div>
                  </td>
                  {targets.map((i) => {
                    const wrongApp = Boolean(s) && i.app !== s.app;
                    const proposed = m.target_instance_id === i.id && m.status !== "confirmed";
                    return (
                      <td key={i.id} data-label={i.id} className="center">
                        <label className="matrix-cell" title={wrongApp ? t("mig.mapping.wrongApp", { app: s.app, target: i.app }) : i.name}>
                          <input type="radio" name={`map-${m.source_id}`} value={i.id} checked={!d.excluded && d.target === i.id} disabled={wrongApp || !can("operator")} onChange={() => set(m.source_id, { target: i.id, excluded: false })} aria-label={t("mig.mapping.cellAria", { source: s?.name ?? m.source_id, target: i.id })} />
                          {proposed ? <span className="t-3" style={{ fontSize: 14 }}>{t("mig.mapping.proposed")}</span> : null}
                        </label>
                      </td>
                    );
                  })}
                  <td data-label={t("mig.mapping.exclude")} className="center"><input type="checkbox" checked={d.excluded} disabled={!can("operator")} onChange={(e) => set(m.source_id, { excluded: e.target.checked })} aria-label={t("mig.mapping.excludeAria", { source: s?.name ?? m.source_id })} /></td>
                  <td data-label={t("mig.col.status")}>
                    <StatusChip tone={m.status === "confirmed" ? "success" : m.status === "excluded" ? "neutral" : "warning"} icon={null}>{t(`mig.mapping.status.${m.status}` as MessageKey)}</StatusChip>
                    {!d.excluded && duplicate(d.target) ? <div className="t-danger" style={{ fontSize: 14 }}>{t("mig.mapping.duplicate")}</div> : null}
                  </td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
        {run.mappings.map((m) => (m.reasons.length || m.warnings.length) ? (
          <details key={`why-${m.source_id}`} className="raw" style={{ border: "1px solid var(--mg-border-soft)", borderRadius: "var(--mg-r)" }}>
            <summary>{t("mig.mapping.why", { source: sources[m.source_id]?.name ?? m.source_id })}{m.warnings.length ? <StatusChip tone="warning" icon={null}>{m.warnings.length}</StatusChip> : null}</summary>
            <ul className="t-2" style={{ fontSize: 14, margin: 0, padding: "0 12px 12px 30px", lineHeight: 1.6 }}>
              {m.reasons.map((r) => <li key={r}>{r}</li>)}
              {m.warnings.map((w) => <li key={w} className="t-warning">{w}</li>)}
            </ul>
          </details>
        ) : null)}
        {run.mapping_items.length ? (
          <Section title={t("mig.mapping.flags", { count: run.mapping_items.length })}>
            <div className="table-wrap" style={{ maxHeight: 280, overflow: "auto" }}>
              <table className="table table--compact table--stack">
                <thead><tr><th>{t("mig.col.item")}</th><th>{t("mig.col.source")}</th><th>{t("mig.col.flag")}</th><th>{t("mig.col.message")}</th></tr></thead>
                <tbody>{run.mapping_items.map((it) => (
                  <tr key={`${it.source_id}-${it.kind}-${it.external_id}-${it.flag}`}>
                    <td className="cell-primary">{it.title}<div className="cell-sub mono">{it.kind} {it.external_id}</div></td>
                    <td data-label={t("mig.col.source")} className="mono">{it.source_id}</td>
                    <td data-label={t("mig.col.flag")}><StatusChip tone={it.flag === "duplicate_same_target" ? "danger" : "warning"} icon={null}>{t(`mig.flag.${it.flag}` as MessageKey)}</StatusChip></td>
                    <td data-label={t("mig.col.message")} style={{ whiteSpace: "normal" }}>{it.message_nl}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </Section>
        ) : null}
        <StepError step={st} />
        <div className="row-between wrap" style={{ gap: 8 }}>
          <span className="t-3" style={{ fontSize: 14 }}>{problems.length ? t("mig.mapping.problems", { count: problems.length }) : allConfirmed ? t("mig.mapping.allConfirmed") : t("mig.mapping.readyToConfirm")}</span>
          <span className="row">
            <Button size="sm" icon={Save} disabled={!dirty || !can("operator")} pending={pending === "map-save"} onClick={() => void save()}>{t("mig.mapping.save")}</Button>
            <Button size="sm" variant="primary" icon={ShieldCheck} disabled={!can("operator") || problems.length > 0 || (!dirty && allConfirmed)} pending={pending === "map-confirm"} onClick={() => void confirm()}>{t("mig.mapping.confirm")}</Button>
          </span>
        </div>
      </StepCard>
      <Card title={t("mig.paths.title")} icon={FolderTree} sub={t("mig.paths.sub")}>
        <div className="stack">
          <p className="t-2" style={{ fontSize: 14, margin: 0 }}>{t("mig.paths.body")}</p>
          {run.mappings.filter((m) => !draft[m.source_id]?.excluded).map((m) => (
            <PathRules key={m.source_id} mapping={m} draft={draft[m.source_id]} onChange={(rules) => set(m.source_id, { path_map: rules })} onMode={(mode) => set(m.source_id, { mode })} dirty={dirty} save={save} />
          ))}
        </div>
      </Card>
    </>
  );
}

function PathRules({ mapping, draft, onChange, onMode, dirty, save }: { mapping: MigMapping; draft: Draft; onChange: (rules: PathRule[]) => void; onMode: (mode: MigMapping["mode"]) => void; dirty: boolean; save: () => Promise<unknown> }) {
  const { t, relative } = useI18n();
  const { run, sources } = useWizard();
  const { can } = useSession();
  const { launch, pending } = useLaunch();
  const s = sources[mapping.source_id];
  const rep = latestReport(run, "path_preview", undefined, mapping.source_id);
  const rules = draft.path_map;
  const update = (i: number, key: keyof PathRule, value: string) => onChange(rules.map((r, j) => (j === i ? { ...r, [key]: value } : r)));
  const invalid = rules.some((r) => (r.from || r.to) && (!r.from.startsWith("/") || !r.to.startsWith("/")));
  const check = async () => {
    if (dirty && (await save()) === undefined) return;
    await launch(`pp-${mapping.source_id}`, `/migration/runs/${run.id}/path-preview`, { source_id: mapping.source_id });
  };
  return (
    <Section title={<span className="row" style={{ gap: 8 }}>{s?.name ?? mapping.source_id} <span className="t-3">→</span> <TargetName id={draft.target} /></span>} testId={`paths-${mapping.source_id}`}>
      <div className="stack-sm">
        <div className="row wrap" style={{ gap: 12, alignItems: "flex-end" }}>
          <Field label={t("mig.mapping.mode")} htmlFor={`mode-${mapping.source_id}`}>
            <select id={`mode-${mapping.source_id}`} className="select" value={draft.mode} disabled={!can("operator")} onChange={(e) => onMode(e.target.value as MigMapping["mode"])}>
              <option value="auto">{t("mig.mode.auto")}</option>
              <option value="native">{t("mig.mode.native")}</option>
              <option value="api" disabled={s?.kind !== "live"}>{t("mig.mode.api")}</option>
            </select>
          </Field>
        </div>
        {rules.length ? (
          <div className="stack-sm">
            {rules.map((r, i) => (
              <div key={i} className="path-rule">
                <input className="input mono" aria-label={t("mig.paths.from")} placeholder="/anime" value={r.from} disabled={!can("operator")} onChange={(e) => update(i, "from", e.target.value)} />
                <span aria-hidden="true" className="t-3">→</span>
                <input className="input mono" aria-label={t("mig.paths.to")} placeholder="/media/anime" value={r.to} disabled={!can("operator")} onChange={(e) => update(i, "to", e.target.value)} />
                <Button size="sm" variant="ghost" iconOnly icon={Trash2} disabled={!can("operator")} onClick={() => onChange(rules.filter((_, j) => j !== i))}>{t("action.delete")}</Button>
              </div>
            ))}
          </div>
        ) : <p className="t-3" style={{ fontSize: 14, margin: 0 }}>{t("mig.paths.noRules")}</p>}
        {invalid ? <div className="field-error" role="alert">{t("mig.paths.absolute")}</div> : null}
        <div className="row wrap">
          <Button size="sm" icon={Plus} disabled={!can("operator")} onClick={() => onChange([...rules, { from: "", to: "" }])}>{t("mig.paths.add")}</Button>
          <Button size="sm" variant="primary" icon={FolderTree} disabled={!can("operator") || invalid} pending={pending === `pp-${mapping.source_id}`} onClick={() => void check()}>{t("mig.paths.check")}</Button>
        </div>
        {rep ? (
          <div className="stack-sm" data-testid={`path-report-${mapping.source_id}`}>
            <div className="row wrap" style={{ gap: 8 }}><VerdictChip verdict={rep.verdict} /><span className="t-3" style={{ fontSize: 14 }}>{t("mig.reportMeta", { id: rep.id, age: relative(rep.created_at) })}</span></div>
            <PathPreviewView p={rep.summary as unknown as PathPreview} />
          </div>
        ) : null}
      </div>
    </Section>
  );
}

export function PathPreviewView({ p }: { p: Omit<PathPreview, "verdict"> }) {
  const { t, num } = useI18n();
  return (
    <div className="stack-sm">
      <InlineState tone={p.blocking ? "danger" : "success"}>{p.message_nl}</InlineState>
      <div className="table-wrap">
        <table className="table table--compact table--stack">
          <thead><tr><th>{t("mig.paths.prefix")}</th><th className="num">{t("mig.paths.count")}</th><th className="num">{t("mig.paths.missing")}</th><th>{t("mig.paths.samples")}</th></tr></thead>
          <tbody>{Object.entries(p.per_prefix ?? {}).map(([prefix, b]) => (
            <tr key={prefix}>
              <td className="cell-primary mono">{prefix === "(geen regel)" ? t("mig.paths.noRule") : prefix}</td>
              <td data-label={t("mig.paths.count")} className="num">{num(b.count)}</td>
              <td data-label={t("mig.paths.missing")} className={b.missing ? "num t-danger" : "num"}>{num(b.missing)}</td>
              <td data-label={t("mig.paths.samples")} className="cell-block" style={{ whiteSpace: "normal" }}>{b.samples.length ? b.samples.map((x) => <div key={x.from} className="mono" style={{ fontSize: 14 }}>{x.exists ? "✓" : "✗"} {x.to}</div>) : "—"}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {p.root_folders?.length ? (
        <dl className="kv">{p.root_folders.map((r) => (
          <div key={r.from} style={{ display: "contents" }}><dt className="mono">{r.from}</dt><dd className="mono">→ {r.to} {r.exists === null ? null : r.exists ? <StatusChip tone="success" icon={null}>{t("mig.paths.exists")}</StatusChip> : <StatusChip tone="danger" icon={null}>{t("mig.paths.notFound")}</StatusChip>}</dd></div>
        ))}</dl>
      ) : null}
      {p.unresolved?.length ? (
        <details className="raw" style={{ border: "1px solid var(--mg-border-soft)", borderRadius: "var(--mg-r)" }}>
          <summary>{t("mig.paths.unresolved", { count: num(p.unresolved_count) })}</summary>
          <pre>{p.unresolved.map((u) => `${u.kind}: ${u.from} → ${u.to}`).join("\n")}</pre>
        </details>
      ) : null}
      <p className="hint" style={{ margin: 0 }}>{t("mig.paths.sampled", { count: num(p.sampled) })}</p>
    </div>
  );
}

