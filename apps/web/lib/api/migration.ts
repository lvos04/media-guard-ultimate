/**
 * Contracts for `/api/v1/migration/*` (apps/core/mediaguard/migration/api.py — the real routes).
 * Field names mirror the core responses 1:1; nothing here is invented.
 */
import { CORE_BASE, ApiError, csrfTokenForMutation } from "./client";

export type MigApp = "sonarr" | "radarr";
export type MigStepId =
  | "preflight" | "backup" | "mapping" | "shadow_import" | "verify" | "scan" | "optimization" | "readiness" | "cutover" | "post_cutover";
export const MIG_STEPS: MigStepId[] = ["preflight", "backup", "mapping", "shadow_import", "verify", "scan", "optimization", "readiness", "cutover", "post_cutover"];
export type MigStepStatus = "pending" | "queued" | "running" | "succeeded" | "warning" | "skipped" | "failed" | "blocked";
export type MigVerdict = "go" | "warn" | "no_go" | "info";

export interface MigBackup { id: number; origin: string; backup_name: string; size: number; sha256: string; verified: number | boolean; app_version: string | null; created_at: string }
export interface MigSource {
  id: string;
  kind: "live" | "backup";
  app: MigApp;
  name: string;
  url: string | null;
  version: string | null;
  app_name: string | null;
  instance_name: string | null;
  database_type: string | null;
  status: string;
  last_error: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
  detected: {
    counts?: Record<string, number>;
    anime?: { score: number; reasons: string[] };
    schema_version?: number | null;
    filename?: string;
    version?: string | null;
    [key: string]: unknown;
  };
  api_key_set: boolean | null;
  latest_backup: MigBackup | null;
  /** Only on the upload response. */
  suggested_target?: string | null;
}

export interface MigStep {
  run_id: number;
  step: MigStepId;
  scope: string;
  status: MigStepStatus;
  job_id: number | null;
  attempts: number;
  result: Record<string, unknown>;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
  label_nl: string;
}

export interface PathRule { from: string; to: string }
export interface MigMapping {
  run_id: number;
  source_id: string;
  target_instance_id: string | null;
  status: "proposed" | "confirmed" | "excluded";
  mode: "auto" | "native" | "api";
  confidence: number | null;
  anime_score: number | null;
  reasons: string[];
  warnings: string[];
  path_map: PathRule[];
  confirmed_by: string | null;
  confirmed_at: string | null;
  updated_at: string;
}
export interface MigMappingItem { run_id: number; source_id: string; kind: string; external_id: string; title: string; target_instance_id: string | null; flag: string; message_nl: string }
export interface MigMatrixRow {
  source_id: string; source_name: string | null; source_app: MigApp | null; source_kind: "live" | "backup" | null;
  target_instance_id: string | null; status: MigMapping["status"]; confidence: number | null; duplicate_target_with: string[]; warnings: string[];
}

export interface MigMismatch { id: string; category: string; key: string; field: string; source: unknown; target: unknown; severity: "no_go" | "warning" | "info" | string; acceptable: boolean; message_nl: string }
export interface MigReport {
  id: number;
  run_id: number;
  kind: "preflight" | "path_preview" | "import_preview" | "verify" | "scan" | "readiness" | "post_cutover" | string;
  source_id: string | null;
  target_instance_id: string | null;
  verdict: MigVerdict | string;
  summary: Record<string, unknown>;
  mismatches: MigMismatch[];
  mismatch_count?: number;
  created_at: string;
  created_by: string | null;
}
export interface MigCutover { id: number; source_id: string; target_instance_id: string; kind: "cutover" | "activate_search"; phase: string; status: "running" | "completed" | "failed" | "rolling_back" | "rolled_back"; confirmed_by: string; error: string | null; started_at: string | null; finished_at: string | null; rolled_back_at: string | null }
export interface MigAccepted { by: string; at: string; reason: string; message_nl: string }
export interface MigRun {
  id: number;
  name: string;
  status: "active" | "completed" | "cancelled";
  options: { follow_new_seasons?: boolean | null; accept_unresolved_paths?: boolean };
  accepted_mismatches: Record<string, Record<string, MigAccepted>>;
  created_at: string;
  created_by: string | null;
  updated_at: string;
  current_step: MigStepId | null;
  steps: MigStep[];
  mappings: MigMapping[];
  mapping_items: MigMappingItem[];
  reports: MigReport[];
  cutovers: MigCutover[];
  /** Automatische doorloop (core ≥ 0.2.9); ontbreekt bij oudere versies. */
  autopilot?: MigAutopilot;
}
export type MigAutopilotState = "idle" | "queued" | "running" | "waiting" | "needs_input" | "ready_for_cutover" | "stopped";
export interface MigAutopilotNeed { kind: string; message_nl: string; target?: string | null; action: string; detail?: string | null; targets?: string[] }
export interface MigAutopilot {
  state: MigAutopilotState;
  current?: { step: string; scope: string; label: string } | null;
  needs: MigAutopilotNeed[];
  allow_backup_create?: boolean;
  updated_at?: string | null;
  steps: { step: string; scope: string; label: string; status: MigStepStatus; error?: string | null }[];
  done: number;
  total: number;
}
export interface MigRunRow { id: number; name: string; status: MigRun["status"]; created_at: string; created_by: string | null; updated_at: string }

