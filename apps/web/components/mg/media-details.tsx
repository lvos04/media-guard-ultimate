"use client";

import { Clapperboard, Film, History, ShieldCheck } from "lucide-react";

import { PlaybackResult, QualityCompare } from "@/components/av1/quality-compare";
import { Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { codeLabel, languageName, plainText } from "@/lib/labels";

interface Media {
  resolution?: string | null; video_codec?: string | null; video_bit_depth?: number | null; fps?: number | null; dynamic_range?: string | null;
  hdr?: boolean; dolby_vision?: boolean; audio_codec?: string | null; audio_channels?: number | null; audio_languages?: string[]; subtitle_languages?: string[];
  runtime?: string | null; video_bitrate?: number | null;
}
interface HistoryRow { at?: string; event?: string; event_nl?: string; title?: string; quality?: string; indexer?: string; download_client?: string; reason_nl?: string; status?: string }
interface Av1Row {
  id?: number; status?: string; status_nl?: string | null; created_at?: string; updated_at?: string; error?: string | null; source_size?: number | null; output_size?: number | null;
  // kwaliteitsmeting (R9): VMAF of de bitrate-ondergrens, welke core/engine ook levert
  rolled_back?: boolean; output_mediainfo?: Media | null; encoder_job_id?: number | null;
  vmaf?: number | null; vmaf_min?: number | null; bitrate_kbps?: number | null; bitrate_floor_kbps?: number | null; quality_ok?: boolean | null; quality_nl?: string | null;
}
/** MediaGuards eigen meting (core: mediainfo_summary) heeft een eigen vorm: video/audio/subtitles. */
interface MgMeasured {
  video?: { codec?: string | null; width?: number | null; height?: number | null; hdr10?: boolean | null; dv_profile?: number | string | null; kbps?: number | null; bit_depth?: number | null; pix_fmt?: string | null; color_transfer?: string | null } | null;
  audio?: Array<{ language?: string | null; codec?: string | null; channels?: number | null; title?: string | null }> | null;
  subtitles?: Array<{ language?: string | null; codec?: string | null }> | null;
  size?: number | null; duration?: number | null; measured_at?: string | null; source?: string;
}
type MeasuredMedia = Media & { measured_at?: string | null; dv_profile?: number | string | null; range_known?: boolean; audio_tracks?: Array<{ language?: string | null; codec?: string | null; channels?: number | null }>; subtitle_tracks?: Array<{ language?: string | null; codec?: string | null }> };

/** Zet beide vormen om naar één weergave; onbekend blijft onbekend (nooit standaard 'SDR'). */
export function normalizeMedia(raw: unknown): MeasuredMedia | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as MgMeasured & Media & { measured_at?: string | null };
  if (r.video && typeof r.video === "object") {
    const v = r.video;
    const dv = v.dv_profile !== null && v.dv_profile !== undefined && v.dv_profile !== "" ? v.dv_profile : null;
    const pq = typeof v.color_transfer === "string" ? /2084|pq|hlg|arib/i.test(v.color_transfer) : false;
    const known = dv !== null || v.hdr10 === true || v.hdr10 === false || typeof v.color_transfer === "string";
    const audio = (r.audio ?? []).filter(Boolean);
    const subs = (r.subtitles ?? []).filter(Boolean);
    const first = audio[0];
    return {
      resolution: v.width && v.height ? `${v.width}x${v.height}` : null,
      video_codec: v.codec ?? null, video_bit_depth: v.bit_depth ?? (v.pix_fmt && /10|p010/.test(v.pix_fmt) ? 10 : null),
      video_bitrate: v.kbps ? v.kbps * 1000 : null,
      dolby_vision: dv !== null, dv_profile: dv, hdr: v.hdr10 === true || pq, range_known: known,
      dynamic_range: dv !== null ? `Dolby Vision ${dv}` : v.hdr10 ? "HDR10" : pq ? "HDR" : known ? "SDR" : null,
      audio_codec: first?.codec ?? null, audio_channels: first?.channels ?? null,
      audio_languages: audio.map((a) => a.language ?? "").filter(Boolean),
      subtitle_languages: subs.map((x) => x.language ?? "").filter(Boolean),
      audio_tracks: audio, subtitle_tracks: subs, measured_at: r.measured_at ?? null,
    };
  }
  return { ...r, range_known: r.dynamic_range !== undefined && r.dynamic_range !== null || r.hdr !== undefined || r.dolby_vision !== undefined };
}

