"use client";

import { History, Inbox, RefreshCw, RotateCcw, Search, Sparkles, Square } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button, Card, StatusChip, Tabs, type Tone } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { useShell } from "@/components/shell/app-shell";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { parseServerDate, useI18n, type MessageKey } from "@/lib/i18n";
import { instanceLabel, plainText } from "@/lib/labels";
import { useSession } from "@/lib/session";

type State = "active" | "waiting_release" | "not_found" | "delivered" | "stopped";

export interface DeliveryItem {
  id: number;
  title: string | null;
  media_type: "series" | "movie";
  source: "seerr" | "arr";
  instance_id: string | null;
  stage: string;
  stage_nl: string;
  state: State;
  detail_nl: string | null;
  next_step_nl: string | null;
  progress: { total?: number; have?: number; missing?: number; upcoming?: number; last_failure_nl?: string | null; vanished_nl?: string | null; next_step_nl?: string | null };
  requested_at: string | null;
  delivered_at: string | null;
  /** laatste downloadfout (S8), als core die meegeeft */
  last_failure_nl?: string | null;
  /** zoekpogingen van de leveringsmotor */
  attempts?: number;
  /** MediaGuard's eigen bibliotheek-ID: link naar de titel (punt 26) */
  library_id?: number | null;
  provider_id?: number | null;
  created_at?: string | null;
}

interface ListResponse { items: DeliveryItem[]; counts: Record<string, number>; last_run?: { at?: string } }
interface DeliveryEvent { id: number; ts: string; stage: string; action: string; actor: string; detail_nl: string }
interface Detail extends DeliveryItem { events: DeliveryEvent[] }

const TONE: Record<State, Tone> = { active: "accent", waiting_release: "neutral", not_found: "warning", delivered: "success", stopped: "neutral" };
const FILTERS: Array<"all" | State> = ["all", "active", "waiting_release", "not_found", "delivered", "stopped"];

/** Hoe lang een aanvraag loopt (of liep tot hij geleverd was), kort en leesbaar. */
function ageText(t: ReturnType<typeof useI18n>["t"], from: string | null | undefined, to: string | null | undefined, now: number): string | null {
  if (!from) return null;
  const start = parseServerDate(from)?.valueOf();
  if (!start) return null;
  const end = to ? parseServerDate(to)?.valueOf() ?? now : now;
  const hours = Math.max(0, (end - start) / 3_600_000);
  if (hours < 1) return t("delivery.age.lessHour");
  if (hours < 48) return t("delivery.age.hours", { count: Math.floor(hours) });
  return t("delivery.age.days", { count: Math.floor(hours / 24) });
}

const FIRST_ROWS = 10;

