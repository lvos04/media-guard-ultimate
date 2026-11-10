"use client";

import { Info, Link2Off, OctagonX, Route, Settings, TriangleAlert, type LucideIcon } from "lucide-react";
import { useState } from "react";

import { isDismissAction, useAttention } from "@/components/mg/attention";
import type { CoreJob, SeerrSettings, WantedKpis, WantedReasons } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { MigRunRow } from "@/lib/api/migration";
import type { ProvStatusView } from "@/lib/api/provisioning";
import { useCoreEvents } from "@/lib/events";
import { useHealth } from "@/lib/health";
import { useI18n } from "@/lib/i18n";
import { taskLabel } from "@/lib/tasks";
import { parseServerDate } from "@/lib/i18n";

/**
 * Soort taak (Peil, PEIL_MIGRATIE §5.1): bepaalt het icoon en of de taak de totaalstatus raakt.
 *   fault   → storing of mislukking (octagon-x)        warning → waarschuwing (triangle-alert)
 *   setup   → inrichting, geen storing (settings, link-2-off, route)   info → ter info (info)
 * Nooit een vinkje (circle-check) voor een open taak.
 */
export type ToDoKind = "fault" | "warning" | "setup" | "info";
export interface ToDoRow { key: string; kind: ToDoKind; icon: LucideIcon; title: string; sub: string; href: string; action: string }
export const TODO_ICON: Record<ToDoKind, LucideIcon> = { fault: OctagonX, warning: TriangleAlert, setup: Settings, info: Info };

/** Wat er nog ingesteld moet worden. Verdwijnt vanzelf als alles klaar is. */
/** Eén lijst voor 'Wacht op jou' én de statusregel bovenaan, zodat de aantallen gelijk zijn. */
const AV1_ROWS: Record<string, "problems.av1NotActivated" | "problems.av1Failed" | "problems.av1Conflicts" | "problems.releaseCheck" | "problems.encoderDegraded"> = {
  av1_not_activated: "problems.av1NotActivated", av1_failed: "problems.av1Failed", av1_conflicts: "problems.av1Conflicts",
  release_check_failed: "problems.releaseCheck", encoder_degraded: "problems.encoderDegraded",
};

