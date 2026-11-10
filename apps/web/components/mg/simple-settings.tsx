"use client";

import { ChevronDown, ChevronUp, Cpu, Info, ShieldAlert, TriangleAlert } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, InlineState } from "@/components/mg/primitives";
import { NO_DRAFT, SettingField, type Draft } from "@/components/mg/setting-field";
import { ErrorState, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import type { EffectiveSetting, SettingDef, SettingOverride, SettingsSchema } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { plainText } from "@/lib/labels";
import { useSession } from "@/lib/session";
import { useUnsavedChanges } from "@/lib/unsaved";

interface ResourceStatus {
  desired: { cpu_percent: number; cpu_workers: number; gpu_workers: number };
  effective: { cores?: number; threads_per_encode?: number; threads_total?: number } | null;
  encoder_reachable: boolean;
  parallel_encodes: number;
}

/**
 * Eenvoudige instellingen: per sectie alleen wat een gewone gebruiker nodig heeft; de rest
 * van die sectie achter 'Geavanceerd tonen'. Alles op globaal niveau; per-serie/-instantie
 * uitzonderingen blijven in 'Alle instellingen'.
 */
interface SafetyRow { key: string; label_nl: string; value_nl?: unknown; default_nl?: unknown; why_nl?: string }

/** Instellingen die afwijken van de veilige standaard (H1): opvallend bovenaan, met één knop terug. */
function SafetyWarning({ onRestored }: { onRestored: () => void }) {
  const { t, locale } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const safety = useResource<{ count: number; deviations: SafetyRow[] }>("/settings/safety");
  useCoreEvents("settings.changed", () => void safety.reload());
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const rows = safety.data?.deviations ?? [];
  if (!rows.length) return null;
  const restore = async () => {
    setBusy(true);
    try {
      await api.post("/settings/safety/restore", { keys: [] });
      toast.push("success", t("safety.restored"));
      await safety.reload();
      onRestored();
    } catch (e) {
      toast.push("danger", t("settings.saveFailed"), e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card card--warning" data-testid="safety-warning" style={{ padding: 16 }}>
      <div className="row-between wrap" style={{ gap: 10 }}>
        <span className="row" style={{ gap: 10 }}><ShieldAlert className="ico" aria-hidden="true" style={{ color: "var(--degraded)" }} /><strong>{t("safety.title", { count: rows.length })}</strong></span>
        <span className="row wrap" style={{ gap: 6 }}>
          <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen((v) => !v)}>{open ? t("safety.hide") : t("safety.show")}</Button>
          {can("operator") ? <Button size="sm" variant="primary" pending={busy} onClick={() => void restore()}>{t("safety.restore")}</Button> : null}
        </span>
      </div>
      {open ? (
        <ul className="list" style={{ marginTop: 10 }}>
          {rows.map((r) => (
            <li key={r.key} className="list-row list-row--wrap">
              <div className="list-main">
                <div className="list-title" style={{ whiteSpace: "normal" }}>{r.label_nl}: <strong>{plainText(String(r.value_nl ?? ""), locale)}</strong> <span className="t-3">({t("safety.default", { value: plainText(String(r.default_nl ?? ""), locale) })})</span></div>
                {r.why_nl ? <div className="list-sub">{r.why_nl}</div> : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export interface SectionSpec { id: string; title: string; intro?: string }

interface SimpleSettingsProps {
  schema: SettingsSchema;
  only?: string[];
  exclude?: string[];
  /** Eigen indeling: secties in deze volgorde (ook secties zonder instellingen, met `extra`). */
  sections?: SectionSpec[];
  /** In welke sectie een instelling hoort (standaard: de sectie uit het schema). */
  sectionOf?: (d: SettingDef) => string;
  /** Zoekterm: alleen passende instellingen en secties tonen. */
  query?: string;
  /** Alle 'geavanceerd'-delen open. */
  expandAll?: boolean;
  /** Extra inhoud onder een sectie (links naar subpagina's); null = niets (ook bij zoeken). */
  extra?: (sectionId: string, query: string) => ReactNode;
  /** Sectie waarin het CPU-blok staat (standaard 'prestaties'). */
  cpuSection?: string;
  /** Wat te tonen als de zoekterm nergens past. */
  empty?: ReactNode;
}

const matches = (d: SettingDef, q: string) => [d.key, d.label_nl, d.label_en, d.help_nl, d.help_en, d.category].join(" ").toLowerCase().includes(q);

export function SimpleSettings({ schema, only, exclude, sections, sectionOf, query = "", expandAll = false, extra, cpuSection = "prestaties", empty }: SimpleSettingsProps) {
  const { t, locale } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const effective = useResource<EffectiveSetting[]>("/settings/effective");
  const overrides = useResource<SettingOverride[]>("/settings/overrides", { scope_type: "global", scope_id: "" });
  const resources = useResource<ResourceStatus>("/encoding/resources");
  useCoreEvents("settings.changed", () => { void effective.reload(); void overrides.reload(); void resources.reload(); });
  // ?open=<sectie> (vanaf een functiepagina): die sectie inclusief 'Geavanceerd' openklappen
  const params = useSearchParams();
  const openParam = params.get("open");
  const [open, setOpen] = useState<Record<string, boolean>>(() => (openParam ? { [openParam]: true } : {}));
  useEffect(() => {
    if (!openParam || !effective.data) return;
    const el = document.getElementById(openParam);
    if (el) el.scrollIntoView({ block: "start" });
  }, [openParam, effective.data]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  // 'Direct verwijderen' kiezen vraagt eerst om bevestiging met uitleg (H1)
  const [confirmImmediate, setConfirmImmediate] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const effByKey = useMemo(() => Object.fromEntries((effective.data ?? []).map((e) => [e.key, e])), [effective.data]);
  const setHere = useMemo(() => new Set((overrides.data ?? []).map((o) => o.key)), [overrides.data]);
  const bySection = useMemo(() => {
    const map = new Map<string, { simple: SettingDef[]; advanced: SettingDef[] }>();
    for (const d of schema.settings) {
      if (!d.scopes.includes("global") || d.hidden) continue;
      const section = sectionOf ? sectionOf(d) : d.section ?? "algemeen";
      const entry = map.get(section) ?? { simple: [], advanced: [] };
      (d.simple ? entry.simple : entry.advanced).push(d);
      map.set(section, entry);
    }
    for (const entry of map.values()) entry.simple.sort((a, b) => (a.order ?? 1000) - (b.order ?? 1000));
    return map;
  }, [schema.settings, sectionOf]);

  const dirty = Object.keys(drafts).filter((k) => drafts[k] !== NO_DRAFT);
  // weg-navigeren met niet-opgeslagen wijzigingen vraagt eerst om bevestiging (punt 24)
  useUnsavedChanges(dirty.length > 0);
  const windowOn = (drafts["resources.encode_window_enabled"] !== undefined && drafts["resources.encode_window_enabled"] !== NO_DRAFT
    ? drafts["resources.encode_window_enabled"] : effByKey["resources.encode_window_enabled"]?.value) === true;

  const save = async () => {
    setSaving(true);
    setErrors({});
    try {
      const changes = dirty.map((key) => ({ scope_type: "global", scope_id: "", key, value: drafts[key] }));
      const ai = changes.filter((c) => c.key.startsWith("ai."));
      const rest = changes.filter((c) => !c.key.startsWith("ai."));
      if (ai.length) await api.patch("/ai/settings", { values: Object.fromEntries(ai.map((c) => [c.key, c.value])), api_keys: {}, reason: "UI" });
      if (rest.length) await api.patch("/settings/scoped", { changes: rest, reason: "UI: eenvoudige instellingen" });
      toast.push("success", t("settings.saved", { count: dirty.length }), t("settings.savedBody"));
      setDrafts({});
      await Promise.all([effective.reload(), overrides.reload(), resources.reload()]);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      const key = dirty.find((k) => message.includes(k)) ?? dirty[0];
      if (key) setErrors({ [key]: message });
      toast.push("danger", t("settings.saveFailed"), message);
    } finally {
      setSaving(false);
    }
  };
  const reset = async (key: string) => {
    try {
      await api.patch("/settings/scoped", { changes: [{ scope_type: "global", scope_id: "", key, reset: true }], reason: "UI: herstel standaard" });
      toast.push("success", t("settings.reset"));
      setDrafts((d) => ({ ...d, [key]: NO_DRAFT }));
      await Promise.all([effective.reload(), overrides.reload(), resources.reload()]);
    } catch (e) {
      toast.push("danger", t("settings.saveFailed"), e instanceof Error ? e.message : undefined);
    }
  };

  if (effective.loading && !effective.data) return <StateView kind="loading" />;
  if (effective.error && !effective.data) return <ErrorState error={effective.error} onRetry={() => void effective.reload()} />;

  // 'Direct verwijderen na controle': bewaartermijn en minimale leeftijd doen dan niets
  const quarantineMode = drafts["quarantine.mode"] !== undefined && drafts["quarantine.mode"] !== NO_DRAFT
    ? drafts["quarantine.mode"] : effByKey["quarantine.mode"]?.value;
  const inactive = quarantineMode === "immediate_after_check" ? new Set(["quarantine.retention_days", "quarantine.min_age_hours"]) : new Set<string>();
  // het tijdvenster zelf staat er altijd (vindbaar), maar doet pas iets als het venster aan staat
  if (!windowOn) inactive.add("resources.encode_window");
  const field = (def: SettingDef) => (
    <SettingField key={def.key} compact def={def} effective={effByKey[def.key]} scope="global" setHere={setHere.has(def.key)}
      draft={def.key in drafts ? drafts[def.key] : NO_DRAFT} error={errors[def.key]}
      disabled={!can("operator") || inactive.has(def.key)} note={inactive.has(def.key) ? (def.key === "resources.encode_window" ? t("set.windowOff") : t("quarantine.mode.disabledHint")) : undefined}
      onDraft={(v) => {
        if (def.key === "quarantine.mode" && v === "immediate_after_check" && effByKey["quarantine.mode"]?.value !== "immediate_after_check") { setConfirmImmediate(true); return; }
        setDrafts((d) => ({ ...d, [def.key]: v })); setErrors((e) => { const n = { ...e }; delete n[def.key]; return n; });
      }}
      onReset={() => void reset(def.key)} />
  );

  const eff = resources.data?.effective;
  const q = query.trim().toLowerCase();
  const sectionList: SectionSpec[] = sections ?? (schema.sections ?? []).map((sec) => ({ id: sec.id, title: locale === "en-US" ? sec.title_en : sec.title_nl, intro: locale === "en-US" ? undefined : sec.intro_nl }));
  return (
    <div className="stack" data-testid="simple-settings">
      {!only ? <SafetyWarning onRestored={() => { void effective.reload(); void overrides.reload(); }} /> : null}
      {((rendered) => (rendered.length || !q ? rendered : empty ?? null))(sectionList.filter((sec) => (!only || only.includes(sec.id)) && !exclude?.includes(sec.id)).map((section) => {
        const full = bySection.get(section.id) ?? { simple: [], advanced: [] };
        const title = section.title;
        // zoeken: past de sectienaam, dan de hele sectie; anders alleen de passende instellingen
        const titleHit = Boolean(q) && `${title} ${section.intro ?? ""}`.toLowerCase().includes(q);
        const entry = !q || titleHit ? full : { simple: [...full.simple, ...full.advanced].filter((d) => matches(d, q)), advanced: [] };
        const more = extra ? extra(section.id, titleHit ? "" : q) : null;
        if (!entry.simple.length && !entry.advanced.length && !more) return null;
        const isOpen = expandAll || Boolean(open[section.id]);
        return (
          <section key={section.id} id={section.id} aria-label={title} data-testid={`section-${section.id}`} className="settings-section">
            <Card title={title} sub={section.intro} icon={section.id === cpuSection ? Cpu : undefined} flush>
              {section.id === cpuSection && !q ? (
                <div className="card-body" style={{ paddingBottom: 0 }}>
                  <InlineState icon={Info} role="status">
                    {eff?.cores
                      // dezelfde rekensom als AV1 → Grenzen (budget = kernen × %), plus hoe dat verdeeld wordt
                      ? (typeof (eff as { budget_threads?: number }).budget_threads === "number"
                        ? t("simple.cpuBudget", { budget: (eff as { budget_threads: number }).budget_threads, cores: eff.cores, gpu: (eff as { gpu_reserved_threads?: number }).gpu_reserved_threads ?? 0,
                          g: (eff as { gpu_workers?: number }).gpu_workers ?? 0, per: eff.threads_per_encode ?? 0, n: resources.data?.desired.cpu_workers ?? 1 })
                        : t("simple.cpuEffective", { threads: eff.threads_per_encode ?? 0, cores: eff.cores, n: resources.data?.desired.cpu_workers ?? 1 }))
                      : t("simple.cpuUnknown")}
                  </InlineState>
                  {(eff as { cpu_grens?: { actief?: boolean; set?: string; uitleg?: string } } | null)?.cpu_grens?.uitleg ? (() => {
                    const g = (eff as { cpu_grens: { actief?: boolean; set?: string; uitleg: string } }).cpu_grens;
                    return <p className="hint" style={{ margin: "8px 0 0" }} data-testid="cpu-confine">{g.uitleg}{g.actief && g.set ? ` · ${t("simple.cpuSet", { set: g.set })}` : ""}</p>;
                  })() : null}
                </div>
              ) : null}
              {entry.simple.map(field)}
              {entry.advanced.length ? (
                <>
                  <div className="card-footer" style={{ justifyContent: "flex-start" }}>
                    <Button size="sm" variant="ghost" icon={isOpen ? ChevronUp : ChevronDown} aria-expanded={isOpen} disabled={expandAll} onClick={() => setOpen((o) => ({ ...o, [section.id]: !isOpen }))} data-testid={`advanced-${section.id}`}>
                      {isOpen ? t("simple.hideAdvanced") : t("simple.showAdvanced", { count: entry.advanced.length })}
                    </Button>
                  </div>
                  {isOpen ? entry.advanced.map(field) : null}
                </>
              ) : null}
              {more}
            </Card>
          </section>
        );
      }).filter(Boolean))}
      <DangerDialog open={confirmImmediate} onClose={() => setConfirmImmediate(false)} icon={TriangleAlert}
        title={t("safety.immediateTitle")} description={t("safety.immediateBody")}
        facts={[{ label: t("dialog.rollback"), value: <span className="t-danger">{t("safety.immediateNoRestore")}</span> }]}
        confirmLabel={t("safety.immediateConfirm")}
        onConfirm={() => { setDrafts((d) => ({ ...d, "quarantine.mode": "immediate_after_check" })); setConfirmImmediate(false); }} />
      {dirty.length || Object.keys(errors).length ? (
        <div className="savebar" role="region" aria-label={t("settings.saveBar", { count: dirty.length })}>
          <Info className="ico" aria-hidden="true" style={{ color: "var(--mg-accent)" }} />
          <span className="grow" style={{ fontSize: 14 }}><strong>{t("settings.saveBar", { count: dirty.length })}</strong></span>
          <Button size="sm" variant="ghost" onClick={() => { setDrafts({}); setErrors({}); }}>{t("action.discard")}</Button>
          <Button size="sm" variant="primary" pending={saving} disabled={!dirty.length || !can("operator")} onClick={() => void save()} data-testid="settings-save">{t("action.save")}</Button>
        </div>
      ) : null}
    </div>
  );
}