/** Aanvragen: elke titel met de stap waar hij staat en wat MediaGuard eraan doet. */
export function DeliveryRequests() {
  const { t, num, relative } = useI18n();
  const { can } = useSession();
  const [filter, setFilter] = useState<"all" | State>("all");
  const [open, setOpen] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [now] = useState(() => Date.now());
  const list = useResource<ListResponse>("/delivery/requests", filter === "all" ? undefined : { state: filter }, 60_000);
  useCoreEvents("library", () => void list.reload());
  const { run, pending } = useAction();
  const counts = list.data?.counts ?? {};
  return (
    <div className="stack" data-testid="delivery-root">
      <Card title={t("delivery.statusTitle")} icon={Inbox} flush
        sub={list.data?.last_run?.at ? t("delivery.lastRun", { when: relative(list.data.last_run.at) }) : undefined}
        actions={can("operator") ? (
          <Button size="sm" icon={RefreshCw} pending={pending === "delivery-run"}
            onClick={() => void run("delivery-run", "post", "/delivery/run", {}, t("delivery.ran")).then(() => list.reload())}>
            {t("delivery.runNow")}
          </Button>) : undefined}>
        <div className="card-body" style={{ paddingBottom: 0 }}><p className="hint" style={{ margin: 0 }}>{t("delivery.sub")}</p></div>
        {/* tabs scrollen in hun eigen kader; de pagina zelf nooit zijwaarts (S9) */}
        <div style={{ padding: "8px 16px 0", maxWidth: "100%", overflowX: "auto" }} className="tabs-scroll">
          <Tabs label={t("delivery.title")} value={filter} onSelect={(f) => { setFilter(f); setShowAll(false); }} items={FILTERS.map((f) => ({
            value: f, label: t(`delivery.filter.${f}` as MessageKey),
            count: f === "all" ? counts.total : counts[f] ? num(counts[f]) : undefined,
          }))} />
        </div>
        <ResourceView resource={list} endpoint="/delivery/requests" isEmpty={(d) => !d.items.length}
          empty={<p className="hint" style={{ padding: 16 }}>{t("delivery.empty")}</p>}>
          {(data) => (
            <div className="list" data-testid="delivery-list">
              {(showAll ? data.items : data.items.slice(0, FIRST_ROWS)).map((it) => (
                <DeliveryRow key={it.id} it={it} now={now} open={open === it.id} onToggle={() => setOpen(open === it.id ? null : it.id)} onChanged={() => void list.reload()} />
              ))}
              {!showAll && data.items.length > FIRST_ROWS ? (
                <div className="list-row"><Button size="sm" variant="ghost" onClick={() => setShowAll(true)} data-testid="delivery-show-all">{t("delivery.showAll", { count: num(data.items.length) })}</Button></div>
              ) : null}
            </div>
          )}
        </ResourceView>
      </Card>
    </div>
  );
}

/**
 * Eén aanvraag: titel (link naar de bibliotheek), wat er gebeurt, hoe lang hij loopt, het aantal
 * zoekpogingen en de knoppen. Knoppen staan samen in `.delivery-actions` (punt 26).
 */
