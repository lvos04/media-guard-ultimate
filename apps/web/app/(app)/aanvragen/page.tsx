"use client";

import { Inbox, Plug, Search, Send } from "lucide-react";
import { useEffect, useState } from "react";

import { DeliveryRequests } from "@/components/delivery/requests";
import { Button, Card, InlineState, LinkButton, Modal, PageHeader, StatusChip, Tabs, type Tone } from "@/components/mg/primitives";
import { ErrorState, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api, ApiError } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/** Seerr via MediaGuard (docs/APP_API.md §3). */
interface SeerrItem {
  media_type: "movie" | "tv"; tmdb_id: number; title: string; year?: number | null;
  poster_url?: string | null; overview?: string | null; status: string; status_nl: string;
}
interface SeerrPage { page: number; total_pages: number; results: SeerrItem[] }
interface SeerrDetails extends SeerrItem { seasons?: Array<{ season_number: number; episodes: number; status: string; status_nl: string }> }
interface MyRequest {
  request_id: number; media_type: string; tmdb_id: number; title: string; year?: number | null; poster_url?: string | null;
  requested_at: string; requested_by?: string | null; seerr_status_nl: string;
  delivery: { state: string; state_nl: string; progress?: { present: number; total: number } | null; next_step_nl?: string | null } | null;
}
type Discover = "trending" | "movies" | "tv";

const STATUS_TONE: Record<string, Tone> = { beschikbaar: "success", gedeeltelijk: "info", aangevraagd: "accent", verwerken: "accent", niet: "neutral" };
const notConfigured = (e: unknown) => e instanceof ApiError && e.status === 409;
const unsupported = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.status === 405 || e.kind === "unavailable");

export default function RequestsPage() {
  const { t } = useI18n();
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [discover, setDiscover] = useState<Discover>("trending");
  const [requesting, setRequesting] = useState<SeerrItem | null>(null);
  // zoeken pas na een korte pauze in het typen
  useEffect(() => { const id = window.setTimeout(() => setQuery(input.trim()), 350); return () => window.clearTimeout(id); }, [input]);
  const results = useResource<SeerrPage>(query ? "/seerr/search" : `/seerr/discover/${discover}`, query ? { q: query, page: 1 } : { page: 1 });
  const mine = useResource<{ results: MyRequest[] }>("/seerr/my-requests", { filter: "all", take: 50 }, 60_000);
  const blocked = notConfigured(results.error) ? "config" : unsupported(results.error) ? "unsupported" : null;

  return (
    <div className="stack">
      <PageHeader title={t("req.title")} sub={t("req.sub")} />
      {/* leverstatus bovenaan: wat loopt, hoe lang al, en 'Nu zoeken' (punt 26) */}
      <section aria-label={t("req.delivery")}><DeliveryRequests /></section>
      {blocked === "config" ? (
        <InlineState tone="info" icon={Plug}>
          <span className="row wrap" style={{ gap: 10 }}>{t("req.notConfigured")}<LinkButton size="sm" href="/instellingen#koppelingen">{t("req.connect")}</LinkButton></span>
        </InlineState>
      ) : blocked === "unsupported" ? <InlineState tone="info">{t("req.unsupported")}</InlineState> : (
        <>
          <div className="search-field">
            <Search className="ico" aria-hidden="true" />
            <input className="input" type="search" placeholder={t("req.search")} aria-label={t("req.searchLabel")} value={input} onChange={(e) => setInput(e.target.value)} data-testid="seerr-search" />
          </div>
          {!query ? (
            <Tabs variant="chips" label={t("req.trending")} value={discover} onSelect={setDiscover}
              items={[{ value: "trending", label: t("req.trending") }, { value: "movies", label: t("req.movies") }, { value: "tv", label: t("req.tv") }]} />
          ) : <h2 style={{ fontSize: 15, margin: 0 }}>{t("req.results", { q: query })}</h2>}
          {results.loading && !results.data ? <StateView kind="loading" compact />
            : results.error && !results.data ? <ErrorState error={results.error} onRetry={() => void results.reload()} compact />
              : !results.data?.results.length ? <StateView kind="empty" compact title={query ? t("req.noResults", { q: query }) : t("state.emptyTitle")} />
                : (
                  <div className="poster-grid" data-testid="seerr-results">
                    {results.data.results.map((it) => <PosterCard key={`${it.media_type}-${it.tmdb_id}`} item={it} onRequest={() => setRequesting(it)} />)}
                  </div>
                )}
        </>
      )}

      {/* zonder Seerr staat de uitleg al bovenaan; geen tweede keer */}
      {notConfigured(mine.error) || blocked ? null : <Card title={t("req.mine")} icon={Inbox} flush>
        {mine.loading && !mine.data ? <StateView kind="loading" compact />
          : mine.error && !mine.data ? (notConfigured(mine.error) || unsupported(mine.error) ? <div className="card-body"><p className="hint" style={{ margin: 0 }}>{notConfigured(mine.error) ? t("req.notConfigured") : t("req.unsupported")}</p></div> : <ErrorState error={mine.error} onRetry={() => void mine.reload()} compact />)
            : !mine.data?.results.length ? <div className="card-body"><p className="hint" style={{ margin: 0 }}>{t("req.mineEmpty")}</p></div>
              : <MyRequests rows={mine.data.results} />}
      </Card>}

      {requesting ? <RequestDialog item={requesting} onClose={() => setRequesting(null)} onDone={() => { setRequesting(null); void results.reload(); void mine.reload(); }} /> : null}
    </div>
  );
}

