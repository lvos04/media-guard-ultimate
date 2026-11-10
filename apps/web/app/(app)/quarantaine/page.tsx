"use client";

import { OrphansSummaryLine } from "@/components/mg/quarantine-orphans";

import { LibraryLockBanner } from "@/components/mg/library-lock";
import { ArchiveRestore, Brush, CircleCheck, CircleHelp, CircleX, HardDrive, History, Pin, RotateCcw, ShieldCheck, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import Link from "next/link";

import { DangerDialog } from "@/components/mg/domain";
import { RowMenu } from "@/components/mg/row-menu";
import { SpaceTabs } from "@/components/shell/space-tabs";
import { checkDetailText, instanceLabel, readableRelease } from "@/lib/labels";
import { Button, Card, InlineState, Modal, PageHeader, Progress, Segmented, StatusChip, type Tone } from "@/components/mg/primitives";
import { Term } from "@/components/mg/term";
import { SettingsLink } from "@/components/mg/settings-link";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { useAction } from "@/lib/api/actions";
import { api, ApiError } from "@/lib/api/client";
import type { BulkDeletePreview, EffectiveSetting, QuarantineItem, QuarantineUsage, ReleaseCheck } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const TONE: Record<QuarantineItem["status"], Tone> = { pending: "accent", held: "warning", restorable: "info", restored: "success", purged: "neutral", discarded: "neutral" };
// 'discarded' = vervanging geannuleerd: er staat geen kopie in quarantaine, het origineel staat nog op zijn plek
const DELETABLE = new Set(["restorable", "restored"]);

type DeleteFlow =
  | { kind: "single"; item: QuarantineItem; force: boolean; error?: string | null }
  | { kind: "bulk"; ids: number[]; preview: BulkDeletePreview; force: boolean; error?: string | null };

/** Door een terugzetting verdrongen (afgekeurde) AV1-versie: geen 'origineel', geen geslaagde controle (R11). */
const isRejectedAv1 = (r: { reason: string | null; rejected_av1?: boolean }) => Boolean(r.rejected_av1) || /herstel van restore/i.test(r.reason ?? "");

function ReleaseCheckChip({ check, rejected }: { check: ReleaseCheck | null | undefined; rejected?: boolean }) {
  const { t } = useI18n();
  // Peil §6.2: gemeten controle geslaagd = Gezond; mislukt = Storing; nog niet gecontroleerd = Onbekend
  if (rejected) return <StatusChip tone="warning">{t("quarantine.check.failed")}</StatusChip>;
  if (!check || check.status === "pending") return <StatusChip tone="unknown">{t("quarantine.check.pending")}</StatusChip>;
  if (check.status === "passed") return <StatusChip tone="healthy">{t("quarantine.check.passed")}</StatusChip>;
  return <StatusChip tone="danger">{t("quarantine.check.failed")}</StatusChip>;
}

/**
 * De echte modus, alleen-lezen: wijzigen gebeurt op één plek (Instellingen → Originelen & herstellen).
 * Bij 'direct verwijderen' staat duidelijk dat terugzetten niet kan (H1).
 */
/** Eén getal voor de quarantaine (A1): incl. originelen die nog gecontroleerd worden. */
function quarantineTotal(q: QuarantineUsage): number {
  // used_bytes telt alles wat in de quarantainemap staat (ook 'nog te controleren'); core 1.0.1+
  // levert total_bytes expliciet
  return q.total_bytes ?? q.used_bytes; // core: used_bytes is het ene getal (incl. nog te controleren)
}
function quarantinePercent(q: QuarantineUsage): number {
  if (typeof q.total_percent === "number") return q.total_percent;
  return q.quota_bytes ? (quarantineTotal(q) / q.quota_bytes) * 100 : q.percent;
}

/** "Vangnet: originelen blijven nog 48 uur (max. 500 GB) — 30 items, 438 GB, eerste gaat weg op …" (N8). */
function SafetyNetLine({ net, total }: { net: NonNullable<QuarantineUsage["safety_net"]>; total?: number }) {
  const { t, num, bytes, dateTime } = useI18n();
  const max = net.max_bytes ?? net.bytes;
  return (
    <span data-testid="safety-net">
      <strong>{t("safetyNet.title")}:</strong> {t("safetyNet.body", { hours: num(net.hours), size: max ? bytes(max) : "—" })}
      {typeof net.items === "number" ? <> — {t("safetyNet.items", { count: num(net.items), size: bytes(total ?? net.used_bytes ?? net.bytes ?? 0) })}</> : null}
      {net.first_expiry_at ? <>, {t("safetyNet.firstExpiry", { when: dateTime(net.first_expiry_at) })}</> : null}.{" "}
      {t("safetyNet.restorable")}{" "}
      <Link className="link-more" href="/instellingen?open=originelen#originelen">{t("safetyNet.setting")}</Link>
    </span>
  );
}

function QuarantineModeNotice({ usage, onChanged }: { usage: QuarantineUsage | null; onChanged?: () => void }) {
  const { t, num } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const [confirmUse, setConfirmUse] = useState(false);
  const eff = useResource<EffectiveSetting[]>("/settings/effective", { keys: "quarantine.mode" });
  const raw = (usage as { mode?: string } | null)?.mode ?? (eff.data?.[0]?.value as string | undefined);
  if (!raw) return null;
  const immediate = raw === "immediate" || raw === "immediate_after_check";
  const net = usage?.safety_net && usage.safety_net.hours > 0 ? usage.safety_net : null;
  return (
    <div style={{ marginBottom: 16 }} data-testid="quarantine-mode" data-mode={immediate ? "immediate" : "retain"}>
      <InlineState tone={immediate ? (net ? "warning" : "danger") : "success"} icon={immediate ? (net ? TriangleAlert : undefined) : ShieldCheck} role="status">
        <strong>{immediate && net ? t("quarantine.modeNow.immediateNet") : immediate ? t("quarantine.modeNow.immediate") : t("quarantine.modeNow.retain", { days: num(usage?.retention_days ?? 0) })}</strong>{" "}
        {immediate && net ? <SafetyNetLine net={net} total={usage ? quarantineTotal(usage) : undefined} /> : immediate ? t("quarantine.modeNow.immediateBody") : t("quarantine.modeNow.retainBody")}{" "}
        {immediate && can("operator") ? <Button size="sm" variant="primary" icon={ShieldCheck} pending={pending === "use-q"} data-testid="use-quarantine"
          onClick={() => setConfirmUse(true)}>{t("quarantine.use")}</Button> : null}{" "}
        <Link className="btn btn--sm" href="/instellingen?open=originelen#originelen">{t("quarantine.modeNow.change")}</Link>
      </InlineState>
      <Modal open={confirmUse} onClose={() => setConfirmUse(false)} title={t("quarantine.useConfirmTitle")} icon={ShieldCheck}
        footer={<>
          <Button onClick={() => setConfirmUse(false)}>{t("action.cancel")}</Button>
          <Button variant="primary" icon={ShieldCheck} pending={pending === "use-q"} data-testid="use-quarantine-confirm"
            onClick={() => void run("use-q", "post", "/quarantine/use-quarantine", { confirm: true }, t("quarantine.useDone")).then(() => { setConfirmUse(false); void eff.reload(); onChanged?.(); })}>{t("quarantine.useConfirm")}</Button>
        </>}>
        <p style={{ fontSize: 14 }}>{t("quarantine.useConfirmBody")}</p>
      </Modal>
    </div>
  );
}

export default function QuarantinePage() {
  const { t, bytes, dateTime, num, locale } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const [status, setStatus] = useState("");
  const items = useResource<QuarantineItem[]>("/quarantine", { status: status || undefined });
  const usage = useResource<QuarantineUsage>("/quarantine/usage", undefined, 60_000);
  const readiness = useResource<{ ready: boolean; reasons: string[]; quarantine_dir: string }>("/recovery/readiness", undefined, 60_000);
  const reloadAll = () => { void items.reload(); void usage.reload(); };
  // de kop telt wat de lijst toont (N18): zonder filter uit de lijst zelf, anders uit core
  const listCounts = !status && items.data ? items.data.reduce((acc, r) => {
    const st = r.release_check?.status;
    if (st === "passed") acc.passed += 1; else if (st === "failed") acc.failed += 1; else acc.pending += 1;
    return acc;
  }, { passed: 0, failed: 0, pending: 0 }) : null;
  useCoreEvents("quarantine", reloadAll);
  useCoreEvents("recovery", reloadAll);
  useCoreEvents("settings.changed", () => void usage.reload());
  const { run, pending } = useAction();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [restore, setRestore] = useState<QuarantineItem[] | null>(null);
  const [del, setDel] = useState<DeleteFlow | null>(null);
  const [delPending, setDelPending] = useState(false);
  // paused_nl: opruimen staat stil (TrueNAS-pool niet gezond, verbeterlijst 37)
  const [purge, setPurge] = useState<{ count: number; bytes: number; paused_nl?: string | null } | null>(null);
  const restoreTotal = (restore ?? []).reduce((s, i) => s + (i.size ?? 0), 0);
  const u = usage.data;
  const immediate = (u as { mode?: string } | null)?.mode === "immediate";
  // ?item=<id> (vanuit Activiteit/'Wacht op jou'): naar die rij scrollen en hem markeren
  const [focusId, setFocusId] = useState<number | null>(null);
  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get("item"));
    if (Number.isInteger(id) && id > 0) setFocusId(id);
  }, []);
  const loaded = Boolean(items.data);
  useEffect(() => {
    if (!focusId || !loaded) return;
    document.querySelector(`[data-testid="q-item-${focusId}"]`)?.scrollIntoView({ block: "center" });
  }, [focusId, loaded]);

  const doRestore = async () => {
    if (!restore) return;
    for (const item of restore) {
      const r = await run(`restore-${item.id}`, "post", `/quarantine/${item.id}/restore`, { confirm: true, reason: "UI" });
      if (r === undefined) return;
    }
    toast.push("success", t("quarantine.restoreQueued"));
    setRestore(null);
    setSelected(new Set());
    reloadAll();
  };

  const startBulkDelete = async () => {
    const ids = [...selected];
    try {
      const preview = await api.post<BulkDeletePreview>("/quarantine/bulk-delete", { item_ids: ids, confirm: false });
      setDel({ kind: "bulk", ids, preview, force: false });
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: e instanceof Error ? e.message : String(e) }));
    }
  };
  const confirmDelete = async () => {
    if (!del) return;
    setDelPending(true);
    try {
      if (del.kind === "single") {
        await api.del(`/quarantine/${del.item.id}`, { confirm: true, reason: "UI", force_unverified: del.force });
        toast.push("success", t("quarantine.deleteQueued", { size: bytes(del.item.size) }));
      } else {
        const r = await api.post<BulkDeletePreview>("/quarantine/bulk-delete", { item_ids: del.ids, confirm: true, force_unverified: del.force, reason: "UI bulk" });
        toast.push("success", t("quarantine.deleteQueued", { size: bytes(r.bytes) }));
      }
      setDel(null);
      setSelected(new Set());
      reloadAll();
    } catch (e) {
      if (del.kind === "single" && e instanceof ApiError && e.status === 409 && !del.force && e.message.includes("force_unverified")) {
        // release check not passed → second, explicit warning step
        setDel({ ...del, force: true, error: e.message });
      } else {
        setDel({ ...del, error: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      setDelPending(false);
    }
  };
  const startPurge = async () => {
    try { setPurge(await api.post<{ count: number; bytes: number; paused_nl?: string | null }>("/quarantine/purge", { confirm: false })); } catch (e) { toast.push("danger", t("toast.failed", { message: e instanceof Error ? e.message : String(e) })); }
  };

  const bulkPreview = del?.kind === "bulk" ? del.preview : null;
  const bulkCount = bulkPreview ? (del?.force ? bulkPreview.count + bulkPreview.unverified.length : bulkPreview.count) : 0;

  return (
    <>
      <LibraryLockBanner />
      <PageHeader title={t("quarantine.pageTitle")} sub={<>{t("quarantine.pageSub")} · <Term k="quarantaine" /> · <Term k="vrijgavecontrole" /> · <Term k="overdracht" /></>} />
      <SpaceTabs />
      <div className="row wrap" style={{ marginBottom: 16, justifyContent: "space-between" }}><>
        <Segmented label={t("library.col.status")} value={status} onChange={(v) => { setStatus(v); setSelected(new Set()); }} options={[{ value: "", label: t("media.all") }, { value: "restorable", label: t("quarantine.status.restorable") }, { value: "held", label: t("quarantine.status.held") }, { value: "restored", label: t("quarantine.status.restored") }]} />
        <Button icon={Brush} variant="ghost" disabled={!can("admin")} onClick={() => void startPurge()} data-testid="purge-button">{t("quarantine.purgeNow")}</Button>
      </></div>

      <section className="card" style={{ padding: 16, marginBottom: 16 }} aria-label={t("quarantine.usageTitle")} data-testid="quarantine-usage">
        <ResourceView resource={usage} endpoint="GET /api/v1/quarantine/usage" compact>
          {(q) => (
            <div className="stack-sm">
              <div className="row-between wrap">
                <div className="row" style={{ gap: 10 }}>
                  <span className={`list-icon list-icon--${q.quota_exceeded ? "danger" : q.percent > 80 ? "warning" : "accent"}`}><HardDrive className="ico" aria-hidden="true" /></span>
                  <div><div className="eyebrow">{t("quarantine.usageTitle")}</div><div style={{ fontSize: 20, fontWeight: 680 }} className="num" data-testid="quarantine-total">{t("quarantine.usageValue", { used: bytes(quarantineTotal(q)), quota: bytes(q.quota_bytes) })}</div>
                    <div className="t-2" style={{ fontSize: 14 }}>{(q.unchecked_bytes ?? q.pending_bytes) ? t("q37.total", { used: bytes(quarantineTotal(q)), quota: bytes(q.cap_bytes ?? q.quota_bytes), pending: bytes((q.unchecked_bytes ?? q.pending_bytes) as number) }) : t("q37.pendingNone")}</div></div>
                </div>
                <div className="row wrap" style={{ gap: 6 }}>
                  {/* Peil: een telling is neutraal; alleen een niet-nul "mislukt" krijgt de Storing-vorm */}
                  <StatusChip tone="success">{t("quarantine.checksPassed", { count: num(listCounts?.passed ?? q.release_checks.passed ?? 0) })}</StatusChip>
                  <StatusChip tone={(listCounts?.failed ?? q.release_checks.failed ?? 0) > 0 ? "danger" : "neutral"} icon={CircleX}>{t("quarantine.checksFailed", { count: num(listCounts?.failed ?? q.release_checks.failed ?? 0) })}</StatusChip>
                  <StatusChip tone="neutral" icon={CircleHelp}>{t("quarantine.checksPending", { count: num(listCounts?.pending ?? q.release_checks.pending ?? 0) })}</StatusChip>
                </div>
              </div>
              <Progress value={quarantinePercent(q)} tone={q.quota_exceeded ? "danger" : quarantinePercent(q) > 80 ? "warning" : undefined} label={t("quarantine.usageTitle")} />
              {q.purge_target_bytes ? <p className="t-2" style={{ margin: 0, fontSize: 14 }} data-testid="purge-target">{t("q37.target", { target: bytes(q.purge_target_bytes), percent: num(q.purge_target_percent ?? (q.quota_bytes ? (q.purge_target_bytes / q.quota_bytes) * 100 : 0), 0) })}{(q.reserve_bytes ?? q.reserved_bytes) ? ` ${t("q37.reserve", { size: bytes((q.reserve_bytes ?? q.reserved_bytes) as number) })}` : ""}</p> : null}
              <div className="row-between wrap t-2" style={{ fontSize: 14 }}>
                <span className="num">{num(quarantinePercent(q), 1)}% · {immediate && q.safety_net?.hours ? `${t("safetyNet.title")}: ${t("safetyNet.body", { hours: num(q.safety_net.hours), size: bytes(q.safety_net.max_bytes ?? q.safety_net.bytes ?? 0) })}` : immediate ? t("quarantine.noRetention") : t("quarantine.retentionDays", { days: num(q.retention_days) })}</span>
                <span data-testid="next-purge">{!q.auto_purge ? t("quarantine.autoPurgeOff")
                  : q.next_purge.count > 0 || q.next_purge.at || q.safety_net?.first_expiry_at
                    ? q.next_purge.at_nl ? q.next_purge.at_nl
                    : (q.next_purge.at ?? q.safety_net?.first_expiry_at)
                      ? t("quarantine.nextPurgeAt", { count: num(q.next_purge.count || q.safety_net?.items || 0), size: bytes(q.next_purge.bytes || q.safety_net?.used_bytes || q.safety_net?.bytes || 0), when: dateTime((q.next_purge.at ?? q.safety_net?.first_expiry_at) as string) })
                      : t("quarantine.nextPurge", { count: num(q.next_purge.count), size: bytes(q.next_purge.bytes) })
                    : t("quarantine.nextPurgeNone")}</span>
              </div>
              {q.encode_queue_paused ? <div data-testid="queue-paused"><InlineState tone="warning" icon={TriangleAlert} role="status"><strong>{t("q37.pausedTitle")}</strong> {q.queue_pause_reason_nl ?? t("q37.pausedBody")}</InlineState></div> : null}
              <OrphansSummaryLine />
            </div>
          )}
        </ResourceView>
      </section>

      <QuarantineModeNotice usage={u ?? null} onChanged={() => void usage.reload()} />

      {readiness.data && !readiness.data.ready ? (
        <div style={{ marginBottom: 16 }}><InlineState tone="gate"><strong>{t("quarantine.notReady")}</strong>{/* Peil GateBlokkade: slot + elke voorwaarde op een eigen regel */}
          <ul className="mt-1 list-disc pl-5">{readiness.data.reasons.map((r) => <li key={r}>{r}</li>)}</ul></InlineState></div>
      ) : null}

      <div className="stack">
        <Card title={t("quarantine.items")} icon={ArchiveRestore} flush actions={selected.size ? <>
          <span className="t-2" style={{ fontSize: 14 }}>{t("table.selected", { count: selected.size })}</span>
          <Button size="sm" icon={RotateCcw} disabled={!can("operator")} onClick={() => setRestore((items.data ?? []).filter((i) => selected.has(i.id) && i.status === "restorable"))}>{t("quarantine.restore")}</Button>
          <Button size="sm" variant="danger" icon={Trash2} disabled={!can("admin")} onClick={() => void startBulkDelete()} data-testid="bulk-delete">{t("quarantine.deleteSelected")}</Button>
        </> : null}>
          <ResourceView resource={items} endpoint="GET /api/v1/quarantine" isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("quarantine.empty")} body={t("quarantine.emptyBody")} />}>
            {(rows) => (
              <div className="table-wrap">
                <table className="table table--stack">
                  <thead><tr><th style={{ width: 52 }} /><th>{t("quarantine.item")}</th><th>{t("quarantine.releaseCheck")}</th><th>{t("quarantine.state")}</th><th className="num">{t("library.col.size")}</th><th>{t("jobs.created")}</th><th /></tr></thead>
                  <tbody>{rows.map((r) => (
                    <tr key={r.id} aria-selected={selected.has(r.id)} aria-current={focusId === r.id ? "true" : undefined} data-testid={`q-item-${r.id}`} style={focusId === r.id ? { outline: "2px solid var(--mg-accent)", outlineOffset: -2 } : undefined}>
                      <td className="hide-mobile"><label className="check-hit"><input type="checkbox" className="check" aria-label={t("table.selectRow", { name: r.original_path.split("/").pop() ?? String(r.id) })} checked={selected.has(r.id)} onChange={() => setSelected((s) => { const n = new Set(s); if (n.has(r.id)) n.delete(r.id); else n.add(r.id); return n; })} /></label></td>
                      <td className="cell-primary"><div className="cell-title">{readableRelease(r.original_path)}</div><div className="cell-sub">{instanceLabel(r.instance_id, null, locale)}{(r as { kind_nl?: string }).kind_nl ? ` · ${(r as { kind_nl?: string }).kind_nl}` : isRejectedAv1(r) ? ` · ${t("quarantine.purgedRejected")}` : r.reason ? ` · ${checkDetailText(r.reason, locale)}` : ""}</div>
                        {r.release_check?.status === "failed" ? (() => {
                          const own = (r as { release_check_reason_nl?: string | null }).release_check_reason_nl;
                          const why = own ? [own] : (r.release_check.checks ?? []).filter((c) => c.passed === false).map((c) => checkDetailText(c.detail || c.name, locale)).filter(Boolean);
                          return why.length ? <div className="cell-sub t-danger" style={{ marginTop: 2 }} data-testid={`q-reason-${r.id}`}>{t("quarantine.whyFailed")} {why.join(" · ")}</div> : null;
                        })() : null}
                        <details className="tech-details"><summary>{t("state.techDetails")}</summary><span className="mono" style={{ overflowWrap: "anywhere" }}>{r.original_path} · tx {r.transaction_id} · #{r.id}</span></details></td>
                      <td data-label={t("quarantine.releaseCheck")}>
                        {r.release_check?.checks?.length ? (
                          <details>
                            <summary style={{ cursor: "pointer", listStyle: "none" }}><ReleaseCheckChip check={r.release_check} rejected={isRejectedAv1(r)} /></summary>
                            <div className="stack-sm" style={{ marginTop: 6 }}>
                              {r.release_check.checks.map((c) => <div key={c.name} className="row" style={{ fontSize: 14, alignItems: "flex-start" }}>{c.passed ? <CircleCheck className="ico ico-sm t-success" aria-hidden="true" /> : c.passed === null ? <CircleHelp className="ico ico-sm t-3" aria-hidden="true" /> : <CircleX className="ico ico-sm t-danger" aria-hidden="true" />}<span><span className="mono t-3">{c.name}</span>{c.detail ? ` — ${checkDetailText(c.detail, locale)}` : ""}</span></div>)}
                              {r.release_check.checked_at ? <span className="t-3" style={{ fontSize: 14 }}>{dateTime(r.release_check.checked_at)}</span> : null}
                            </div>
                          </details>
                        ) : <ReleaseCheckChip check={r.release_check} rejected={isRejectedAv1(r)} />}
                      </td>
                      <td data-label={t("quarantine.state")}><span className="row wrap" style={{ gap: 4 }}><StatusChip tone={(r as { error?: boolean }).error ? "danger" : TONE[r.status]} icon={null}>{(r as { status_nl?: string }).status_nl ?? (isRejectedAv1(r) && r.status === "purged" ? t("quarantine.purgedRejected")
                        : r.status === "discarded" && ((r as { superseded_at?: string | null }).superseded_at ?? (r as { replaced_later_at?: string | null }).replaced_later_at) ? t("quarantine.cancelledLater", { when: dateTime(((r as { superseded_at?: string }).superseded_at ?? (r as { replaced_later_at?: string }).replaced_later_at) as string) })
                        : t(`quarantine.status.${r.status}`))}</StatusChip>{r.pinned ? <Pin className="ico ico-sm t-3" aria-label={t("quarantine.pinned")} /> : null}</span></td>
                      <td data-label={t("library.col.size")} className="num">{bytes(r.size)}</td>
                      <td data-label={t("jobs.created")}>{dateTime(r.created_at)}</td>
                      <td><div className="row" style={{ justifyContent: "flex-end" }}>
                        {r.status === "restorable" ? <Button size="sm" icon={RotateCcw} disabled={!can("operator")} onClick={() => setRestore([r])}>{t("quarantine.restore")}</Button> : null}
                        <RowMenu>
                          <Button size="sm" icon={ShieldCheck} disabled={!can("operator")} pending={pending === `rc-${r.id}`} onClick={() => void run(`rc-${r.id}`, "post", `/quarantine/${r.id}/release-check`, {}, t("quarantine.checkQueued")).then(reloadAll)}>{t("quarantine.checkNow")}</Button>
                          <Button size="sm" icon={Pin} disabled={!can("operator")} pending={pending === `pin-${r.id}`} onClick={() => void run(`pin-${r.id}`, "post", `/quarantine/${r.id}/pin${r.pinned ? "?pinned=false" : ""}`, {}, t("quarantine.pinned")).then(reloadAll)}>{r.pinned ? t("quarantine.unpin") : t("quarantine.pinShort")}</Button>
                          {DELETABLE.has(r.status) ? <Button size="sm" variant="danger" icon={Trash2} disabled={!can("admin")} onClick={() => setDel({ kind: "single", item: r, force: false })} data-testid={`delete-${r.id}`}>{t("quarantine.delete")}</Button> : null}
                        </RowMenu>
                      </div></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </ResourceView>
        </Card>
        <SettingsLink sections={["originelen"]} />
      </div>

      <DangerDialog open={Boolean(restore)} onClose={() => setRestore(null)} icon={History} pending={Boolean(pending?.startsWith("restore"))}
        title={t("quarantine.restoreTitle", { count: num(restore?.length ?? 0) })} description={t("quarantine.restoreBody")}
        impact={[{ label: t("dialog.impactCount"), value: num(restore?.length ?? 0) }, { label: t("dialog.impactSize"), value: bytes(restoreTotal) }, { label: t("quarantine.transaction"), value: restore?.map((r) => r.transaction_id).join(", ") ?? "—" }]}
        facts={[{ label: t("dialog.statusChange"), value: t("quarantine.restoreStatus") }, { label: t("dialog.rollback"), value: t("quarantine.restoreRollback") }]}
        confirmWord={(restore?.length ?? 0) > 1 ? t("quarantine.confirmWord") : undefined}
        confirmLabel={t("quarantine.restoreConfirm", { count: num(restore?.length ?? 0) })} onConfirm={() => void doRestore()} />

      <DangerDialog open={Boolean(del)} onClose={() => setDel(null)} icon={Trash2} pending={delPending}
        key={del ? `${del.kind}-${del.force}` : "none"}
        title={del?.kind === "single" ? t("quarantine.deleteTitle", { name: del.item.original_path.split("/").pop() ?? "" }) : t("quarantine.bulkDeleteTitle", { count: num(bulkCount) })}
        description={del?.force ? t("quarantine.deleteUnverifiedBody") : t("quarantine.deleteBody")}
        impact={[
          { label: t("dialog.impactCount"), value: del?.kind === "single" ? "1" : num(bulkCount) },
          { label: t("quarantine.freed"), value: del?.kind === "single" ? bytes(del.item.size) : bytes(bulkPreview?.bytes) },
          { label: t("quarantine.unverified"), value: del?.kind === "single" ? (del.item.release_check?.status === "passed" ? "0" : "1") : num(bulkPreview?.unverified.length ?? 0) },
        ]}
        facts={[
          { label: t("dialog.statusChange"), value: t("quarantine.deleteStatus") },
          { label: t("dialog.rollback"), value: <span className="t-danger">{t("quarantine.deleteNoRollback")}</span> },
          ...(bulkPreview?.blocked.length ? [{ label: t("quarantine.blocked"), value: t("quarantine.blockedValue", { count: num(bulkPreview.blocked.length) }) }] : []),
        ]}
        confirmWord={t("quarantine.deleteWord")} confirmLabel={del?.force ? t("quarantine.deleteForce") : t("quarantine.deleteConfirm")} error={del?.error && !del.force ? del.error : null}
        onConfirm={() => void confirmDelete()}>
        {del?.force ? <div style={{ marginTop: 12 }}><InlineState tone="danger" role="alert"><strong>{t("quarantine.unverifiedTitle")}</strong> {del.error ?? ""}</InlineState></div> : null}
        {del?.kind === "bulk" && (bulkPreview?.unverified.length ?? 0) > 0 ? (
          <label className="row" style={{ marginTop: 12, fontSize: 14, alignItems: "flex-start" }}>
            <input type="checkbox" className="check" checked={del.force} onChange={(e) => setDel({ ...del, force: e.target.checked })} data-testid="force-unverified" />
            <span>{t("quarantine.forceUnverified", { count: num(bulkPreview?.unverified.length ?? 0) })}</span>
          </label>
        ) : null}
      </DangerDialog>

      <DangerDialog open={Boolean(purge)} onClose={() => setPurge(null)} icon={Brush} pending={pending === "purge"}
        title={t("quarantine.purgeTitle", { count: num(purge?.count ?? 0) })} description={t("quarantine.purgeBody")}
        impact={[{ label: t("dialog.impactCount"), value: num(purge?.count ?? 0) }, { label: t("quarantine.freed"), value: bytes(purge?.bytes ?? 0) }, { label: t("quarantine.retentionDays", { days: num(u?.retention_days ?? 0) }), value: "" }]}
        facts={[{ label: t("dialog.rollback"), value: <span className="t-danger">{t("quarantine.deleteNoRollback")}</span> }]}
        confirmWord={(purge?.count ?? 0) > 0 ? t("quarantine.deleteWord") : undefined} confirmLabel={t("quarantine.purgeNow")}
        error={purge?.paused_nl ?? null}
        onConfirm={async () => { const r = await run("purge", "post", "/quarantine/purge", { confirm: true }, t("quarantine.purgeQueued")); if (r !== undefined) { setPurge(null); reloadAll(); } }} />
    </>
  );
}
