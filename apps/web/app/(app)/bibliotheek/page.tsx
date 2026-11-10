"use client";

import { CalendarClock, Eye, EyeOff, ScanSearch, Search, SearchX, TriangleAlert, Zap } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { MediaRow } from "@/components/mg/domain";
import { Button, PageHeader, Progress, StatusChip, Tabs } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { Instance, LibraryPage } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { MediaTabs } from "@/components/shell/media-tabs";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { instanceLabel } from "@/lib/labels";
import { libraryStatus } from "@/lib/status";
import { libraryComplete } from "@/lib/viewmodels";

const TYPES = [
  { value: "tv", label: "media.series" }, { value: "anime", label: "media.anime" },
  { value: "movie", label: "media.movies" }, { value: "anime_movie", label: "media.animeMovies" },
] as const;
const STATES: Array<{ value: string; label: MessageKey; icon: typeof SearchX }> = [
  { value: "missing", label: "library.filter.missing", icon: SearchX },
  { value: "cutoff_unmet", label: "library.filter.cutoff", icon: TriangleAlert },
  { value: "upcoming", label: "library.filter.upcoming", icon: CalendarClock },
  { value: "unmonitored", label: "library.filter.unmonitored", icon: EyeOff },
];
const PAGE = 100;

export default function LibraryPage() {
  return <Suspense><Library /></Suspense>;
}

