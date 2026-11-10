"use client";

import { ArrowLeft, ArrowRight, ChevronsDownUp, ChevronsUpDown, Info, Search } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { DiffViewer } from "@/components/mg/domain";
import { Button, Modal, Segmented, Switch } from "@/components/mg/primitives";
import { NO_DRAFT, SettingField, formatValue, scopeLabelKey, type Draft } from "@/components/mg/setting-field";
import { ErrorState, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api, ApiError } from "@/lib/api/client";
import type { EffectiveSetting, Instance, ScopedChange, SettingDef, SettingOverride, SettingsSchema } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { useUnsavedChanges } from "@/lib/unsaved";
import { SimpleSettings, type SectionSpec } from "@/components/mg/simple-settings";
import { TitlePicker } from "@/components/mg/title-picker";

const MEDIA_TYPES = ["tv", "anime", "movie", "anime_movie"] as const;
const PRIMARY_SCOPES = ["global", "media_type", "instance", "library", "profile"];
/** Scopes waarvoor je een titel kiest in plaats van een ID te typen. */
const TITLE_SCOPES = ["series", "movie", "episode"];

export default function GeneralSettingsPage() {
  return <Suspense><SettingsHome /></Suspense>;
}

/**
 * Instellingen op één pagina: secties onder elkaar, inhoudsopgave en zoeken bovenaan. Elke
 * globale instelling staat in een sectie; links naar subpagina's (instanties, meldingen, …)
 * staan onderaan de sectie waar ze bij horen. Uitzonderingen per serie/instantie: ?view=all.
 */
const EXTRA_CAT: Record<string, string> = {
  // gelijk aan EXTRA_CAT in scripts/gen-feature-inventory.py
  "Meldingen": "meldingen", "Downloads opruimen": "downloads", "Downloaden": "downloads", "Aanvragen": "downloads",
  "Mediaserver (Emby)": "koppelingen", "Koppelingen": "koppelingen", "Seerr": "koppelingen", "Instanties": "koppelingen",
  "Inrichten": "koppelingen", "Ondertitels": "taal", "Onderhoud": "systeem", "Back-ups": "systeem", "Migratie": "systeem",
  "Bibliotheek": "systeem", "Beveiliging": "beveiliging", "AI Adviseur": "gereedschap",
};
const sectionOf = (d: SettingDef) => {
  if (d.section === "prestaties") return "systeem";
  if (d.section && d.section !== "algemeen") return d.section;
  return EXTRA_CAT[d.category] ?? "algemeen";
};
const ORDER = ["algemeen", "av1", "originelen", "downloads", "taal", "koppelingen", "meldingen", "beveiliging", "systeem", "gereedschap", "scope"];
const EXTRA_TITLES: Record<string, [MessageKey, MessageKey]> = {
  koppelingen: ["set.sec.koppelingen", "set.sec.koppelingen.intro"],
  meldingen: ["set.sec.meldingen", "set.sec.meldingen.intro"],
  beveiliging: ["set.sec.beveiliging", "set.sec.beveiliging.intro"],
  systeem: ["set.sec.systeem", "set.sec.systeem.intro"],
  gereedschap: ["set.sec.gereedschap", "set.sec.gereedschap.intro"],
  scope: ["set.sec.scope", "set.sec.scope.intro"],
};
type LinkKey = "instances" | "connections" | "downloads" | "seerr" | "quality" | "av1" | "dv" | "audio" | "notify" | "security" | "users" | "apikeys" | "secrets" | "audit" | "system" | "releaseLab" | "anime" | "ai" | "migration" | "help";
const HREF: Record<LinkKey, string> = {
  instances: "/instellingen/instanties", connections: "/inrichten", downloads: "/downloads", seerr: "/seerr", quality: "/kwaliteit",
  av1: "/av1/instellingen", dv: "/dolby-vision", audio: "/audio", notify: "/instellingen/meldingen", security: "/instellingen/beveiliging",
  users: "/instellingen/gebruikers", apikeys: "/instellingen/api-sleutels", secrets: "/instellingen/geheimen", audit: "/instellingen/audit",
  system: "/systeem", releaseLab: "/release-lab", anime: "/anime", ai: "/ai", migration: "/migratie", help: "/hoe-werkt-het",
};
const LINKS: Record<string, LinkKey[]> = {
  av1: ["av1", "dv", "quality"],
  downloads: ["downloads"],
  taal: ["audio", "anime"],
  koppelingen: ["instances", "connections", "downloads", "seerr"],
  meldingen: ["notify"],
  beveiliging: ["security", "users", "apikeys", "secrets", "audit"],
  systeem: ["system"],
  gereedschap: ["releaseLab", "anime", "ai", "migration", "quality", "audio", "dv", "help"],
};

