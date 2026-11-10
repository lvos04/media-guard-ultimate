"use client";

import { Check, EyeOff, ListTree, Pause, RefreshCw, Route, Search, SearchX, Stethoscope, TriangleAlert, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { KpiCard } from "@/components/mg/domain";
import { Button, Card, InlineState, LinkButton, Modal, PageHeader, Progress, Segmented, StatusChip } from "@/components/mg/primitives";
import { SubtitlesMissing } from "@/components/mg/subtitles-missing";
import { Term } from "@/components/mg/term";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import { api } from "@/lib/api/client";
import type { BackfillQuota, Instance, WantedItem, WantedKpis, WantedPage, WantedReasons } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { MediaTabs } from "@/components/shell/media-tabs";
import { parseServerDate, useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { codeLabel, failureCause, instanceLabel, plainText } from "@/lib/labels";
import { RowMenu } from "@/components/mg/row-menu";

interface PlanResult { dry_run: boolean; plans: Array<{ instance_id: string; eligible: number; enabled: boolean; suspended?: boolean; interval_hours: number; deferred: Record<string, number>; commands: Array<{ name?: string; cost: number; items: Array<{ id: number; title: string }> }> }> }

export default function WantedPageRoute() {
  return <Suspense><Wanted /></Suspense>;
}

function Wanted() {
  const { t, num, pct, dateTime, relative, locale } = useI18n();
  const { can } = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const instance = params.get("instance") ?? "";
  const reasonCode = params.get("reason_code") ?? "";
  // ontbrekend en onder kwaliteit apart (H6); standaard ontbrekend
  const kindParam = params.get("reason") === "cutoff" ? "cutoff" : "missing";
  const [page, setPage] = useState(1);
  const kpis = useResource<WantedKpis>("/wanted/kpis");
  const reasons = useResource<WantedReasons>("/wanted/reasons");
  const quota = useResource<BackfillQuota>("/wanted/backfill/quota", undefined, 60_000);
  const instances = useResource<Instance[]>("/instances");
  const list = useResource<WantedPage>("/wanted", { instance_id: instance || undefined, reason: kindParam, reason_code: reasonCode || undefined, page, page_size: 50 });
  useCoreEvents("wanted", () => { void kpis.reload(); void list.reload(); });
  const { run, pending } = useAction();
  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [diag, setDiag] = useState<{ item: WantedItem; data: unknown } | null>(null);
  const k = kpis.data;
  const scoped = k && instance ? k.per_instance[instance] : k;
  const blocked = (scoped as { blocked?: { import_failed: number; client_error: number; unavailable: number; ignored: number; total: number } } | null)?.blocked;

  const set = (key: string, value: string) => {
    const qs = new URLSearchParams(params.toString());
    if (value) qs.set(key, value); else qs.delete(key);
    router.replace(`/ontbrekend?${qs.toString()}`);
    setPage(1);
  };
  const makePlan = async () => {
    const r = await run<PlanResult>("plan", "post", "/wanted/backfill-plan", { instance_id: instance || undefined });
    if (r) setPlan(r);
  };
  const openDiagnosis = async (item: WantedItem) => {
    try { setDiag({ item, data: await api.get(`/wanted/${item.id}/diagnosis`) }); } catch (e) { setDiag({ item, data: { error: e instanceof Error ? e.message : String(e) } }); }
  };
  const dist = kindParam === "cutoff" ? (scoped?.reason_distribution_cutoff ?? {}) : (scoped?.reason_distribution ?? {});
  const detail = scoped?.reason_distribution_detail?.[kindParam] ?? {};
  // eerst wat jou iets vraagt, daarna de rest op aantal
  const needsUser = (code: string) => reasons.data?.[code]?.needs_user === true;
  const reasonRows = Object.entries(dist).sort((a, b) => Number(needsUser(b[0])) - Number(needsUser(a[0])) || b[1] - a[1]);
  const countText = (code: string, count: number) => {
    const d = detail[code];
    if (!d) return num(count);
    return [d.episodes ? t("wanted.nEpisodes", { count: num(d.episodes) }) : null, d.movies ? t("wanted.nMovies", { count: num(d.movies) }) : null].filter(Boolean).join(" · ") || num(count);
  };
  const upcoming = scoped?.upcoming;
  const undiagnosed = scoped?.undiagnosed?.[kindParam] ?? 0;

  return (
    <>
      <PageHeader title={t("nav.wanted")} sub={<>{t("wanted.sub")} · <Term k="backfill" /></>} actions={<>
        <Button icon={Route} variant="primary" pending={pending === "plan"} onClick={() => void makePlan()}>{t("wanted.makePlan")}</Button>
      </>} />
      <MediaTabs />
      <div style={{ marginBottom: 14 }}>
        <Segmented label={t("library.instance")} value={instance} onChange={(v) => set("instance", v)}
          options={[{ value: "", label: t("library.allInstances") }, ...(instances.data ?? []).map((i) => ({ value: i.id, label: instanceLabel(i.id, i.name, locale) }))]} />
      </div>

      {/* core telt Geblokkeerd op uit precies deze delen (rapport 2, N8) */}
      <section className="kpi-grid" style={{ marginBottom: 16 }}>
        <KpiCard label={t("wanted.kpi.total")} icon={SearchX} tone="warning" unavailable={!scoped} source={t("kpi.source.wanted")} updatedAt={kpis.fetchedAt} value={num((scoped as { missing_total?: number } | null | undefined)?.missing_total ?? scoped?.missing)} meta={scoped ? t("wanted.kpi.totalMeta", { cutoff: num(scoped.cutoff), week: num(scoped.missing_aired_7d) }) : null} />
        <KpiCard label={t("wanted.kpi.recovered")} icon={Check} tone="success" unavailable={!scoped} source={t("kpi.source.wanted")} updatedAt={kpis.fetchedAt} value={num(scoped?.resolved_7d)} meta={scoped ? (scoped.resolved_30d === scoped.resolved_7d ? t("wanted.kpi.recoveredSame2") : t("wanted.kpi.recoveredMeta", { month: num(scoped.resolved_30d) })) : null} />
        <KpiCard label={t("wanted.kpi.backfill")} icon={Search} unavailable={!scoped} source={t("kpi.source.wanted")} updatedAt={kpis.fetchedAt} value={num(scoped?.backfill_30d.searches)} meta={scoped ? t("wanted.kpi.backfillMeta", { grabbed: num(scoped.backfill_30d.grabbed), rate: scoped.backfill_30d.success_rate === null ? "—" : pct(scoped.backfill_30d.success_rate * 100) }) : null} />
        <KpiCard label={t("wanted.kpi.blocked")} icon={TriangleAlert} tone="danger" unavailable={!scoped} source={t("kpi.source.wanted")} updatedAt={kpis.fetchedAt} value={num(blocked ? blocked.total : (scoped?.unavailable ?? 0) + (scoped?.import_failed ?? 0))}
          meta={blocked ? t("wanted.kpi.blockedMeta4", { imports: num(blocked.import_failed), client: num(blocked.client_error), unavailable: num(blocked.unavailable), ignored: num(blocked.ignored) })
            : scoped ? t("wanted.kpi.blockedMeta3", { imports: num(scoped.import_failed ?? 0), unavailable: num(scoped.unavailable), ignored: num(scoped.ignored) }) : null} />
      </section>
      <div style={{ marginBottom: 16 }}><SubtitlesMissing /></div>
      {upcoming?.total || undiagnosed ? (
        <div className="stack-sm" style={{ marginBottom: 16 }} data-testid="wanted-notes">
          {upcoming?.total ? <InlineState tone="info">{t("wanted.upcomingNote", { count: num(upcoming.total), movies: num(upcoming.movies), episodes: num(upcoming.episodes) })}</InlineState> : null}
          {undiagnosed ? <InlineState tone="info">{t("wanted.undiagnosedNote", { count: num(undiagnosed) })}</InlineState> : null}
        </div>
      ) : null}

      <div className="grid">
        <Card className="span-8" title={t("wanted.reasons")} icon={ListTree} flush
          actions={<Segmented label={t("wanted.kindLabel")} value={kindParam} onChange={(v) => { const qs = new URLSearchParams(params.toString()); if (v === "cutoff") qs.set("reason", "cutoff"); else qs.delete("reason"); qs.delete("reason_code"); router.replace(`/ontbrekend?${qs.toString()}`); setPage(1); }}
            options={[{ value: "missing", label: t("wanted.kind.missing") }, { value: "cutoff", label: t("wanted.kind.cutoff") }]} />}>
          <ResourceView resource={kpis} endpoint="GET /api/v1/wanted/kpis" compact isEmpty={() => reasonRows.length === 0} empty={<StateView kind="empty" compact title={t("wanted.none")} />}>
            {() => (
              <div className="table-wrap">
                <table className="table table--stack">
                  <thead><tr><th>{t("wanted.col.reason")}</th><th className="num">{t("wanted.col.episodes")}</th><th>{t("wanted.col.fix")}</th><th /></tr></thead>
                  <tbody>{reasonRows.map(([code, count]) => (
                    <tr key={code} aria-selected={reasonCode === code}>
                      <td className="cell-primary"><div className="row"><span className="list-icon list-icon--warning"><SearchX className="ico" aria-hidden="true" /></span><div><div className="cell-title">{reasons.data?.[code]?.label_nl ?? codeLabel(code, locale)}{" "}{needsUser(code) ? <StatusChip tone="warning">{t("wanted.needsYou")}</StatusChip> : <StatusChip tone="neutral" icon={null} outline>{t("wanted.automatic")}</StatusChip>}</div><details className="tech-details"><summary>{t("state.techDetails")}</summary><span className="mono">{code}</span></details></div></div></td>
                      <td data-label={t("wanted.col.episodes")} className="num">{countText(code, count)}</td>
                      <td data-label={t("wanted.col.fix")} className="t-2" style={{ fontSize: 14 }}>{reasons.data?.[code]?.fix_nl ?? "—"}</td>
                      <td><span className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                        {needsUser(code) && /import/i.test(code) ? <Button size="sm" variant="primary" onClick={() => { set("reason_code", code); document.getElementById("wanted-items")?.scrollIntoView({ behavior: "smooth" }); }}>{t("wanted.fixIt")}</Button>
                          : needsUser(code) && reasons.data?.[code]?.href && !reasons.data[code].href!.startsWith("/ontbrekend") ? <LinkButton size="sm" variant="primary" href={reasons.data[code].href!}>{t("wanted.fixIt")}</LinkButton> : null}
                        <Button size="sm" onClick={() => set("reason_code", reasonCode === code ? "" : code)}>{reasonCode === code ? t("wanted.clearFilter") : t("wanted.showItems")}</Button>
                      </span></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </ResourceView>
        </Card>
        <Card className="span-4" title={t("wanted.perInstance")} icon={Route} bodyClass="meter-list">
          <ResourceView resource={quota} endpoint="GET /api/v1/wanted/backfill/quota" compact>
            {(q) => (
              <>
                {Object.entries(q).map(([iid, v]) => {
                  const inst = instances.data?.find((i) => i.id === iid);
                  return (
                    <div className="meter-row" key={iid}>
                      <span><strong>{instanceLabel(iid, inst?.name, locale)}</strong> <span className="t-3">· {num(k?.per_instance[iid]?.missing)}</span></span>
                      {inst?.suspended ? <StatusChip tone="neutral" icon={Pause}>{t("status.paused")}</StatusChip> : <span className="num t-2">{t("wanted.quota", { used: num(v.day_used), limit: num(v.day_max) })}</span>}
                      {(v as { plain_nl?: string }).plain_nl ? <span className="t-2" style={{ gridColumn: "1 / -1", fontSize: 14 }}>{(v as { plain_nl?: string }).plain_nl}</span> : null}
                      <Progress value={v.day_max ? (v.day_used / v.day_max) * 100 : 0} tone={v.day_max && v.day_used / v.day_max > 0.8 ? "warning" : undefined} label={iid} />
                      <details className="tech-details" style={{ gridColumn: "1 / -1" }}><summary>{t("state.techDetails")}</summary><span className="t-3">{t("wanted.quotaHour", { used: num(v.hour_used), max: num(v.hour_max), batch: num(v.batch_size) })}</span></details>
                      <div style={{ gridColumn: "1 / -1" }}><Button size="sm" variant="ghost" icon={RefreshCw} disabled={!can("operator") || inst?.suspended} pending={pending === `bf-${iid}`} onClick={() => void run(`bf-${iid}`, "post", `/instances/${iid}/backfill`, { dry_run: false }, t("wanted.planned"))}>{t("wanted.runBackfill")}</Button></div>
                    </div>
                  );
                })}
                <InlineState>{t("wanted.dedupNote")}</InlineState>
              </>
            )}
          </ResourceView>
        </Card>

        <Card className="span-12" id="wanted-items" title={reasonCode ? `${t("wanted.items")} · ${reasons.data?.[reasonCode]?.label_nl ?? reasonCode}` : t("wanted.items")} icon={SearchX} flush
          actions={reasonCode ? <Button size="sm" variant="ghost" icon={X} onClick={() => set("reason_code", "")}>{t("wanted.clearFilter")}</Button> : null}>
          <ResourceView resource={list} endpoint="GET /api/v1/wanted" isEmpty={(r) => r.items.length === 0} empty={<StateView kind="empty" compact title={t("wanted.none")} />}>
            {(r) => (
              <>
                {reasonCode ? <CauseGroups items={r.items} groups={(r as { cause_groups?: Array<{ reason_nl: string; count: number }> }).cause_groups} /> : null}
                <div className="table-wrap">
                  <table className="table table--compact table--stack">
                    <thead><tr><th>{t("library.col.title")}</th><th>{t("library.instance")}</th><th>{t("wanted.col.reason")}</th><th>{t("wanted.col.status")}</th><th className="num">{t("jobs.attempts")}</th><th>{t("wanted.lastSearch")}</th><th /></tr></thead>
                    <tbody>{r.items.map((w) => (
                      <tr key={w.id}>
                        <td className="cell-primary"><div className="cell-title">{w.item_title ?? w.title}</div>{w.kind === "episode" ? <div className="cell-sub">{episodeSub(w.season_number, w.episode_number, w.title, w.item_title)}</div> : null}</td>
                        <td data-label={t("library.instance")}><span className="tag">{instanceLabel(w.instance_id, null, locale)}</span></td>
                        <td data-label={t("wanted.col.reason")}>{w.last_reason_code === "not-yet-aired" ? t("wanted.upcoming") : w.last_reason_code ? reasons.data?.[w.last_reason_code]?.label_nl ?? codeLabel(w.last_reason_code, locale) : codeLabel(w.reason, locale)}
                          {itemMessage(w) ? <div className="cell-sub" style={{ whiteSpace: "normal" }} data-testid={`wanted-msg-${w.id}`}>{plainText(itemMessage(w), locale)}</div> : null}</td>
                        <td data-label={t("wanted.col.status")}>{(() => {
                          // 'Wordt gezocht' terwijl de laatste poging uren geleden was: zeg dat hij op de volgende ronde wacht (S15)
                          const last = w.last_searched_at ? parseServerDate(w.last_searched_at)?.valueOf() ?? null : null;
                          // core zegt het zelf al ("Volgende zoekpoging …"); anders afleiden uit de laatste poging
                          const coreSays = typeof w.status_nl === "string" && /volgende/i.test(w.status_nl);
                          const idle = !coreSays && w.status === "searching" && last !== null && Date.now() - last > 3600_000;
                          return <StatusChip tone={w.status === "searching" ? (idle ? "neutral" : "accent") : w.status === "unavailable" ? "warning" : w.status === "ignored" ? "neutral" : "danger"} pending={w.status === "searching" && !idle && !coreSays}>{idle ? t("wanted.nextRound") : typeof w.status_nl === "string" ? w.status_nl : codeLabel(w.status, locale)}</StatusChip>;
                        })()}</td>
                        <td data-label={t("jobs.attempts")} className="num">{num(w.attempts)}</td>
                        <td data-label={t("wanted.lastSearch")} title={dateTime(w.last_searched_at)}>{w.last_searched_at ? relative(w.last_searched_at) : t("wanted.never")}</td>
                        <td><div className="row" style={{ justifyContent: "flex-end" }}>
                          {itemActions(w).map((a) => <Button key={a.path} size="sm" variant="primary" disabled={!can("operator")} pending={pending === `a-${w.id}-${a.path}`} onClick={() => void run(`a-${w.id}-${a.path}`, (a.method ?? "post").toLowerCase() as "post", a.path.replace(/^\/api\/v1/, ""), a.body ?? {}, t("wanted.actionDone")).then(() => list.reload())}>{a.label_nl}</Button>)}
                          <Button size="sm" icon={Search} disabled={!can("operator")} pending={pending === `s-${w.id}`} onClick={() => void run(`s-${w.id}`, "post", `/wanted/${w.id}/search`, {}, t("library.searchQueued")).then(() => list.reload())}>{t("wanted.searchNow")}</Button>
                          <RowMenu>
                            <Button size="sm" icon={Stethoscope} onClick={() => void openDiagnosis(w)}>{t("action.diagnose")}</Button>
                            <Button size="sm" icon={EyeOff} disabled={!can("operator")} pending={pending === `i-${w.id}`} onClick={() => void run(`i-${w.id}`, "post", `/wanted/${w.id}/ignore`, {}, t("wanted.ignored")).then(() => list.reload())}>{t("wanted.ignore")}</Button>
                          </RowMenu>
                        </div></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
                <div className="card-footer">
                  <span>{t("table.showing", { from: num((r.page - 1) * r.page_size + 1), to: num((r.page - 1) * r.page_size + r.items.length), total: num(r.total) })}</span>
                  <span className="row"><Button size="sm" disabled={r.page <= 1} onClick={() => setPage((p) => p - 1)}>{t("action.previous")}</Button><Button size="sm" disabled={r.page * r.page_size >= r.total} onClick={() => setPage((p) => p + 1)}>{t("action.next")}</Button></span>
                </div>
              </>
            )}
          </ResourceView>
        </Card>
      </div>

      <Modal open={Boolean(plan)} onClose={() => setPlan(null)} title={t("wanted.planTitle")} icon={Route} description={t("wanted.planBody")} footer={<Button onClick={() => setPlan(null)}>{t("action.close")}</Button>}>
        {plan?.plans.length ? (
          <table className="table table--compact">
            <thead><tr><th>{t("library.instance")}</th><th className="num">{t("wanted.eligible")}</th><th className="num">{t("wanted.commands")}</th><th>{t("wanted.deferred")}</th></tr></thead>
            <tbody>{plan.plans.map((p) => <tr key={p.instance_id}><td>{instanceLabel(p.instance_id, null, locale)}{p.suspended ? ` · ${t("status.paused")}` : ""}</td><td className="num">{num(p.eligible)}</td><td className="num">{num(p.commands.length)}</td><td style={{ fontSize: 14 }}>{Object.entries(p.deferred).map(([kk, v]) => `${kk}: ${v}`).join(", ") || "—"}</td></tr>)}</tbody>
          </table>
        ) : <StateView kind="empty" compact />}
        <p className="hint"><Link href="/instellingen?q=backfill" className="link-more">{t("wanted.planSettings")}</Link></p>
      </Modal>
      <Modal open={Boolean(diag)} onClose={() => setDiag(null)} title={t("wanted.diagnosisTitle", { title: diag?.item.item_title ?? diag?.item.title ?? "" })} icon={Stethoscope} footer={<Button onClick={() => setDiag(null)}>{t("action.close")}</Button>}>
        <DiagnosisSummary data={diag?.data} />
      </Modal>
    </>
  );
}

/** "S01E03 · Proloog…" zonder de serietitel nog eens te herhalen (M6). */
function episodeSub(season: number | null | undefined, episode: number | null | undefined, title: string | null | undefined, series: string | null | undefined): string {
  const code = `S${String(season ?? 0).padStart(2, "0")}E${String(episode ?? 0).padStart(2, "0")}`;
  let rest = (title ?? "").trim();
  if (series && rest.toLowerCase().startsWith(series.toLowerCase())) rest = rest.slice(series.length);
  rest = rest.replace(/^[\s–—:-]*/, "").replace(new RegExp(`^${code}[\\s–—:-]*`, "i"), "").trim();
  return rest ? `${code} · ${rest}` : code;
}

/** Diagnose in gewone taal; ruwe gegevens alleen onder 'Technische details'. */
function DiagnosisSummary({ data }: { data: unknown }) {
  const { t } = useI18n();
  const d = (data ?? {}) as Record<string, unknown>;
  const text = [d.summary_nl, d.verdict_nl, d.reason_nl, d.message_nl, d.advice_nl].filter((x): x is string => typeof x === "string" && x.length > 0);
  return (
    <div className="stack-sm">
      {text.length ? text.map((x, i) => <p key={i} style={{ margin: 0 }}>{x}</p>) : <p style={{ margin: 0 }}>{t("wanted.diagnosisNoSummary")}</p>}
      <details className="tech-details"><summary>{t("state.techDetails")}</summary><pre className="payload-preview">{JSON.stringify(data, null, 2)}</pre></details>
    </div>
  );
}

/** Oorzaken gegroepeerd ("47× uitpakken mislukt"); core-groepen als die er zijn, anders van deze pagina (R6). */
function CauseGroups({ items, groups }: { items: WantedItem[]; groups?: Array<{ reason_nl: string; count: number }> }) {
  const { t, num, locale } = useI18n();
  // echte redenen van SABnzbd/qBittorrent (core /downloads/failures), anders van deze pagina
  const failures = useResource<{ groups?: Array<{ reason_nl: string; count: number }> }>("/downloads/failures", undefined, 120_000);
  let rows = (groups ?? failures.data?.groups)?.filter((g) => g.count > 0) ?? [];
  if (!rows.length) {
    const by = new Map<string, number>();
    for (const w of items) {
      const cause = failureCause(plainText(itemMessage(w), locale)) || t("wanted.importReasonUnknown");
      by.set(cause, (by.get(cause) ?? 0) + 1);
    }
    rows = [...by.entries()].map(([reason_nl, count]) => ({ reason_nl, count })).sort((a, b) => b.count - a.count);
  }
  if (rows.length < 1) return null;
  return (
    <div className="card-body stack-sm" data-testid="cause-groups" style={{ paddingBottom: 0 }}>
      {rows.slice(0, 6).map((g) => <div key={g.reason_nl} className="t-2" style={{ fontSize: 14 }}>{t("wanted.importGroup", { count: num(g.count), reason: g.reason_nl })}</div>)}
    </div>
  );
}

/** Importmelding van Sonarr/Radarr per item (core levert die onder één van deze velden). */
function itemMessage(w: WantedItem): string {
  for (const k of ["client_reason_nl", "download_failure_nl", "reason_detail_nl", "import_message_nl", "import_message", "last_reason_detail", "status_message", "last_message"]) {
    const v = w[k];
    if (typeof v === "string" && v.trim()) return v;
  }
  return "";
}
interface ItemAction { label_nl: string; path: string; method?: string; body?: Record<string, unknown> }
/** Acties die core per item aanbiedt (bv. 'Importeren' bij een ID-match). */
function itemActions(w: WantedItem): ItemAction[] {
  const a = w.actions;
  return Array.isArray(a) ? (a as ItemAction[]).filter((x) => x && typeof x.path === "string" && typeof x.label_nl === "string") : [];
}