function DeliveryRow({ it, now, open, onToggle, onChanged }: { it: DeliveryItem; now: number; open: boolean; onToggle: () => void; onChanged: () => void }) {
  const { t, num, locale } = useI18n();
  const { can } = useSession();
  const { status } = useShell();
  const toast = useToast();
  const { run, pending } = useAction();
  const href = it.library_id && it.instance_id ? `/bibliotheek/${it.instance_id}/${it.library_id}` : null;
  const delivered = it.state === "delivered";
  const age = ageText(t, it.requested_at ?? it.created_at, delivered ? it.delivered_at : null, now);
  const attempts = it.attempts ?? 0;
  const estop = Boolean(status?.emergency_stop?.active);
  const searchable = can("operator") && (it.state === "active" || it.state === "not_found");
  const searchNow = async () => {
    const res = await run<SearchResult>(`search-${it.id}`, "post", `/delivery/requests/${it.id}/search`, {});
    if (!res) return;
    if (res.searched) toast.push("success", t("delivery.searched"), res.next_step_nl ?? undefined);
    else toast.push("info", t("delivery.notSearched"), [res.detail_nl, res.next_step_nl].filter(Boolean).join(" · ") || undefined);
    onChanged();
  };
  return (
    <div className="list-row list-row--wrap" style={{ alignItems: "flex-start" }} data-testid="delivery-row">
      <div className="list-main" style={{ minWidth: 0 }}>
        <div className="list-title" style={{ overflowWrap: "anywhere", wordBreak: "break-word", whiteSpace: "normal", maxWidth: "100%" }}>
          {href ? <Link className="link-more" href={href} prefetch={false} data-testid="delivery-title-link">{it.title ?? "—"}</Link> : it.title ?? "—"}
        </div>
        <div className="list-sub" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>
          {it.detail_nl ?? it.stage_nl}
          {it.progress?.total ? ` · ${it.progress.upcoming
            ? t("delivery.progressUpcoming", { have: num(it.progress.have ?? 0), total: num(it.progress.total), upcoming: num(it.progress.upcoming) })
            : t("delivery.progress", { have: num(it.progress.have ?? 0), total: num(it.progress.total) })}` : ""}
        </div>
        {it.progress?.vanished_nl ? <div className="list-sub" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{plainText(it.progress.vanished_nl, locale)}</div> : null}
        {it.progress?.next_step_nl && it.progress.next_step_nl !== it.next_step_nl ? <div className="list-sub" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{t("delivery.nextStep", { step: plainText(it.progress.next_step_nl, locale) })}</div> : null}
        {(it.last_failure_nl ?? it.progress?.last_failure_nl) ? <div className="list-sub" style={{ whiteSpace: "normal", overflowWrap: "anywhere", color: "var(--warning, inherit)" }}>{t("delivery.lastFailure", { reason: plainText(it.last_failure_nl ?? it.progress?.last_failure_nl, locale) })}</div> : null}
        <div className="list-sub t-2" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>
          {t(`delivery.source.${it.source}` as MessageKey)}
          {it.instance_id ? ` · ${instanceLabel(it.instance_id, null, locale)}` : ""}
          {it.next_step_nl ? ` · ${it.next_step_nl}` : ""}
        </div>
        <div className="list-sub t-2" data-testid="delivery-age">
          {[age ? t(delivered ? "delivery.deliveredAfter" : "delivery.runningFor", { age }) : null,
            attempts ? t("delivery.attempts", { count: num(attempts) }) : null].filter(Boolean).join(" · ")}
        </div>
        <div className="row wrap delivery-actions" style={{ gap: 6, marginTop: 6 }}>
          {searchable ? (
            <Button size="sm" icon={Search} pending={pending === `search-${it.id}`} disabled={estop} title={estop ? t("estop.active") : undefined}
              aria-label={t("delivery.searchNowLabel", { title: it.title ?? "" })} onClick={() => void searchNow()} data-testid="delivery-search-now">
              {t("delivery.searchNow")}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" icon={History} aria-expanded={open} onClick={onToggle}>{t("delivery.timeline")}</Button>
        </div>
        {/* Stop / Nu opnieuw (verbeterlijst 11): bij het openklappen, zodat de rij op de telefoon rustig blijft */}
        {open && can("operator") ? (
          <div className="row wrap delivery-actions" style={{ gap: 6, marginTop: 6 }} data-testid="delivery-actions">
            <Button size="sm" icon={RotateCcw} pending={pending === `retry-${it.id}`} disabled={estop} title={estop ? t("estop.active") : undefined}
              onClick={() => void run(`retry-${it.id}`, "post", `/delivery/requests/${it.id}/retry`, {}, t("delivery.retried")).then(onChanged)}>
              {t("delivery.retry")}
            </Button>
            {it.state !== "stopped" ? (
              <Button size="sm" icon={Square} pending={pending === `stop-${it.id}`}
                onClick={() => void run(`stop-${it.id}`, "post", `/delivery/requests/${it.id}/stop`, {}, t("delivery.stoppedToast")).then(onChanged)}>
                {t("delivery.stop")}
              </Button>) : null}
          </div>) : null}
        {open ? <Timeline id={it.id} /> : null}
      </div>
      <StatusChip tone={TONE[it.state] ?? "neutral"}>{it.stage_nl}</StatusChip>
    </div>
  );
}

interface SearchResult { searched: boolean; stage?: string | null; detail_nl?: string | null; next_step_nl?: string | null }

function Timeline({ id }: { id: number }) {
  const { t, dateTime } = useI18n();
  const detail = useResource<Detail>(`/delivery/requests/${id}`);
  return (
    <div style={{ marginTop: 8 }} data-testid="delivery-timeline">
      <div className="t-2" style={{ fontSize: 14, fontWeight: 600 }}>{t("delivery.timeline")}</div>
      <ResourceView resource={detail} compact>
        {(d) => (
          <ol style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 14 }}>
            {d.events.slice(0, 30).map((e) => (
              <li key={e.id}>
                <span className="t-2 num">{dateTime(e.ts)}</span> — {e.detail_nl}
                {e.actor.startsWith("ai:") ? <> <StatusChip tone="accent" icon={Sparkles}>{t("delivery.byAi")}</StatusChip></> : null}
              </li>
            ))}
          </ol>
        )}
      </ResourceView>
    </div>
  );
}
