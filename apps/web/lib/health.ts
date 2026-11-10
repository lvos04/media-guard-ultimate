"use client";

import type { AttentionItem } from "@/components/mg/attention";
import { useAttention } from "@/components/mg/attention";
import type { SystemStatus } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";

export type HealthLevel = "ok" | "warning" | "action" | "offline" | "unknown";

/** Eén gezondheidsmodel voor kopbalk, zijbalk, Overzicht en Activiteit (B2/H8). */
export interface Health {
  level: HealthLevel;
  /** downloads + zoekacties + encodes + taken */
  active: number | null;
  activeParts: { downloads?: number; searches?: number; encodes?: number; tasks?: number };
  /** noodstop: encodes staan bevroren en tellen niet als 'bezig' (punt 39) */
  paused: { encodes: number; estop: boolean };
  errors: number;
  /** waar de fouten vandaan komen (core: errors.*), bv. av1_not_activated */
  breakdown: Record<string, number>;
  attention: number;
  reasons: Array<{ key: string; title: string; href: string; severity: "error" | "warning" | "info"; count: number }>;
  /** waar 'Aandacht N' heen gaat: precies die meldingen */
  href: string;
  /** echte storingen van de AV1-engine (kijken, eigen pauze en stille uren tellen niet mee) */
  encoderReasons: string[];
  loaded: boolean;
}

interface SummaryWire {
  level?: string; status?: string;
  active?: number | { total?: number; downloads?: number; searches?: number; encodes?: number; tasks?: number };
  paused?: { encodes?: number; emergency_stop?: boolean };
  errors?: number | ({ total?: number } & Record<string, number | undefined>);
  attention?: number | { count?: number };
  reasons?: Array<{ key?: string; code?: string; title_nl?: string; title?: string; text?: string; href?: string; severity?: string; count?: number }>;
  items?: SummaryWire["reasons"];
  encoder?: { state?: string; reasons?: string[]; waiting?: string[] };
}

const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);

function fromAttention(items: AttentionItem[]): Health["reasons"] {
  return items.map((i) => ({ key: i.key, title: i.title_nl, href: i.href, severity: i.severity, count: i.count }));
}

/** Leest GET /health/summary (core); valt terug op /status + /attention als die (nog) niet bestaat. */
export function useHealth(): Health {
  const summary = useResource<SummaryWire>("/health/summary", undefined, 30_000, { liveSlowdown: true });
  const status = useResource<SystemStatus>("/status", undefined, 20_000, { liveSlowdown: true });
  const attention = useAttention();
  useCoreEvents("system.", () => void summary.reload());
  useCoreEvents("job", () => void summary.reload());
  useCoreEvents("encoding.", () => void summary.reload());

  const s = summary.data;
  const att = attention.data;
  const attItems = att?.items ?? [];
  let reasons = fromAttention(attItems);
  let activeParts: Health["activeParts"] = {};
  let active: number | null = null;
  let errors = att?.errors ?? attItems.filter((i) => i.severity === "error").length;
  let attentionCount = att?.count ?? attItems.length;
  let breakdown: Record<string, number> = {};

  if (s) {
    if (typeof s.active === "object" && s.active) {
      activeParts = { downloads: num(s.active.downloads), searches: num(s.active.searches), encodes: num(s.active.encodes), tasks: num(s.active.tasks) };
      active = num(s.active.total) ?? Object.values(activeParts).reduce<number>((a, b) => a + (b ?? 0), 0);
    } else active = num(s.active) ?? null;
    errors = num(typeof s.errors === "object" ? s.errors?.total : s.errors) ?? errors;
    if (typeof s.errors === "object" && s.errors) breakdown = Object.fromEntries(Object.entries(s.errors).filter(([k, v]) => k !== "total" && typeof v === "number" && v > 0)) as Record<string, number>;
    attentionCount = num(typeof s.attention === "object" ? s.attention?.count : s.attention) ?? attentionCount;
    const wire = s.reasons ?? s.items;
    if (wire?.length) {
      reasons = wire.map((r, i) => ({
        key: r.key ?? r.code ?? String(i), title: r.title_nl ?? r.title ?? r.text ?? "", href: r.href ?? "/activiteit?tab=problemen",
        severity: r.severity === "error" ? "error" : r.severity === "info" ? "info" : "warning", count: r.count ?? 1,
      }));
    }
  }
  if (active === null && status.data) active = (status.data.jobs.running ?? 0) + (status.data.jobs.queued ?? 0);

  const wireLevel = s?.level ?? s?.status;
  let level: HealthLevel = status.error && !status.data ? "offline"
    : wireLevel === "ok" || wireLevel === "warning" || wireLevel === "action" ? wireLevel
      : wireLevel === "action_needed" || wireLevel === "error" ? "action"
        : !att && !s ? "unknown"
          : errors > 0 ? "action" : attentionCount > 0 ? "warning" : "ok";
  if (level === "ok" && attentionCount > 0) level = errors > 0 ? "action" : "warning";

  // één melding: rechtstreeks erheen; meer: naar de Problemen-lijst (die exact deze items toont)
  const href = reasons.length === 1 && reasons[0].href ? reasons[0].href : "/activiteit?tab=problemen";
  const encoderReasons = (s?.encoder?.reasons ?? []).filter((r): r is string => typeof r === "string");
  // /status is de verse bron voor de noodstop (de samenvatting kan tot 12 s gecachet zijn)
  const estop = Boolean(status.data ? status.data.emergency_stop?.active : s?.paused?.emergency_stop);
  const paused = { encodes: num(s?.paused?.encodes) ?? 0, estop };
  return { level, active, activeParts, paused, errors, breakdown, attention: attentionCount, reasons, href, encoderReasons, loaded: Boolean(att || s || status.data) };
}
