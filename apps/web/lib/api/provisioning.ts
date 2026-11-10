/** Contracts for `/api/v1/provisioning/*` (apps/core/mediaguard/provisioning/api.py). Secrets are write-only. */

export type ProvStatus = "ok" | "drift" | "missing" | "error" | "needs_input" | "skipped" | "unknown";
export const SECTION_IDS = ["tags", "rootfolders", "mediamanagement", "naming", "qualitydefinitions", "customformats", "qualityprofiles", "delayprofile", "releaseprofiles", "downloadclients", "indexers", "webhook"] as const;
export type SectionId = (typeof SECTION_IDS)[number] | "prowlarr" | "seerr";

export interface ProvSectionState { status: ProvStatus; message_nl: string; changes: number; checked_at: string | null }
export interface ProvNeed { key: string; message_nl: string; section: string; severity: "required" | "confirm" | "optional"; instance_id: string | null }
export interface ProvInstance {
  instance_id: string; name: string; app: "sonarr" | "radarr"; media_type: string;
  mode: "manage" | "hold" | "optimized"; mode_nl: string; shadow_mode: boolean; suspended: boolean;
  initialized: boolean; shadow_import_done: boolean;
  optimize: { phase: string; snapshot_id: number | null; optimized_at: string | null; cleaned_at: string | null; rolled_back_at: string | null; error: string | null } | null;
  root_folder: string; category: string; sections: Record<string, ProvSectionState>;
}
export interface ProvStatusView {
  instances: ProvInstance[]; global: Record<string, ProvSectionState>; needs: ProvNeed[]; ready: boolean;
  indexer_mode: "none" | "prowlarr" | "direct"; active_shadow_phase: string[];
  settings: Record<string, unknown>; last_runs: Record<string, { id: number; status: string; finished_at?: string | null; started_at?: string } | null>;
}
export interface DiffLine { field: string; label_nl: string; old: unknown; new: unknown }
export interface ProvChange { section: string; section_nl: string | null; action: string; key: string; title_nl: string; instance_id: string | null; diff: DiffLine[]; owned: boolean; auto: boolean; message_nl: string }
export interface ProvPlan {
  run_id?: number; dry_run?: boolean;
  instances: Record<string, { instance_id?: string; name?: string; error?: string; sections: Record<string, { status: ProvStatus; message_nl: string; changes: ProvChange[] }> }>;
  global: Record<string, { status: ProvStatus; message_nl: string; changes: ProvChange[] }>;
  summary: { changes: number; create: number; update: number; delete: number; blocked: number; needs_input: number };
  text_nl: string[];
}
export interface ProvApplyResult { run_id: number; status: string; summary: Record<string, number>; applied: unknown[]; failed: unknown[]; remaining: ProvChange[]; blocked: ProvChange[] }

