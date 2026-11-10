import type { MessageKey } from "@/lib/i18n";

/** Taaktype → Nederlandse omschrijving (geen 'library.sync #12' op het scherm). */
export function taskLabel(t: (k: MessageKey, v?: Record<string, string | number>) => string, jobType: string): string {
  const key = `task.${jobType}` as MessageKey;
  const label = t(key);
  return label === key ? t("task.generic", { type: jobType.replace(/[._]/g, " ") }) : label;
}
