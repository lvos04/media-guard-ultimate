import {
  CalendarClock, Check, CircleHelp, Download, EyeOff, FileX, Search, TrendingUp, Ban, type LucideIcon,
} from "lucide-react";

import type { Tone } from "@/components/mg/primitives";
import type { MessageKey } from "@/lib/i18n";

import type { LibraryItem } from "@/lib/api/contracts";

/**
 * Peil (PEIL_MIGRATIE §6.2, ADR-0016 punt 6): beschikbaarheid is geen gezondheid. "Binnen", "Ontbreekt", "Zoekt",
 * "Verwacht", "Niet gevolgd" en "Compleet" zijn neutrale labels met een eigen icoon en woord — nooit status-groen,
 * -rood of -amber. Alleen "Onzeker" (identiteit niet bevestigd) is de as Onbekend (stippel + circle-help).
 * "neutral-strong" = neutraal label met sterke rand (ontbreekt: er is iets weg, maar het is geen storing).
 */
export interface StatusView { tone: Tone; label: MessageKey; icon: LucideIcon; count?: number }

/** Status chip for a library row, derived from the core's per-item stats. */
export function libraryStatus(item: LibraryItem): StatusView {
  if (!item.monitored) return { tone: "neutral", label: "status.unmonitored", icon: EyeOff };
  if (item.stats.missing > 0) return { tone: "strong", label: "library.missingCount", icon: FileX, count: item.stats.missing };
  if (item.stats.cutoff_unmet > 0) return { tone: "neutral", label: "library.cutoffUnmet", icon: TrendingUp, count: item.stats.cutoff_unmet };
  if (item.stats.total > 0 && item.stats.available === 0 && item.stats.upcoming > 0) return { tone: "neutral", label: "status.expected", icon: CalendarClock };
  return { tone: "neutral", label: "status.complete", icon: Check };
}

export const EPISODE_STATE: Record<string, StatusView> = {
  available: { tone: "neutral", label: "status.available", icon: Check },
  cutoff_unmet: { tone: "neutral", label: "library.cutoffUnmetShort", icon: TrendingUp },
  missing: { tone: "strong", label: "status.missing", icon: FileX },
  upcoming: { tone: "neutral", label: "status.expected", icon: CalendarClock },
  unmonitored: { tone: "neutral", label: "status.unmonitored", icon: EyeOff },
  downloading: { tone: "neutral", label: "status.downloading", icon: Download },
  ignored: { tone: "neutral", label: "status.ignored", icon: Ban },
  unavailable: { tone: "neutral", label: "status.unavailable", icon: Search },
};

/** "Onzeker — identiteit niet bevestigd": de enige beschikbaarheidstoestand op de as Onbekend. */
export const UNCERTAIN_VIEW: StatusView = { tone: "unknown", label: "status.uncertain", icon: CircleHelp };
