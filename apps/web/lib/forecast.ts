import type { MessageKey } from "@/lib/i18n";

type T = (key: MessageKey, vars?: Record<string, string | number>) => string;

/** Prognose als "± X weken" (R12): nooit een exacte datum, niets bij een lege wachtrij. */
export function forecastText(t: T, s: { prognose_klaar?: string | null; prognose_dagen?: number | null; prognose_nl?: string | null; resterend_bytes?: number | null } | null | undefined,
  /** langste resterende tijd van een lopende encode (s): de wachtrij kan niet eerder klaar zijn (S16) */
  longestRunningSeconds?: number | null): string {
  if (!s) return "—";
  if (s.resterend_bytes === 0) return t("av1.kpi.forecastNone");
  const minDays = longestRunningSeconds && longestRunningSeconds > 0 ? longestRunningSeconds / 86_400 : 0;
  let days = typeof s.prognose_dagen === "number" ? s.prognose_dagen : null;
  if (s.prognose_nl && !(days !== null && minDays > days)) return s.prognose_nl;
  if (days !== null && minDays > days) days = minDays;
  if (days === null && s.prognose_klaar) days = (new Date(s.prognose_klaar).valueOf() - Date.now()) / 86_400_000;
  if (days === null || !Number.isFinite(days) || days < 0) return t("av1.kpi.noForecast");
  if (days > 365) return t("av1.kpi.forecastYear");
  if (days < 1) return t("av1.kpi.forecastHours", { n: Math.max(1, Math.round(days * 24)) });
  if (days < 14) return t("av1.kpi.forecastDays", { n: Math.max(1, Math.round(days)) });
  return t("av1.kpi.forecastWeeks", { n: Math.round(days / 7) });
}

/** AV1-fouten uit het gezondheidsmodel: dezelfde telling op Overzicht en Ruimte (R4). */
export const AV1_ERROR_KEYS = ["av1_not_activated", "av1_failed", "av1_conflicts", "release_check_failed"] as const;
export function av1Errors(breakdown: Record<string, number>): number {
  return AV1_ERROR_KEYS.reduce((sum, k) => sum + (breakdown[k] ?? 0), 0);
}
