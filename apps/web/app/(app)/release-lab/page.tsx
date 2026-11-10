"use client";

import { CircleCheck, CircleX, FlaskConical, Search, Trophy } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button, Card, InlineState, PageHeader, StatusChip } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { TitlePicker } from "@/components/mg/title-picker";
import { useAction } from "@/lib/api/actions";
import { api } from "@/lib/api/client";
import type { Instance } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { arrMessage } from "@/lib/arr-messages";
import { arrLanguageName } from "@/lib/labels";
import { useI18n, type Locale } from "@/lib/i18n";
import { useSession } from "@/lib/session";

interface RawRelease {
  guid?: string; title?: string; size?: number; indexer?: string; releaseGroup?: string; customFormatScore?: number; rejected?: boolean; rejections?: Rejection[];
  quality?: { quality?: { name?: string; resolution?: number; source?: string } } | string; languages?: Array<{ name?: string } | string>;
  custom_format_score?: number | null; release_group?: string | null; approved?: boolean; raw?: RawRelease;
}
/** Eén vorm voor de tabel: core levert een samenvatting (quality = naam) plus 'raw' van Sonarr/Radarr (N20). */
interface ArrRelease {
  guid?: string; title: string; size?: number; indexer?: string; releaseGroup?: string; score: number | null; rejected: boolean; rejections: Rejection[];
  qualityName: string | null; resolution: number | null; source: string | null; languages: string[];
}
function toRelease(r: RawRelease): ArrRelease {
  const raw = r.raw ?? r;
  const q = typeof raw.quality === "object" ? raw.quality?.quality : undefined;
  const langs = (r.languages ?? raw.languages ?? []).map((l) => (typeof l === "string" ? l : l?.name ?? "")).filter(Boolean);
  const rejections = r.rejections ?? raw.rejections ?? [];
  return {
    guid: r.guid ?? raw.guid, title: r.title ?? raw.title ?? "—", size: r.size ?? raw.size, indexer: r.indexer ?? raw.indexer,
    releaseGroup: r.release_group ?? raw.releaseGroup ?? undefined,
    score: r.custom_format_score ?? raw.customFormatScore ?? null,
    rejected: Boolean(r.rejected ?? raw.rejected) || rejections.length > 0,
    rejections,
    qualityName: (typeof r.quality === "string" ? r.quality : null) ?? q?.name ?? null,
    resolution: q?.resolution ?? null, source: q?.source ?? null, languages: langs,
  };
}
/** Sonarr/Radarr geven tekst; core verrijkt tot {text, code}. Beide komen voor. */
type Rejection = string | { text?: string | null; code?: string | null; reason?: string | null };
export function rejectionText(r: Rejection): string {
  if (typeof r === "string") return r;
  return r?.text ?? r?.reason ?? "";
}
function rejectionCode(r: Rejection): string | null {
  return typeof r === "string" ? null : r?.code ?? null;
}
interface SimResult { profile_name_nl?: string; ranking_order: string[]; winner: string | null; why_nl: string; results: Array<Record<string, unknown>> }

export default function ReleaseLabPage() {
  return <Suspense><ReleaseLab /></Suspense>;
}