export interface Details {
  media?: Media | null;
  /** Mediainfo uit MediaGuards eigen controle (ffprobe) van het geplaatste AV1-bestand (R10). */
  own_media?: (Media & { measured_at?: string | null }) | null; verified_media?: (Media & { measured_at?: string | null }) | null;
  media_mediaguard?: (Media & { measured_at?: string | null }) | null; history?: HistoryRow[]; history_error?: string | null; av1?: Av1Row[]; identity?: { status?: string; reason_nl?: string | null; checked_at?: string | null; evidence_count?: number; source?: string | null } | null }

/** Wat Sonarr/Radarr al weten (mediainfo, historie) plus de AV1-geschiedenis van het bestand (H14). */
export function MediaDetails({ path, show }: { path: string; show: Array<"media" | "history" | "av1" | "identity"> }) {
  const { t, relative, bytes, locale, num } = useI18n();
  const langs = (list?: string[]) => (list ?? []).map((c) => languageName(c, locale)).join(", ");
  const res = useResource<Details>(path);
  // kwaliteitsuitslag per omzetting (R9): VMAF als die er is, anders bitrate t.o.v. de ondergrens
  const qualityText = (j: Av1Row): string | null => {
    if (j.quality_nl) return plainText(j.quality_nl, locale);
    if (typeof j.vmaf === "number") return j.vmaf_min && j.vmaf < j.vmaf_min ? t("quality.vmafLow", { score: num(j.vmaf, 1) }) : t("quality.vmaf", { score: num(j.vmaf, 1) });
    if (typeof j.bitrate_kbps === "number") return j.bitrate_floor_kbps && j.bitrate_kbps < j.bitrate_floor_kbps ? `${t("quality.bitrate", { kbps: num(j.bitrate_kbps) })} — ${t("quality.bitrateFloor", { kbps: num(j.bitrate_floor_kbps) })}` : t("quality.bitrate", { kbps: num(j.bitrate_kbps) });
    return j.output_size ? t("quality.notMeasured") : null;
  };
  return (
    <ResourceView resource={res} endpoint={`GET /api/v1${path}`}>
      {(d) => (
        <div className="stack">
          {show.includes("media") ? (() => {
            // na een AV1-overdracht weet Sonarr/Radarr vaak nog niets: dan telt MediaGuards eigen meting
            const own = normalizeMedia(d.media_mediaguard ?? d.own_media ?? d.verified_media ?? null);
            const m = own ?? normalizeMedia(d.media);
            const unknown = t("details.notMeasured");
            const range = !m ? unknown : m.dolby_vision ? (m.dynamic_range ?? "Dolby Vision") : m.hdr ? (m.dynamic_range ?? "HDR") : m.range_known === false ? unknown : (m.dynamic_range ?? unknown);
            const tracks = (list?: Array<{ language?: string | null; codec?: string | null; channels?: number | null }>) =>
              (list ?? []).map((a) => [a.language ? languageName(a.language, locale) : t("details.langUnknown"), a.codec, a.channels ? `${a.channels} kan.` : null].filter(Boolean).join(" · ")).join(" — ");
            return (
              <Card title={t("details.media")} icon={Film} sub={own ? `${t("media.ownProbe")}${own.measured_at ? ` · ${relative(own.measured_at)}` : ""}` : undefined}>
                {!own && (d as { media_stale?: boolean }).media_stale ? <div style={{ marginBottom: 10 }}><InlineState tone="info">{t("media.staleArr")}</InlineState></div> : null}
                {m ? (
                  <dl className="kv">
                    <dt>{t("details.video")}</dt><dd>{[m.resolution, m.video_codec, m.video_bit_depth ? `${m.video_bit_depth}-bit` : null, m.fps ? `${m.fps} fps` : null, m.video_bitrate ? `${num(Math.round(m.video_bitrate / 1000))} kbit/s` : null].filter(Boolean).join(" · ") || unknown}</dd>
                    <dt>{t("details.range")}</dt><dd>{range}</dd>
                    <dt>{t("details.audio")}</dt><dd style={{ overflowWrap: "anywhere" }}>{m.audio_tracks?.length ? tracks(m.audio_tracks) : [m.audio_codec, m.audio_channels ? `${m.audio_channels} kan.` : null].filter(Boolean).join(" · ") || unknown}</dd>
                    <dt>{t("details.audioLangs")}</dt><dd>{langs(m.audio_languages) || unknown}</dd>
                    <dt>{t("details.subs")}</dt><dd style={{ overflowWrap: "anywhere" }}>{m.subtitle_tracks?.length ? tracks(m.subtitle_tracks) : langs(m.subtitle_languages) || (own ? t("details.none") : unknown)}</dd>
                    {m.runtime ? <><dt>{t("details.runtime")}</dt><dd>{m.runtime}</dd></> : null}
                  </dl>
                ) : <StateView kind="empty" compact title={t("details.noMedia")} />}
              </Card>
            );
          })() : null}
          {show.includes("identity") && d.identity ? (
            <Card title={t("details.identity")} icon={ShieldCheck}>
              <div className="row-between wrap">
                <span className="t-2" style={{ fontSize: 14 }}>{d.identity.reason_nl ?? t("details.identityNoReason")}{d.identity.checked_at ? ` · ${relative(d.identity.checked_at)}` : ""}</span>
                <StatusChip tone={d.identity.status === "verified" || d.identity.status === "match" || d.identity.status === "ok" ? "success" : d.identity.status === "pending" ? "neutral" : d.identity.status === "mismatch" ? "danger" : "warning"}>{codeLabel(d.identity.status ?? "pending", locale)}</StatusChip>
              </div>
            </Card>
          ) : null}
          {show.includes("history") ? (
            <Card title={t("detail.tab.history")} icon={History} flush>
              {d.history?.length ? (
                <div className="list">{d.history.slice(0, 30).map((h, i) => (
                  <div className="list-row list-row--wrap" key={i}>
                    <div className="list-main"><div className="list-title">{h.event_nl ?? codeLabel(h.event, locale)}{h.quality ? ` · ${h.quality}` : ""}</div>
                      <div className="list-sub">{[h.indexer, h.download_client, h.reason_nl, h.at ? relative(h.at) : null].filter(Boolean).join(" · ")}</div></div>
                  </div>
                ))}</div>
              ) : <StateView kind="empty" compact title={d.history_error ? t("details.historyUnavailable") : t("details.noHistory")} body={d.history_error ?? undefined} />}
            </Card>
          ) : null}
          {show.includes("av1") ? (
            <Card title={t("details.av1")} icon={Clapperboard} flush>
              {d.av1?.length ? (
                <div className="list">{d.av1.map((j, i) => (
                  <div className="list-row list-row--wrap" key={j.id ?? i}>
                    <div className="list-main"><div className="list-title">{j.status_nl ?? codeLabel(j.status, locale)}</div>
                      <div className="list-sub" style={{ whiteSpace: "normal" }}>{[j.source_size && j.output_size ? `${bytes(j.source_size)} → ${bytes(j.output_size)}` : null, qualityText(j), j.error ? plainText(j.error, locale) : null, (j.updated_at ?? j.created_at) ? relative(j.updated_at ?? j.created_at ?? "") : null].filter(Boolean).join(" · ")}</div>
                      {j.encoder_job_id && ["committed", "awaiting_activation", "handed_off", "rejected"].includes(String(j.status)) ? <QualityCompare engineJobId={j.encoder_job_id} /> : null}
                      {j.id && j.status === "committed" ? <PlaybackResult coreJobId={j.id} /> : null}</div>
                    <StatusChip tone={j.quality_ok === false ? "warning" : j.status === "completed" || j.status === "activated" || j.status === "committed" ? "success" : j.error ? "danger" : "neutral"}>{j.status_nl ?? codeLabel(j.status, locale)}</StatusChip>
                  </div>
                ))}</div>
              ) : <StateView kind="empty" compact title={t("details.noAv1")} />}
            </Card>
          ) : null}
        </div>
      )}
    </ResourceView>
  );
}