/** Response of a step launch: either queued as a job, or executed synchronously (wait=true). */
export interface MigLaunch { queued?: boolean; job_id?: number; already_running?: boolean; status?: string; error?: string; result?: unknown; log?: string[]; message_nl?: string }

export interface MigCheck { check: string; status: "ok" | "warn" | "fail" | "skipped"; message_nl: string; [key: string]: unknown }
export interface PreflightSummary { sources: Record<string, MigCheck[]>; targets: Record<string, MigCheck[]>; seerr: MigCheck | null; local: MigCheck | null }
export interface PathPreview {
  verdict: MigVerdict;
  source_id: string;
  rules: PathRule[];
  per_prefix: Record<string, { count: number; samples: Array<{ from: string; to: string; exists: boolean | null }>; missing: number }>;
  root_folders: Array<{ from: string; to: string; exists: boolean | null }>;
  sampled: number;
  unresolved: Array<{ kind: string; from: string; to: string }>;
  unresolved_count: number;
  blocking: boolean;
  message_nl: string;
}
export interface ImportPreviewTarget {
  target_instance_id: string; source_id: string; mode: "native" | "api"; mode_reason: string; writes_nl: string; source_writes: string;
  items: number; indexers: number; download_clients: number; quality_profiles: number; custom_formats: number; tags: number;
  root_folders: string[]; credentials_needed: Array<{ kind: string; name: string }>; target_existing_items: number | null;
  path_preview: Omit<PathPreview, "verdict"> | null;
}
export interface Av1Import { id: number; source_instance: string; file_path: string; sha256: string; counts: Record<string, number>; path_map: PathRule[]; created_at: string; created_by: string | null }

export const TARGETS = ["sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms"] as const;

/** Step status → stepper state. */
export function stepTone(status: MigStepStatus | undefined): "done" | "current" | "warning" | "blocked" | "todo" {
  switch (status) {
    case "succeeded": case "skipped": return "done";
    case "warning": return "warning";
    case "failed": case "blocked": return "blocked";
    case "running": case "queued": return "current";
    default: return "todo";
  }
}

export function verdictTone(v: string | null | undefined): "success" | "warning" | "danger" | "info" | "neutral" {
  return v === "go" ? "success" : v === "warn" ? "warning" : v === "no_go" ? "danger" : v === "info" ? "info" : "neutral";
}

export function runStep(run: MigRun, step: MigStepId, scope = ""): MigStep | undefined {
  return run.steps.find((s) => s.step === step && s.scope === scope);
}
export function latestReport(run: MigRun, kind: string, target?: string | null, source?: string | null): MigReport | undefined {
  return run.reports
    .filter((r) => r.kind === kind && (target === undefined || r.target_instance_id === target) && (source === undefined || r.source_id === source))
    .sort((a, b) => b.id - a.id)[0];
}

export interface UploadProgress { loaded: number; total: number }

/**
 * multipart upload with progress (fetch has no upload progress). Same-origin; CSRF header like
 * coreRequest; errors become ApiError with the core's Dutch `detail`.
 */
export async function uploadFile<T>(path: string, fields: Record<string, string | Blob | null | undefined>, onProgress?: (p: UploadProgress) => void, signal?: AbortSignal): Promise<T> {
  // direct na het openen van de pagina kan /auth/me nog lopen: eerst het CSRF-token
  const csrf = await csrfTokenForMutation();
  if (signal?.aborted) throw new DOMException("aborted", "AbortError");
  return new Promise<T>((resolve, reject) => {
    const form = new FormData();
    for (const [k, v] of Object.entries(fields)) if (v !== null && v !== undefined && v !== "") form.append(k, v);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${CORE_BASE}${path}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.onprogress = (e) => onProgress?.({ loaded: e.loaded, total: e.lengthComputable ? e.total : 0 });
    xhr.onerror = () => reject(new ApiError("offline", 0, "network", path));
    xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));
    xhr.onload = () => {
      let body: unknown = null;
      try { body = JSON.parse(xhr.responseText); } catch { body = null; }
      if (xhr.status >= 200 && xhr.status < 300) { resolve(body as T); return; }
      const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : null;
      const message = typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail ? String((detail as { message: unknown }).message) : xhr.statusText || String(xhr.status);
      const kind = xhr.status === 401 ? "unauthorized" : xhr.status === 403 ? "forbidden" : xhr.status === 413 || xhr.status === 422 || xhr.status === 409 || xhr.status === 400 ? "validation" : xhr.status >= 502 && xhr.status <= 504 ? "offline" : xhr.status === 404 ? "not_found" : "server";
      reject(new ApiError(kind, xhr.status, message, path, xhr.getResponseHeader("x-correlation-id"), detail));
    };
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(form);
  });
}