function Library() {
  const { t, num, bytes, locale } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const type = params.get("type") ?? "tv";
  const instance = params.get("instance") ?? "";
  const state = params.get("state") ?? "";
  const sort = params.get("sort") ?? "title";
  const [q, setQ] = useState(params.get("q") ?? "");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const instances = useResource<Instance[]>("/instances");
  const items = useResource<LibraryPage>("/library", { media_type: type, instance_id: instance || undefined, state: state || undefined, q: params.get("q") || undefined, page, page_size: PAGE, sort });
  useCoreEvents("library", () => void items.reload());
  const { run, pending } = useAction();

  const setParam = (key: string, value: string) => {
    const qs = new URLSearchParams(params.toString());
    if (value) qs.set(key, value); else qs.delete(key);
    router.replace(`/bibliotheek?${qs.toString()}`);
    setSelected(new Set());
    setPage(1);
  };
  const typeInstances = useMemo(() => (instances.data ?? []).filter((i) => i.media_type === type), [instances.data, type]);
  const rows = items.data?.items ?? [];
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const isMovie = type === "movie" || type === "anime_movie";
  const selectedInstances = [...new Set(rows.filter((r) => selected.has(r.id)).map((r) => r.instance_id))];

  return (
    <>
      <PageHeader title={t("library.title")} sub={t("library.sub")} actions={<>
        <Button icon={ScanSearch} pending={pending === "rescan"} onClick={async () => { await run("rescan", "post", `/library/sync${instance ? `?instance_id=${encodeURIComponent(instance)}` : ""}`, {}, t("library.rescanQueued")); }}>{t("library.rescan")}</Button>
      </>} />
      <MediaTabs />
      <CountChanges />
      <div style={{ marginBottom: 14 }}>
        <Tabs label={t("library.typeTabs")} value={type} items={TYPES.map((x) => ({ value: x.value, label: t(x.label), href: `/bibliotheek?type=${x.value}` }))} />
      </div>
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <form className="search-field" style={{ flex: "1 1 260px", maxWidth: 360 }} onSubmit={(e) => { e.preventDefault(); setParam("q", q.trim()); }}>
          <Search className="ico" aria-hidden="true" />
          <input className="input" type="search" placeholder={t("library.filterPlaceholder")} aria-label={t("library.filterPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
        <select className="select" style={{ width: "auto" }} aria-label={t("library.instance")} value={instance} onChange={(e) => setParam("instance", e.target.value)}>
          <option value="">{t("library.allInstances")}</option>
          {typeInstances.map((i) => <option key={i.id} value={i.id}>{instanceLabel(i.id, i.name, locale)}</option>)}
        </select>
        {STATES.map((f) => (
          <button key={f.value} type="button" className="filter-chip" aria-pressed={state === f.value} onClick={() => setParam("state", state === f.value ? "" : f.value)}>
            <f.icon className="ico ico-sm" aria-hidden="true" />{t(f.label)}
          </button>
        ))}
        <span className="topbar-spacer" />
        <select className="select" style={{ width: "auto" }} aria-label={t("library.sort")} value={sort} onChange={(e) => setParam("sort", e.target.value)}>
          <option value="title">{t("library.sort.title")}</option><option value="added">{t("library.sort.added")}</option><option value="year">{t("library.sort.year")}</option><option value="size">{t("library.sort.size")}</option>
        </select>
      </div>

      <div className="card">
        {selected.size ? (
          <div className="card-header" style={{ borderBottom: "1px solid var(--mg-border-soft)" }}>
            <strong style={{ fontSize: 14 }}>{t("table.selected", { count: selected.size })}</strong>
            <div className="row wrap">
              {selectedInstances.map((iid) => (
                <Button key={iid} size="sm" icon={Search} pending={pending === `bf-${iid}`} onClick={() => void run(`bf-${iid}`, "post", `/instances/${iid}/backfill`, { dry_run: false }, t("library.searchQueued"))}>{t("library.bulk.backfill", { instance: instanceLabel(iid, null, locale) })}</Button>
              ))}
              <Link className="btn btn--sm" href={`/kwaliteit?tab=optimaliseren&type=${type}${instance ? `&instance=${instance}` : ""}`}><Zap className="ico" aria-hidden="true" />{t("library.bulk.optimize")}</Link>
            </div>
          </div>
        ) : null}
        <ResourceView resource={items} endpoint="GET /api/v1/library" isEmpty={(p) => p.items.length === 0} empty={<StateView kind="empty" title={t("library.empty")} body={t("library.emptyBody")} />}>
          {(pg) => {
            // kolom Kwaliteit alleen als core de bestandskwaliteit meestuurt (N19): nooit een lege kolom
            const showQuality = isMovie && pg.items.some((i) => movieQuality(i));
            return (
            <>
              <div className="table-wrap">
                <table className="table table--stack">
                  <thead><tr>
                    <th style={{ width: 52 }}><label className="check-hit"><input type="checkbox" className="check" aria-label={t("table.selectAll")} checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.id)))} /></label></th>
                    <th aria-sort={sort === "title" ? "ascending" : undefined}>{t("library.col.title")}</th>
                    {!instance ? <th>{t("library.instance")}</th> : null}
                    {!isMovie ? <th>{t("library.col.complete")}</th> : showQuality ? <th>{t("library.col.quality")}</th> : null}
                    <th>{t("library.col.status")}</th><th>{t("library.col.monitoring")}</th><th className="num">{t("library.col.size")}</th>
                  </tr></thead>
                  <tbody>
                    {pg.items.map((item) => {
                      const c = libraryComplete(item);
                      const st = libraryStatus(item);
                      return (
                        <tr key={item.id} aria-selected={selected.has(item.id)}>
                          <td className="hide-mobile"><label className="check-hit"><input type="checkbox" className="check" aria-label={t("table.selectRow", { name: item.title })} checked={selected.has(item.id)} onChange={() => setSelected((s) => { const n = new Set(s); if (n.has(item.id)) n.delete(item.id); else n.add(item.id); return n; })} /></label></td>
                          <td className="cell-primary">
                            <MediaRow title={item.title} poster={item.poster_url} href={`/bibliotheek/${item.instance_id}/${item.id}`}
                              sub={[item.year, item.network].filter(Boolean).join(" · ")} />
                          </td>
                          {!instance ? <td data-label={t("library.instance")}><span className="tag">{instanceLabel(item.instance_id, item.instance_name, locale)}</span></td> : null}
                          {!isMovie ? (
                            <td data-label={t("library.col.complete")}>
                              <div style={{ minWidth: 120 }}>
                                <div className="row-between" style={{ fontSize: 14 }}><span className="num">{num(c.have)} / {num(c.total)}</span><span className={item.stats.cutoff_unmet > 0 ? "t-2" : c.pct === null || c.pct >= 100 ? "t-success" : "t-warning"}>{c.pct === null ? "—" : `${num(c.pct)}%`}</span></div>
                                <div style={{ marginTop: 5 }}><Progress value={c.pct ?? 100} thin tone={item.stats.cutoff_unmet > 0 ? undefined : c.pct === null || c.pct >= 100 ? "success" : "warning"} label={item.title} /></div>
                              </div>
                            </td>
                          ) : showQuality ? <td data-label={t("library.col.quality")}>{movieQuality(item) ? <span className="tag">{movieQuality(item)}</span> : <span className="t-3">—</span>}</td> : null}
                          <td data-label={t("library.col.status")}><StatusChip tone={st.tone} icon={st.icon}>{t(st.label, { count: st.count ?? 0 })}</StatusChip></td>
                          <td data-label={t("library.col.monitoring")}>{item.monitored ? <StatusChip tone="accent" icon={Eye}>{item.monitor_new_items === "all" ? t("library.monitorAllFuture") : t("episode.monitored")}</StatusChip> : <StatusChip tone="neutral" icon={EyeOff}>{t("status.unmonitored")}</StatusChip>}</td>
                          <td data-label={t("library.col.size")} className="num">{(() => {
                            // na een AV1-overdracht kan Sonarr/Radarr nog de oude grootte melden (S5); core levert dan av1_size
                            const av1 = (item as { av1_size?: number | null }).av1_size;
                            return av1 ? <span title={t("episode.av1Badge")}>{bytes(av1)} <span className="tag">AV1</span></span> : bytes(item.size_on_disk);
                          })()}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="card-footer">
                <span>{t("table.showing", { from: num((pg.page - 1) * pg.page_size + 1), to: num((pg.page - 1) * pg.page_size + pg.items.length), total: num(pg.total) })}</span>
                <span className="row">
                  <Button size="sm" disabled={pg.page <= 1} onClick={() => setPage((p) => p - 1)}>{t("action.previous")}</Button>
                  <Button size="sm" disabled={pg.page * pg.page_size >= pg.total} onClick={() => setPage((p) => p + 1)}>{t("action.next")}</Button>
                </span>
              </div>
            </>
            );
          }}
        </ResourceView>
      </div>
    </>
  );
}

/** Kwaliteit van het filmbestand (niet de status); core stuurt die mee zodra hij bekend is. */
function movieQuality(item: LibraryPage["items"][number]): string | null {
  const x = item as unknown as { quality?: string | null; file_quality?: string | null; file?: { quality?: string | null } | null };
  return x.quality ?? x.file_quality ?? x.file?.quality ?? null;
}

interface CountDay { day: string; delta: Record<string, Record<string, number>> | null; removed: Array<{ instance_id: string; kind: string; title: string }>; reasons_nl: string[] }
/** "Waarom veranderd?": dagen waarop de aantallen daalden of stegen, met de oorzaak die MediaGuard kan aanwijzen (N28). */
function CountChanges() {
  const { t, date, locale } = useI18n();
  const changes = useResource<{ days: CountDay[] }>("/library-count-changes", { days: 14 });
  const days = (changes.data?.days ?? []).filter((d) => d.delta && Object.values(d.delta).some((v) => Object.values(v).some((n) => n !== 0)) && d.reasons_nl.length).reverse();
  if (!days.length) return null;
  return (
    <details className="raw" style={{ marginBottom: 12 }} data-testid="count-changes">
      <summary style={{ fontSize: 14 }}>{t("library.whyChanged")}</summary>
      <ul className="list" style={{ marginTop: 6 }}>{days.slice(0, 7).map((d) => (
        <li key={d.day} className="list-row list-row--wrap">
          <div className="list-main">
            <div className="list-title">{date(d.day)}: {d.reasons_nl.join(" · ")}</div>
            {d.removed.length ? <div className="list-sub" style={{ whiteSpace: "normal" }}>{d.removed.slice(0, 8).map((r) => `${r.title} (${instanceLabel(r.instance_id, null, locale)})`).join(", ")}</div> : null}
          </div>
        </li>
      ))}</ul>
    </details>
  );
}