export interface DownloadClientCfg {
  name: string; implementation: "Sabnzbd" | "Nzbget" | "QBittorrent" | "Transmission" | "Deluge"; enabled: boolean;
  host: string; port: number; use_ssl: boolean; url_base: string; username: string; priority: number;
  instances: string[] | null; categories: Record<string, string>; extra_fields?: Record<string, unknown>;
  api_key_set?: boolean; password_set?: boolean; protocol?: string;
  /** write-only: undefined/null = unchanged, "" = clear */
  api_key?: string | null; password?: string | null;
}
export interface IndexerCfg {
  name: string; implementation: "Newznab" | "Torznab"; base_url: string; api_path: string; scope: "normal" | "anime" | "both" | null;
  instances: string[] | null; enabled: boolean; priority: number; minimum_seeders?: number;
  categories?: number[] | null; anime_categories?: number[] | null; movie_categories?: number[] | null; extra_fields?: Record<string, unknown>;
  api_key_set?: boolean; effective_scope?: string; api_key?: string | null;
}
export interface ProwlarrCfg { url: string; url_from_arr: string; instance_urls: Record<string, string>; manage_indexer_tags: boolean; indexer_scopes: Record<string, "normal" | "anime" | "both">; sync_level: "fullSync" | "addOnly"; api_key_set?: boolean; api_key?: string | null }
export interface InstanceCfg { root_folder: string | null; category: string | null; mode: "auto" | "manage" | "hold"; full_manage: string[]; disabled_sections: string[] }
export interface MediaCfg { hardlinks: boolean; rename: boolean; propers: "preferAndUpgrade" | "doNotUpgrade" | "doNotPrefer"; recycle_bin: string; unmonitor_deleted: boolean; import_extra_files: boolean; extra_file_extensions: string; naming_overrides: Record<string, string> }
export interface QualityCfg { enabled: boolean; language_films: "original" | "any"; score_overrides: Record<string, Record<string, number>>; extra_trash_formats: Record<string, string[]>; quality_definitions: boolean }
export interface DelayCfg { preferred_protocol: "auto" | "usenet" | "torrent"; usenet_delay: number; torrent_delay: number; bypass_if_highest_quality: boolean }
export interface ProvTemplate { name: string; label_nl: string; allowed: string[]; cutoff: string; scores: Record<string, number> }
export interface ProvConfig {
  indexer_mode: "none" | "prowlarr" | "direct"; download_clients: DownloadClientCfg[]; indexers: IndexerCfg[]; prowlarr: ProwlarrCfg;
  instances: Record<string, InstanceCfg>; media: MediaCfg; quality: QualityCfg; delay: DelayCfg; seerr: { enabled: boolean; scan: boolean };
  seerr_connection: { url: string; api_key_set: boolean };
  defaults: Record<string, { root_folder: string; category: string; profiles: string[]; registry_root: string | null }>;
  templates: Record<string, ProvTemplate>;
  sections: Array<{ id: SectionId; label_nl: string; full_manage_possible: boolean }>;
}
export interface TestResult { ok: boolean; instance_id?: string; message_nl: string; details?: unknown }
export interface ProwlarrTest extends TestResult { version?: string; indexers?: Array<{ id: number; name: string; protocol: string; tags: number[]; suggested_scope: "anime" | "both" }>; applications?: Array<{ id: number; name: string; implementation: string }> }
export interface PathCheck { instance_id: string; path: string; exists: boolean | null; is_root_folder: boolean; accessible: boolean | null; free_space: number | null; message_nl: string }

export interface OptimizeStatusRow { instance_id: string; name: string; shadow_import_done: boolean; phase: string | null; snapshot_id: number | null; result: Record<string, unknown> | null; error: string | null }
export interface OptimizeImpact { items: number; items_with_files: number; files: number; upgrade_candidates: number; upgrade_items: number; cutoff_unmet_before: number; by_profile: Record<string, number>; estimated_searches: number; searches_per_day: number; estimated_days: number; include_cutoff_unmet: boolean; message_nl: string }
export interface OptimizePreviewInstance {
  instance_id: string; name: string; already?: boolean; error?: string; message_nl?: string;
  sections?: Record<string, { status: ProvStatus; message_nl: string; changes: ProvChange[] }>;
  extra_changes?: Array<{ section: string; action: string; key: string; title_nl: string; diff: DiffLine[] }>;
  reassign?: { items: number; by_profile: Record<string, number>; message_nl: string } | null;
  impact?: OptimizeImpact | null; changes?: number; text_nl?: string[]; keeps_nl?: string[]; no_renames_nl?: string;
}
export interface OptimizePreview { requires_confirmation: boolean; sections: string[]; instances: OptimizePreviewInstance[]; message_nl: string }
export interface RenameFile { owner_id: number; file_id: number; existing_path: string | null; new_path: string | null; locked: boolean }
export interface RenamePreview { instance_id: string; files: RenameFile[]; count: number; truncated: boolean; message_nl: string }

export const NEED_SECTION_TAB: Record<string, string> = {
  instances: "/instellingen/instanties", downloadclients: "downloads", indexers: "indexers", seerr: "seerr", rootfolders: "paden",
};
