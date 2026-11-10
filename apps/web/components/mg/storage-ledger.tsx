"use client";

import { ArrowDownUp, Database, FolderOpen, HardDrive, Scale } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { Button, Card, InlineState, Segmented, StatusChip } from "@/components/mg/primitives";
import { ErrorState, StateView } from "@/components/mg/states";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";

type Period = "24h" | "7d" | "30d";

interface LedgerItem {
  at: string; instance_id?: string; instance_name?: string; category_nl?: string; media_kind?: string;
  title: string; episode?: string | null; kind: "new" | "upgrade" | "av1"; kind_nl: string;
  size?: number | null; old_size?: number | null; net_bytes: number; old_size_unknown?: boolean;
  quality?: string | null; release_group?: string | null; indexer?: string | null;
  source: string; source_nl: string; href?: string | null;
  /** bij soort 'upgrade': waarom (kwaliteitsstap, scorewinst, REPACK, …) */
  waarom?: string | null;
}
interface Bucket { key: string; label_nl: string; bytes: number; bytes_nl: string; hint_nl: string }
interface Total { key: string; label_nl: string; count: number; bytes: number; bytes_nl: string }
interface DirRow { path: string; group: string; label_nl: string; bytes: number; own_bytes: number }
export interface Ledger {
  period: Period; period_nl: string; summary_nl: string;
  totals: Record<string, number>;
  buckets: Bucket[];
  reconciliation: { used_delta: number | null; explained: number; unexplained: number | null; text_nl: string };
  by_category: Total[]; by_source: Total[];
  items: LedgerItem[];
  dirs: DirRow[]; dirs_measured_at?: string | null;
}

// Peil §7: vaste slot per post in de gestapelde balk (chart-1…5, "niet verklaard" = Overig + arcering); geen
// status- of violette kleuren. De balk per post is één tint (chart-1), een negatieve post chart-2.
const COLORS: Record<string, string> = {
  added: "var(--chart-1)", upgrades: "var(--chart-2)", av1: "var(--chart-3)",
  quarantine: "var(--chart-4)", downloads: "var(--chart-5)", unexplained: "var(--chart-other)",
};
const PERIODS: Period[] = ["24h", "7d", "30d"];
type SortKey = "at" | "title" | "kind" | "net" | "source" | "instance";

function signed(bytes: (v: number) => string, v: number): string {
  return `${v >= 0 ? "+" : "−"}${bytes(Math.abs(v))}`;
}

/** Eén regel voor het Overzicht: wat kwam er de laatste 24 uur bij (met doorklik). */
export function StorageLedgerLine() {
  const { t } = useI18n();
  const res = useResource<Ledger>("/storage/ledger", { period: "24h" }, 300_000);
  if (!res.data) return null;
  return (
    <p className="hint" style={{ margin: 0, display: "flex", gap: 6, alignItems: "baseline", flexWrap: "wrap" }} data-testid="storage-ledger-line">
      <HardDrive className="ico ico-sm" aria-hidden="true" style={{ alignSelf: "center" }} />
      <span style={{ overflowWrap: "anywhere" }}>{res.data.summary_nl}</span>
      <Link href="/av1#opslag">{t("ledger.look")}</Link>
    </p>
  );
}

