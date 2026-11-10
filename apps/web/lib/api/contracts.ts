/**
 * Typed contracts for the MediaGuard core API.
 * Section A = implemented in apps/core today (verified against mediaguard/api/*.py).
 * Section B = feature modules (instances, library, wanted, DV, AI, …), verified against the
  *            running core on 2026-11-02 (GET /api/v1/openapi.json). Fields the UI does not
  *            need are typed loosely; missing routes render an explicit "niet beschikbaar" state.
 */

// ===================================================================== A. implemented
export type Role = "admin" | "operator" | "viewer";

export interface Me {
  username: string;
  role: Role;
  csrf_token: string | null;
  locale: string;
  /** Per-user UI preferences stored server-side (PUT /auth/me/preferences). */
  preferences?: { theme?: "dark" | "light" | "system" };
  /** Tweestapsverificatie (rapport 5, M12). */
  twofa_enabled?: boolean;
  /** Beheerder moet eerst 2FA instellen: de API weigert dan alles behalve Beveiliging. */
  twofa_required?: boolean;
  /** Aansporing (niet verplicht): beheerder zonder 2FA, herinnering staat aan. */
  twofa_nudge?: boolean;
}
export interface SetupStatus { needs_setup: boolean }
export interface LoginResult { csrf_token: string; role: Role; username: string; needs_2fa?: false; twofa_nudge?: boolean }
export interface LoginChallenge { needs_2fa: true; challenge: string; username: string }

