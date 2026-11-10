import { limited } from "./limiter";

/**
 * Typed client for the MediaGuard core API (`/api/v1/*`, proxied by server.mjs).
 * - Session = HttpOnly cookie (sent automatically, same origin).
 * - Mutations send `X-CSRF-Token` (value from `GET /api/v1/auth/me`).
 * - Every failure becomes an `ApiError` with a `kind` so the UI can render the right state
 *   (unavailable endpoint ≠ backend offline ≠ validation error ≠ forbidden).
 */

export const CORE_BASE = "/api/v1";

export type ApiErrorKind =
  | "unauthorized" // 401 – not logged in / session expired
  | "forbidden" // 403 – role too low or CSRF failure
  | "unavailable" // 404/405/501 on a route the backend does not (yet) implement
  | "not_found" // 404 for an existing route but missing resource
  | "validation" // 400/409/422 – user-correctable
  | "offline" // network error / 502 / 503 / 504 – backend not reachable
  | "server"; // other 5xx

export class ApiError extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    public readonly status: number,
    message: string,
    public readonly path: string,
    public readonly correlationId: string | null = null,
    public readonly detail: unknown = null,
    /** machineleesbare foutcode uit de foutvorm van core ({detail, code, correlation_id}); null als die ontbreekt */
    public readonly code: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let csrfToken: string | null = null;
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}
export function getCsrfToken(): string | null {
  return csrfToken;
}

/**
 * De shell toont de pagina al terwijl GET /auth/me nog loopt (sneller via de tunnel). Een
 * wijziging die in dat venster vertrekt (bv. direct een back-up kiezen na het openen van
 * /migratie) had nog geen CSRF-token en kreeg 403 "CSRF-controle mislukt". Zolang de sessie
 * laadt, wacht een wijziging daarom op het token (hooguit CSRF_WAIT_MS); daarna gaat hij
 * gewoon zonder token, zodat core zelf de echte reden geeft (401/403).
 */
export const CSRF_WAIT_MS = 10_000;
let sessionLoad: { promise: Promise<void>; done: () => void } | null = null;
export function beginSessionLoad(): void {
  if (sessionLoad) return;
  let done = () => {};
  const promise = new Promise<void>((resolve) => { done = resolve; });
  sessionLoad = { promise, done };
}
export function endSessionLoad(): void {
  sessionLoad?.done();
  sessionLoad = null;
}
/** CSRF-token voor een wijziging; wacht zo nodig op een sessie die nog geladen wordt. */
export async function csrfTokenForMutation(): Promise<string | null> {
  if (csrfToken || !sessionLoad) return csrfToken;
  let timer: ReturnType<typeof setTimeout> | undefined;
  await Promise.race([
    sessionLoad.promise,
    new Promise<void>((resolve) => { timer = setTimeout(resolve, CSRF_WAIT_MS); }),
  ]);
  if (timer !== undefined) clearTimeout(timer);
  return csrfToken;
}

type MutationListener = () => void;
const mutationListeners = new Set<MutationListener>();
/** De gedeelde cache (hooks.ts) luistert: na elke geslaagde wijziging is alles 'verouderd'. */
export function onMutation(listener: MutationListener): () => void {
  mutationListeners.add(listener);
  return () => mutationListeners.delete(listener);
}

type Listener = (error: ApiError) => void;
const unauthorizedListeners = new Set<Listener>();
/** Session layer subscribes so an expired session anywhere sends the user to /login. */
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

// Velden uit pydantic-foutpaden in gewone taal (de rest blijft leesbaar als woord).
const FIELD_NL: Record<string, string> = {
  download_clients: "Downloadclient", indexers: "Indexer", instances: "Instantie", host: "adres", port: "poort",
  url: "adres (URL)", url_base: "URL-basis", api_key: "API-sleutel", apikey: "API-sleutel", name: "naam",
  username: "gebruikersnaam", password: "wachtwoord", priority: "prioriteit", categories: "categorie",
  value: "waarde", path: "pad", root_folder: "hoofdmap", email: "e-mailadres",
};
const MSG_NL: Array<[RegExp, string]> = [
  [/^field required$/i, "is verplicht"],
  [/^string should have at least 1 character/i, "is verplicht"],
  [/^string should have at least (\d+) characters?/i, "moet minstens $1 tekens hebben"],
  [/^string should have at most (\d+) characters?/i, "mag hoogstens $1 tekens hebben"],
  [/^input should be a valid integer/i, "moet een geheel getal zijn"],
  [/^input should be a valid number/i, "moet een getal zijn"],
  [/^input should be a valid boolean/i, "moet aan of uit zijn"],
  [/^input should be greater than or equal to (\S+)/i, "moet minstens $1 zijn"],
  [/^input should be less than or equal to (\S+)/i, "mag hoogstens $1 zijn"],
  [/^input should be (.+)$/i, "moet een van deze zijn: $1"],
  [/^value error, /i, ""],
];

