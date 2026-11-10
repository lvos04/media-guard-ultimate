"use client";

import { FileSignature, RotateCcw, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button, Card, Field, InlineState, Modal, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { Instance } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { OptimizePreview, OptimizeStatusRow, RenamePreview } from "@/lib/api/provisioning";
import { TitlePicker } from "@/components/mg/title-picker";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { instanceLabel } from "@/lib/labels";

interface CleanupPreview { requires_confirmation: boolean; instances: Array<{ instance_id: string; profiles: Array<{ profile_id: number; name: string; original: string; in_use: boolean; deleted?: boolean }> }>; message_nl: string }
interface RollbackPreview { requires_confirmation?: boolean; instances: Array<{ instance_id: string; name?: string; rolled_back?: boolean; error?: string }>; message_nl?: string; status?: string }

/** "Optimaliseren na migratie": preview → confirm, cleanup of old profiles, rollback to the migrated snapshot. */
export function OptimizeTab() {
  const { t, num, locale } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const status = useResource<OptimizeStatusRow[]>("/provisioning/optimize-migrated");
  const [preview, setPreview] = useState<OptimizePreview | null>(null);
  const [result, setResult] = useState<{ status: string; instances: Array<Record<string, unknown>> } | null>(null);
  const [cleanup, setCleanup] = useState<CleanupPreview | null>(null);
  const [rollback, setRollback] = useState<RollbackPreview | null>(null);
  const [confirm, setConfirm] = useState<"optimize" | "rollback" | null>(null);
  const reload = () => void status.reload();
  return (
    <div className="stack">
      <Card title={t("prov.opt.title")} icon={Sparkles} sub={t("prov.opt.sub")} flush
        actions={<>
          <Button size="sm" disabled={!can("admin")} pending={pending === "op"} onClick={() => void run<OptimizePreview>("op", "post", "/provisioning/optimize-migrated", { confirm: false }).then((r) => { if (r) { setPreview(r); setResult(null); } })}>{t("prov.opt.preview")}</Button>
          <Button size="sm" icon={Trash2} disabled={!can("admin")} pending={pending === "cp"} onClick={() => void run<CleanupPreview>("cp", "post", "/provisioning/optimize-migrated/cleanup", { confirm: false }).then((r) => { if (r) setCleanup(r); })}>{t("prov.opt.cleanup")}</Button>
          <Button size="sm" variant="danger" icon={RotateCcw} disabled={!can("admin")} pending={pending === "rp"} onClick={() => void run<RollbackPreview>("rp", "post", "/provisioning/optimize-migrated/rollback", { confirm: false }).then((r) => { if (r) { setRollback(r); setConfirm("rollback"); } })}>{t("prov.opt.rollback")}</Button>
        </>}>
        <ResourceView resource={status} endpoint="GET /api/v1/provisioning/optimize-migrated" compact>
          {(rows) => (
            <div className="table-wrap">
              <table className="table table--compact table--stack" data-testid="opt-status">
                <thead><tr><th>{t("prov.col.instance")}</th><th>{t("prov.opt.shadowDone")}</th><th>{t("prov.opt.phase")}</th><th>{t("prov.opt.snapshot")}</th></tr></thead>
                <tbody>{rows.map((r) => (
                  <tr key={r.instance_id}>
                    <td className="cell-primary">{r.name}<div className="cell-sub">{instanceLabel(r.instance_id, null, locale)}</div></td>
                    <td data-label={t("prov.opt.shadowDone")}>{r.shadow_import_done ? <StatusChip tone="success" icon={null}>{t("prov.yes")}</StatusChip> : <span className="t-3">{t("prov.no")}</span>}</td>
                    <td data-label={t("prov.opt.phase")}>{r.phase ? <StatusChip tone={r.phase === "failed" ? "danger" : r.phase === "optimized" || r.phase === "cleaned" ? "success" : "warning"} icon={null}>{t(`prov.phase.${r.phase}` as MessageKey)}</StatusChip> : <span className="t-3">—</span>}{r.error ? <div className="t-danger" style={{ fontSize: 14 }}>{r.error}</div> : null}</td>
                    <td data-label={t("prov.opt.snapshot")} className="num">{r.snapshot_id ? `#${r.snapshot_id}` : "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </ResourceView>
      </Card>

      {preview ? (
        <Card title={t("prov.opt.previewTitle")} icon={Sparkles} sub={preview.message_nl}
          actions={<Button size="sm" variant="primary" disabled={!can("admin") || !preview.instances.some((i) => !i.already && !i.error)} onClick={() => setConfirm("optimize")}>{t("prov.opt.apply")}</Button>}>
          <div className="stack" data-testid="opt-preview">
            {preview.instances.length === 0 ? <StateView kind="empty" compact title={t("prov.opt.noCandidates")} body={t("prov.opt.noCandidatesBody")} /> : null}
            {preview.instances.map((i) => (
              <section key={i.instance_id} className="prov-item">
                <h3 className="mig-section-title">{instanceLabel(i.instance_id, i.name, locale)}</h3>
                {i.already ? <InlineState tone="success">{i.message_nl}</InlineState> : null}
                {i.error ? <InlineState tone="danger">{i.error}</InlineState> : null}
                {i.impact ? (
                  <div className="impact" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))", margin: 0 }}>
                    <div><span>{t("prov.opt.candidates")}</span><strong>{num(i.impact.upgrade_candidates)}</strong></div>
                    <div><span>{t("prov.opt.titles")}</span><strong>{num(i.impact.upgrade_items)}</strong></div>
                    <div><span>{t("prov.opt.searches")}</span><strong>{num(i.impact.estimated_searches)}</strong></div>
                    <div><span>{t("prov.opt.days")}</span><strong>{num(i.impact.estimated_days)}</strong></div>
                  </div>
                ) : null}
                {i.impact ? <p className="t-2" style={{ fontSize: 14, margin: 0 }}>{i.impact.message_nl}</p> : null}
                {i.reassign ? <InlineState tone="info">{i.reassign.message_nl} {Object.entries(i.reassign.by_profile).map(([p, n]) => `${p}: ${num(n)}`).join(" · ")}</InlineState> : null}
                {i.text_nl?.length ? <pre className="payload-preview" style={{ margin: 0, maxHeight: 220, overflow: "auto" }}>{i.text_nl.join("\n")}</pre> : null}
                {i.keeps_nl?.length ? <div><strong style={{ fontSize: 14 }}>{t("prov.opt.keeps")}</strong><ul className="t-2" style={{ fontSize: 14, margin: "4px 0 0", paddingLeft: 18 }}>{i.keeps_nl.map((k) => <li key={k}>{k}</li>)}</ul></div> : null}
                {i.no_renames_nl ? <p className="hint" style={{ margin: 0 }}>{i.no_renames_nl}</p> : null}
              </section>
            ))}
          </div>
        </Card>
      ) : null}

      {result ? (
        <Card title={t("prov.opt.resultTitle")} actions={<Button size="sm" variant="ghost" onClick={() => setResult(null)}>{t("action.close")}</Button>}>
          <div className="stack-sm">
            <StatusChip tone={result.status === "succeeded" ? "success" : "warning"}>{t(`prov.runStatus.${result.status}` as MessageKey)}</StatusChip>
            {result.instances.map((r, n) => <InlineState key={n} tone={r.error ? "danger" : "success"}>{String(r.instance_id)}: {String(r.error ?? r.status ?? t("prov.runStatus.succeeded"))}</InlineState>)}
          </div>
        </Card>
      ) : null}

      {cleanup ? (
        <Card title={t("prov.opt.cleanupTitle")} icon={Trash2} sub={cleanup.message_nl}
          actions={<>{cleanup.requires_confirmation ? <Button size="sm" variant="danger" disabled={!can("admin") || !cleanup.instances.some((i) => i.profiles.some((p) => !p.in_use))} pending={pending === "cc"} onClick={() => void run<CleanupPreview>("cc", "post", "/provisioning/optimize-migrated/cleanup", { confirm: true }, t("prov.opt.cleaned")).then((r) => { if (r) { setCleanup(r); reload(); } })}>{t("prov.opt.cleanupConfirm")}</Button> : null}<Button size="sm" variant="ghost" onClick={() => setCleanup(null)}>{t("action.close")}</Button></>}>
          {cleanup.instances.length === 0 ? <p className="t-3" style={{ fontSize: 14, margin: 0 }}>{t("prov.opt.cleanupNone")}</p> : (
            <ul className="list">{cleanup.instances.flatMap((i) => i.profiles.map((p) => (
              <li key={`${i.instance_id}-${p.profile_id}`} className="list-row"><span className="tag">{instanceLabel(i.instance_id, null, locale)}</span><div className="list-main"><div className="list-title">{p.name}</div><div className="list-sub">{t("prov.opt.wasNamed", { name: p.original })}</div></div>
                {p.deleted ? <StatusChip tone="success" icon={null}>{t("prov.opt.deleted")}</StatusChip> : p.in_use ? <StatusChip tone="warning" icon={null}>{t("prov.opt.inUse")}</StatusChip> : <StatusChip tone="neutral" icon={null}>{t("prov.opt.removable")}</StatusChip>}</li>
            )))}</ul>
          )}
        </Card>
      ) : null}

      <Modal open={confirm === "optimize"} onClose={() => setConfirm(null)} icon={Sparkles} title={t("prov.opt.confirmTitle")} description={t("prov.opt.confirmBody")}
        footer={<><Button onClick={() => setConfirm(null)} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" pending={pending === "oa"} onClick={() => void run<{ status: string; instances: Array<Record<string, unknown>> }>("oa", "post", "/provisioning/optimize-migrated", { confirm: true, instances: preview?.instances.filter((i) => !i.already && !i.error).map((i) => i.instance_id) }, t("prov.opt.done")).then((r) => { if (r) { setResult(r); setPreview(null); setConfirm(null); reload(); } })}>{t("prov.opt.apply")}</Button></>} />
      <Modal open={confirm === "rollback"} onClose={() => setConfirm(null)} danger icon={RotateCcw} title={t("prov.opt.rollbackTitle")} description={rollback?.message_nl}
        footer={<><Button onClick={() => setConfirm(null)} data-autofocus>{t("action.cancel")}</Button><Button variant="danger-solid" disabled={!rollback?.instances.length} pending={pending === "ra"} onClick={() => void run<RollbackPreview>("ra", "post", "/provisioning/optimize-migrated/rollback", { confirm: true }, t("prov.opt.rolledBack")).then((r) => { if (r) { setRollback(r); setConfirm(null); reload(); } })}>{t("prov.opt.rollback")}</Button></>}>
        {rollback?.instances.length ? <ul className="list">{rollback.instances.map((i) => <li key={i.instance_id} className="list-row">{instanceLabel(i.instance_id, i.name, locale)}</li>)}</ul> : <p className="t-3" style={{ margin: 0 }}>{t("prov.opt.rollbackNone")}</p>}
      </Modal>
    </div>
  );
}

/** Rename files to the new naming scheme: preview (old → new, locked flagged) → select → confirm. */
export function RenameTab() {
  const { t, num } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const instances = useResource<Instance[]>("/instances");
  const [instance, setInstance] = useState("");
  // titels kiezen i.p.v. interne serie-/film-ID's te typen (M9)
  const [chosen, setChosen] = useState<Array<{ id: number; label: string }>>([]);
  const [preview, setPreview] = useState<RenamePreview | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState(false);
  const keyOf = (f: { owner_id: number; file_id: number }) => `${f.owner_id}:${f.file_id}`;
  const load = async () => {
    const list = chosen.map((c) => c.id);
    const r = await run<RenamePreview>("rp", "post", "/provisioning/rename/preview", { instance_id: instance, ids: list.length ? list : null });
    if (r) { setPreview(r); setPicked(new Set(r.files.filter((f) => !f.locked).map(keyOf))); }
  };
  const files = preview?.files.filter((f) => picked.has(keyOf(f))) ?? [];
  return (
    <Card title={t("prov.ren.title")} icon={FileSignature} sub={t("prov.ren.sub")}>
      <div className="stack">
        <div className="grid" style={{ gap: 12, alignItems: "end" }}>
          <div className="span-4"><Field label={t("prov.col.instance")} htmlFor="ren-inst"><select id="ren-inst" className="select" value={instance} onChange={(e) => { setInstance(e.target.value); setPreview(null); setChosen([]); }}><option value="">{t("prov.ren.choose")}</option>{(instances.data ?? []).filter((i) => !i.archived).map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}</select></Field></div>
          <div className="span-5">
            {instance ? <TitlePicker instanceId={instance} onPick={(p) => setChosen((c) => c.some((x) => x.id === p.item.provider_id) ? c : [...c, { id: p.item.provider_id, label: p.label }])} /> : null}
            {chosen.length ? <div className="row wrap" style={{ gap: 6, marginTop: 6 }}>{chosen.map((c) => <button key={c.id} type="button" className="filter-chip" aria-pressed="true" onClick={() => setChosen((x) => x.filter((y) => y.id !== c.id))} title={t("action.remove")}>{c.label} ×</button>)}</div> : <p className="hint">{t("prov.ren.titlesHint")}</p>}
          </div>
          <div className="span-3"><Button variant="primary" disabled={!instance || !can("operator")} pending={pending === "rp"} onClick={() => void load()}>{t("prov.ren.preview")}</Button></div>
        </div>
        {preview ? (
          <>
            <InlineState tone="info">{preview.message_nl}</InlineState>
            {preview.files.length === 0 ? <StateView kind="empty" compact title={t("prov.ren.none")} /> : (
              <div className="table-wrap" style={{ maxHeight: 460, overflow: "auto" }}>
                <table className="table table--compact table--stack">
                  <thead><tr><th style={{ width: 36 }}><input type="checkbox" aria-label={t("table.selectAll")} checked={files.length === preview.files.filter((f) => !f.locked).length} onChange={(e) => setPicked(e.target.checked ? new Set(preview.files.filter((f) => !f.locked).map(keyOf)) : new Set())} /></th><th>{t("prov.ren.old")}</th><th>{t("prov.ren.new")}</th></tr></thead>
                  <tbody>{preview.files.map((f) => (
                    <tr key={keyOf(f)}>
                      <td><input type="checkbox" aria-label={t("prov.ren.pick", { path: f.existing_path ?? "" })} disabled={f.locked} checked={picked.has(keyOf(f))} onChange={(e) => setPicked((s) => { const n = new Set(s); if (e.target.checked) n.add(keyOf(f)); else n.delete(keyOf(f)); return n; })} /></td>
                      <td data-label={t("prov.ren.old")} className="mono" style={{ fontSize: 14, whiteSpace: "normal", overflowWrap: "anywhere" }}>{f.existing_path}{f.locked ? <> <StatusChip tone="warning" icon={null}>{t("prov.ren.locked")}</StatusChip></> : null}</td>
                      <td data-label={t("prov.ren.new")} className="mono" style={{ fontSize: 14, whiteSpace: "normal", overflowWrap: "anywhere" }}>{f.new_path}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            {preview.truncated ? <p className="hint">{t("prov.ren.truncated", { count: num(preview.count) })}</p> : null}
            <div><Button variant="primary" icon={FileSignature} disabled={!can("admin") || files.length === 0} onClick={() => setConfirm(true)}>{t("prov.ren.apply", { count: num(files.length) })}</Button></div>
          </>
        ) : null}
      </div>
      <Modal open={confirm} onClose={() => setConfirm(false)} icon={FileSignature} title={t("prov.ren.confirmTitle", { count: num(files.length) })} description={t("prov.ren.confirmBody")}
        footer={<><Button onClick={() => setConfirm(false)} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" pending={pending === "ra"} onClick={() => void run<{ renamed_files: number }>("ra", "post", "/provisioning/rename/apply", { instance_id: instance, files: files.map((f) => ({ owner_id: f.owner_id, file_id: f.file_id })), confirm: true }, t("prov.ren.done")).then((r) => { if (r) { setConfirm(false); setPreview(null); } })}>{t("prov.ren.applyShort")}</Button></>} />
    </Card>
  );
}
