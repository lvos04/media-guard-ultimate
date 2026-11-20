import { parseServerDate } from "@/lib/i18n";

export const OVERVIEW_REFRESH_MS = 30_000;
export const OVERVIEW_MAX_AGE_MS = 2 * OVERVIEW_REFRESH_MS;
const CLOCK_SKEW_MS = 5_000;

export interface Reading {
  state: "fresh" | "stale" | "unknown";
  at: number | null;
}

export const unknownReading: Reading = { state: "unknown", at: null };

interface Source {
  data: unknown;
  error?: unknown;
  stale: boolean;
  fetchedAt: number | null;
  observedAt?: number | null;
}

/** Bij een cacheantwoord dateert de bron van vóór dit verzoek. Date en Age
 * beschrijven dezelfde ouderdom; tel ze niet bij elkaar op. */
export function responseTime(headers: Headers, now = Date.now()) {
  const date = headers.get("date");
  const server = date === null ? now : Date.parse(date);
  const cacheAge = headers.get("X-MediaGuard-Stale");
  const seconds = Math.max(Number(headers.get("age")), Number(cacheAge));
  const valid = Number.isFinite(server) && server <= now + CLOCK_SKEW_MS && Number.isFinite(seconds) && seconds >= 0;
  return { at: valid ? Math.min(server, now - seconds * 1000) : null, stale: cacheAge !== null };
}

export function readingAt(at: number | null, stale: boolean, now: number, maxAge = OVERVIEW_MAX_AGE_MS): Reading {
  if (at === null || !Number.isFinite(at) || at > now + CLOCK_SKEW_MS) return unknownReading;
  return { at, state: stale || now - at > maxAge ? "stale" : "fresh" };
}

/** Een veld in de meting gaat voor de antwoordtijd. Een ontbrekende verplichte
 * taakmeting (null) of een onleesbare datering heeft geen bekende ouderdom. */
export function sourceReading(source: Source, now: number, stamp?: unknown, maxAge = OVERVIEW_MAX_AGE_MS): Reading {
  if (source.data == null) return unknownReading;
  const data = source.data;
  if (stamp === undefined && typeof data === "object" && "generated_at" in data) stamp = data.generated_at;
  const responseAt = source.observedAt === undefined ? source.fetchedAt : source.observedAt;
  const at = stamp === undefined ? responseAt : typeof stamp === "string" ? parseServerDate(stamp)?.valueOf() ?? null : null;
  const response = readingAt(responseAt, source.stale || Boolean(source.error), now, maxAge);
  const measurement = readingAt(at, false, now, maxAge);
  if (response.at === null || measurement.at === null) return unknownReading;
  // Een oude transportkop mag een nieuwere payload-datering niet vers maken.
  if (response.state === "stale") return { state: "stale", at: Math.min(measurement.at, response.at) };
  return measurement;
}

/** Een gezamenlijke stand is zo oud als de oudste vereiste deelmeting. */
export function combinedReading(readings: Reading[]): Reading {
  if (!readings.length || readings.some(r => r.state === "unknown")) return unknownReading;
  return { at: Math.min(...readings.map(r => r.at!)), state: readings.some(r => r.state === "stale") ? "stale" : "fresh" };
}