function SettingsHome() {
  const { t, locale } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const schema = useResource<SettingsSchema>("/settings/schema");
  const view = params.get("view") ?? (params.get("q") || params.get("scope") ? "all" : "simple");
  const [query, setQuery] = useState("");
  const [expandAll, setExpandAll] = useState(false);
  // 'Systeem & prestaties' en opslag hebben een eigen pagina (Systeem): oude links daarheen
  useEffect(() => {
    if (params.get("open") === "prestaties" || window.location.hash === "#prestaties") router.replace("/systeem?open=prestaties#prestaties");
    if (params.get("tab") === "opslag") router.replace("/systeem#opslag");
  }, [params, router]);
  // #sectie (bv. #gereedschap vanaf het oude 'Gevorderd'): naar die sectie scrollen zodra hij er is
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id || !schema.data) return;
    const timer = window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ block: "start" }), 200);
    return () => window.clearTimeout(timer);
  }, [schema.data]);

  const sections = useMemo<SectionSpec[]>(() => {
    const curated = Object.fromEntries((schema.data?.sections ?? []).map((sec) => [sec.id, sec]));
    return ORDER.map((id) => {
      const extra = EXTRA_TITLES[id];
      if (extra) return { id, title: t(extra[0]), intro: t(extra[1]) };
      const sec = curated[id];
      return { id, title: sec ? (locale === "en-US" ? sec.title_en : sec.title_nl) : id, intro: sec && locale !== "en-US" ? sec.intro_nl : undefined };
    });
  }, [schema.data, t, locale]);

  const extra = useCallback((id: string, q: string) => {
    if (id === "scope") {
      if (q && !`${t("set.sec.scope")} ${t("set.sec.scope.intro")} serie instantie profiel uitzondering`.toLowerCase().includes(q)) return null;
      return (
        <div className="card-footer" style={{ justifyContent: "flex-start" }}>
          <Link className="btn btn--sm" href="/instellingen?view=all" data-testid="settings-scope-open">{t("set.sec.scope.open")}</Link>
        </div>
      );
    }
    const links = (LINKS[id] ?? []).filter((k) => !q || `${t(`set.link.${k}` as MessageKey)} ${t(`set.link.${k}.sub` as MessageKey)}`.toLowerCase().includes(q));
    if (!links.length) return null;
    return (
      <div className="settings-links" aria-label={t("set.alsoHere")}>
        <div className="settings-links-label">{t("set.alsoHere")}</div>
        <div className="list">
          {links.map((k) => (
            <Link key={k} href={HREF[k]} className="list-row list-row--hover" prefetch={false}>
              <div className="list-main"><div className="list-title">{t(`set.link.${k}` as MessageKey)}</div><div className="list-sub" style={{ whiteSpace: "normal" }}>{t(`set.link.${k}.sub` as MessageKey)}</div></div>
              <ArrowRight className="ico ico-sm t-3" aria-hidden="true" />
            </Link>
          ))}
        </div>
      </div>
    );
  }, [t]);

  if (view === "all") {
    return (
      <div className="stack">
        <div><Link href="/instellingen" className="back-link"><ArrowLeft className="ico" aria-hidden="true" />{t("nav.back", { page: t("set.title") })}</Link></div>
        <SchemaSettings />
      </div>
    );
  }
  const q = query.trim().toLowerCase();
  return (
    <div className="stack">
      <div className="settings-bar">
        <div className="search-field grow" style={{ minWidth: 200 }}>
          <Search className="ico" aria-hidden="true" />
          <input className="input" type="search" placeholder={t("set.search")} aria-label={t("set.searchLabel")} value={query} onChange={(e) => setQuery(e.target.value)} data-testid="settings-search" />
        </div>
        <Button size="sm" variant="ghost" icon={expandAll ? ChevronsDownUp : ChevronsUpDown} aria-pressed={expandAll} onClick={() => setExpandAll((v) => !v)} data-testid="settings-expand-all">
          {expandAll ? t("set.collapseAll") : t("set.expandAll")}
        </Button>
      </div>
      {!q ? (
        <nav className="settings-toc" aria-label={t("set.toc")} data-testid="settings-toc">
          {sections.map((sec) => <a key={sec.id} href={`#${sec.id}`} className="chip chip--outline">{sec.title}</a>)}
        </nav>
      ) : null}
      {schema.loading ? <StateView kind="loading" />
        : schema.error || !schema.data ? <ErrorState error={schema.error ?? new ApiError("server", 0, "", "/settings/schema")} onRetry={() => void schema.reload()} />
          : <SimpleSettings schema={schema.data} sections={sections} sectionOf={sectionOf} query={q} expandAll={expandAll} extra={extra} cpuSection="systeem"
            empty={<StateView kind="empty" compact title={t("set.noResults", { q: query.trim() })} />} />}
    </div>
  );
}

