"use client";

import { Gauge, Images, PlayCircle } from "lucide-react";
import { useState } from "react";

import { useEncoder } from "@/components/av1/use-encoder";
import { Button, InlineState, StatusChip } from "@/components/mg/primitives";
import { ENCODER_BASE } from "@/lib/api";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";

interface Still { nummer: number; seconde?: number | null; bron_url: string; resultaat_url: string }
interface Comparison { gemeten: boolean; methode?: "vmaf" | "ssim" | null; score?: number | null; slechtste?: number | null; beelden?: Still[]; uitleg_nl?: string }

/** Engine-URL ("/api/kwaliteit/…") via de MediaGuard-gateway. */
const viaGateway = (url: string) => `${ENCODER_BASE}${url.replace(/^\/api(?=\/)/, "")}`;

/** Eén vergelijking origineel ↔ AV1 met een schuifregelaar. */
function StillCompare({ still, index }: { still: Still; index: number }) {
  const { t, duration } = useI18n();
  const [pos, setPos] = useState(50);
  return (
    <figure className="stack-sm" style={{ margin: 0 }}>
      <div className="compare">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={viaGateway(still.resultaat_url)} alt={t("av1.q.result")} loading="lazy" />
        <div className="compare-top" style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={viaGateway(still.bron_url)} alt={t("av1.q.source")} loading="lazy" />
        </div>
        <span className="compare-line" style={{ left: `calc(${pos}% - 1px)` }} aria-hidden="true" />
        <span className="compare-tag" style={{ left: 8 }}>{t("av1.q.source")}</span>
        <span className="compare-tag" style={{ right: 8 }}>{t("av1.q.result")}</span>
      </div>
      <input className="range" type="range" min={0} max={100} value={pos} aria-label={t("compare.slider", { n: index + 1 })} onChange={(e) => setPos(Number(e.target.value))} />
      {typeof still.seconde === "number" ? <figcaption className="t-3" style={{ fontSize: 14 }}>{t("compare.at", { time: duration(still.seconde) })}</figcaption> : null}
    </figure>
  );
}

/**
 * Kwaliteit van een omzetting (rapport 5, M4): VMAF- of SSIM-score met de slechtste
 * steekproef, en op verzoek drie vergelijkingsbeelden origineel ↔ AV1.
 */
export function QualityCompare({ engineJobId }: { engineJobId: number }) {
  const { t, num } = useI18n();
  const [showStills, setShowStills] = useState(false);
  const res = useEncoder<Comparison>(`/kwaliteit/jobs/${engineJobId}/vergelijking`);
  const c = res.data;
  if (!c) return res.error ? null : <span className="t-3" style={{ fontSize: 14 }}>{t("compare.loading")}</span>;
  const vmaf = c.methode === "vmaf";
  const fmt = (v: number | null | undefined) => (typeof v !== "number" ? "—" : vmaf ? num(v, 1) : num(v, 3));
  const good = typeof c.score === "number" && (vmaf ? c.score >= 93 : c.score >= 0.98);
  return (
    <div className="stack-sm" style={{ gap: 8, marginTop: 8 }} data-testid={`quality-${engineJobId}`}>
      {c.gemeten ? (
        <span className="row wrap" style={{ gap: 6, alignItems: "center" }}>
          <StatusChip tone={good ? "success" : "warning"} icon={Gauge}>{vmaf ? "VMAF" : "SSIM"} {fmt(c.score)}</StatusChip>
          {typeof c.slechtste === "number" ? <span className="t-3" style={{ fontSize: 14 }}>{t("compare.worst", { value: fmt(c.slechtste) })}</span> : null}
          <span className="t-3" style={{ fontSize: 14 }}>{c.uitleg_nl}</span>
        </span>
      ) : <InlineState tone="neutral">{c.uitleg_nl ?? t("compare.notMeasured")}</InlineState>}
      {c.beelden?.length ? (
        showStills ? (
          <div className="grid" style={{ gap: 12 }}>
            {c.beelden.slice(0, 3).map((s, i) => <div key={s.nummer} className="span-4">{<StillCompare still={s} index={i} />}</div>)}
          </div>
        ) : <span><Button size="sm" variant="ghost" icon={Images} onClick={() => setShowStills(true)}>{t("compare.show", { count: Math.min(3, c.beelden.length) })}</Button></span>
      ) : null}
    </div>
  );
}

/** Afspeeltest in de mediaserver (Emby/Jellyfin/Plex) na de overdracht (rapport 5, M3). */
export function PlaybackResult({ coreJobId }: { coreJobId: number }) {
  const { t, relative } = useI18n();
  const res = useResource<{ result: { result?: string | null; detail_nl?: string | null; done_at?: string | null } | null }>(`/mediaserver/playback/${coreJobId}`);
  const r = res.data?.result;
  if (!r) return null;
  const code = r.result ?? "pending";
  const tone = code === "ok" ? "success" : code === "transcode" ? "accent" : code === "failed" ? "danger" : "neutral";
  const label = code === "ok" ? t("playback.direct") : code === "transcode" ? t("playback.transcode") : code === "failed" ? t("playback.failed") : code === "skipped" ? t("playback.skipped") : t("playback.pending");
  return (
    <span className="row wrap" style={{ gap: 6, alignItems: "center", marginTop: 6 }} data-testid={`playback-${coreJobId}`}>
      <StatusChip tone={tone} icon={PlayCircle}>{label}</StatusChip>
      {r.detail_nl ? <span className="t-3" style={{ fontSize: 14 }}>{r.detail_nl}</span> : null}
      {r.done_at ? <span className="t-3" style={{ fontSize: 14 }}>{relative(r.done_at)}</span> : null}
    </span>
  );
}