export function useTodoRows(): ToDoRow[] {
  const health = useHealth();
  const { t } = useI18n();
  const prov = useResource<ProvStatusView>("/provisioning/status");
  const runs = useResource<MigRunRow[]>("/migration/runs");
  const seerr = useResource<SeerrSettings>("/seerr/settings");
  const attention = useAttention();
  const failedJobs = useResource<CoreJob[]>("/jobs", { status: "failed", limit: 20 });
  const wanted = useResource<WantedKpis>("/wanted/kpis", undefined, 120_000);
  const wantedReasons = useResource<WantedReasons>("/wanted/reasons");
  useCoreEvents("job", () => void failedJobs.reload());
  const [since] = useState(() => Date.now() - 7 * 86_400_000);
  const rows: ToDoRow[] = [];
  const active = (runs.data ?? []).find((r) => r.status === "active");
  if (active) rows.push({ key: "mig", kind: "setup", icon: Route, title: t("todo.migration"), sub: t("todo.migrationSub", { name: active.name }), href: `/migratie?run=${active.id}`, action: t("todo.continue") });
  const required = (prov.data?.needs ?? []).filter((n) => n.severity !== "optional");
  if (required.length) rows.push({ key: "prov", kind: "setup", icon: Settings, title: t("todo.provisioning", { count: required.length }), sub: required[0].message_nl, href: "/inrichten", action: t("todo.fix") });
  if (seerr.data && !seerr.data.api_key_set) rows.push({ key: "seerr", kind: "setup", icon: Link2Off, title: t("todo.seerr"), sub: t("todo.seerrSub"), href: "/inrichten?tab=seerr", action: t("todo.connect") });
  // alles waar MediaGuard op jou wacht (activeren, conflicten, pauzes, twijfelgevallen, …)
  // de vergrendeling heeft op Overzicht al een eigen balk met knop
  for (const it of (attention.data?.items ?? []).filter((i) => i.key !== "library.locked")) {
    // 'Bewust zo gekozen' staat in de Problemen-lijst, maar is geen reden om daarheen te sturen
    const actions = it.actions.filter((a) => !isDismissAction(a));
    // fouten (bv. conflicten, quarantaine vol) bovenaan; de rest na de inrichtingsstappen
    const kind: ToDoKind = it.severity === "error" ? "fault" : it.severity === "info" ? "info" : "warning";
    rows[it.severity === "error" ? "unshift" : "push"]({ key: it.key, kind, icon: TODO_ICON[kind], title: it.title_nl, sub: it.detail_nl,
      href: it.rows.length || actions.length ? "/activiteit?tab=problemen" : it.href, action: t("todo.look") });
  }
  const failed7 = (failedJobs.data ?? []).filter((j) => (parseServerDate(String((j.finished_at as string | null | undefined) ?? j.created_at))?.valueOf() ?? 0) >= since);
  if (failed7.length) rows.push({ key: "jobs-failed", kind: "fault", icon: OctagonX, title: t("todo.tasksFailed", { count: failed7.length }), sub: failed7.slice(0, 2).map((j) => taskLabel(t, j.job_type)).join(", "), href: "/activiteit?tab=geschiedenis&status=failed", action: t("todo.look") });
  // fouten uit het gezondheidsmodel die nergens anders in deze lijst staan (bv. AV1 niet geplaatst)
  for (const [key, n] of Object.entries(health.breakdown)) {
    const label = AV1_ROWS[key];
    // de engine zegt zelf wat er mis is; kijken, eigen pauze en stille uren tellen niet (lijst 8)
    const sub = key === "encoder_degraded" && health.encoderReasons[0] ? health.encoderReasons[0] : t("todo.av1Sub");
    if (label) rows.unshift({ key: `h-${key}`, kind: "fault", icon: OctagonX, title: t(label, { count: n }), sub, href: key === "encoder_degraded" ? "/av1" : "/activiteit?tab=problemen", action: t("todo.look") });
  }
  // ontbrekend-oorzaken die echt iets van jou vragen (de rest zoekt MediaGuard zelf)
  const needs = wanted.data?.needs_user?.reasons;
  if (needs) {
    for (const r of needs.filter((x) => x.total > 0)) rows.push({ key: `wanted-${r.reason}-${r.code}`, kind: "warning", icon: TriangleAlert, title: t("todo.wantedReason", { label: r.label_nl }), sub: r.fix_nl, href: r.href || `/ontbrekend?reason_code=${encodeURIComponent(r.code)}`, action: t("todo.look") });
  } else {
    // oudere core: afleiden uit de verdeling + needs_user per oorzaak
    for (const [code, n] of Object.entries(wanted.data?.reason_distribution ?? {})) {
      const r = wantedReasons.data?.[code];
      if (n > 0 && r?.needs_user) rows.push({ key: `wanted-${code}`, kind: "warning", icon: TriangleAlert, title: t("todo.wantedReason", { label: r.label_nl }), sub: r.fix_nl, href: r.href ?? `/ontbrekend?reason_code=${encodeURIComponent(code)}`, action: t("todo.look") });
    }
  }
  // geen GPU is geen taak voor de gebruiker: encodes lopen gewoon op de CPU (zie Ruimte)
  return rows;
}


/**
 * Totaalstatus (Peil, SysteemStatusPaneel): het slechtste dat GEMETEN is. Core onbereikbaar → Onbekend; een storing of
 * waarschuwing in de lijst, of fouten in het gezondheidsmodel → Beperkt; anders Gezond. Inrichting en info tellen
 * niet mee (die staan alleen in "Wat actie nodig heeft"). Eén regel voor Overzicht en zijbalk.
 */
export type TotalStatus = "healthy" | "warning" | "unknown";
export function totalStatus(health: { level: string; errors: number }, rows: Array<Pick<ToDoRow, "kind">>): TotalStatus {
  if (health.level === "offline") return "unknown";
  if (health.errors > 0 || rows.some((r) => r.kind === "fault" || r.kind === "warning")) return "warning";
  return "healthy";
}
