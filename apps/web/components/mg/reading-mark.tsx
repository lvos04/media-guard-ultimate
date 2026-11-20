"use client";

import { StaleMark } from "@/components/mg/states";
import { StatusChip } from "@/components/mg/primitives";
import { useI18n } from "@/lib/i18n";
import type { Reading } from "@/lib/reading";

export function ReadingMark({ reading, dated = true }: { reading: Reading; dated?: boolean }) {
  const { t, time, dateTime } = useI18n();
  if (reading.state === "fresh") return null;
  if (reading.at === null) return <StatusChip tone="unknown">{t("status.unknown")}</StatusChip>;
  const label = t("overview.readingStale", { when: time(reading.at) });
  if (!dated) return <StaleMark>{label}</StaleMark>;
  return <StaleMark><time dateTime={new Date(reading.at).toISOString()} title={dateTime(reading.at)}>{label}</time></StaleMark>;
}
