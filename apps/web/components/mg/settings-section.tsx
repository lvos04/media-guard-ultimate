"use client";

import { Info, Settings } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Button, Card, InlineState } from "@/components/mg/primitives";
import { NO_DRAFT, SettingField, type Draft } from "@/components/mg/setting-field";
import { ErrorState, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import type { EffectiveSetting, SettingOverride, SettingsSchema } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { useUnsavedChanges } from "@/lib/unsaved";

/**
 * Schema-driven settings block for a feature page (Audio, Dolby Vision, AI, Backfill…).
 * Renders every registered setting whose key starts with one of `prefixes` (or whose category
 * matches), at the given scope, with the same validation/save/reset semantics as Instellingen.
 */
export function SettingsSection({ title, prefixes, categories = [], scope = "global", scopeId = "", empty, exclude = [], saveVia, disabledKeys = [], disabledHint }: {
  title: string; prefixes: string[]; categories?: string[]; scope?: string; scopeId?: string; empty?: string;
  /** keys rendered elsewhere on the page */
  exclude?: string[];
  /** feature-specific save endpoint (e.g. PATCH /ai/settings, which enforces AI enable gates) */
  saveVia?: (values: Record<string, unknown>) => Promise<unknown>;
  /** keys shown greyed out because another choice on the page makes them irrelevant */
  disabledKeys?: string[];
  disabledHint?: string;
}) {
  const { t } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const schema = useResource<SettingsSchema>("/settings/schema");
  const chain = scope === "global" ? undefined : { scope: `${scope}:${scopeId}` };
  const defs = useMemo(() => (schema.data?.settings ?? []).filter((d) => d.scopes.includes(scope) && (prefixes.some((p) => d.key.startsWith(p)) || categories.includes(d.category)) && !exclude.includes(d.key)), [categories, exclude, prefixes, schema.data, scope]);
  const keys = defs.map((d) => d.key).join(",");
  const effective = useResource<EffectiveSetting[]>(keys ? "/settings/effective" : null, { keys, ...chain });
  const overrides = useResource<SettingOverride[]>(keys ? "/settings/overrides" : null, { scope_type: scope, scope_id: scopeId || undefined });
  useCoreEvents("settings.changed", () => { void effective.reload(); void overrides.reload(); });
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const dirty = Object.keys(drafts).filter((k) => drafts[k] !== NO_DRAFT);
  useUnsavedChanges(dirty.length > 0);
  const effByKey = Object.fromEntries((effective.data ?? []).map((e) => [e.key, e]));
  const setHere = new Set((overrides.data ?? []).map((o) => o.key));

  const save = async () => {
    setSaving(true);
    setErrors({});
    try {
      if (saveVia) await saveVia(Object.fromEntries(dirty.map((key) => [key, drafts[key]])));
      else await api.patch("/settings/scoped", { changes: dirty.map((key) => ({ scope_type: scope, scope_id: scopeId, key, value: drafts[key] })), reason: title });
      toast.push("success", t("settings.saved", { count: dirty.length }), t("settings.savedBody"));
      setDrafts({});
      await Promise.all([effective.reload(), overrides.reload()]);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      const key = dirty.find((k) => message.includes(k)) ?? dirty[0];
      setErrors({ [key]: message });
      toast.push("danger", t("settings.saveFailed"), message);
    } finally {
      setSaving(false);
    }
  };
  const reset = async (key: string) => {
    try {
      await api.patch("/settings/scoped", { changes: [{ scope_type: scope, scope_id: scopeId, key, reset: true }], reason: title });
      toast.push("success", t("settings.reset"));
      await Promise.all([effective.reload(), overrides.reload()]);
    } catch (e) {
      toast.push("danger", t("settings.saveFailed"), e instanceof Error ? e.message : undefined);
    }
  };

  return (
    <Card title={title} icon={Settings} flush actions={<Link className="link-more" href={`/instellingen?q=${encodeURIComponent(prefixes[0] ?? "")}`}>{t("nav.settings")}</Link>}>
      {schema.loading ? <StateView kind="loading" compact />
        : schema.error ? <ErrorState error={schema.error} compact onRetry={() => void schema.reload()} />
          : defs.length === 0 ? <div className="card-body"><InlineState icon={Info}>{empty ?? t("section.noSettings", { prefix: prefixes.join(", ") })}</InlineState></div>
            : (
              <>
                {defs.map((def) => (
                  <SettingField key={def.key} def={def} effective={effByKey[def.key]} scope={scope} setHere={setHere.has(def.key)}
                    draft={def.key in drafts ? drafts[def.key] : NO_DRAFT} error={errors[def.key] ?? (disabledKeys.includes(def.key) ? null : undefined)} disabled={!can("operator") || disabledKeys.includes(def.key)} note={disabledKeys.includes(def.key) ? disabledHint : undefined}
                    onDraft={(v) => { setDrafts((d) => ({ ...d, [def.key]: v })); setErrors({}); }} onReset={() => void reset(def.key)} />
                ))}
                {dirty.length ? (
                  <div className="card-footer">
                    <span>{t("settings.saveBar", { count: dirty.length })}</span>
                    <span className="row"><Button size="sm" variant="ghost" onClick={() => { setDrafts({}); setErrors({}); }}>{t("action.discard")}</Button><Button size="sm" variant="primary" pending={saving} onClick={() => void save()}>{t("action.save")}</Button></span>
                  </div>
                ) : null}
              </>
            )}
    </Card>
  );
}