function SchemaSettings() {
  const { t, tp, locale } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const router = useRouter();
  const params = useSearchParams();
  const schema = useResource<SettingsSchema>("/settings/schema");
  const instances = useResource<Instance[]>("/instances");

  const scope = params.get("scope") ?? "global";
  const [scopeId, setScopeId] = useState(params.get("id") ?? "");
  // gekozen titel (serie/film/aflevering) als leesbare naam i.p.v. een intern ID (punt 25)
  const [scopeLabel, setScopeLabel] = useState<string | null>(params.get("label"));
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [advanced, setAdvanced] = useState(false);
  // uitzonderingen per serie/instantie zitten achter één knop (de meeste mensen hebben ze niet nodig)
  const [showScope, setShowScope] = useState(() => Boolean(params.get("scope")) && params.get("scope") !== "global");
  const [category, setCategory] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [simulation, setSimulation] = useState<{ before: Record<string, unknown>; after: Record<string, unknown> } | null>(null);

  const chain = scope === "global" ? null : scopeId ? `${scope}:${scopeId}` : null;
  const effective = useResource<EffectiveSetting[]>(schema.data && (scope === "global" || chain) ? "/settings/effective" : null, chain ? { scope: chain } : undefined);
  const overrides = useResource<SettingOverride[]>("/settings/overrides", { scope_type: scope, scope_id: scope === "global" ? "" : scopeId });
  useCoreEvents("settings.changed", () => { void effective.reload(); void overrides.reload(); });

  const setScope = (next: string) => {
    const qs = new URLSearchParams(params.toString());
    qs.set("scope", next);
    qs.delete("id");
    router.replace(`/instellingen?${qs.toString()}`);
    setScopeId(next === "media_type" ? "tv" : next === "instance" ? instances.data?.[0]?.id ?? "" : "");
    setScopeLabel(null);
    setDrafts({});
    setErrors({});
  };
  useEffect(() => {
    if (scope === "media_type" && !scopeId) setScopeId("tv");
  }, [scope, scopeId]);

  const effByKey = useMemo(() => Object.fromEntries((effective.data ?? []).map((e) => [e.key, e])), [effective.data]);
  const setHere = useMemo(() => new Set((overrides.data ?? []).filter((o) => o.scope_type === scope && (scope === "global" || o.scope_id === scopeId)).map((o) => o.key)), [overrides.data, scope, scopeId]);

  const label = useCallback((d: SettingDef) => (locale === "en-US" ? d.label_en : d.label_nl), [locale]);
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (schema.data?.settings ?? []).filter((d) => d.scopes.includes(scope))
      .filter((d) => !d.hidden || Boolean(q))
      .filter((d) => advanced || !d.advanced || (q && d.key.toLowerCase().includes(q)))
      .filter((d) => !q || [d.key, d.label_nl, d.label_en, d.help_nl, d.help_en, d.category].join(" ").toLowerCase().includes(q));
  }, [advanced, query, schema.data, scope]);
  const categories = useMemo(() => {
    const map = new Map<string, number>();
    visible.forEach((d) => map.set(d.category, (map.get(d.category) ?? 0) + 1));
    return [...map.entries()];
  }, [visible]);
  const activeCategory = query ? null : category && categories.some(([c]) => c === category) ? category : categories[0]?.[0] ?? null;
  const shown = activeCategory ? visible.filter((d) => d.category === activeCategory) : visible;

  const dirtyKeys = Object.keys(drafts).filter((k) => drafts[k] !== NO_DRAFT);
  useUnsavedChanges(dirtyKeys.length > 0);
  const canEdit = can("operator");
  const scopeReady = scope === "global" || Boolean(scopeId);

  const buildChanges = (): ScopedChange[] => dirtyKeys.map((key) => ({ scope_type: scope, scope_id: scope === "global" ? "" : scopeId, key, value: drafts[key] }));

  const attachError = (error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    const key = dirtyKeys.find((k) => message.includes(k));
    if (key) setErrors({ [key]: message });
    else setGlobalError(message);
  };

  const save = async () => {
    setSaving(true);
    setErrors({});
    setGlobalError(null);
    try {
      // AI keys go through PATCH /ai/settings so the backend can enforce the AI enable gates (S4)
      const changes = buildChanges();
      const ai = scope === "global" ? changes.filter((c) => c.key.startsWith("ai.")) : [];
      const rest = changes.filter((c) => !ai.includes(c));
      if (ai.length) await api.patch("/ai/settings", { values: Object.fromEntries(ai.map((c) => [c.key, c.value])), api_keys: {}, reason: "UI" });
      if (rest.length) await api.patch("/settings/scoped", { changes: rest, reason: "UI" });
      toast.push("success", t("settings.saved", { count: dirtyKeys.length }), t("settings.savedBody"));
      setDrafts({});
      await Promise.all([effective.reload(), overrides.reload()]);
    } catch (error) {
      attachError(error);
      toast.push("danger", t("settings.saveFailed"), error instanceof Error ? error.message : undefined);
    } finally {
      setSaving(false);
    }
  };

  const reset = async (key: string) => {
    try {
      await api.patch("/settings/scoped", { changes: [{ scope_type: scope, scope_id: scope === "global" ? "" : scopeId, key, reset: true }], reason: "UI: herstel standaard" });
      toast.push("success", t("settings.reset"));
      setDrafts((d) => ({ ...d, [key]: NO_DRAFT }));
      await Promise.all([effective.reload(), overrides.reload()]);
    } catch (error) {
      toast.push("danger", t("settings.saveFailed"), error instanceof Error ? error.message : undefined);
    }
  };

  const simulate = async () => {
    try {
      const context = chain ? { [scope]: scopeId } : {};
      const result = await api.post<{ before: Record<string, unknown>; after: Record<string, unknown> }>("/settings/simulate", { changes: buildChanges(), context, keys: dirtyKeys });
      setSimulation(result);
    } catch (error) {
      attachError(error);
    }
  };

  if (schema.loading) return <StateView kind="loading" />;
  if (schema.error || !schema.data) return <ErrorState error={schema.error ?? new ApiError("server", 0, "", "/settings/schema")} onRetry={() => void schema.reload()} />;
  const defs = Object.fromEntries(schema.data.settings.map((d) => [d.key, d]));
  const scopes = schema.data.scopes;

  return (
    <div className="grid">
      <aside className="span-3 desktop-only" aria-label={t("settings.categories")}>
        <div className="card" style={{ padding: 8, position: "sticky", top: 76 }}>
          <div className="nav">
            {categories.map(([c, n]) => (
              <button key={c} type="button" className="nav-item" aria-current={c === activeCategory ? "page" : undefined} onClick={() => { setCategory(c); setQuery(""); }} style={{ border: 0, background: c === activeCategory ? undefined : "transparent", width: "100%", textAlign: "left" }}>
                <span className="nav-label">{c}</span><span className="nav-count">{n}</span>
              </button>
            ))}
          </div>
        </div>
      </aside>
      <div className="span-9 stack">
        <div className="card" style={{ padding: "12px 16px" }}>
          {!showScope ? (
            <div className="row wrap" style={{ gap: 12 }}>
              <Button size="sm" variant="ghost" onClick={() => setShowScope(true)} data-testid="settings-add-exception">{t("settings.addException")}</Button>
              <label className="row t-2" style={{ fontSize: 14, marginLeft: "auto" }}><Switch checked={advanced} onChange={setAdvanced} label={t("settings.advanced")} />{t("settings.advanced")}</label>
            </div>
          ) : (
          <div className="row wrap" style={{ gap: 12 }}>
            <span className="label" style={{ margin: 0 }}>{t("settings.scope")}</span>
            <Segmented label={t("settings.scope")} value={PRIMARY_SCOPES.includes(scope) ? scope : "__more"} onChange={(v) => v !== "__more" && setScope(v)}
              options={PRIMARY_SCOPES.filter((s) => scopes.includes(s)).map((s) => ({ value: s, label: t(scopeLabelKey(s)) }))} />
            <select className="select" style={{ width: "auto" }} aria-label={t("settings.scope")} value={PRIMARY_SCOPES.includes(scope) ? "" : scope} onChange={(e) => e.target.value && setScope(e.target.value)}>
              <option value="">…</option>
              {scopes.filter((s) => !PRIMARY_SCOPES.includes(s)).map((s) => <option key={s} value={s}>{t(scopeLabelKey(s))}</option>)}
            </select>
            {scope === "media_type" ? (
              <select className="select" style={{ width: 180 }} aria-label={t("settings.scopeId")} value={scopeId} onChange={(e) => setScopeId(e.target.value)}>
                {MEDIA_TYPES.map((m) => <option key={m} value={m}>{t(`media.${m}`)}</option>)}
              </select>
            ) : scope === "instance" && instances.data?.length ? (
              <select className="select" style={{ width: 200 }} aria-label={t("settings.scopeId")} value={scopeId} onChange={(e) => setScopeId(e.target.value)}>
                {instances.data.map((i) => <option key={i.id} value={i.id}>{i.name} · {i.id}</option>)}
              </select>
            ) : TITLE_SCOPES.includes(scope) ? (
              scopeId ? (
                <span className="row wrap" style={{ gap: 8 }} data-testid="settings-scope-title">
                  <span className="chip chip--outline">{scopeLabel ?? scopeId}</span>
                  <Button size="sm" variant="ghost" onClick={() => { setScopeId(""); setScopeLabel(null); setDrafts({}); setErrors({}); }}>{t("titleSet.otherTitle")}</Button>
                </span>
              ) : (
                <TitlePicker key={scope} mediaKind={scope === "movie" ? "movie" : "series"} needEpisode={scope === "episode"}
                  onPick={(p) => {
                    setScopeId(scope === "episode" ? `${p.instance}:${p.episodeId}` : `${p.instance}:${p.item.provider_id}`);
                    setScopeLabel(p.label);
                    setDrafts({});
                    setErrors({});
                  }} />
              )
            ) : scope !== "global" ? (
              <input className="input" style={{ width: 220 }} aria-label={t("settings.scopeId")} placeholder={t("settings.scopeIdPlaceholder")} value={scopeId} onChange={(e) => setScopeId(e.target.value.trim())} />
            ) : null}
            <label className="row t-2" style={{ fontSize: 14, marginLeft: "auto" }}><Switch checked={advanced} onChange={setAdvanced} label={t("settings.advanced")} />{t("settings.advanced")}</label>
          </div>
          )}
          <div className="search-field" style={{ marginTop: 10 }}>
            <Search className="ico" aria-hidden="true" />
            <input className="input" type="search" placeholder={t("settings.search")} aria-label={t("settings.searchLabel")} value={query} onChange={(e) => setQuery(e.target.value)} data-testid="settings-search" />
          </div>
        </div>

        <section className="card" aria-label={activeCategory ?? t("settings.title")}>
          {/* op de telefoon ontbreekt de categorielijst links: hier een keuzelijst (punt 25) */}
          {!query && categories.length > 1 ? (
            <div className="card-body settings-cat-mobile" style={{ paddingBottom: 0 }}>
              <label className="label" htmlFor="settings-category">{t("settings.categories")}</label>
              <select id="settings-category" className="select" value={activeCategory ?? ""} onChange={(e) => setCategory(e.target.value)} data-testid="settings-category">
                {categories.map(([c, n]) => <option key={c} value={c}>{`${c} (${n})`}</option>)}
              </select>
            </div>
          ) : null}
          <div className="card-header" style={{ borderBottom: "1px solid var(--mg-border-soft)" }}>
            <h2 className="card-title">{query ? t("settings.searchLabel") : activeCategory ?? t("settings.title")}</h2>
            <span className="card-sub">{tp("settings.countPlural", shown.length)} · {t("settings.changedHere", { count: shown.filter((d) => setHere.has(d.key)).length })}</span>
          </div>
          {!scopeReady ? (TITLE_SCOPES.includes(scope)
            ? <StateView kind="empty" compact title={t("titleSet.pickFirst")} />
            : <StateView kind="empty" compact title={t("settings.scopeId")} body={t("settings.scopeIdPlaceholder")} />)
            : effective.error && !effective.data ? <ErrorState error={effective.error} onRetry={() => void effective.reload()} compact />
              : effective.loading ? <StateView kind="loading" compact />
                : shown.length === 0 ? <StateView kind="empty" compact title={query ? t("settings.noResults", { q: query }) : t("settings.noneForScope")} />
                  : shown.map((def) => (
                    <SettingField
                      key={`${def.key}-${scope}-${scopeId}`}
                      def={def}
                      effective={effByKey[def.key]}
                      scope={scope}
                      setHere={setHere.has(def.key)}
                      draft={def.key in drafts ? drafts[def.key] : NO_DRAFT}
                      error={errors[def.key]}
                      disabled={!canEdit}
                      onDraft={(value) => { setDrafts((d) => ({ ...d, [def.key]: value })); setErrors((e) => { const n = { ...e }; delete n[def.key]; return n; }); }}
                      onReset={() => void reset(def.key)}
                    />
                  ))}
        </section>

        {dirtyKeys.length || Object.keys(errors).length || globalError ? (
          <div className="savebar" role="region" aria-label={t("settings.saveBar", { count: dirtyKeys.length })}>
            <Info className="ico" aria-hidden="true" style={{ color: "var(--mg-accent)" }} />
            <span className="grow" style={{ fontSize: 14 }}>
              <strong>{t("settings.saveBar", { count: dirtyKeys.length })}</strong>
              {Object.keys(errors).length ? <> · <span className="t-danger">{t("settings.saveBarErrors", { count: Object.keys(errors).length })}</span></> : null}
              {globalError ? <> · <span className="t-danger" role="alert">{globalError}</span></> : null}
            </span>
            {showScope ? <Button size="sm" onClick={() => void simulate()} disabled={!dirtyKeys.length}>{t("settings.simulate")}</Button> : null}
            <Button size="sm" variant="ghost" onClick={() => { setDrafts({}); setErrors({}); setGlobalError(null); }}>{t("action.discard")}</Button>
            <Button size="sm" variant="primary" pending={saving} disabled={!dirtyKeys.length || !canEdit} onClick={() => void save()} data-testid="settings-save">{t("action.save")}</Button>
          </div>
        ) : null}
      </div>

      <Modal open={Boolean(simulation)} onClose={() => setSimulation(null)} title={t("settings.simulateTitle")} description={t("settings.simulateBody")} footer={<Button onClick={() => setSimulation(null)}>{t("action.close")}</Button>}>
        {simulation ? (
          <DiffViewer
            headers={[t("settings.scope"), t("settings.before"), t("settings.after")]}
            keyWidth={150}
            rows={Object.keys(simulation.after).map((k) => ({
              key: defs[k] ? label(defs[k]) : k,
              before: defs[k] ? formatValue(defs[k], simulation.before[k], locale, t("settings.yes"), t("settings.no")) : String(simulation.before[k]),
              after: defs[k] ? formatValue(defs[k], simulation.after[k], locale, t("settings.yes"), t("settings.no")) : String(simulation.after[k]),
              change: JSON.stringify(simulation.before[k]) === JSON.stringify(simulation.after[k]) ? "same" : "changed",
            }))}
          />
        ) : null}
      </Modal>
    </div>
  );
}
