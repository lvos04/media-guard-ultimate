"use client";

import { Bot, CalendarDays, CircleCheck, CircleX, Eye, EyeOff, Film, FlaskConical, History, ListTree, RefreshCw, ScanSearch, Search, ShieldCheck, TriangleAlert, Zap } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";

import { EvidenceCard, Timeline } from "@/components/mg/domain";
import { Button, Card, InlineState, LinkButton, PageHeader, StatusChip, Switch, Tabs } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { LibraryDetail, Season, WantedPage, WantedReasons } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { EPISODE_STATE } from "@/lib/status";
import { evidenceFromIdentity } from "@/lib/viewmodels";
import { codeLabel, instanceLabel } from "@/lib/labels";
import { MediaDetails, type Details } from "@/components/mg/media-details";
import { QualityPanel, UpgradeTimeline } from "@/components/library/quality";

type Tab = "overview" | "identity" | "quality" | "media" | "releases" | "conversions" | "history";
interface IdentityResponse { status: string; current?: Record<string, unknown>; history?: Array<Record<string, unknown>>; reason_nl?: string }

export default function EpisodePage({ params }: { params: Promise<{ instance: string; id: string; episode: string }> }) {
  const { instance, id, episode } = use(params);
  const { t, date, dateTime, num, locale } = useI18n();
  const { can } = useSession();
  const series = useResource<LibraryDetail>(`/library/${encodeURIComponent(id)}`);
  const identity = useResource<IdentityResponse>(`/identity/${instance}/episode/${episode}/evidence`);
  const reasons = useResource<WantedReasons>("/wanted/reasons");
  const [tab, setTab] = useState<Tab>("identity");
  // zelfde (gedeelde) gegevens als de tabbladen: is dit bestand al door MediaGuard naar AV1 omgezet? (N26)
  const details = useResource<Details>(`/library/${id}/episodes/${episode}/details`);
  const isAv1 = /av1/i.test(details.data?.media?.video_codec ?? "") || (details.data?.av1 ?? []).some((j) => j.status === "committed" || j.status === "activated");
  const { run, pending } = useAction();
  const seasons = series.data?.seasons;
  const found = findEpisode(seasons, episode);
  const wanted = useResource<WantedPage>(found && series.data ? "/wanted" : null, { instance_id: instance, series_id: series.data?.provider_id, season: found?.season, status: "" });
  const wantedItem = wanted.data?.items.find((w) => String(w.provider_id) === episode);

  return (
    <ResourceView resource={series} endpoint={`GET /api/v1/library/${id}`}>
      {(m) => {
        if (!found) return <StateView kind="not_found" />;
        const { ep, season } = found;
        const code = `S${String(season).padStart(2, "0")}E${String(ep.episode_number).padStart(2, "0")}`;
        const view = EPISODE_STATE[ep.state] ?? EPISODE_STATE.missing;
        const idStatus = identity.data?.status ?? "unresolved";
        const uncertain = idStatus !== "verified";
        const reason = ep.reason_code ? reasons.data?.[ep.reason_code] : undefined;
        return (
          <>
            <PageHeader crumbs={[{ label: t("nav.library"), href: `/bibliotheek?type=${m.media_type}` }, { label: m.title, href: `/bibliotheek/${instance}/${id}` }, { label: code }]} title={`${m.title} — ${ep.title ?? code}`}
              actions={<LinkButton href={`/release-lab?instance=${encodeURIComponent(instance)}&episode=${ep.episode_id}&title=${encodeURIComponent(`${m.title} ${code}`)}`} icon={FlaskConical}>{t("detail.whyRelease")}</LinkButton>} />
            <div className="card" style={{ padding: 16, marginBottom: 16 }}>
              <div className="row wrap" style={{ alignItems: "flex-start", gap: 18 }}>
                <div className="grow stack-sm" style={{ minWidth: 240 }}>
                  <div className="row wrap"><span className="tag">{instanceLabel(instance, m.instance_name, locale)}</span><span className="tag">{code}</span>{ep.absolute_episode_number && m.media_type === "anime" ? <span className="tag">{t("episode.absolute", { n: ep.absolute_episode_number })}</span> : null}</div>
                  <div className="row wrap t-2" style={{ fontSize: 14, gap: 14 }}>
                    <span className="row" style={{ gap: 6 }}><CalendarDays className="ico ico-sm" aria-hidden="true" />{t("episode.aired", { date: date(ep.air_date_utc) })}</span>
                    {ep.quality ? <span className="row" style={{ gap: 6 }}><Film className="ico ico-sm" aria-hidden="true" />{ep.quality}{isAv1 ? ` → ${t("episode.av1Badge")}` : ""}</span> : null}
                    <label className="row" style={{ gap: 6 }} data-testid="episode-monitor">
                      {ep.monitored ? <Eye className="ico ico-sm" aria-hidden="true" /> : <EyeOff className="ico ico-sm" aria-hidden="true" />}
                      {/* aflevering aan/uit via MediaGuard (docs/APP_API.md §1) */}
                      <Switch checked={ep.monitored} label={t("lib.episodeMonitor")} disabled={!can("operator") || pending === "monitor"}
                        onChange={async (v) => { await run("monitor", "put", `/library/${encodeURIComponent(id)}/episodes/${episode}/monitor`, { monitored: v, search: false }, t("toast.saved")); void series.reload(); }} />
                      {ep.monitored ? t("episode.monitored") : t("status.unmonitored")}
                    </label>
                  </div>
                  <div className="row wrap">
                    <StatusChip tone={view.tone} icon={view.icon} size="lg">{t(view.label)}</StatusChip>
                    {identity.loading ? null : uncertain
                      ? <StatusChip tone={idStatus === "mismatch" || idStatus === "suspect" ? "warning" : "unknown"} size="lg">{t("episode.identityStatus", { status: codeLabel(idStatus, locale) })}</StatusChip>
                      : <StatusChip tone="success" size="lg">{t("episode.identityConfirmed")}</StatusChip>}
                  </div>
                </div>
                <div className="stack-sm" style={{ minWidth: 220 }}>
                  <Button variant={uncertain ? "primary" : "secondary"} icon={ListTree} disabled={!can("operator")} pending={pending === "verify"} onClick={async () => { await run("verify", "post", `/identity/${instance}/episode/${episode}/verify`, {}, t("episode.queued")); void identity.reload(); }}>{t("episode.recheckIdentity")}</Button>
                  {idStatus === "uncertain" || idStatus === "mismatch" ? <Button size="sm" icon={ShieldCheck} disabled={!can("operator")} pending={pending === "keep"} onClick={async () => { await run("keep", "post", `/identity/${instance}/episode/${episode}/decision`, { status: "verified" }, t("episode.identityKept")); void identity.reload(); }}>{t("episode.identityKeep")}</Button> : null}
                  <div className="row wrap">
                    <Button size="sm" icon={Search} disabled={!can("operator")} pending={pending === "search"} onClick={() => void run("search", "post", `/instances/${instance}/search`, { type: "episodes", episode_ids: [Number(episode)] }, t("episode.queued"))}>{t("episode.searchNow")}</Button>
                    {wantedItem ? <Button size="sm" icon={RefreshCw} disabled={!can("operator")} pending={pending === "relaxed"} onClick={() => void run("relaxed", "post", `/wanted/${wantedItem.id}/retry-relaxed`, {}, t("episode.queued"))}>{t("episode.searchAlt")}</Button> : null}
                  </div>
                  <div className="row wrap">
                    <LinkButton size="sm" icon={Zap} href={`/kwaliteit?tab=optimaliseren&instance=${instance}&series=${m.provider_id}&season=${season}`}>{t("episode.optimize")}</LinkButton>
                  </div>
                </div>
              </div>
            </div>

            <div style={{ marginBottom: 16 }}>
              <Tabs label={code} value={tab} onSelect={setTab} items={[
                { value: "overview", label: t("episode.tab.overview") }, { value: "identity", label: t("episode.tab.identity") },
                { value: "quality", label: t("lib.quality") }, { value: "media", label: t("episode.tab.media") },
                { value: "releases", label: t("episode.tab.releases") }, { value: "conversions", label: t("episode.tab.conversions") },
                { value: "history", label: t("episode.tab.history") },
              ]} />
            </div>

            <div className="grid">
              <div className="span-8 stack">
                {tab === "overview" ? (
                  <Card title={t("episode.whyState")} icon={ScanSearch}>
                    <p style={{ fontSize: 14 }}>{reason ? <><strong>{reason.label_nl}.</strong> {reason.fix_nl}</> : t(`episode.stateExplain.${ep.state in EPISODE_STATE ? ep.state : "missing"}` as "episode.stateExplain.missing")}</p>
                    {wantedItem ? (
                      <dl className="kv" style={{ marginTop: 12 }}>
                        <dt>{t("wanted.col.status")}</dt><dd>{codeLabel(wantedItem.status, locale)}</dd>
                        <dt>{t("jobs.attempts")}</dt><dd>{num(wantedItem.attempts)}</dd>
                        <dt>{t("wanted.lastSearch")}</dt><dd>{dateTime(wantedItem.last_searched_at)}</dd>
                        <dt>{t("wanted.nextSearch")}</dt><dd>{dateTime(wantedItem.next_search_at)}</dd>
                      </dl>
                    ) : null}
                    {wantedItem ? <p className="hint"><Link className="link-more" href={`/ontbrekend?open=${wantedItem.id}`}>{t("episode.openDiagnosis")}</Link></p> : null}
                  </Card>
                ) : null}
                {tab === "identity" ? (
                  <>
                    {uncertain && identity.data?.reason_nl ? <InlineState tone="warning" icon={TriangleAlert}><strong>{t("episode.whyUncertain")}</strong> {identity.data.reason_nl}</InlineState> : null}
                    <Card title={t("episode.evidence")} icon={ScanSearch} sub={identity.data?.history ? t("episode.sources", { count: identity.data.history.length }) : undefined}>
                      <ResourceView resource={identity} endpoint="GET /api/v1/identity/{instance}/episode/{id}/evidence" compact>
                        {(r) => r.current ? <div className="stack-sm">{evidenceFromIdentity(r.current).map((e) => <EvidenceCard key={e.id} item={e} />)}</div> : <StateView kind="empty" compact title={r.reason_nl ?? t("state.emptyTitle")} />}
                      </ResourceView>
                    </Card>
                  </>
                ) : null}
                {tab === "quality" ? <><QualityPanel itemId={id} episodeId={episode} /><UpgradeTimeline itemId={id} label={code} /></> : null}
                {tab === "media" ? (
                  <MediaDetails path={`/library/${id}/episodes/${episode}/details`} show={["media"]} />
                ) : null}
                {tab === "releases" ? (
                  <Card title={t("episode.tab.releases")}><p className="t-2" style={{ fontSize: 14 }}>{t("episode.releasesHint")}</p><p className="hint"><Link className="link-more" href={`/release-lab?instance=${instance}&episode=${episode}`}>{t("nav.releaseLab")}</Link></p></Card>
                ) : null}
                {tab === "conversions" ? (
                  <MediaDetails path={`/library/${id}/episodes/${episode}/details`} show={["av1"]} />
                ) : null}
                {tab === "history" ? <MediaDetails path={`/library/${id}/episodes/${episode}/details`} show={["history"]} /> : null}
                {tab === "history" ? (
                  <Card title={t("episode.tab.history")} icon={History}>
                    {(identity.data?.history ?? []).length ? (
                      <Timeline items={(identity.data?.history ?? []).map((h) => ({ title: `${t("episode.identityCheck")}: ${codeLabel(String(h.status), locale)}`, meta: dateTime(String(h.checked_at ?? h.created_at ?? "")), tone: h.status === "verified" ? "success" : h.status === "mismatch" ? "danger" : "warning", icon: h.status === "verified" ? CircleCheck : h.status === "mismatch" ? CircleX : TriangleAlert }))} />
                    ) : <StateView kind="empty" compact />}
                  </Card>
                ) : null}
              </div>
              <div className="span-4 stack">
                <Card title={t("episode.facts")} icon={Film}>
                  <dl className="kv">
                    <dt>{t("episode.fileState")}</dt><dd>{ep.has_file ? t("status.available") : t("status.missing")}</dd>
                    <dt>{t("library.col.quality")}</dt><dd>{ep.quality ?? "—"}</dd>
                    <dt>{t("episode.wantedStatus")}</dt><dd>{ep.wanted_status ?? "—"}</dd>
                    <dt>{t("episode.reasonCode")}</dt><dd className="mono">{ep.reason_code ?? "—"}</dd>
                  </dl>
                </Card>
                <section className="card ai-proposal" aria-label={t("nav.ai")}>
                  <div className="card-header"><h2 className="card-title"><Bot className="ico" aria-hidden="true" />{t("episode.aiAdvice")}</h2></div>
                  <div className="card-body stack-sm" style={{ fontSize: 14 }}>
                    <p className="t-2">{t("episode.aiHint")}</p>
                    <LinkButton size="sm" href="/ai">{t("nav.ai")}</LinkButton>
                  </div>
                </section>
              </div>
            </div>

          </>
        );
      }}
    </ResourceView>
  );
}

function findEpisode(seasons: Season[] | undefined, episode: string) {
  for (const s of seasons ?? []) {
    const ep = s.episodes.find((e) => String(e.episode_id) === episode);
    if (ep) return { season: s.season_number, ep };
  }
  return null;
}

