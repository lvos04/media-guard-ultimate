"use client";

import { SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";

import { errorText, useEncoder } from "@/components/av1/use-encoder";
import { Button, Card, InlineState, Switch } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { mutation } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export interface TuningKnob {
  sleutel: string; soort: "int" | "float" | "bool" | "keuze"; waarde: number | boolean | string; standaard: number | boolean | string;
  keuzes?: { waarde: string; label_nl: string }[];
  minimum: number | null; maximum: number | null; label_nl: string; uitleg_nl: string; vastgezet_door_omgeving?: boolean;
}

/**
 * Uitvoeringsafstelling van de AV1-engine (4K-maxima, nachtvenster, vastloper, VMAF/SSIM,
 * bitrate-ondergrens, hardwaredecode, CPU-grens …), generiek uit GET /afstelling: elke nieuwe
 * knop in de engine verschijnt hier vanzelf met naam, uitleg, bereik en standaard (rapport 5:
 * alles in de app instelbaar).
 */
export function TuningSection() {
  const { t, num } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const res = useEncoder<{ items: TuningKnob[] }>("/afstelling");
  const [draft, setDraft] = useState<Record<string, number | boolean | string>>({});
  const [saving, setSaving] = useState(false);
  useEffect(() => { setDraft({}); }, [res.data]);
  if (!res.data) {
    return <Card title={t("tuning.title")} icon={SlidersHorizontal}><StateView kind={res.error ? "offline" : "loading"} compact /></Card>;
  }
  const editable = can("operator");
  const items = res.data.items ?? [];
  const value = (k: TuningKnob) => (k.sleutel in draft ? draft[k.sleutel] : k.waarde);
  const dirty = Object.keys(draft).length > 0;
  const save = async () => {
    setSaving(true);
    try {
      await mutation("/afstelling", "PUT", draft);
      toast.push("success", t("tuning.saved"));
      await res.reload();
    } catch (e) {
      toast.push("danger", t("tuning.saveFailed"), errorText(e));
    } finally { setSaving(false); }
  };
  return (
    <Card title={t("tuning.title")} sub={t("tuning.sub")} icon={SlidersHorizontal}
      footer={editable ? (
        <div className="row-between" style={{ gap: 8, flexWrap: "wrap" }}>
          <span className="t-3" style={{ fontSize: 14 }}>{dirty ? t("tuning.unsaved") : ""}</span>
          <span style={{ display: "inline-flex", gap: 8 }}>
            <Button variant="ghost" disabled={!dirty || saving} onClick={() => setDraft({})}>{t("tuning.reset")}</Button>
            <Button variant="primary" disabled={!dirty} pending={saving} onClick={() => void save()}>{t("tuning.save")}</Button>
          </span>
        </div>
      ) : undefined}>
      <div style={{ display: "grid", gap: 14 }} data-testid="tuning">
        {items.map((k) => {
          const v = value(k);
          const locked = Boolean(k.vastgezet_door_omgeving) || !editable;
          const isDefault = v === k.standaard;
          const range = k.minimum !== null && k.maximum !== null ? t("tuning.range", { min: num(k.minimum), max: num(k.maximum) }) : null;
          return (
            <div key={k.sleutel} className="row-between" style={{ gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 260px", minWidth: 0 }}>
                <label htmlFor={`tuning-${k.sleutel}`} style={{ fontWeight: 600, fontSize: 14 }}>{k.label_nl}</label>
                <div className="t-3" style={{ fontSize: 14, lineHeight: 1.45 }}>{k.uitleg_nl}</div>
                <div className="t-3" style={{ fontSize: 14 }}>
                  {t("tuning.default", { value: k.soort === "bool" ? (k.standaard ? t("tuning.on") : t("tuning.off")) : k.soort === "keuze" ? (k.keuzes?.find((c) => c.waarde === k.standaard)?.label_nl ?? String(k.standaard)) : num(k.standaard as number, k.soort === "float" ? 1 : 0) })}
                  {range ? ` · ${range}` : ""}
                  {k.vastgezet_door_omgeving ? ` · ${t("tuning.envLocked")}` : ""}
                  {!isDefault && !locked ? <> · <button type="button" className="link-more" style={{ border: 0, background: "none", padding: 0, fontSize: 14 }} onClick={() => setDraft((d) => ({ ...d, [k.sleutel]: k.standaard }))}>{t("tuning.toDefault")}</button></> : null}
                </div>
              </div>
              {k.soort === "keuze" ? (
                <select id={`tuning-${k.sleutel}`} className="input" style={{ minWidth: 180 }} disabled={locked} value={String(v)}
                  onChange={(e) => setDraft((d) => ({ ...d, [k.sleutel]: e.target.value }))}>
                  {(k.keuzes ?? []).map((c) => <option key={c.waarde} value={c.waarde}>{c.label_nl}</option>)}
                </select>
              ) : k.soort === "bool" ? (
                <Switch id={`tuning-${k.sleutel}`} checked={Boolean(v)} disabled={locked} label={k.label_nl}
                  onChange={(next) => setDraft((d) => ({ ...d, [k.sleutel]: next }))} />
              ) : (
                <input id={`tuning-${k.sleutel}`} className="input" type="number" inputMode="decimal" style={{ width: 110 }}
                  disabled={locked} value={String(v)} step={k.soort === "float" ? 0.1 : 1}
                  min={k.minimum ?? undefined} max={k.maximum ?? undefined}
                  onChange={(e) => { const n = Number(e.target.value); if (e.target.value !== "" && Number.isFinite(n)) setDraft((d) => ({ ...d, [k.sleutel]: n })); }} />
              )}
            </div>
          );
        })}
        {!items.length ? <InlineState tone="neutral">{t("tuning.none")}</InlineState> : null}
      </div>
    </Card>
  );
}