function PosterCard({ item, onRequest }: { item: SeerrItem; onRequest: () => void }) {
  const { t } = useI18n();
  const { can } = useSession();
  const done = item.status === "beschikbaar" || item.status === "aangevraagd" || item.status === "verwerken";
  return (
    <article className="poster-card">
      <div className="poster-card-img">
        {/* eslint-disable-next-line @next/next/no-img-element -- poster van TMDB via Seerr */}
        {item.poster_url ? <img src={item.poster_url} alt="" loading="lazy" /> : <span aria-hidden="true">{item.title.slice(0, 1)}</span>}
      </div>
      <div className="poster-card-body">
        <div className="poster-card-title" title={item.title}>{item.title}</div>
        <div className="t-3" style={{ fontSize: 14 }}>{[item.year, item.media_type === "tv" ? t("req.tv") : t("req.movies")].filter(Boolean).join(" · ")}</div>
        {item.status !== "niet" ? <StatusChip tone={STATUS_TONE[item.status] ?? "neutral"}>{item.status_nl}</StatusChip> : null}
        {!done || item.status === "gedeeltelijk" ? <Button size="sm" variant="primary" icon={Send} disabled={!can("operator")} onClick={onRequest} aria-label={t("req.requestTitle", { title: item.title })}>{t("req.request")}</Button> : null}
      </div>
    </article>
  );
}

function RequestDialog({ item, onClose, onDone }: { item: SeerrItem; onClose: () => void; onDone: () => void }) {
  const { t, num } = useI18n();
  const toast = useToast();
  const details = useResource<SeerrDetails>(item.media_type === "tv" ? `/seerr/media/tv/${item.tmdb_id}` : null);
  const seasons = (details.data?.seasons ?? []).filter((s) => s.season_number > 0);
  const [picked, setPicked] = useState<Set<number> | "all">("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<{ message_nl?: string }>("/seerr/request", { media_type: item.media_type, tmdb_id: item.tmdb_id, seasons: item.media_type === "tv" ? (picked === "all" ? "all" : [...picked].sort((a, b) => a - b)) : undefined, is4k: false });
      toast.push("success", t("req.sent"), res?.message_nl);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const toggle = (n: number) => setPicked((p) => {
    const next = new Set(p === "all" ? seasons.map((s) => s.season_number) : p);
    if (next.has(n)) next.delete(n); else next.add(n);
    return next.size === seasons.length ? "all" : next;
  });
  const nothing = picked !== "all" && picked.size === 0;
  return (
    <Modal open onClose={onClose} title={t("req.requestTitle", { title: item.year ? `${item.title} (${item.year})` : item.title })} description={item.overview ?? undefined}
      footer={<><Button onClick={onClose} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" icon={Send} pending={busy} disabled={nothing} onClick={() => void send()} data-testid="seerr-send">{t("req.send")}</Button></>}>
      <div className="stack-sm">
        {item.media_type === "tv" ? (
          details.loading && !details.data ? <StateView kind="loading" compact />
            : seasons.length ? (
              <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
                <legend className="label">{t("req.seasons")}</legend>
                <label className="row" style={{ gap: 8 }}><input type="checkbox" className="check" checked={picked === "all"} onChange={() => setPicked(picked === "all" ? new Set() : "all")} />{t("req.allSeasons")}</label>
                {seasons.map((s) => (
                  <label key={s.season_number} className="row" style={{ gap: 8 }}>
                    <input type="checkbox" className="check" checked={picked === "all" || picked.has(s.season_number)} onChange={() => toggle(s.season_number)} />
                    <span className="grow">{t("lib.season", { n: s.season_number })} <span className="t-3">· {t("req.episodes", { n: num(s.episodes) })}</span></span>
                    {s.status !== "niet" ? <StatusChip tone={STATUS_TONE[s.status] ?? "neutral"}>{s.status_nl}</StatusChip> : null}
                  </label>
                ))}
              </fieldset>
            ) : null
        ) : null}
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      </div>
    </Modal>
  );
}

function MyRequests({ rows }: { rows: MyRequest[] }) {
  const { t, relative, num } = useI18n();
  return (
    <div className="table-wrap">
      <table className="table table--compact table--stack" data-testid="seerr-my-requests">
        <thead><tr><th>{t("req.colTitle")}</th><th>{t("req.colSeerr")}</th><th>{t("req.colDelivery")}</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.request_id}>
              <td className="cell-primary">{r.title}{r.year ? ` (${r.year})` : ""}<div className="t-3" style={{ fontSize: 14 }}>{[relative(r.requested_at), r.requested_by].filter(Boolean).join(" · ")}</div></td>
              <td data-label={t("req.colSeerr")}>{r.seerr_status_nl}</td>
              <td data-label={t("req.colDelivery")}>
                {r.delivery ? <>
                  {r.delivery.state_nl}{r.delivery.progress ? <span className="t-3"> · {t("lib.seasonHave", { have: num(r.delivery.progress.present), total: num(r.delivery.progress.total) })}</span> : null}
                  {r.delivery.next_step_nl ? <div className="t-3" style={{ fontSize: 14 }}>{r.delivery.next_step_nl}</div> : null}
                </> : <span className="t-3">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