function ReleaseLab() {
  const { t, num, bytes, locale } = useI18n();
  const { can } = useSession();
  const params = useSearchParams();
  const instances = useResource<Instance[]>("/instances");
  const [instance, setInstance] = useState(params.get("instance") ?? "");
  const [episodeId, setEpisodeId] = useState(params.get("episode") ?? "");
  const [movieId, setMovieId] = useState(params.get("movie") ?? "");
  // vanaf een aflevering- of filmpagina: alles is al ingevuld, niets hoeft getypt te worden
  const [itemTitle, setItemTitle] = useState(params.get("title"));
  const prefilled = Boolean(params.get("instance") && (params.get("episode") || params.get("movie")));
  const [picking, setPicking] = useState(!prefilled);
  const [releases, setReleases] = useState<ArrRelease[] | null>(null);
  const [sim, setSim] = useState<SimResult | null>(null);
  const { run, pending } = useAction();
  const inst = instances.data?.find((i) => i.id === instance);

  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  // Zoeken gebeurt alleen op verzoek: elk bezoek een interactieve indexerzoekopdracht was te veel.
  const searchGet = async (over?: { instance: string; episodeId?: string; movieId?: string }) => {
    setSim(null);
    setSearchError(null);
    setSearching(true);
    const instId = over?.instance ?? instance;
    const app = instances.data?.find((i) => i.id === instId)?.app;
    const q = app === "radarr" ? { movie_id: over?.movieId ?? movieId } : { episode_id: over?.episodeId ?? episodeId };
    try {
      setReleases((await api.get<RawRelease[]>(`/instances/${instId}/releases`, q)).map(toRelease));
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : String(e));
    } finally {
      setSearching(false);
    }
  };
  const simulate = async () => {
    if (!releases?.length) return;
    const candidates = releases.slice(0, 200).map((r) => ({
      title: r.title, size: r.size ?? null, resolution: r.resolution, source: r.source,
      languages: r.languages, custom_format_score: r.score,
      release_group: r.releaseGroup ?? null, indexer: r.indexer ?? null, guid: r.guid ?? null,
    }));
    const r = await run<SimResult>("sim", "post", "/policy/simulate", { candidates, instance_id: instance, media_type: inst?.media_type });
    if (r) setSim(r);
  };

  return (
    <>
      <PageHeader title={prefilled ? t("releaseLab.whyTitle") : t("nav.releaseLab")} sub={itemTitle ? t("releaseLab.forItem", { title: itemTitle }) : t("releaseLab.sub")} />
      {!picking ? (
        <div className="row wrap" style={{ marginBottom: 12 }}>
          <Button size="sm" variant={releases ? "secondary" : "primary"} icon={Search} pending={searching} disabled={!can("operator") || !instance} onClick={() => void searchGet()}>{t("releaseLab.searchReleases")}</Button>
          <Button size="sm" variant="ghost" onClick={() => setPicking(true)}>{t("releaseLab.otherItem")}</Button>
          {searchError ? <InlineState tone="danger" role="alert">{searchError}</InlineState> : null}
        </div>
      ) : null}
      {picking ? <Card title={t("releaseLab.pick")} icon={Search}>
        <TitlePicker needEpisode autoFocus onPick={(p) => {
          setInstance(p.instance);
          setItemTitle(p.label);
          setReleases(null);
          setSim(null);
          if (p.item.kind === "movie") { setMovieId(String(p.item.provider_id)); setEpisodeId(""); } else if (p.episodeId) { setEpisodeId(String(p.episodeId)); setMovieId(""); }
          setPicking(false);
        }} />
        <p className="hint">{t("releaseLab.hint3")}</p>
        {searchError ? <InlineState tone="danger" role="alert">{searchError}</InlineState> : null}
      </Card> : null}
      <div className="stack" style={{ marginTop: 16 }}>
        {!releases ? <StateView kind="empty" title={!picking ? t("releaseLab.emptyPrefilled") : t("releaseLab.emptyTitle")} body={!picking ? t("releaseLab.emptyPrefilledBody") : t("releaseLab.emptyBody3")} /> : (
          <Card title={t("releaseLab.candidates", { count: releases.length })} icon={FlaskConical} flush actions={<Button size="sm" variant="primary" icon={Trophy} disabled={!releases.length} pending={pending === "sim"} onClick={() => void simulate()}>{t("releaseLab.rank")}</Button>}>
            {sim ? <div className="card-body"><InlineState tone={sim.winner ? "success" : "warning"} icon={Trophy}><strong>{sim.winner ?? t("releaseLab.noWinner")}</strong> — {sim.why_nl} <span className="t-3">({sim.profile_name_nl} · {sim.ranking_order.join(" → ")})</span></InlineState></div> : null}
            <div className="table-wrap">
              <table className="table table--compact table--stack">
                <thead><tr><th>{t("episode.release")}</th><th>{t("library.col.quality")}</th><th>{t("episode.language")}</th><th className="num">{t("episode.score")}</th><th className="num">{t("library.col.size")}</th><th>{t("releaseLab.hardRequirements")}</th></tr></thead>
                {groupReleases(releases, locale).map((g) => (
                  <tbody key={g.key}>
                    <tr className="table-group"><td colSpan={6}><strong>{g.accepted ? t("releaseLab.groupAccepted", { count: num(g.items.length) }) : t("releaseLab.groupRejected", { count: num(g.items.length), reason: g.label })}</strong></td></tr>
                    {g.items.map((r, i) => (
                      <tr key={r.guid ?? `${g.key}-${i}`} aria-selected={sim?.winner === r.title}>
                        <td className="cell-primary"><div className="cell-title" style={{ overflowWrap: "anywhere" }}>{r.title}</div><div className="cell-sub">{[r.indexer, r.releaseGroup].filter(Boolean).join(" · ")}</div></td>
                        <td data-label={t("library.col.quality")}>{r.qualityName ?? "—"}</td>
                        <td data-label={t("episode.language")}>{r.languages.map((l) => arrLanguageName(l, locale)).join(", ") || "—"}</td>
                        <td data-label={t("episode.score")} className="num">{r.score === null ? "—" : num(r.score)}</td>
                        <td data-label={t("library.col.size")} className="num">{bytes(r.size)}</td>
                        <td data-label={t("releaseLab.hardRequirements")}><Requirements r={r} /></td>
                      </tr>
                    ))}
                  </tbody>
                ))}
              </table>
            </div>
          </Card>
        )}
      </div>
    </>
  );
}

/** Groepen op (eerste) afwijsreden; goedgekeurde releases bovenaan (N20). */
function groupReleases(list: ArrRelease[], locale: Locale): Array<{ key: string; label: string; accepted: boolean; items: ArrRelease[] }> {
  const groups = new Map<string, { key: string; label: string; accepted: boolean; items: ArrRelease[] }>();
  for (const r of list) {
    const first = r.rejections.map(rejectionText).find(Boolean);
    const accepted = !r.rejected && !first;
    const label = accepted ? "" : arrMessage(first ?? "", locale).replace(/\s*\(.*\)$/, "") || "—";
    const key = accepted ? "ok" : `x:${label}`;
    const g = groups.get(key) ?? { key, label, accepted, items: [] };
    g.items.push(r);
    groups.set(key, g);
  }
  return [...groups.values()].sort((a, b) => (a.accepted === b.accepted ? b.items.length - a.items.length : a.accepted ? -1 : 1));
}

function Requirements({ r }: { r: ArrRelease }) {
  const { t, locale } = useI18n();
  const list = r.rejections.filter((x) => rejectionText(x));
  if (!r.rejected && !list.length) return <StatusChip tone="success" icon={CircleCheck}>{t("status.ok")}</StatusChip>;
  const texts = list.map((x) => arrMessage(rejectionText(x), locale));
  const codes = list.map(rejectionCode).filter(Boolean);
  return (
    <div className="stack-sm">
      <StatusChip tone="danger" icon={CircleX} title={texts.join("\n")}>{t("releaseLab.decision.reject")}</StatusChip>
      {texts.length ? <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>{texts.map((x, i) => <li key={i}>{x}</li>)}</ul> : null}
      {codes.length ? <details className="tech-details"><summary>{t("state.techDetails")}</summary><span className="mono">{codes.join(" · ")}</span></details> : null}
    </div>
  );
}
