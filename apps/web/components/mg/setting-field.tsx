"use client";

import { Lock, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Trace } from "@/components/mg/domain";
import { LibraryWriteControl } from "@/components/mg/library-lock";
import { Button, StatusChip, Switch } from "@/components/mg/primitives";
import type { EffectiveSetting, SettingDef } from "@/lib/api/contracts";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

export const NO_DRAFT = Symbol("no-draft");
export type Draft = unknown | typeof NO_DRAFT;

export function scopeLabelKey(scope: string): MessageKey {
  return (`scope.${scope}` as MessageKey);
}

export function formatValue(def: SettingDef, value: unknown, locale: string, yes: string, no: string): string {
  if (value === null || value === undefined) return "—";
  if (def.type === "bool") return value ? yes : no;
  if (def.type === "enum") return (locale === "nl-NL" ? def.choice_labels_nl?.[String(value)] : undefined) ?? String(value);
  if (def.type === "time_window" && typeof value === "object") {
    const v = value as { start?: string; end?: string };
    return `${v.start ?? "?"}–${v.end ?? "?"}`;
  }
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/**
 * One registered backend setting: name, key, explanation, effect, inheritance, default, range,
 * restart/integrity flags, a type-appropriate control, server validation error and reset.
 */
export function SettingField({ def, effective, scope, setHere, draft, error, onDraft, onReset, disabled, note, compact }: {
  def: SettingDef; effective: EffectiveSetting | undefined; scope: string; setHere: boolean; draft: Draft; error?: string | null;
  onDraft: (value: Draft) => void; onReset: () => void; disabled?: boolean; note?: string;
  /** eenvoudige weergave: alleen naam, uitleg en invoer (geen technische sleutel/overerving) */
  compact?: boolean;
}) {
  const { t, locale } = useI18n();
  const { can } = useSession();
  // gevaarlijke instellingen alleen voor een beheerder; 'bibliotheek vrijgeven' alleen via
  // de knop met bevestiging (core weigert het via de gewone instellingen)
  const managed = Boolean(def.managed_nl);
  const adminLocked = Boolean(def.admin_only) && !can("admin");
  const blocked = Boolean(disabled) || managed || adminLocked;
  const guardNote = managed ? def.managed_nl : adminLocked ? t("guard.adminOnly") : undefined;
  const label = locale === "en-US" ? def.label_en : def.label_nl;
  const help = locale === "en-US" ? def.help_en || def.help_nl : def.help_nl;
  const current = draft === NO_DRAFT ? effective?.value ?? def.default : draft;
  // al gelijk aan de standaard: geen 'Herstel standaard' en geen 'Standaard …'-regel tonen (R17)
  const atDefault = JSON.stringify(effective?.value ?? def.default) === JSON.stringify(def.default);
  const dirty = draft !== NO_DRAFT;
  const locked = def.integrity_locked;
  const inputId = `setting-${def.key.replace(/[^a-z0-9]/gi, "-")}`;
  const source = effective?.source ?? "default";
  const fmt = (v: unknown) => formatValue(def, v, locale, t("settings.yes"), t("settings.no"));

  return (
    <div className={cn("setting", dirty && "setting--dirty", error && "setting--error", locked && "setting--locked")} data-setting={def.key} data-inactive={note ? "true" : undefined}>
      <div>
        <div className="setting-name">
          <label htmlFor={inputId}>{label}</label>
          {def.restart_required ? <StatusChip tone="warning" icon={RefreshCw}>{t("settings.restart")}</StatusChip> : null}
          {locked ? <StatusChip tone="neutral" icon={Lock}>{t("settings.integrity")}</StatusChip> : null}
          {def.advanced && !compact ? <StatusChip tone="neutral" icon={null} outline>{t("settings.advanced")}</StatusChip> : null}
        </div>
        {compact ? null : <div className="setting-key">{def.key}</div>}
        {help ? <div className="setting-help">{help}</div> : null}
        {locked ? <div className="setting-help">{t("settings.integrityHelp")}</div> : null}
        {def.effect_nl && !compact ? <div className="setting-help">{t("settings.effect", { text: def.effect_nl })}</div> : null}
        {note ? <div className="setting-help" style={{ fontStyle: "italic" }}>{note}</div> : null}
        {guardNote ? <div className="setting-help" style={{ fontStyle: "italic" }} data-testid="setting-guard-note">{guardNote}</div> : null}
        {compact ? (
          <div className="setting-meta">
            {def.unit || def.minimum !== null || def.maximum !== null ? <span>{t("settings.range", { min: def.minimum ?? "−∞", max: def.maximum ?? "∞" })}{def.unit ? ` ${def.unit}` : ""}</span> : null}
            {setHere && !atDefault ? <span>{def.unit || def.minimum !== null || def.maximum !== null ? "· " : ""}{t("settings.default", { value: fmt(def.default) })}</span> : null}
            {dirty ? <span className="t-accent">{t("settings.dirty")}</span> : null}
          </div>
        ) : <div className="setting-meta">
          {setHere
            ? <StatusChip tone="accent" icon={null}>{t("settings.setAt", { scope: t(scopeLabelKey(scope)) })}</StatusChip>
            : <StatusChip tone="neutral" icon={null} outline>{t("settings.inherits", { scope: t(scopeLabelKey(source)) })}</StatusChip>}
          <span>{t("settings.default", { value: fmt(def.default) })}</span>
          {def.minimum !== null || def.maximum !== null ? <span>· {t("settings.range", { min: def.minimum ?? "−∞", max: def.maximum ?? "∞" })}</span> : null}
          {def.unit ? <span>· {t("settings.unit", { unit: def.unit })}</span> : null}
          {dirty ? <span className="t-accent">{t("settings.dirty")}</span> : null}
        </div>}
        {!compact && effective && effective.trace.length > 1 ? (
          <div className="setting-meta" aria-label={t("settings.trace")}>
            <Trace steps={effective.trace.map((step) => ({ label: `${t(scopeLabelKey(step.scope))}${step.set ? ` · ${fmt(step.value)}` : ""}`, active: step.scope === effective.source, unset: !step.set }))} />
          </div>
        ) : null}
      </div>
      <div className="setting-control">
        {def.key === "library.write_enabled" && managed ? <LibraryWriteControl /> : (
          <Control def={def} id={inputId} value={current} onChange={onDraft} disabled={blocked || locked} invalid={Boolean(error)} />
        )}
        {error ? <div className="field-error" id={`${inputId}-error`} role="alert">{error}</div> : null}
        <div className="row">
          {dirty ? <Button size="sm" variant="ghost" onClick={() => onDraft(NO_DRAFT)}>{t("action.undo")}</Button> : null}
          {!locked && !managed && (!compact || setHere) && !atDefault ? <Button size="sm" variant="ghost" disabled={!setHere || blocked} onClick={onReset}>{t("action.restoreDefault")}</Button> : null}
        </div>
      </div>
    </div>
  );
}

function Control({ def, id, value, onChange, disabled, invalid }: {
  def: SettingDef; id: string; value: unknown; onChange: (v: Draft) => void; disabled?: boolean; invalid?: boolean;
}) {
  const { t, locale } = useI18n();
  const describedBy = invalid ? `${id}-error` : undefined;
  // text-ish inputs keep raw text locally so partial input ("1.", "") is possible
  const [raw, setRawState] = useState<string>(() => toRaw(def, value));
  const typed = useRef(false);
  const setRaw = (next: string) => { typed.current = true; setRawState(next); };
  useEffect(() => {
    // external value change (undo, reload, reset) → resync; own typing keeps the raw text
    if (typed.current) { typed.current = false; return; }
    setRawState(toRaw(def, value));
  }, [def, value]);

  switch (def.type) {
    case "bool":
      return <div className="row" style={{ justifyContent: "flex-end" }}><Switch id={id} checked={Boolean(value)} onChange={(v) => onChange(v)} label={locale === "en-US" ? def.label_en : def.label_nl} disabled={disabled} /></div>;
    case "enum":
      return (
        <select id={id} className="select" value={String(value ?? "")} disabled={disabled} aria-invalid={invalid} aria-describedby={describedBy} onChange={(e) => onChange(e.target.value)}>
          {def.choices.map((c) => <option key={c} value={c}>{(locale === "nl-NL" ? def.choice_labels_nl?.[c] : undefined) ?? c}</option>)}
        </select>
      );
    case "int":
    case "float":
    case "duration":
      return (
        <input
          id={id} className="input num" inputMode={def.type === "float" ? "decimal" : "numeric"} value={raw} disabled={disabled}
          aria-invalid={invalid} aria-describedby={describedBy}
          onChange={(e) => {
            setRaw(e.target.value);
            const normalized = e.target.value.replace(",", ".").trim();
            const n = def.type === "float" ? Number.parseFloat(normalized) : Number(normalized);
            onChange(normalized === "" || Number.isNaN(n) ? normalized : n);
          }}
        />
      );
    case "time_window": {
      const v = (value && typeof value === "object" ? value : { start: "", end: "" }) as { start: string; end: string };
      return (
        <div className="row">
          <input className="input num" type="time" aria-label={t("settings.timeFrom")} id={id} value={v.start} disabled={disabled} onChange={(e) => onChange({ ...v, start: e.target.value })} aria-invalid={invalid} />
          <span className="t-3">–</span>
          <input className="input num" type="time" aria-label={t("settings.timeTo")} value={v.end} disabled={disabled} onChange={(e) => onChange({ ...v, end: e.target.value })} aria-invalid={invalid} />
        </div>
      );
    }
    case "list":
      if (def.choices.length) {
        const selected = new Set(Array.isArray(value) ? (value as string[]) : []);
        return (
          <div className="row wrap" role="group" aria-label={def.key} id={id}>
            {def.choices.map((c) => (
              <button key={c} type="button" className="filter-chip" aria-pressed={selected.has(c)} disabled={disabled} onClick={() => {
                const next = new Set(selected);
                if (next.has(c)) next.delete(c); else next.add(c);
                onChange(def.choices.filter((x) => next.has(x)));
              }}>{(locale === "nl-NL" ? def.choice_labels_nl?.[c] : undefined) ?? c}</button>
            ))}
          </div>
        );
      }
      return (
        <>
          <input id={id} className="input" value={raw} disabled={disabled} aria-invalid={invalid} aria-describedby={describedBy}
            onChange={(e) => { setRaw(e.target.value); onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean)); }} />
          <span className="hint">{t("settings.listHint")}</span>
        </>
      );
    case "json":
      return (
        <textarea id={id} className="textarea mono" value={raw} disabled={disabled} aria-invalid={invalid} aria-describedby={describedBy}
          onChange={(e) => {
            setRaw(e.target.value);
            try { onChange(JSON.parse(e.target.value)); } catch { onChange({ __invalid_json__: e.target.value }); }
          }} />
      );
    case "secret":
      return <span className="hint">{t("settings.secretHint")}</span>;
    default:
      return <input id={id} className="input" value={raw} disabled={disabled} aria-invalid={invalid} aria-describedby={describedBy} onChange={(e) => { setRaw(e.target.value); onChange(e.target.value); }} />;
  }
}

function toRaw(def: SettingDef, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (def.type === "list" && Array.isArray(value)) return value.join(", ");
  if (def.type === "json") return JSON.stringify(value, null, 2);
  return String(value);
}
