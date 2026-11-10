"use client";

import { ArrowRight, Gauge, History, Sparkles } from "lucide-react";

import { Card, StatusChip } from "@/components/mg/primitives";
import { ErrorState, StateView } from "@/components/mg/states";
import { ApiError } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";

/** GET /library/{item_id}/files — kwaliteit per bestand (docs/APP_API.md §2). */
export interface FileQuality {
  file_id: number;
  episode_id?: number | null;
  season_number?: number | null;
  episode_number?: number | null;
  label?: string | null;
  size?: number | null;
  quality?: { name?: string | null; resolution?: number | null; source?: string | null } | null;
  media?: {
    video_codec?: string | null; dynamic_range?: string | null; hdr?: boolean; dolby_vision?: boolean;
    audio_languages?: string[]; subtitle_languages?: string[];
  } | null;
  custom_formats?: Array<{ name: string; score: number }>;
  score?: number | null;
  cutoff_met?: boolean;
  quality_cutoff_met?: boolean;
  score_cutoff_met?: boolean;
  av1_by_mediaguard?: boolean;
  summary_nl?: string | null;
}
export interface FilesResponse {
  item_id: number;
  kind: "series" | "movie";
  profile?: { name?: string | null; cutoff?: string | null; cutoff_score?: number | null } | null;
  files: FileQuality[];
}
interface Upgrade {
  at: string;
  label?: string | null;
  old?: { quality?: string | null; score?: number | null; av1_by_mediaguard?: boolean } | null;
  new?: { quality?: string | null; score?: number | null; release_group?: string | null; indexer?: string | null } | null;
  cf_added?: string[];
  cf_removed?: string[];
  source?: string;
  source_nl?: string | null;
  reason_nl: string;
}

/** Oudere core zonder dit endpoint: rustige uitleg in plaats van een foutmelding. */
const unsupported = (e: Error | null | undefined) => e instanceof ApiError && (e.status === 404 || e.status === 405 || e.kind === "unavailable");

const langs = (l?: string[]) => (l?.length ? l.map((x) => x.toUpperCase()).join(" · ") : "—");

function Goal({ f, cutoff, cutoffScore }: { f: FileQuality; cutoff?: string | null; cutoffScore?: number | null }) {
  const { t, num } = useI18n();
  if (f.cutoff_met) return <StatusChip tone="success">{t("lib.goalMet")}</StatusChip>;
  if (f.quality_cutoff_met === false && cutoff) return <StatusChip tone="info">{t("lib.goalMissingQuality", { cutoff })}</StatusChip>;
  if (cutoffScore != null) return <StatusChip tone="info">{t("lib.goalMissingScore", { score: num(f.score ?? 0), cutoff: num(cutoffScore) })}</StatusChip>;
  return null;
}

function FileFacts({ f, data }: { f: FileQuality; data: FilesResponse }) {
  const { t, num, bytes } = useI18n();
  const picture = [f.media?.video_codec, f.quality?.resolution ? `${f.quality.resolution}p` : null, f.media?.dolby_vision ? "Dolby Vision" : f.media?.dynamic_range].filter(Boolean).join(" · ");
  return (
    <dl className="kv">
      <dt>{t("lib.qualityName")}</dt>
      <dd>{f.quality?.name ?? "—"}{f.size ? <span className="t-3"> · {bytes(f.size)}</span> : null}{f.av1_by_mediaguard ? <> <span className="tag tag--accent">{t("lib.av1Badge")}</span></> : null}</dd>
      <dt>{t("lib.qualityPicture")}</dt><dd>{picture || "—"}</dd>
      <dt>{t("lib.qualityAudio")}</dt><dd>{langs(f.media?.audio_languages)}</dd>
      <dt>{t("lib.qualitySubs")}</dt><dd>{langs(f.media?.subtitle_languages)}</dd>
      <dt>{t("lib.qualityFormats")}</dt>
      <dd>{f.custom_formats?.length ? <span className="row wrap" style={{ gap: 4 }}>{f.custom_formats.map((cf) => <span key={cf.name} className="tag">{cf.name} <span className="t-3">{cf.score >= 0 ? "+" : ""}{num(cf.score)}</span></span>)}</span> : "—"}</dd>
      <dt>{t("lib.qualityScore")}</dt>
      <dd>{data.profile?.cutoff_score != null ? t("lib.qualityScoreOf", { score: num(f.score ?? 0), cutoff: num(data.profile.cutoff_score) }) : num(f.score ?? 0)}</dd>
      <dt>{t("lib.qualityGoal")}</dt><dd><Goal f={f} cutoff={data.profile?.cutoff} cutoffScore={data.profile?.cutoff_score} /></dd>
    </dl>
  );
}