/** Opslag &amp; downloads: wat kwam erbij en waar ging de ruimte heen (per 24 u / 7 d / 30 d). */
export function StorageLedger() {
  const { t, bytes, num, dateTime, relative } = useI18n();
  const params = useSearchParams();
  const initial = (params?.get("periode") ?? "24h") as Period;
  const [period, setPeriod] = useState<Period>(PERIODS.includes(initial) ? initial : "24h");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "at", dir: -1 });
  const res = useResource<Ledger>("/storage/ledger", { period }, 120_000);
  const d = res.data;
  const rows = useMemo(() => {
    const items = [...(d?.items ?? [])];
    const val = (i: LedgerItem): string | number => {
      switch (sort.key) {
        case "title": return `${i.title} ${i.episode ?? ""}`.toLowerCase();
        case "kind": return i.kind_nl;
        case "net": return i.net_bytes;
        case "source": return i.source_nl;
        case "instance": return i.instance_name ?? "";
        default: return i.at;
      }
    };
    items.sort((a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : 0) * sort.dir);
    return items;
  }, [d, sort]);
  if (res.loading && !d) return <StateView kind="loading" />;
  if (!d) {
    // core onbereikbaar = Onbekend (zelfde vorm als de andere kaarten), geen Storing van het opslagoverzicht zelf
    if (res.error && (res.error.kind === "offline" || res.error.kind === "unavailable")) return <ErrorState error={res.error} onRetry={() => void res.reload()} />;
    if (res.error) return <StateView kind="error" title={t("ledger.error")} action={<Button size="sm" onClick={() => void res.reload()}>{t("action.retry")}</Button>} />;
    return <StateView kind="empty" title={t("ledger.unavailable")} />;
  }
  const positive = d.buckets.filter((b) => b.bytes > 0);
  const posTotal = positive.reduce((s, b) => s + b.bytes, 0);
  const maxAbs = Math.max(1, ...d.buckets.map((b) => Math.abs(b.bytes)));
  const head = (key: SortKey, label: string, cls?: string) => (
    <th className={cls} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button type="button" className="th-sort" onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : key === "title" || key === "source" || key === "instance" || key === "kind" ? 1 : -1 }))}>
        {label}{sort.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );
  return (
    <div style={{ display: "grid", gap: 16 }} data-testid="storage-ledger">
      <Card title={t("ledger.title")} icon={Scale}
        actions={<Segmented label={t("ledger.period")} value={period} onChange={setPeriod}
          options={[{ value: "24h", label: t("ledger.p24h") }, { value: "7d", label: t("ledger.p7d") }, { value: "30d", label: t("ledger.p30d") }]} />}>
        <div style={{ display: "grid", gap: 12 }}>
          <strong style={{ overflowWrap: "anywhere" }} data-testid="ledger-summary">{d.summary_nl}</strong>
          {posTotal > 0 ? (
            <div className="ledger-stack" role="img" aria-label={positive.map((b) => `${b.label_nl} ${b.bytes_nl}`).join(", ")}
              style={{ display: "flex", gap: 2, height: 14, borderRadius: 7, overflow: "hidden", background: "var(--chart-meter-track)" }}>
              {positive.map((b) => (
                <span key={b.key} title={`${b.label_nl}: ${b.bytes_nl}`} className={b.key === "unexplained" ? "arcering-gat" : undefined} style={{ width: `${(b.bytes / posTotal) * 100}%`, backgroundColor: COLORS[b.key] ?? "var(--chart-1)" }} />
              ))}
            </div>
          ) : null}
          <div style={{ display: "grid", gap: 8 }} data-testid="ledger-buckets">
            {d.buckets.map((b) => (
              <div key={b.key} style={{ display: "grid", gap: 2 }}>
                <div className="row-between" style={{ gap: 8, fontSize: 14 }}>
                  <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                    <span aria-hidden="true" className={b.key === "unexplained" ? "arcering-gat" : undefined} style={{ width: 12, height: 12, borderRadius: 3, border: "1px solid var(--border)", backgroundColor: COLORS[b.key] ?? "var(--chart-1)", flex: "none" }} />
                    <strong>{b.label_nl}</strong>
                  </span>
                  <span style={{ fontVariantNumeric: "tabular-nums" }}>{b.bytes_nl}</span>
                </div>
                <div aria-hidden="true" style={{ height: 6, borderRadius: 3, background: "var(--chart-meter-track)", overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${(Math.abs(b.bytes) / maxAbs) * 100}%`, background: b.bytes < 0 ? "var(--chart-2)" : "var(--chart-1)" }} />
                </div>
                <span className="t-3" style={{ fontSize: 14 }}>{b.hint_nl}</span>
              </div>
            ))}
          </div>
          <InlineState tone={d.reconciliation.unexplained !== null && Math.abs(d.reconciliation.unexplained) > 50 * 1024 ** 3 ? "warning" : "info"} icon={ArrowDownUp}>
            {d.reconciliation.text_nl}
          </InlineState>
        </div>
      </Card>

      {(d.totals.upgrades ?? 0) >= 20 ? <UpgradeDiagnosis period={period} /> : null}

      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))" }}>
        <Card title={t("ledger.byCategory")} icon={Database} flush>
          <TotalsList rows={d.by_category} empty={t("ledger.nothing")} />
        </Card>
        <Card title={t("ledger.bySource")} icon={Database} flush>
          <TotalsList rows={d.by_source} empty={t("ledger.nothing")} />
        </Card>
      </div>

      <Card title={t("ledger.items", { n: num(rows.length) })} icon={HardDrive} flush>
        {rows.length ? (
          <div className="table-wrap">
            <table className="table table--compact table--stack" data-testid="ledger-table">
              <thead><tr>
                {head("title", t("ledger.col.title"))}{head("instance", t("ledger.col.instance"))}{head("kind", t("ledger.col.kind"))}<th>{t("ledger.why")}</th>
                {head("net", t("ledger.col.size"), "num")}{head("source", t("ledger.col.source"))}{head("at", t("ledger.col.when"))}
              </tr></thead>
              <tbody>
                {rows.map((i, n) => (
                  <tr key={`${i.at}-${n}`}>
                    <td className="cell-primary" style={{ overflowWrap: "anywhere" }}>
                      {i.href ? <Link href={i.href}>{i.title}</Link> : i.title}{i.episode ? ` ${i.episode}` : ""}
                      {i.quality || i.release_group ? <div className="t-3" style={{ fontSize: 14 }}>{[i.quality, i.release_group, i.indexer].filter(Boolean).join(" · ")}</div> : null}
                    </td>
                    <td data-label={t("ledger.col.instance")}>{i.instance_name ?? i.category_nl ?? "—"}</td>
                    <td data-label={t("ledger.col.kind")}><StatusChip tone={i.kind === "av1" ? "success" : i.kind === "upgrade" ? "accent" : "info"}>{i.kind_nl}</StatusChip></td>
                    <td data-label={t("ledger.why")} style={{ overflowWrap: "anywhere", minWidth: 160 }}>{i.waarom ?? <span className="t-3">—</span>}</td>
                    <td data-label={t("ledger.col.size")} className="num" title={i.old_size ? t("ledger.was", { size: bytes(i.old_size) }) : undefined}>
                      {signed(bytes, i.net_bytes)}{i.old_size_unknown ? <div className="t-3" style={{ fontSize: 14 }}>{t("ledger.oldUnknown")}</div> : null}
                    </td>
                    <td data-label={t("ledger.col.source")}>{i.source_nl}</td>
                    <td data-label={t("ledger.col.when")} title={dateTime(i.at)}>{relative(i.at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <StateView kind="empty" compact title={t("ledger.nothing")} body={t("ledger.nothingBody")} />}
      </Card>

      <Card title={t("ledger.dirsTitle")} icon={FolderOpen} sub={d.dirs_measured_at ? t("ledger.dirsMeasured", { when: relative(d.dirs_measured_at) }) : t("ledger.dirsSoon")} flush>
        {d.dirs.length ? (
          <div className="list">
            {d.dirs.map((x) => (
              <div className="list-row" key={x.path}>
                <div className="list-main">
                  <div className="list-title">{x.label_nl}</div>
                  <div className="list-sub" style={{ overflowWrap: "anywhere" }}>{x.path}</div>
                </div>
                <span style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                  {bytes(x.own_bytes)}
                  {x.bytes > x.own_bytes ? <div className="t-3" style={{ fontSize: 14 }}>{t("ledger.linked", { size: bytes(x.bytes - x.own_bytes) })}</div> : null}
                </span>
              </div>
            ))}
          </div>
        ) : <StateView kind="empty" compact title={t("ledger.dirsSoon")} />}
      </Card>
    </div>
  );
}

interface Diagnosis {
  upgrades: number; items: number; replaced_av1: number; backlog: number; churn_items: number;
  verdict_nl: string[];
  by_group: { name: string; count: number }[];
  top_items: { key: string; title: string; episode?: string | null; href?: string | null; upgrades_7d: number; held: boolean }[];
}

/** Waarom zoveel upgrades? (rapport 8: ±611 anime-upgrades per dag). */
function UpgradeDiagnosis({ period }: { period: Period }) {
  const { t, num } = useI18n();
  const res = useResource<Diagnosis>("/storage/ledger/diagnose", { period }, 300_000);
  const d = res.data;
  if (!d || !d.upgrades) return null;
  return (
    <Card title={t("ledger.diagTitle", { n: num(d.upgrades) })} icon={ArrowDownUp} data-testid="ledger-diagnosis">
      <div style={{ display: "grid", gap: 10 }}>
        {d.verdict_nl.map((v) => <p key={v} className="muted" style={{ margin: 0, overflowWrap: "anywhere" }}>{v}</p>)}
        {d.by_group.length ? (
          <p className="muted" style={{ margin: 0, overflowWrap: "anywhere" }}>
            {t("ledger.diagGroups")} {d.by_group.slice(0, 5).map((g) => `${g.name} (${num(g.count)})`).join(", ")}
          </p>
        ) : null}
        {d.top_items.filter((i) => i.upgrades_7d >= 2).slice(0, 5).map((i) => (
          <div key={i.key} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {i.href ? <Link href={i.href}>{i.title}{i.episode ? ` ${i.episode}` : ""}</Link> : <span>{i.title}</span>}
            <StatusChip tone="warning">{t("ledger.diagTimes", { n: num(i.upgrades_7d) })}</StatusChip>
            {i.held ? <StatusChip tone="info">{t("ledger.diagHeld")}</StatusChip> : null}
          </div>
        ))}
      </div>
    </Card>
  );
}

function TotalsList({ rows, empty }: { rows: Total[]; empty: string }) {
  const { num } = useI18n();
  if (!rows.length) return <StateView kind="empty" compact title={empty} />;
  return (
    <div className="list">
      {rows.map((r) => (
        <div className="list-row" key={r.key}>
          <div className="list-main"><div className="list-title">{r.label_nl}</div></div>
          <span className="t-3" style={{ fontSize: 14 }}>{num(r.count)}×</span>
          <strong style={{ fontVariantNumeric: "tabular-nums", minWidth: 72, textAlign: "right" }}>{r.bytes_nl}</strong>
        </div>
      ))}
    </div>
  );
}
