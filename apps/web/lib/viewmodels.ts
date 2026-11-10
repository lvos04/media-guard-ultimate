/**
 * UI view models + adapters from core API shapes. Components render view models only, so a
 * backend contract change is fixed in one adapter instead of in every page.
 */
import type { Instance, LibraryItem, MediaKind, Season } from "@/lib/api/contracts";

export interface QualityInfo {
  resolution?: string | null;
  source?: string | null;
  codec?: string | null;
  hdr?: string | null;
  dv_profile?: string | null;
  audio_languages?: string[];
}

/** Parse an arr quality name ("WEBDL-2160p", "Bluray-1080p", "HDTV-720p") into ribbon parts. */
export function qualityFromName(name: string | null | undefined, languages?: string[]): QualityInfo | null {
  if (!name) return null;
  const res = name.match(/(2160|1080|720|576|480)p/i)?.[0] ?? null;
  const source = name.replace(/-?(2160|1080|720|576|480)p/i, "").replace(/[-_]/g, " ").trim() || null;
  return { resolution: res, source, audio_languages: languages };
}

export type EpisodeCellState = "ok" | "av1" | "missing" | "searching" | "future" | "unmonitored" | "uncertain";
export interface SeasonRow { label: string; title?: string; href?: string; cells: Array<{ state: EpisodeCellState; label: string; href?: string }>; percent: number | null }

const CELL: Record<string, EpisodeCellState> = {
  available: "ok", cutoff_unmet: "ok", missing: "missing", upcoming: "future", unmonitored: "unmonitored",
  downloading: "searching", ignored: "unmonitored", unavailable: "uncertain",
};

export function seasonRows(seasons: Season[], hrefFor?: (season: number, episodeId: number) => string, prefix = "S"): SeasonRow[] {
  return seasons.map((s) => ({
    label: `${prefix}${String(s.season_number).padStart(2, "0")}`,
    cells: s.episodes.map((e) => ({
      state: CELL[e.state] ?? "uncertain",
      label: `S${String(s.season_number).padStart(2, "0")}E${String(e.episode_number).padStart(2, "0")}${e.title ? ` ${e.title}` : ""}`,
      href: hrefFor?.(s.season_number, e.episode_id),
    })),
    percent: s.completeness === null || s.completeness === undefined ? null : s.completeness * 100,
  }));
}

export interface EvidenceView {
  id: string;
  source: string;
  verdict: "confirms" | "contradicts" | "neutral";
  confidence: number | null;
  matching_id?: string | null;
  claim?: string | null;
  method?: string | null;
  trace?: string[];
  raw?: unknown;
  checked_at?: string | null;
}

/** identity_results rows → evidence cards (one per evidence entry of the current result). */
export function evidenceFromIdentity(result: Record<string, unknown> | undefined): EvidenceView[] {
  if (!result) return [];
  const list = Array.isArray(result.evidence) ? (result.evidence as Array<Record<string, unknown>>) : [];
  const confidence = typeof result.confidence === "number" ? result.confidence : null;
  if (!list.length) {
    return [{ id: String(result.id ?? "result"), source: String(result.method ?? result.source ?? "identity"), verdict: result.status === "verified" ? "confirms" : result.status === "mismatch" ? "contradicts" : "neutral", confidence, claim: typeof result.reason_nl === "string" ? result.reason_nl : null, raw: result, checked_at: typeof result.checked_at === "string" ? result.checked_at : null }];
  }
  return list.map((e, i) => ({
    id: `${String(result.id ?? "r")}-${i}`,
    source: String(e.source ?? e.kind ?? `bron ${i + 1}`),
    verdict: e.agrees === false || e.verdict === "contradicts" ? "contradicts" : e.agrees === true || e.verdict === "confirms" ? "confirms" : "neutral",
    confidence: typeof e.confidence === "number" ? e.confidence : confidence,
    matching_id: typeof e.id === "string" || typeof e.id === "number" ? String(e.id) : null,
    claim: typeof e.claim === "string" ? e.claim : typeof e.value === "string" ? e.value : null,
    method: typeof e.method === "string" ? e.method : null,
    raw: e,
  }));
}

export interface JobLane {
  lane: "gpu" | "cpu";
  encoder: string;
  file: string | null;
  percent: number | null;
  fps: number | null;
  eta_seconds: number | null;
  bitrate_kbps: number | null;
  saving_percent: number | null;
  stale?: boolean;
  quality?: QualityInfo | null;
}

export type DistributionKey = "behouden" | "vervangen" | "zoeken" | "AV1" | "onzeker" | "overslaan";
export const DISTRIBUTION_KEYS: DistributionKey[] = ["behouden", "AV1", "vervangen", "zoeken", "onzeker", "overslaan"];

export function libraryComplete(item: LibraryItem): { have: number; total: number; pct: number | null } {
  const expected = item.stats.total - item.stats.upcoming - item.stats.unmonitored;
  return { have: item.stats.available, total: expected, pct: expected > 0 ? (item.stats.available / expected) * 100 : null };
}

export function instanceRoot(instance: Instance): string | null {
  return instance.default_root_folder ?? null;
}

export const MEDIA_TYPES: MediaKind[] = ["tv", "anime", "movie", "anime_movie"];
