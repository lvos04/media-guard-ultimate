"use client";

import { SlidersHorizontal, Trash2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Button, Card, Switch } from "@/components/mg/primitives";
import { NO_DRAFT, SettingField, type Draft } from "@/components/mg/setting-field";
import { ErrorState, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import type { EffectiveSetting, SettingDef, SettingOverride, SettingsSchema } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { useUnsavedChanges } from "@/lib/unsaved";

/** 'Niet meer zoeken': backfill én leveringsmotor slaan de titel over (core: backfill.excluded). */
export const NO_SEARCH_KEY = "backfill.excluded";

/**
 * Instellingen voor deze titel (verbeterlijst 25): uitzonderingen per serie of film zonder een
 * intern ID te typen. Eén schakelaar 'Niet meer zoeken', de uitzonderingen die er al zijn, en
 * een keuzelijst met onderwerpen om er een toe te voegen. Alles via dezelfde scoped API als
 * Instellingen → Alle instellingen.
 */
export function TitleSettings({ instance, kind, providerId, mediaType, title }: {
  instance: string; kind: "series" | "movie"; providerId: number; mediaType?: string | null; title?: string;
}) {
  const { t, locale } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const scopeId = `${instance}:${providerId}`;
  const chain = [mediaType ? `media_type:${mediaType}` : null, `instance:${instance}`, `${kind}:${scopeId}`].filter(Boolean).join(",");
  const schema = useResource<SettingsSchema>("/settings/schema");
  const defs = useMemo(() => (schema.data?.settings ?? []).filter((d) => d.scopes.includes(kind)), [schema.data, kind]);
  const keys = defs.map((d) => d.key).join(",");
  const effective = useResource<EffectiveSetting[]>(keys ? "/settings/effective" : null, { keys, scope: chain });
  const overrides = useResource<SettingOverride[]>("/settings/overrides", { scope_type: kind, scope_id: scopeId });
  useCoreEvents("settings.changed", () => { void effective.reload(); void overrides.reload(); });
  const [category, setCategory] = useState("");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const dirty = Object.keys(drafts).filter((k) => drafts[k] !== NO_DRAFT);
  useUnsavedChanges(dirty.length > 0);

  const effByKey = useMemo(() => Object.fromEntries((effective.data ?? []).map((e) => [e.key, e])), [effective.data]);
  const own = useMemo(() => new Map((overrides.data ?? []).filter((o) => o.scope_type === kind && o.scope_id === scopeId).map((o) => [o.key, o.value])), [overrides.data, kind, scopeId]);
  const noSearch = own.get(NO_SEARCH_KEY) === true;
  const exceptions = defs.filter((d) => own.has(d.key) && d.key !== NO_SEARCH_KEY);
  // toevoegen: alleen wat ook onder Alle instellingen staat (geen vervallen/verborgen sleutels)
  const isAdmin = can("admin");
  // alleen-beheerder (bv. quarantaine): een operator ziet ze niet in de lijst, de server weigert ze toch
  const addable = useMemo(() => defs.filter((d) => d.key !== NO_SEARCH_KEY && !d.hidden && (isAdmin || !d.admin_only)), [defs, isAdmin]);
  const categories = useMemo(() => {
    const map = new Map<string, number>();
    addable.forEach((d) => map.set(d.category, (map.get(d.category) ?? 0) + 1));
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0], locale));
  }, [addable, locale]);
  const candidates = category ? addable.filter((d) => d.category === category && !own.has(d.key)) : [];
  const editable = can("operator");

  const write = (changes: Array<{ key: string; value?: unknown; reset?: boolean }>) =>
    api.patch("/settings/scoped", { changes: changes.map((c) => ({ scope_type: kind, scope_id: scopeId, ...c })), reason: "UI: instellingen voor deze titel" });
  const reload = () => Promise.all([effective.reload(), overrides.reload()]);

  const toggleNoSearch = async (on: boolean) => {
    setBusy(NO_SEARCH_KEY);
    try {
      await write([on ? { key: NO_SEARCH_KEY, value: true } : { key: NO_SEARCH_KEY, reset: true }]);
      toast.push("success", on ? t("titleSet.noSearchOn") : t("titleSet.noSearchOff"));
      await reload();
    } catch (e) {
      toast.push("danger", t("settings.saveFailed"), e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  const remove = async (key: string) => {
    setBusy(key);
    try {
      await write([{ key, reset: true }]);
      setDrafts((d) => ({ ...d, [key]: NO_DRAFT }));
      toast.push("success", t("titleSet.removed"));
      await reload();
    } catch (e) {
      toast.push("danger", t("settings.saveFailed"), e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  const save = async () => {
    setSaving(true);
    setErrors({});
    try {
      await write(dirty.map((key) => ({ key, value: drafts[key] })));
      toast.push("success", t("settings.saved", { count: dirty.length }), t("titleSet.savedBody"));
      setDrafts({});
      await reload();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      const key = dirty.find((k) => message.includes(k)) ?? dirty[0];
      if (key) setErrors({ [key]: message });
      toast.push("danger", t("settings.saveFailed"), message);
    } finally {
      setSaving(false);
    }
  };

  const field = (def: SettingDef, isException: boolean) => (
    <div key={def.key}>
      <SettingField compact def={def} effective={effByKey[def.key]} scope={kind} setHere={false}
        draft={def.key in drafts ? drafts[def.key] : NO_DRAFT} error={errors[def.key]} disabled={!editable}
        onDraft={(v) => { setDrafts((d) => ({ ...d, [def.key]: v })); setErrors((e) => { const n = { ...e }; delete n[def.key]; return n; }); }}
        onReset={() => void remove(def.key)} />
      {isException && editable && (isAdmin || !def.admin_only) ? (
        <div className="card-footer" style={{ justifyContent: "flex-end", borderTop: 0, paddingTop: 0 }}>
          <Button size="sm" variant="ghost" icon={Trash2} pending={busy === def.key} onClick={() => void remove(def.key)} data-testid={`title-remove-${def.key}`}>{t("titleSet.remove")}</Button>
        </div>
      ) : null}
    </div>
  );

  return (
    <Card title={t("titleSet.title")} icon={SlidersHorizontal} flush id="titel-instellingen"
      actions={<Link className="link-more" href={`/instellingen?view=all&scope=${kind}&id=${encodeURIComponent(scopeId)}${title ? `&label=${encodeURIComponent(title)}` : ""}`} prefetch={false} data-testid="title-settings-all">{t("titleSet.allSettings")}</Link>}>
      {schema.loading && !schema.data ? <StateView kind="loading" compact />
        : schema.error && !schema.data ? <ErrorState error={schema.error} compact onRetry={() => void schema.reload()} />
          : (
            <div data-testid="title-settings">
              <div className="setting" data-setting={NO_SEARCH_KEY}>
                <div>
                  <div className="setting-name"><label htmlFor="title-nosearch">{t("titleSet.noSearch")}</label></div>
                  <div className="setting-help">{t(kind === "series" ? "titleSet.noSearchHintSeries" : "titleSet.noSearchHintMovie")}</div>
                </div>
                <div className="setting-control">
                  <div className="row" style={{ justifyContent: "flex-end" }}>
                    <Switch id="title-nosearch" checked={noSearch} disabled={!editable || busy === NO_SEARCH_KEY || overrides.loading && !overrides.data} label={t("titleSet.noSearch")} onChange={(v) => void toggleNoSearch(v)} />
                  </div>
                </div>
              </div>
              {exceptions.length ? (
                <div className="card-body" style={{ paddingBottom: 0 }} data-testid="title-exceptions">
                  <div className="eyebrow">{t("titleSet.exceptions", { count: exceptions.length })}</div>
                </div>
              ) : null}
              {exceptions.map((d) => field(d, true))}
              {editable ? (
                <div className="card-body" style={{ borderTop: "1px solid var(--mg-border-soft)" }}>
                  <label className="label" htmlFor="title-category">{t("titleSet.add")}</label>
                  <select id="title-category" className="select" value={category} onChange={(e) => setCategory(e.target.value)} data-testid="title-category">
                    <option value="">{t("titleSet.pickCategory")}</option>
                    {categories.map(([c, n]) => <option key={c} value={c}>{`${c} (${n})`}</option>)}
                  </select>
                  {category && !candidates.length ? <p className="hint" style={{ margin: "8px 0 0" }}>{t("titleSet.allSet")}</p> : null}
                  {!exceptions.length && !category ? <p className="hint" style={{ margin: "8px 0 0" }}>{t("titleSet.none")}</p> : null}
                </div>
              ) : !exceptions.length ? <div className="card-body"><p className="hint" style={{ margin: 0 }}>{t("titleSet.none")}</p></div> : null}
              {candidates.map((d) => field(d, false))}
              {dirty.length ? (
                <div className="card-footer" data-testid="title-savebar">
                  <span>{t("settings.saveBar", { count: dirty.length })}</span>
                  <span className="row">
                    <Button size="sm" variant="ghost" onClick={() => { setDrafts({}); setErrors({}); }}>{t("action.discard")}</Button>
                    <Button size="sm" variant="primary" pending={saving} onClick={() => void save()} data-testid="title-save">{t("action.save")}</Button>
                  </span>
                </div>
              ) : null}
            </div>
          )}
    </Card>
  );
}