function fieldLabel(loc: unknown[]): string {
  const parts: string[] = [];
  for (const p of loc) {
    if (p === "body" || p === "query") continue;
    if (typeof p === "number") { if (parts.length) parts[parts.length - 1] = `${parts[parts.length - 1]} ${p + 1}`; continue; }
    parts.push(FIELD_NL[String(p)] ?? String(p).replace(/_/g, " "));
  }
  return parts.join(" → ");
}

function friendly(msg: string): string {
  for (const [re, nl] of MSG_NL) if (re.test(msg)) return msg.replace(re, nl);
  return msg;
}

/** Fout die Sonarr/Radarr doorgaf ('sonarr (sonarr-series) POST /api/v3/x: …') zonder technische kop. */
function stripArrPrefix(text: string): string {
  const down = /^(sonarr|radarr|prowlarr|seerr)\s*\(([^)]+)\)\s+niet bereikbaar\b/i.exec(text);
  if (down) return `${down[1].charAt(0).toUpperCase() + down[1].slice(1)} (${down[2]}) is nu niet bereikbaar. MediaGuard probeert het vanzelf opnieuw; controleer anders of de app draait.`;
  const m = /^(sonarr|radarr|prowlarr|seerr)\s*\(([^)]+)\)\s+[A-Z]+\s+\/\S+:\s*(.*)$/is.exec(text);
  if (!m) return text;
  const app = m[1].charAt(0).toUpperCase() + m[1].slice(1);
  const rest = m[3].replace(/^HTTP 5\d\d\s*/i, "").replace(/^ongeldig verzoek:\s*/i, "");
  return `${app} (${m[2]}) meldt: ${rest}`;
}

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return stripArrPrefix(detail);
  if (Array.isArray(detail)) {
    // FastAPI/pydantic validation errors → "Downloadclient 1 → adres: is verplicht"
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const label = "loc" in item && Array.isArray(item.loc) ? fieldLabel(item.loc as unknown[]) : "";
          const msg = friendly(String(item.msg));
          return label ? `${label.charAt(0).toUpperCase()}${label.slice(1)}: ${msg}` : msg;
        }
        return JSON.stringify(item);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    const text = d.message_nl ?? d.message ?? d.detail;
    if (typeof text === "string") return stripArrPrefix(text);
    return JSON.stringify(detail);
  }
  return fallback;
}

function kindFor(status: number, detail: unknown): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return detail === "Not Found" ? "unavailable" : "not_found";
  if (status === 405 || status === 501) return "unavailable";
  if (status === 400 || status === 409 || status === 422) return "validation";
  if (status === 502 || status === 503 || status === 504) return "offline";
  return "server";
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export function buildPath(path: string, query?: RequestOptions["query"]): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== null && v !== undefined && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `${path}${path.includes("?") ? "&" : "?"}${s}` : path;
}

export function coreRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  // wijzigingen gaan altijd direct; lezen wacht zo nodig op een vrije plek
  return (options.method ?? "GET") === "GET" ? limited(() => coreRequestNow<T>(path, options)) : coreRequestNow<T>(path, options);
}

async function coreRequestNow<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const url = `${CORE_BASE}${buildPath(path, options.query)}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const token = await csrfTokenForMutation();
    if (token) headers["X-CSRF-Token"] = token;
  }
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "same-origin",
      cache: "no-store",
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError("offline", 0, "network", path);
  }
  const headerCorrelation = response.headers.get("x-correlation-id") ?? response.headers.get("x-request-id");
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : null;
    // foutvorm van core: {detail, code, correlation_id}; de header gaat voor (zelfde waarde)
    const field = (name: string): string | null => {
      const v = body && typeof body === "object" ? (body as Record<string, unknown>)[name] : null;
      return typeof v === "string" && v ? v : null;
    };
    const correlationId = headerCorrelation ?? field("correlation_id");
    const kind = kindFor(response.status, detail);
    const error = new ApiError(kind, response.status, messageFrom(detail, response.statusText || String(response.status)), path, correlationId, detail, field("code"));
    if (kind === "unauthorized") unauthorizedListeners.forEach((l) => l(error));
    throw error;
  }
  if (method !== "GET") mutationListeners.forEach((l) => l());
  if (response.status === 204) return undefined as T;
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("json") ? await response.json() : await response.text()) as T;
}

export const api = {
  get: <T>(path: string, query?: RequestOptions["query"], signal?: AbortSignal) => coreRequest<T>(path, { query, signal }),
  post: <T>(path: string, body?: unknown) => coreRequest<T>(path, { method: "POST", body: body ?? {} }),
  put: <T>(path: string, body?: unknown) => coreRequest<T>(path, { method: "PUT", body: body ?? {} }),
  patch: <T>(path: string, body?: unknown) => coreRequest<T>(path, { method: "PATCH", body: body ?? {} }),
  del: <T>(path: string, body?: unknown) => coreRequest<T>(path, { method: "DELETE", body }),
};
