// Mediaservers (Δ-MG-DUAL, ADR-0022): vormen van GET /api/v1/mediaserver/status en de regels waarmee de web-UI er
// een status van maakt. Geen React hier: deze regels worden los getest (tests/unit/mediaserver.test.tsx).
//
// Peil §4: groen ("Gezond") staat alleen bij een gemeten, geslaagd contact; "Gekoppeld" is geen gezondheid. Zonder
// meting is de stand "Onbekend" met een reden, nooit een aanname.

export type ServerKind = "auto" | "emby" | "jellyfin" | "plex";
export type SettingKind = ServerKind | "off";

export interface MsServer {
  server_id: number;
  kind: ServerKind;
  label: string;
  url: string;
  url_source: string;
  key_set: boolean;
  enabled: boolean;
  followed: boolean;
  /** server 1 = de koppeling die er al was (instellingen van 1.2.2, nooit verplaatst) */
  legacy: boolean;
  auth: string | null;
  connected: boolean;
  contact: { at: string | null; ok: boolean | null; last_ok_at: string | null; error: string | null } | null;
  hold: { active: boolean };
  path_map: Array<[string, string]>;
  results: { open: number; ok: number; transcode: number; failed: number; not_found: number; nl_visible: number; nl_missing: number };
}

/** Hoe de koppeling is ingesteld (geen meting). */
export type Link = "linked" | "detached" | "noAddress" | "noKey";
/** Wat er gemeten is; alleen bij een gekoppelde server. */
export type Health = "healthy" | "down" | "unknown";
export type Why = "error" | "notMeasured" | "stale";

export interface ServerState { link: Link; health: Health | null; why: Why | null }

export function serverState(s: MsServer): ServerState {
  if (!s.enabled) return { link: "detached", health: null, why: null };
  if (!s.url) return { link: "noAddress", health: null, why: null };
  if (!s.key_set) return { link: "noKey", health: null, why: null };
  if (s.connected) return { link: "linked", health: "healthy", why: null };
  if (s.contact && s.contact.ok === false) return { link: "linked", health: "down", why: "error" };
  // nog nooit contact gehad (net gekoppeld of net bijgewerkt), of het laatste goede contact is te oud om te tellen
  return { link: "linked", health: "unknown", why: s.contact?.at ? "stale" : "notMeasured" };
}

/** Het adresveld meldt waar het adres vandaan komt; de API geeft dat als Nederlandse tekst. */
export function sourceKey(source: string): "ms.src.set" | "ms.src.found" | "ms.src.none" {
  return source === "ingesteld" ? "ms.src.set" : source === "automatisch gevonden" ? "ms.src.found" : "ms.src.none";
}

/** Uitkomsten van afspeeltests die de UI een eigen tekst geeft; al het andere toont ze letterlijk. */
export const RESULT_KEYS = ["ok", "transcode", "failed", "not_found"] as const;
export type ResultKey = (typeof RESULT_KEYS)[number];
export function isResultKey(value: string): value is ResultKey {
  return (RESULT_KEYS as readonly string[]).includes(value);
}

/** Een gevonden server die nog niet in de lijst staat, is een voorstel om toe te voegen. */
export function suggestions<T extends { url: string }>(found: T[], servers: MsServer[]): T[] {
  const known = new Set(servers.map((s) => s.url.replace(/\/+$/, "").toLowerCase()).filter(Boolean));
  return found.filter((f) => !known.has(f.url.replace(/\/+$/, "").toLowerCase()));
}

export const MAX_EXTRA_SERVERS = 8;
