"use client";

import { FlaskConical, LayoutDashboard, RefreshCw, Search, ShieldCheck, Zap } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";

import { EvidenceCard, Poster } from "@/components/mg/domain";
import { Button, Card, LinkButton, Modal, PageHeader, StatusChip, Switch, Tabs } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { LibraryDetail } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { EPISODE_STATE, libraryStatus } from "@/lib/status";
import { evidenceFromIdentity, libraryComplete } from "@/lib/viewmodels";
import { capFirst, codeLabel, instanceLabel } from "@/lib/labels";
import { MediaDetails, type Details } from "@/components/mg/media-details";
import { QualityPanel, UpgradeTimeline } from "@/components/library/quality";
import { TitleSettings } from "@/components/mg/title-settings";

type Tab = "seasons" | "quality" | "upgrades" | "evidence" | "media" | "conversions" | "history";
type Confirm = { title: string; body: string; label?: string; go: () => Promise<void>; alt?: { label: string; go: () => Promise<void> } };

export default function MediaDetailPage({ params }: { params: Promise<{ instance: string; id: string }> }) {
  const { instance, id } = use(params);
  const { t, num, bytes, date, locale } = useI18n();
  const { can } = useSession();
  const detail = useResource<LibraryDetail>(`/library/${encodeURIComponent(id)}`);
  const [tab, setTab] = useState<Tab>("seasons");
  const { run, pending } = useAction();
  // monitoring wijzigen vraagt eerst om bevestiging (M13)
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const isMovie = detail.data?.kind === "movie";
  // film: geen seizoenen, dus standaard de kwaliteit tonen
  const active: Tab = tab === "seasons" && isMovie ? "quality" : tab;
  // seizoen aan/uit via MediaGuard (docs/APP_API.md §1); aanzetten biedt 'meteen zoeken'
  const setSeason = async (n: number, monitored: boolean, search: boolean) => {
    await run(`s-${n}`, "put", `/library/${encodeURIComponent(id)}/seasons/${n}`, { monitored, search }, t("toast.saved"));
    void detail.reload();
  };
  const seasonConfirm = (n: number, on: boolean): Confirm => on
    ? { title: t("lib.seasonOnTitle", { n }), body: t("lib.seasonOnBody"), label: t("lib.seasonOnSearch"), go: () => setSeason(n, true, true), alt: { label: t("lib.seasonOnOnly"), go: () => setSeason(n, true, false) } }
    : { title: t("detail.confirmSeasonOff", { n }), body: t("detail.confirmSeasonOffBody"), go: () => setSeason(n, false, false) };
  // film: AV1-stand uit MediaGuards eigen gegevens (Radarr kan na de overdracht achterlopen, S5)
  const movieDetails = useResource<Details>(detail.data && isMovie ? `/library/${encodeURIComponent(id)}/details` : null);
  const av1Now = (movieDetails.data?.av1 ?? []).find((j) => j.status === "committed" && !j.rolled_back) ?? null;
  const evidence = useResource<{ status: string; current?: Record<string, unknown>; reason_nl?: string }>(detail.data && isMovie ? `/identity/${instance}/movie/${detail.data.provider_id}/evidence` : null);

  return (
    <ResourceView resource={detail} endpoint={`GET /api/v1/library/${id}`}>
      {(m) => {
        const st = libraryStatus(m);
        const c = libraryComplete(m);
        return (
          <>
            <PageHeader crumbs={[{ label: t("nav.library"), href: `/bibliotheek?type=${m.media_type}` }, { label: m.title }]} title={m.title}
              sub={[m.year, m.network, m.instance_name].filter(Boolean).join(" · ")}
              actions={<>
                <Button icon={Search} pending={pending === "search"} disabled={!can("operator")} onClick={() => void run("search", "post", `/instances/${instance}/search`, m.kind === "series" ? { type: "series", series_id: m.provider_id } : { type: "movies", movie_ids: [m.provider_id] }, t("library.searchQueued"))}>{t("detail.searchMissing")}</Button>
                {m.kind === "movie" ? <Button icon={ShieldCheck} pending={pending === "verify"} disabled={!can("operator")} onClick={async () => { await run("verify", "post", `/identity/${instance}/movie/${m.provider_id}/verify`, {}, t("detail.verifyQueued")); void evidence.reload(); }}>{t("detail.verify")}</Button> : null}
                {m.kind === "movie" ? <LinkButton href={`/release-lab?instance=${encodeURIComponent(instance)}&movie=${m.provider_id}&title=${encodeURIComponent(m.year ? `${m.title} (${m.year})` : m.title)}`} icon={FlaskConical}>{t("detail.whyRelease")}</LinkButton> : null}
                <LinkButton href={`/kwaliteit?tab=optimaliseren&instance=${instance}&${m.kind === "series" ? "series" : "movie"}=${m.provider_id}`} icon={Zap}>{t("detail.optimize")}</LinkButton>
              </>} />
            <div className="card detail-card" style={{ padding: 16, marginBottom: 16 }}>
              <div className="detail-head">
                {/* poster ~160 px naast de titel i.p.v. een half scherm (M12) */}
                <Poster title={m.title} url={m.poster_url} size="lg" />
                <div className="stack-sm" style={{ minWidth: 0 }}>
                  <div className="row wrap">
                    <span className="tag">{instanceLabel(instance, m.instance_name, locale)}</span>
                    <StatusChip tone={st.tone} icon={st.icon} size="lg">{t(st.label, { count: st.count ?? 0 })}</StatusChip>
                    {m.status ? <StatusChip tone="neutral" icon={null} outline>{capFirst(codeLabel(m.status, locale))}</StatusChip> : null}
                  </div>
                  <dl className="kv" style={{ marginTop: 6 }}>
                    <dt>{t("library.col.size")}</dt><dd>{av1Now?.output_size ? <>{bytes(av1Now.output_size)} <span className="t-3">({t("detail.wasSize", { size: bytes(av1Now.source_size ?? m.size_on_disk) })})</span></> : bytes(m.size_on_disk)}</dd>
                    {m.kind === "series" ? <><dt>{t("library.col.complete")}</dt><dd>{num(c.have)} / {num(c.total)}{c.pct !== null ? ` (${num(c.pct)}%)` : ""} · {t("detail.upcoming", { count: num(m.stats.upcoming) })}</dd></> : null}
                    {m.kind === "movie" && m.file ? <><dt>{t("library.col.quality")}</dt><dd>{m.file.quality ?? "—"}{av1Now ? ` → ${t("episode.av1Badge")}` : ""}</dd></> : null}
                    <dt>{t("library.col.monitoring")}</dt><dd>{m.monitored ? (m.monitor_new_items === "all" ? t("library.monitorAllFuture") : t("episode.monitored")) : t("status.unmonitored")}</dd>
                  </dl>
                  {m.kind === "series" ? (
                    <label className="row t-2" style={{ fontSize: 14, gap: 10 }}>
                      <Switch checked={m.monitor_new_items === "all"} disabled={!can("operator")} label={t("detail.futureSeasons")}
                        onChange={(v) => setConfirm({ title: v ? t("detail.confirmFutureOn") : t("detail.confirmFutureOff"), body: t("detail.futureHint"), go: async () => { await run("future", "patch", `/instances/${instance}/series/${m.provider_id}`, { monitor_new_items: v ? "all" : "none" }, t("toast.saved")); void detail.reload(); } })} />
                      {t("detail.futureSeasons")}
                    </label>
                  ) : null}
                  <details className="tech-details"><summary>{t("state.techDetails")}</summary>
                    <dl className="kv" style={{ marginTop: 6 }}>
                      <dt>{t("detail.root")}</dt><dd className="mono" style={{ overflowWrap: "anywhere" }}>{m.root_folder_path ?? "—"}</dd>
                      <dt>{t("detail.path")}</dt><dd className="mono" style={{ overflowWrap: "anywhere" }}>{m.path ?? "—"}</dd>
                      <dt>ID</dt><dd className="mono">{[m.tvdb_id ? `tvdb ${m.tvdb_id}` : null, m.tmdb_id ? `tmdb ${m.tmdb_id}` : null, m.imdb_id, `${instance} #${m.provider_id}`].filter(Boolean).join(" · ")}</dd>
                    </dl>
                  </details>
                </div>
              </div>
            </div>
            {/* uitzonderingen voor alleen deze titel, zonder ID's te typen (punt 25) */}
            <div style={{ marginBottom: 16 }}><TitleSettings instance={instance} kind={m.kind} providerId={m.provider_id} mediaType={m.media_type} title={m.year ? `${m.title} (${m.year})` : m.title} /></div>
            <div style={{ marginBottom: 16 }}>
              <Tabs label={m.title} value={active} onSelect={setTab} items={[
                ...(m.kind === "series" ? [{ value: "seasons" as Tab, label: t("detail.tab.seasons"), count: m.seasons?.length }] : []),
                { value: "quality", label: t("lib.quality") },
                { value: "upgrades", label: t("lib.upgrades") },
                { value: "evidence", label: t("detail.tab.evidence") },
                ...(m.kind === "movie" ? [{ value: "media" as Tab, label: t("episode.tab.media") }, { value: "conversions" as Tab, label: t("episode.tab.conversions") }] : []),
                { value: "history", label: t("detail.tab.history") },
              ]} />
            </div>
            {active === "quality" ? <div className="stack" style={{ marginBottom: 16 }}><QualityPanel itemId={id} /></div> : null}
            {active === "upgrades" ? <div className="stack" style={{ marginBottom: 16 }}><UpgradeTimeline itemId={id} /></div> : null}
            {active === "seasons" && m.kind === "series" ? (
              <Card title={t("overview.seasons")} icon={LayoutDashboard} flush actions={<Button size="sm" variant="ghost" icon={RefreshCw} pending={pending === "refresh"} onClick={async () => { await run("refresh", "post", `/library/sync?instance_id=${encodeURIComponent(instance)}`, {}, t("library.rescanQueued")); }}>{t("detail.refresh")}</Button>}>
                {m.seasons?.length ? (
                  <div className="list" data-testid="season-list">
                    {[...m.seasons].sort((a2, b2) => (a2.season_number === 0 ? 1 : b2.season_number === 0 ? -1 : b2.season_number - a2.season_number)).map((sn, idx) => (
                      // de volg-schakelaar staat NAAST de summary (niet erin: axe nested-interactive), op dezelfde regel
                      <div key={sn.season_number} className="season-wrap">
                      <details className="season" open={idx === 0}>
                        <summary className="list-row" style={{ cursor: "pointer", minHeight: 48 }}>
                          <div className="list-main">
                            <div className="list-title">{sn.season_number === 0 ? t("picker.specials") : t("detail.seasonN", { n: sn.season_number })}</div>
                            <div className="list-sub">{t("detail.seasonCounts", { have: num(sn.counts.available ?? 0), total: num(sn.counts.total ?? sn.episodes.length), missing: num(sn.counts.missing ?? 0) })}</div>
                          </div>
                        </summary>
                        <div className="list">
                          {sn.episodes.map((ep) => {
                            const view = EPISODE_STATE[ep.state] ?? EPISODE_STATE.missing;
                            return (
                              <Link key={ep.episode_id} className="list-row list-row--hover" href={`/bibliotheek/${instance}/${id}/aflevering/${ep.episode_id}`} style={{ paddingLeft: 28 }}>
                                <span className="num t-3" style={{ width: 34, flex: "none" }}>{`E${String(ep.episode_number).padStart(2, "0")}`}</span>
                                <div className="list-main"><div className="list-title">{ep.title ?? t("detail.episodeN", { n: ep.episode_number })}</div><div className="list-sub">{[ep.quality, ep.air_date_utc ? date(ep.air_date_utc) : null].filter(Boolean).join(" · ")}</div></div>
                                <StatusChip tone={view.tone} icon={view.icon}>{t(view.label)}</StatusChip>
                              </Link>
                            );
                          })}
                        </div>
                      </details>
                      <span className="season-switch row" style={{ gap: 8 }} data-testid={`season-switch-${sn.season_number}`}>
                        <span className="t-3 desktop-only">{sn.monitored ? t("lib.seasonMonitored") : t("lib.seasonNotMonitored")}</span>
                        <Switch checked={sn.monitored} disabled={!can("operator") || pending === `s-${sn.season_number}`} label={t("detail.monitorSeason", { n: sn.season_number })}
                          onChange={(v) => setConfirm(seasonConfirm(sn.season_number, v))} />
                      </span>
                      </div>
                    ))}
                  </div>
                ) : <StateView kind="empty" compact />}
              </Card>
            ) : null}
            {active === "evidence" ? (
              <Card title={t("detail.tab.evidence")} sub={evidence.data?.status ? capFirst(codeLabel(evidence.data.status, locale)) : undefined}>
                {m.kind === "series" ? <p className="t-2" style={{ fontSize: 14 }}>{t("detail.evidencePerEpisode")}</p> : <ResourceView resource={evidence} endpoint="GET /api/v1/identity/{instance}/movie/{id}/evidence" compact>
                  {(e) => e.current ? <div className="stack-sm">{evidenceFromIdentity(e.current).map((ev) => <EvidenceCard key={ev.id} item={ev} />)}</div> : <StateView kind="empty" compact title={e.reason_nl ?? t("state.emptyTitle")} />}
                </ResourceView>}
              </Card>
            ) : null}
            {active === "media" && m.kind === "movie" ? <div style={{ marginBottom: 16 }}><MediaDetails path={`/library/${id}/details`} show={["media"]} /></div> : null}
            {active === "conversions" && m.kind === "movie" ? <div style={{ marginBottom: 16 }}><MediaDetails path={`/library/${id}/details`} show={["av1"]} /></div> : null}
            {active === "history" && m.kind === "movie" ? <div style={{ marginBottom: 16 }}><MediaDetails path={`/library/${id}/details`} show={["identity", "history"]} /></div> : null}
            {active === "history" ? (
              <Card title={t("detail.tab.wanted")}>
                {m.wanted?.length ? (
                  <dl className="kv">{m.wanted.map((w) => <div key={w.id} style={{ display: "contents" }}><dt>{codeLabel(w.reason, locale)}</dt><dd>{codeLabel(w.status, locale)} · {t("wanted.attempts", { count: w.attempts })} · {date(w.last_searched_at)}</dd></div>)}</dl>
                ) : <p className="t-2" style={{ fontSize: 14 }}><Link className="link-more" href={`/activiteit?tab=geschiedenis&status=all&q=${encodeURIComponent(m.title)}`}>{t("nav.jobs")}</Link></p>}
                <p className="hint"><Link className="link-more" href="/activiteit?tab=geschiedenis&view=audit">{t("audit.title")}</Link></p>
              </Card>
            ) : null}
            <Modal open={Boolean(confirm)} onClose={() => setConfirm(null)} title={confirm?.title ?? ""} description={confirm?.body}
              footer={<>
                <Button onClick={() => setConfirm(null)} data-autofocus>{t("action.cancel")}</Button>
                {confirm?.alt ? <Button onClick={async () => { const c = confirm; setConfirm(null); await c?.alt?.go(); }} data-testid="confirm-monitor-only">{confirm.alt.label}</Button> : null}
                <Button variant="primary" pending={Boolean(pending)} onClick={async () => { const c = confirm; setConfirm(null); await c?.go(); }} data-testid="confirm-monitor">{confirm?.label ?? t("action.confirm")}</Button>
              </>} />
          </>
        );
      }}
    </ResourceView>
  );
}
