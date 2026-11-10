"use client";

import { CheckCheck, ClipboardList, Hand, Lock, RefreshCcw, Search, SquareMinus, SquarePen, SquarePlus, Wand2, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button, Card, InlineState, Modal, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { useAction } from "@/lib/api/actions";
import { api } from "@/lib/api/client";
import { useResource, type Resource } from "@/lib/api/hooks";
import { NEED_SECTION_TAB, SECTION_IDS, type ProvApplyResult, type ProvChange, type ProvPlan, type ProvStatus, type ProvStatusView } from "@/lib/api/provisioning";
import { useCoreEvents } from "@/lib/events";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { instanceLabel, plainText } from "@/lib/labels";

// Peil: "Gezond" (gemeten: de instantie komt overeen met de gewenste instelling) heeft de Gezond-vorm; "Nog niet
// gecontroleerd" is Onbekend (stippel), nooit een gewoon label.
export const PROV_TONE: Record<ProvStatus, Tone> = { ok: "healthy", drift: "warning", missing: "warning", error: "danger", needs_input: "info", skipped: "neutral", unknown: "unknown" };

/** Wijziging in een plan of voorbeeld (Peil C-20 diff): geen rood/groen; teken in een kader + woord. Alleen een echte
 *  fout is Storing; geblokkeerd is een voorwaarde (slot). */
export const ACTION_VIEW: Record<string, { tone: Tone; icon: LucideIcon | null }> = {
  create: { tone: "info", icon: SquarePlus }, update: { tone: "info", icon: SquarePen }, delete: { tone: "strong", icon: SquareMinus },
  blocked: { tone: "strong", icon: Lock }, error: { tone: "danger", icon: null }, needs_input: { tone: "neutral", icon: null },
  info: { tone: "info", icon: null }, noop: { tone: "neutral", icon: null },
};
export function ActionChip({ action }: { action: string }) {
  const { t } = useI18n();
  const v = ACTION_VIEW[action] ?? ACTION_VIEW.noop;
  return <StatusChip tone={v.tone} icon={v.icon}>{t(`prov.action.${action}` as MessageKey)}</StatusChip>;
}

export function ProvChip({ status, compact }: { status: ProvStatus | string; compact?: boolean }) {
  const { t } = useI18n();
  const s = (status in PROV_TONE ? status : "unknown") as ProvStatus;
  const label = t(`prov.status.${s}` as MessageKey);
  return compact
    ? <span className={`prov-cell prov-cell--${s}`} title={label} role="img" aria-label={label} />
    : <StatusChip tone={PROV_TONE[s]} icon={s === "skipped" ? null : undefined}>{label}</StatusChip>;
}

export function sectionLabel(t: (k: MessageKey) => string, id: string): string {
  return t(`prov.section.${id}` as MessageKey);
}

export function ProvOverview({ status, onTab }: { status: Resource<ProvStatusView>; onTab: (tab: string) => void }) {
  const { t, relative, locale } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const [plan, setPlan] = useState<ProvPlan | null>(null);
  // klik op een vakje: precies het verschil van dat onderdeel (gewenst ↔ huidig), N17
  const [focus, setFocus] = useState<{ instance: string; section: string } | null>(null);
  const openCell = (instance: string, section: string) => {
    setFocus({ instance, section });
    if (plan) return;
    void run<ProvPlan>(`cell-${instance}-${section}`, "post", "/provisioning/plan", {}).then((p) => { if (p) setPlan(p); });
  };
  const [confirm, setConfirm] = useState(false);
  const [applied, setApplied] = useState<ProvApplyResult | null>(null);
  useCoreEvents("provisioning", () => void status.reload());

  const [checking, setChecking] = useState(false);
  const toast = useToast();
  // "Controleren": the core re-reads every instance (GET /status?refresh=true)
  const check = async () => {
    setChecking(true);
    try {
      const fresh = await api.get<ProvStatusView>("/provisioning/status", { refresh: true });
      status.setData(() => fresh);
      toast.push("success", t("prov.checked"));
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: e instanceof Error ? e.message : String(e) }));
    } finally {
      setChecking(false);
    }
  };
  return (
    <div className="stack">
      <ResourceView resource={status} endpoint="GET /api/v1/provisioning/status">
        {(s) => (
          <div className="stack">
            {s.active_shadow_phase.length ? <InlineState tone="warning">{t("prov.shadowPhase", { list: s.active_shadow_phase.join(", ") })}</InlineState> : null}
            {s.needs.length ? (
              <Card title={t("prov.needs.title")} icon={ClipboardList} sub={s.ready ? t("prov.needs.readySub") : t("prov.needs.notReady")}>
                <ul className="list" data-testid="prov-needs">
                  {s.needs.map((n) => {
                    const target = NEED_SECTION_TAB[n.section];
                    return (
                      <li key={n.key} className="list-row" data-need={n.key}>
                        <StatusChip tone={n.severity === "required" ? "strong" : "neutral"} icon={null}>{t(`prov.severity.${n.severity}` as MessageKey)}</StatusChip>
                        <div className="list-main"><div className="list-title" style={{ whiteSpace: "normal" }}>{n.message_nl}</div></div>
                        {target ? (target.startsWith("/") ? <Link className="link-more" href={target}>{t("prov.fix")}</Link> : <button type="button" className="link-more" onClick={() => onTab(target)}>{t("prov.fix")}</button>) : null}
                      </li>
                    );
                  })}
                </ul>
              </Card>
            ) : <InlineState tone="success">{t("prov.needs.none")}</InlineState>}
            <Card title={t("prov.grid.title")} icon={Wand2} flush
              sub={s.last_runs.drift ? t("prov.lastDrift", { age: relative(s.last_runs.drift.finished_at ?? s.last_runs.drift.started_at ?? null) }) : undefined}
              actions={<>
                <Button size="sm" icon={RefreshCcw} pending={checking} onClick={() => void check()}>{t("prov.check")}</Button>
                {/* 'Toepassen' alleen als hoofdknop wanneer er echt iets afwijkt (M11) */}
                <Button size="sm" variant={deviating(s) ? "primary" : "ghost"} icon={CheckCheck} disabled={!can("admin")} onClick={() => setConfirm(true)} data-testid="prov-apply">{deviating(s) ? t("prov.applyN", { count: deviating(s) }) : t("prov.apply")}</Button>
                <details className="more-actions">
                  <summary className="btn btn--sm btn--ghost">{t("prov.more")}</summary>
                  <div className="more-actions-menu">
                    <Button size="sm" icon={Search} disabled={!can("operator")} pending={pending === "drift"} onClick={() => void run("drift", "post", "/provisioning/drift-check", {}, t("prov.driftDone")).then(() => status.reload())}>{t("prov.driftCheck")}</Button>
                    <Button size="sm" icon={ClipboardList} disabled={!can("operator")} pending={pending === "plan"} onClick={() => void run<ProvPlan>("plan", "post", "/provisioning/plan", {}).then((p) => { if (p) { setPlan(p); void status.reload(); } })}>{t("prov.viewChanges")}</Button>
                  </div>
                </details>
              </>}>
              <div className="table-wrap">
                <table className="table table--compact table--stack prov-grid" data-testid="prov-grid">
                  <thead><tr><th>{t("prov.col.instance")}</th>{SECTION_IDS.map((id) => <th key={id} className="center" title={sectionLabel(t, id)}><span className="prov-th">{sectionLabel(t, id)}</span></th>)}</tr></thead>
                  <tbody>{s.instances.map((inst) => (
                    <tr key={inst.instance_id} data-instance={inst.instance_id}>
                      <td className="cell-primary">
                        <div className="cell-title">{inst.name}</div>
                        <div className="cell-sub">{inst.mode_nl}{inst.shadow_mode ? ` · ${t("status.shadow")}` : ""}{inst.suspended ? ` · ${t("status.paused")}` : ""}</div>
                      </td>
                      {SECTION_IDS.map((id) => {
                        const sec = inst.sections[id];
                        const st = sec?.status ?? "unknown";
                        return <td key={id} data-label={sectionLabel(t, id)} className="center" title={`${sectionLabel(t, id)}: ${sec?.message_nl ?? ""}`}>
                          {st === "ok" || st === "skipped" || st === "unknown"
                            ? <ProvChip status={st} compact />
                            : <button type="button" className="prov-cell-btn" aria-label={t("prov.showDiff", { section: sectionLabel(t, id), name: inst.name })} data-testid={`prov-cell-${inst.instance_id}-${id}`}
                                style={{ border: 0, background: "none", padding: 0, cursor: "pointer" }} onClick={() => openCell(inst.instance_id, id)}><ProvChip status={st} compact /></button>}
                        </td>;
                      })}
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              {/* de echte weigering van Sonarr/Radarr per onderdeel, en of MediaGuard ermee is gestopt (R7) */}
              {(() => {
                const failing = (s as { failing?: Array<{ key: string; count?: number; error?: string; instance_id?: string | null; section?: string; title_nl?: string; gave_up?: boolean }> }).failing;
                const errs = failing ? failing.map((f) => ({
                  key: f.key, name: f.instance_id ? (s.instances.find((i) => i.instance_id === f.instance_id)?.name ?? instanceLabel(f.instance_id, null, locale)) : "",
                  section: f.title_nl ?? sectionLabel(t, f.section ?? ""), err: f.error ?? "", n: f.count ?? 0, stopped: Boolean(f.gave_up),
                })) : s.instances.flatMap((inst) => SECTION_IDS.map((id) => {
                  const sec = inst.sections[id] as ({ status: string; message_nl?: string; last_error?: string | null; error?: string | null; failures?: number; attempts?: number; stopped?: boolean } | undefined);
                  const err = sec?.last_error ?? sec?.error ?? (sec?.status === "error" ? sec?.message_nl : null);
                  return err ? { key: `${inst.instance_id}-${id}`, name: inst.name, section: sectionLabel(t, id), err, n: sec?.failures ?? sec?.attempts ?? 0, stopped: Boolean(sec?.stopped) } : null;
                })).filter((x): x is NonNullable<typeof x> => x !== null);
                return errs.length ? (
                  <div className="card-body stack-sm" data-testid="prov-errors">
                    {errs.map((e) => (
                      <InlineState key={e.key} tone="danger">
                        <strong>{[e.name, e.section].filter(Boolean).join(" · ")}</strong> — {t("prov.failedReason", { reason: plainText(e.err, locale) })}
                        {e.stopped || e.n >= 3 ? ` · ${t("prov.failedStopped", { n: e.n || 3 })}` : ""}
                      </InlineState>
                    ))}
                  </div>
                ) : null;
              })()}
              <details style={{ padding: "10px 16px 14px" }}>
                <summary className="t-3" style={{ fontSize: 14, cursor: "pointer" }}>{t("prov.legend")}</summary>
                <div className="legend" style={{ marginTop: 8 }}>
                  {(["ok", "drift", "missing", "error", "needs_input", "skipped", "unknown"] as ProvStatus[]).map((st) => <span key={st} className="legend-item"><span className={`prov-cell prov-cell--${st}`} aria-hidden="true" />{t(`prov.status.${st}` as MessageKey)}</span>)}
                </div>
              </details>
              {Object.keys(s.global).length ? (
                <div className="row wrap" style={{ padding: "0 16px 14px", gap: 12 }}>
                  {Object.entries(s.global).map(([k, g]) => (
                    // klik toont welk veld afwijkt (gewenst ↔ huidig), zoals bij de instantievakjes (S10)
                    <button type="button" key={k} className="row link-more" style={{ gap: 6, border: 0, background: "none", padding: 0, textAlign: "left" }} data-testid={`prov-global-${k}`}
                      onClick={() => openCell("*", k)}>
                      <strong style={{ fontSize: 14 }}>{sectionLabel(t, k)}</strong><ProvChip status={g.status} /><span className="t-3" style={{ fontSize: 14, whiteSpace: "normal" }}>{g.message_nl}</span>
                    </button>))}
                </div>
              ) : null}
            </Card>
          </div>
        )}
      </ResourceView>
      {plan ? <PlanView plan={plan} focus={focus} onClose={() => { setPlan(null); setFocus(null); }} onAll={() => setFocus(null)} /> : null}
      {applied ? (
        <Card title={t("prov.applied.title")} icon={CheckCheck} actions={<Button size="sm" variant="ghost" onClick={() => setApplied(null)}>{t("action.close")}</Button>}>
          <div className="stack-sm">
            <StatusChip tone={applied.status === "succeeded" ? "success" : applied.status === "failed" ? "danger" : "warning"}>{t(`prov.runStatus.${applied.status}` as MessageKey)}</StatusChip>
            <p className="t-2" style={{ fontSize: 14, margin: 0 }}>{t("prov.applied.summary", { applied: applied.summary.applied ?? 0, failed: applied.summary.failed ?? 0, remaining: applied.summary.remaining ?? 0, blocked: applied.summary.blocked ?? 0 })}</p>
            {(applied.failed as Array<ProvChange & { error?: string }>).slice(0, 30).map((c, i) => <InlineState key={`f${i}`} tone="danger">{c.instance_id ? `[${c.instance_id}] ` : ""}{c.title_nl}{c.error ? ` — ${c.error}` : c.message_nl ? ` — ${c.message_nl}` : ""}</InlineState>)}
            {[...applied.blocked, ...applied.remaining].filter((c) => !(applied.failed as ProvChange[]).some((f) => f.title_nl === c.title_nl && f.instance_id === c.instance_id)).slice(0, 30).map((c, i) => <InlineState key={i} tone={c.action === "error" || c.action === "blocked" ? "danger" : "warning"}>{c.instance_id ? `[${c.instance_id}] ` : ""}{c.title_nl}{c.message_nl ? ` — ${c.message_nl}` : ""}</InlineState>)}
          </div>
        </Card>
      ) : null}
      <Modal open={confirm} onClose={() => setConfirm(false)} icon={CheckCheck} title={t("prov.applyConfirmTitle")} description={t("prov.applyConfirmBody")}
        footer={<><Button onClick={() => setConfirm(false)} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" pending={pending === "apply"} onClick={() => void run<ProvApplyResult>("apply", "post", "/provisioning/apply", { confirm: true }, t("prov.appliedToast")).then((r) => { if (r) { setApplied(r); setConfirm(false); void status.reload(); } })}>{t("prov.apply")}</Button></>}>
        <ul className="t-2" style={{ fontSize: 14, margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
          <li>{t("prov.applyRule1")}</li><li>{t("prov.applyRule2")}</li><li>{t("prov.applyRule3")}</li>
        </ul>
      </Modal>
    </div>
  );
}

const OWNABLE = new Set(["downloadclients", "indexers", "customformats", "qualityprofiles"]);
interface ItemRow { instance_id: string; section: string; item_key: string; ownership: string }

function PlanView({ plan, onClose, focus, onAll }: { plan: ProvPlan; onClose: () => void; focus?: { instance: string; section: string } | null; onAll?: () => void }) {
  const { t, locale } = useI18n();
  const items = useResource<ItemRow[]>("/provisioning/items");
  const all: ProvChange[] = [
    ...Object.values(plan.instances).flatMap((i) => Object.values(i.sections ?? {}).flatMap((s) => s.changes ?? [])),
    ...Object.values(plan.global).flatMap((g) => g.changes ?? []),
  ].filter((c) => c.action !== "noop").filter((c) => !focus || ((focus.instance === "*" || c.instance_id === focus.instance) && c.section === focus.section));
  return (
    <Card title={t("prov.plan.title")} icon={ClipboardList} sub={t("prov.plan.summary", { changes: plan.summary.changes, create: plan.summary.create, update: plan.summary.update, delete: plan.summary.delete })} actions={<Button size="sm" variant="ghost" onClick={onClose}>{t("action.close")}</Button>}>
      <div className="stack" data-testid="prov-plan">
        {focus ? <InlineState tone="info">{t("prov.focusHint", { section: sectionLabel(t, focus.section) })} <button type="button" className="link-more" style={{ border: 0, background: "none", padding: 0 }} onClick={onAll}>{t("prov.showAll")}</button></InlineState> : null}
        {focus && !all.length ? <InlineState tone="success">{t("prov.focusNone")}</InlineState> : null}
        {!focus && plan.text_nl.length ? <pre className="payload-preview" style={{ margin: 0, maxHeight: 260, overflow: "auto" }}>{plan.text_nl.join("\n")}</pre> : !focus ? <InlineState tone="success">{t("prov.plan.none")}</InlineState> : null}
        {all.length ? (
          <ul className="list">{all.map((c, i) => (
            <li key={`${c.instance_id}-${c.section}-${c.key}-${i}`} className="list-row" style={{ alignItems: "flex-start" }}>
              <ActionChip action={c.action} />
              <div className="list-main stack-sm" style={{ gap: 4 }}>
                <div className="list-title" style={{ whiteSpace: "normal" }}>{c.instance_id ? <span className="tag" style={{ marginRight: 6 }}>{instanceLabel(c.instance_id, null, locale)}</span> : null}{c.section_nl}: {c.title_nl}</div>
                {c.message_nl ? <div className="t-3" style={{ fontSize: 14 }}>{c.message_nl}</div> : null}
                {c.diff.length ? (
                  <dl className="kv" style={{ fontSize: 14 }}>{c.diff.map((d) => <div key={d.field} style={{ display: "contents" }}><dt>{d.label_nl}</dt><dd className="mono"><span className="diff-removed">{String(d.old ?? "—")}</span> → <span className="diff-added">{String(d.new ?? "—")}</span></dd></div>)}</dl>
                ) : null}
              </div>
              {c.instance_id && OWNABLE.has(c.section) ? <OwnershipToggle change={c} items={items.data ?? []} onChanged={() => void items.reload()} /> : null}
            </li>
          ))}</ul>
        ) : null}
      </div>
    </Card>
  );
}

function OwnershipToggle({ change, items, onChanged }: { change: ProvChange; items: ItemRow[]; onChanged: () => void }) {
  const { t } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const row = items.find((r) => r.instance_id === change.instance_id && r.section === change.section && r.item_key === change.key);
  const mine = row?.ownership === "user";
  const key = `own-${change.instance_id}-${change.key}`;
  return (
    <label className="choice" style={{ fontSize: 14, whiteSpace: "nowrap" }} title={t("prov.ownership.hint")}>
      <input type="checkbox" checked={mine} disabled={!can("admin") || pending === key}
        onChange={(e) => void run(key, "put", "/provisioning/items/ownership", { instance_id: change.instance_id, section: change.section, key: change.key, ownership: e.target.checked ? "user" : "mediaguard" }, t("toast.saved")).then(onChanged)} />
      <span><Hand className="ico ico-sm" aria-hidden="true" /> {t("prov.ownership.mine")}</span>
    </label>
  );
}

/** Aantal onderdelen dat afwijkt of ontbreekt (alleen dan is 'Toepassen' de hoofdknop). */
function deviating(s: { instances: Array<{ sections: Record<string, { status: string } | undefined> }>; global: Record<string, { status: string }> }): number {
  const bad = new Set(["drift", "missing", "error"]);
  let n = 0;
  for (const inst of s.instances) for (const sec of Object.values(inst.sections)) if (sec && bad.has(sec.status)) n += 1;
  for (const g of Object.values(s.global)) if (bad.has(g.status)) n += 1;
  return n;
}