export interface UserRow {
  id: number;
  username: string;
  role: Role;
  locale: string;
  disabled: number | boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface EmergencyStop { active: boolean; reason?: string; by?: string; since?: string }
export interface SystemStatus {
  version: string;
  role: string;
  emergency_stop: EmergencyStop;
  jobs: Record<string, number>;
  unread_notifications: number;
  time: string;
}

export type CoreJobStatus = "queued" | "running" | "retry_scheduled" | "failed" | "succeeded" | "cancelled" | string;
export interface CoreJob {
  id: number;
  job_type: string;
  status: CoreJobStatus;
  instance_id: string | null;
  payload: Record<string, unknown>;
  result: unknown;
  error: string | null;
  attempts: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  progress?: number | null;
  [key: string]: unknown;
}
export interface CoreJobDetail extends CoreJob {
  logs: Array<{ ts: string; level: string; message: string }>;
  /** core geeft hooguit de laatste 2000 regels (rapport 31) */
  logs_truncated?: boolean;
}

export interface AuditEvent {
  id: number;
  ts: string;
  actor: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  instance_id: string | null;
  reason: string | null;
  old_value: unknown;
  new_value: unknown;
  undo_hint: string | null;
  correlation_id?: string | null;
}

export interface NotificationRow {
  id: number;
  ts: string;
  severity: "info" | "warning" | "error" | "success" | string;
  title: string;
  body: string;
  link: string | null;
  instance_id: string | null;
  read: number;
}

export type SettingType = "bool" | "int" | "float" | "string" | "enum" | "list" | "secret" | "duration" | "time_window" | "json";
export interface SettingDef {
  key: string;
  type: SettingType;
  default: unknown;
  category: string;
  label_nl: string;
  label_en: string;
  help_nl: string;
  help_en: string;
  choices: string[];
  choice_labels_nl: Record<string, string>;
  minimum: number | null;
  maximum: number | null;
  unit: string | null;
  scopes: string[];
  advanced: boolean;
  integrity_locked: boolean;
  restart_required: boolean;
  effect_nl: string;
  /** curatie (core settings_curation): sectie, eenvoudige weergave, vervallen */
  section?: string;
  simple?: boolean;
  hidden?: boolean;
  hidden_reason?: string;
  order?: number;
  /** alleen een beheerder mag dit wijzigen (core settings_guard) */
  admin_only?: boolean;
  /** niet via de gewone instellingen; uitleg waar het wel kan (bv. 'Bibliotheek vrijgeven') */
  managed_nl?: string;
}
export interface SettingsSection { id: string; title_nl: string; title_en: string; intro_nl: string; categories: string[] }
export interface SettingsSchema { scopes: string[]; settings: SettingDef[]; sections?: SettingsSection[] }
export interface EffectiveSetting {
  key: string;
  value: unknown;
  source: string;
  default: unknown;
  trace: Array<{ scope: string; scope_id: string; value: unknown; set: boolean }>;
}
export interface SettingOverride {
  scope_type: string;
  scope_id: string;
  key: string;
  value: unknown;
  updated_at: string;
  updated_by: string;
}
export interface ScopedChange { scope_type: string; scope_id: string; key: string; value?: unknown; reset?: boolean }
export interface SecretRow { name: string; updated_at: string; updated_by: string; is_set: number | boolean; readable?: boolean }

export interface CoreEvent {
  id?: number;
  topic: string;
  instance_id?: string | null;
  payload: unknown;
  ts?: string;
}

// ===================================================================== B. feature modules
export type InstanceStatus = "healthy" | "degraded" | "offline" | "unknown";
export type MediaKind = "tv" | "anime" | "movie" | "anime_movie";

export interface Instance {
  id: string;
  app: "sonarr" | "radarr";
  media_type: MediaKind;
  name: string;
  url: string;
  public_url: string | null;
  status: InstanceStatus;
  suspended: boolean;
  shadow_mode: boolean;
  archived: boolean;
  hook_fail_policy?: string;
  version: string | null;
  api_key_valid: boolean | null;
  last_check_at: string | null;
  last_error: string | null;
  default_root_folder: string | null;
  download_category?: string | null;
  [key: string]: unknown;
}

export interface LibraryStats { total: number; available: number; missing: number; upcoming: number; unmonitored: number; cutoff_unmet: number }
export interface LibraryItem {
  id: number;
  instance_id: string;
  instance_name: string;
  kind: "series" | "movie";
  provider_id: number;
  title: string;
  year: number | null;
  tvdb_id: number | null;
  tmdb_id: number | null;
  imdb_id: string | null;
  network: string | null;
  status: string | null;
  monitored: number | boolean;
  monitor_new_items: string | null;
  root_folder_path: string | null;
  path: string | null;
  size_on_disk: number;
  episode_count: number | null;
  episode_file_count: number | null;
  poster_url: string | null;
  media_type: MediaKind;
  app: string;
  state?: string;
  stats: LibraryStats;
  synced_at?: string;
}
export interface LibraryPage { page: number; page_size: number; total: number; items: LibraryItem[] }

export type EpisodeState = "available" | "missing" | "upcoming" | "unmonitored" | "cutoff_unmet" | "downloading" | "ignored" | "unavailable";
export interface SeasonEpisode {
  episode_id: number;
  episode_number: number;
  absolute_episode_number: number | null;
  title: string | null;
  air_date_utc: string | null;
  monitored: boolean;
  has_file: boolean;
  quality: string | null;
  state: EpisodeState;
  wanted_status: string | null;
  reason_code: string | null;
}
export interface Season {
  season_number: number;
  monitored: boolean;
  counts: Partial<Record<EpisodeState | "total", number>>;
  episodes: SeasonEpisode[];
  expected_aired?: number;
  completeness?: number | null;
}
export interface LibraryDetail extends LibraryItem {
  seasons?: Season[];
  file?: { quality?: string | null; languages?: string[]; media_info?: Record<string, unknown>; size?: number; path?: string } | null;
  wanted?: Array<{ id: number; reason: string; status: string; attempts: number; last_searched_at: string | null; next_search_at: string | null; last_reason_code: string | null }>;
}

export interface LibraryKpis {
  generated_at: string;
  totals: LibraryStats & { upcoming_7d: number; series: number; movies: number; missing_aired_7d: number; missing_aired_30d: number; missing_aired_90d: number };
  instances: Array<{ id: string; name: string; app: string; media_type: MediaKind; status: InstanceStatus; suspended: boolean; kpis: LibraryStats & { upcoming_7d: number; future_season_coverage: number | null } }>;
}

export interface CalendarItem {
  type: "episode" | "movie";
  instance_id: string;
  instance_name: string;
  media_type: MediaKind;
  lib_item_id: number;
  title: string;
  episode_title?: string | null;
  season_number?: number;
  episode_number?: number;
  release_label_nl?: string;
  date: string;
  has_file: boolean;
  monitored: boolean;
  state: string;
}
export interface CalendarResponse { start: string; end: string; total: number; items: CalendarItem[] }

export interface WantedKpiBlock {
  missing: number; cutoff: number; unavailable: number; ignored: number;
  missing_aired_7d: number; resolved_7d: number; missing_aired_30d: number; resolved_30d: number;
  /** alleen ontbrekend; onder kwaliteit staat in reason_distribution_cutoff */
  reason_distribution: Record<string, number>;
  reason_distribution_cutoff?: Record<string, number>;
  reason_distribution_detail?: Record<"missing" | "cutoff", Record<string, { total: number; episodes: number; movies: number }>>;
  undiagnosed?: { missing: number; cutoff: number; automatic?: boolean };
  /** gevolgd maar nog niet verschenen: telt niet als ontbrekend */
  upcoming?: { total: number; movies: number; episodes: number };
  /** oorzaken die echt iets van jou vragen, met de plek waar je het oplost */
  needs_user?: { total: number; reasons: Array<{ code: string; reason: "missing" | "cutoff"; label_nl: string; fix_nl: string; href: string; total: number; episodes: number; movies: number }> };
  automatic_total?: number;
  backfill_30d: { searches: number; completed: number; grabbed: number; errors: number; success_rate: number | null };
  import_failed: number;
}
export interface WantedKpis extends WantedKpiBlock { per_instance: Record<string, WantedKpiBlock> }
export type WantedReasons = Record<string, { code?: string; label_nl: string; fix_nl: string; needs_user?: boolean; automatic?: boolean; href?: string }>;
export interface WantedItem {
  id: number;
  instance_id: string;
  kind: "episode" | "movie";
  provider_id: number;
  item_provider_id: number;
  reason: "missing" | "cutoff";
  title: string;
  item_title: string | null;
  season_number: number | null;
  episode_number: number | null;
  absolute_episode_number: number | null;
  air_date_utc: string | null;
  status: string;
  priority: number;
  attempts: number;
  last_searched_at: string | null;
  next_search_at: string | null;
  last_reason_code: string | null;
  [key: string]: unknown;
}
export interface WantedPage { page: number; page_size: number; total: number; items: WantedItem[] }
export type BackfillQuota = Record<string, { hour_used: number; hour_max: number; day_used: number; day_max: number; batch_size: number; remaining: number }>;

export interface OptimizationPlan {
  id: number;
  status: string;
  item_count: number;
  created_at: string;
  summary: Record<string, number>;
  filters: Record<string, unknown>;
  forecast: {
    items: number; buckets: Record<string, { count: number; bytes: number }>; bytes_total: number; av1_bytes: number;
    estimated_savings_bytes: number; staging_needed_bytes: number; staging_total_needed_bytes: number;
    download_bytes_estimate: number; download_days_estimate: number | null; note_nl?: string;
  };
  progress: Record<string, number>;
}
export interface OptimizationDashboard {
  latest_plan: OptimizationPlan | null;
  limits: { searches_last_hour: number; bytes_today: number; active_encodes: number; active_downloads: number; staging_free_bytes: number };
  failed: unknown[];
  in_progress: Record<string, unknown>;
}

export interface QuarantineItem {
  id: number;
  transaction_id: number;
  instance_id: string | null;
  media_kind: string | null;
  provider_id: string | null;
  original_path: string;
  quarantine_path: string;
  size: number | null;
  method: string;
  status: "pending" | "held" | "restorable" | "restored" | "purged" | "discarded";
  reason: string | null;
  created_at: string;
  pinned: number;
  release_check?: ReleaseCheck | null;
}
export interface ReleaseCheck { status: "passed" | "failed" | "pending" | string; checks: Array<{ name: string; passed: boolean | null; detail?: string | null }>; checked_at: string | null }
export interface QuarantineUsage {
  used_bytes: number; quota_bytes: number; percent: number; quota_exceeded: boolean; encode_queue_paused: boolean;
  release_checks: Record<string, number>; retention_days: number; auto_purge: boolean;
  next_purge: { count: number; bytes: number; at?: string | null; at_nl?: string | null };
  mode?: string; mode_nl?: string; restorable?: boolean;
  /** vangnet bij 'Direct verwijderen' (rapport 6, N8) */
  safety_net?: { hours: number; bytes?: number; max_bytes?: number; items?: number; used_bytes?: number; first_expiry_at?: string | null } | null;
  /** rapport 7, A1: één getal incl. 'nog te controleren', opruimdoel (hysterese) en gereserveerde ruimte */
  total_bytes?: number; pending_bytes?: number; total_percent?: number;
  purge_target_bytes?: number | null; purge_target_percent?: number | null; reserved_bytes?: number | null;
  reserve_bytes?: number | null; unchecked_bytes?: number | null; cap_bytes?: number | null;
  queue_pause_reason_nl?: string | null;
  orphans?: { checked_at?: string | null; removed?: number; left?: number; left_bytes?: number; freed_bytes?: number } | null;
}
export interface BulkDeletePreview { dry_run?: boolean; count: number; bytes: number; blocked: number[]; unverified: number[]; job_ids?: number[] }

export interface DvTraceEntry { value: unknown; source: string; source_label_nl?: string; levels: Array<{ scope: string; scope_id: string; value: unknown; set: boolean; label_nl?: string }> }
export interface DvPolicyResponse {
  chain: Record<string, string>;
  effective: { allow: boolean; mode: string; allowed_profiles: string[]; hdr10plus_mode: string; trace: Record<string, DvTraceEntry> };
  [key: string]: unknown;
}
export interface DvCapabilities { available: boolean; error?: string | null; routes: Record<string, unknown>; statuses: string[]; [key: string]: unknown }
export interface PlaybackConfirmation { id: number; client: string; server: string; container: string; codec: string; dv_profile: string; note: string; ts?: string; actor?: string }

export interface AiProviderInfo {
  id: "anthropic" | "openai";
  label_nl: string;
  selected: boolean;
  api_key_set: boolean;
  api_key_secret_name: string;
  model: string | null;
  suggested_models: string[];
  models: string[];
  models_error: string | null;
  consent: { provider: string; granted: boolean; ts: string | null; actor: string | null; version: string | null; current_version: string };
  last_test: { ok: boolean; at: string | null; model: string | null; detail: string | null; latency_ms: number | null };
  blockers: string[];
}
export interface AiProviders { options: Array<{ id: string; label_nl: string }>; providers: AiProviderInfo[] }
export interface AiUsage {
  currency: string; timezone: string; month: string; monthly_limit: number; spent: number; reserved: number; remaining: number;
  calls_today: number; max_calls_per_day: number; per_analysis_limit: number; cache_hits_month: number; price_note_nl?: string;
}
export interface AiCase {
  id?: number;
  case_id: string;
  task_type: string;
  status?: string;
  title?: string | null;
  trigger_reason?: string | null;
  proposal?: { suggested_action?: string; reasoning_summary?: string; uncertainty?: string; reason_codes?: string[]; evidence_ids?: string[] } | null;
  policy_result?: { outcome?: string; detail?: string } | null;
  cost?: number | null;
  [key: string]: unknown;
}
export interface AiCases { items: AiCase[]; status: { mode: string; provider: string; model: string; active: boolean; blockers: string[]; message_nl: string; bounded_auto: { unlocked: boolean; reasons: string[] } } }
export interface AiConsent { text_nl: string; providers: AiProviderInfo["consent"][]; history: Array<{ id: number; ts: string; provider: string; granted: number; consent_version: string; actor: string }> }

export interface AdminHealth {
  components: Record<string, { ok: boolean; version?: string; status?: string; detail?: string | null; suspended?: boolean; status_code?: number | null }>;
  storage?: Array<{ path: string; ok: boolean; total?: number; free?: number; used?: number; error?: string }>;
}
export interface AdminCoverage { total: number; settings: Array<{ key: string; category: string; type: string; scopes: string[]; label_nl: string; ui_control: string; integrity_locked: boolean }> }
export interface AdminVersion { version: string; schema_versions: string[]; manifest: Record<string, unknown> }

export interface PolicyProfile { id: string; name_nl: string; description_nl: string; builtin: boolean; version: number; updated_at: string; updated_by: string; config: Record<string, unknown> }
export interface SeerrSettings { url: string; api_key_set: boolean; request_sync_interval_minutes: number }
export interface SeerrRouting { instance_id: string; routing: { seerr_kind: string; seerr_server_id: number | null; role: string; expected_is_default: boolean; expected_is_4k: boolean; source: string; expected_root_folder: string | null } }
export interface OwnershipConflicts { total: number; conflicts: Array<Record<string, unknown>> }
export interface EncodingJob { id: number; instance_id: string | null; media_kind: string | null; source_path: string; status: string; progress: number; error: string | null; trial_only: number; created_at?: string; [key: string]: unknown }
/** Arr passthrough resources (indexers, download clients): upstream Sonarr/Radarr v3 shapes. */
export interface ArrResource { id: number; name: string; enable?: boolean; implementation?: string; implementationName?: string; protocol?: string; priority?: number; [key: string]: unknown }
export interface SeerrCheck { check: string; ok: boolean | null; expected: unknown; actual: unknown; message_nl: string }
export interface SeerrRouteReport { ok?: boolean; checked_at?: string; unmapped_servers?: unknown[]; instances?: Array<{ instance_id: string; ok: boolean | null; checks: SeerrCheck[]; message_nl?: string; server?: { name?: string; id?: number } | null }>; [key: string]: unknown }