/** Kwaliteit van wat er nu op schijf staat, met het doel van het profiel ernaast. */
export function QualityPanel({ itemId, episodeId }: { itemId: string; episodeId?: string | number }) {
  const { t, num } = useI18n();
  const res = useResource<FilesResponse>(`/library/${encodeURIComponent(itemId)}/files`);
  const sub = res.data?.profile?.name ? [res.data.profile.name, res.data.profile.cutoff].filter(Boolean).join(" · ") : undefined;
  let body: React.ReactNode;
  if (res.loading && !res.data) body = <StateView kind="loading" compact />;
  else if (res.error && !res.data) body = unsupported(res.error) ? <p className="hint" style={{ margin: 0 }}>{t("lib.qualityUnsupported")}</p> : <ErrorState error={res.error} onRetry={() => void res.reload()} compact />;
  else {
    const data = res.data as FilesResponse;
    const files = episodeId != null ? data.files.filter((f) => String(f.episode_id) === String(episodeId)) : data.files;
    if (!files.length) body = <p className="hint" style={{ margin: 0 }}>{t("lib.qualityNone")}</p>;
    else if (files.length === 1) body = <FileFacts f={files[0]} data={data} />;
    else {
      body = (
        <div className="table-wrap">
          <table className="table" data-testid="quality-table">
            <thead><tr><th>{t("lib.season", { n: "" }).trim()}</th><th>{t("lib.qualityName")}</th><th>{t("lib.qualityPicture")}</th><th>{t("lib.qualityAudio")}</th><th>{t("lib.qualityScore")}</th><th>{t("lib.qualityGoal")}</th></tr></thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.file_id}>
                  <td className="num">{f.label ?? "—"}</td>
                  <td>{f.quality?.name ?? "—"}{f.av1_by_mediaguard ? <> <span className="tag tag--accent">AV1</span></> : null}</td>
                  <td>{[f.media?.video_codec, f.media?.dolby_vision ? "DV" : f.media?.dynamic_range].filter(Boolean).join(" · ") || "—"}</td>
                  <td>{langs(f.media?.audio_languages)}</td>
                  <td className="num">{num(f.score ?? 0)}</td>
                  <td><Goal f={f} cutoff={data.profile?.cutoff} cutoffScore={data.profile?.cutoff_score} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
  }
  return <div data-testid="quality-panel"><Card title={t("lib.quality")} sub={sub} icon={Gauge}>{body}</Card></div>;
}

/** Waarom er geüpgraded is: nieuwste eerst, met de reden in gewone taal. */
export function UpgradeTimeline({ itemId, label }: { itemId: string; label?: string }) {
  const { t, num, dateTime } = useI18n();
  const res = useResource<{ upgrades: Upgrade[] }>(`/library/${encodeURIComponent(itemId)}/upgrades`, { days: 90 });
  let body: React.ReactNode;
  if (res.loading && !res.data) body = <StateView kind="loading" compact />;
  else if (res.error && !res.data) body = unsupported(res.error) ? <p className="hint" style={{ margin: 0 }}>{t("lib.upgradesUnsupported")}</p> : <ErrorState error={res.error} onRetry={() => void res.reload()} compact />;
  else {
    const items = (res.data?.upgrades ?? []).filter((u) => !label || u.label === label);
    body = !items.length ? <p className="hint" style={{ margin: 0 }}>{t("lib.upgradesNone")}</p> : (
      <ol className="timeline" data-testid="upgrade-timeline" style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {items.map((u, i) => (
          <li key={`${u.at}-${i}`} className="tl-item">
            <span className="tl-dot tl-dot--accent" aria-hidden="true"><Sparkles className="ico ico-sm" /></span>
            <div className="stack-sm" style={{ minWidth: 0, gap: 4 }}>
              <div className="tl-meta">{dateTime(u.at)}{u.label ? ` · ${u.label}` : ""}{u.source_nl ? ` · ${t("lib.upgradeVia", { source: u.source_nl })}` : ""}</div>
              <div className="tl-title">{u.reason_nl}</div>
              <div className="row wrap t-2" style={{ gap: 8, fontSize: 14 }}>
                {u.old?.quality || u.new?.quality ? <span>{u.old?.quality ?? "—"} <ArrowRight className="ico ico-sm" aria-hidden="true" style={{ verticalAlign: "-2px" }} /> {u.new?.quality ?? "—"}</span> : null}
                {u.old?.score != null && u.new?.score != null && u.old.score !== u.new.score ? <span>{t("lib.upgradeScore", { from: num(u.old.score), to: num(u.new.score) })}</span> : null}
                {u.cf_added?.length ? <span className="t-success">{t("lib.upgradeAdded", { list: u.cf_added.join(", ") })}</span> : null}
                {u.cf_removed?.length ? <span>{t("lib.upgradeRemoved", { list: u.cf_removed.join(", ") })}</span> : null}
                {u.old?.av1_by_mediaguard ? <span className="tag tag--accent">{t("lib.upgradeReplacedAv1")}</span> : null}
              </div>
            </div>
          </li>
        ))}
      </ol>
    );
  }
  return <div data-testid="upgrades-panel"><Card title={t("lib.upgrades")} icon={History}>{body}</Card></div>;
}
