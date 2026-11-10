"use client";

import { Ban, CircleAlert, Gauge, PauseCircle } from "lucide-react";
import { useState } from "react";

import { Card, Segmented, StatusChip } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { plainText } from "@/lib/labels";

interface SourceRow { name: string; grabs: number; imported: number; failed: number; success_rate: number | null; avoided?: boolean }
interface ClientError { client?: string; name?: string | null; category?: string | null; reason_nl?: string; at?: string | null }
interface Hold { key: string; reason_nl?: string; until?: string; since?: string; kind?: string }
interface DownloadHealth {
  sources_7d?: { indexer?: SourceRow[]; group?: SourceRow[] };
  sources_30d?: { indexer?: SourceRow[]; group?: SourceRow[] };
  client_errors?: ClientError[];
  causes?: Array<{ reason_nl: string; count: number }>;
  paused_items?: Hold[];
}

/**
 * Downloads (rapport 5, M7): succes per indexer en releasegroep, de laatste fouten uit
 * SABnzbd/qBittorrent, de oorzaken die MediaGuard zelf afhandelde en items die even gepauzeerd
 * zijn. Alleen lezen: MediaGuard blokkeert slechte bronnen zelf.
 */
export function DownloadHealth() {
  const { t, num, relative, locale } = useI18n();
  const [days, setDays] = useState<"7" | "30">("7");
  const res = useResource<DownloadHealth>("/downloads/health", undefined, 60_000);
  if (res.loading && !res.data) return <StateView kind="loading" />;
  if (!res.data) {
    // oudere core zonder deze route: rustige lege staat in plaats van een foutmelding
    return <StateView kind="empty" title={t("dl.unavailable")} body={t("dl.unavailableBody")} />;
  }
  const d = res.data;
  const src = (days === "7" ? d.sources_7d : d.sources_30d) ?? {};
  const pct = (v: number | null) => (v === null || v === undefined ? "—" : `${num(v * 100, 0)}%`);
  const table = (rows: SourceRow[] | undefined, kind: "indexer" | "group") => (
    !rows?.length ? <StateView kind="empty" compact title={t("dl.noSources")} /> : (
      <div className="table-wrap">
        <table className="table table--compact table--stack" data-testid={`dl-${kind}`}>
          <thead><tr><th>{t(kind === "indexer" ? "dl.indexer" : "dl.group")}</th><th className="num">{t("dl.grabs")}</th><th className="num">{t("dl.imported")}</th><th className="num">{t("dl.failed")}</th><th className="num">{t("dl.success")}</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td className="cell-primary" style={{ overflowWrap: "anywhere" }}>{r.name} {r.avoided ? <StatusChip tone="danger" icon={Ban}>{t("dl.avoided")}</StatusChip> : null}</td>
                <td data-label={t("dl.grabs")} className="num">{num(r.grabs)}</td>
                <td data-label={t("dl.imported")} className="num">{num(r.imported)}</td>
                <td data-label={t("dl.failed")} className="num">{num(r.failed)}</td>
                <td data-label={t("dl.success")} className="num">
                  <StatusChip tone={r.success_rate === null ? "neutral" : r.success_rate >= 0.8 ? "success" : r.success_rate >= 0.5 ? "warning" : "danger"}>{pct(r.success_rate)}</StatusChip>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  );
  return (
    <div style={{ display: "grid", gap: 16 }} data-testid="download-health">
      <p className="hint" style={{ margin: 0 }}>{t("dl.intro")}</p>
      <Card title={t("dl.sourcesTitle")} icon={Gauge} flush
        actions={<Segmented label={t("dl.period")} value={days} onChange={setDays} options={[{ value: "7", label: t("dl.days7") }, { value: "30", label: t("dl.days30") }]} />}>
        <div style={{ display: "grid", gap: 0 }}>
          <div className="card-subhead" style={{ padding: "10px 16px 0", fontWeight: 600 }}>{t("dl.byIndexer")}</div>
          {table(src.indexer, "indexer")}
          <div className="card-subhead" style={{ padding: "10px 16px 0", fontWeight: 600 }}>{t("dl.byGroup")}</div>
          {table(src.group, "group")}
        </div>
      </Card>
      <Card title={t("dl.causesTitle")} icon={CircleAlert} sub={t("dl.causesSub")} flush>
        {d.causes?.length ? (
          <div className="list">
            {d.causes.map((c) => (
              <div className="list-row" key={c.reason_nl}><div className="list-main"><div className="list-title" style={{ whiteSpace: "normal" }}>{plainText(c.reason_nl, locale)}</div></div><span className="chip">{num(c.count)}×</span></div>
            ))}
          </div>
        ) : <StateView kind="empty" compact title={t("dl.noCauses")} />}
      </Card>
      <Card title={t("dl.clientErrorsTitle")} icon={CircleAlert} sub={t("dl.clientErrorsSub")} flush>
        {d.client_errors?.length ? (
          <div className="list" data-testid="dl-client-errors">
            {d.client_errors.slice(0, 30).map((e, i) => (
              <div className="list-row" key={`${e.name}-${i}`}>
                <div className="list-main">
                  <div className="list-title" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{e.name ?? "—"}</div>
                  <div className="list-sub" style={{ whiteSpace: "normal" }}>{e.reason_nl ? plainText(e.reason_nl, locale) : t("dl.unknownReason")}{e.client ? ` · ${e.client}` : ""}</div>
                </div>
                {e.at ? <span className="t-3" style={{ fontSize: 14 }}>{relative(e.at)}</span> : null}
              </div>
            ))}
          </div>
        ) : <StateView kind="empty" compact title={t("dl.noClientErrors")} />}
      </Card>
      {d.paused_items?.length ? (
        <Card title={t("dl.pausedTitle")} icon={PauseCircle} sub={t("dl.pausedSub")} flush>
          <div className="list">
            {d.paused_items.map((h) => (
              <div className="list-row" key={h.key}>
                <div className="list-main">
                  <div className="list-title" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{h.reason_nl ? plainText(h.reason_nl, locale) : h.key}</div>
                  {h.until ? <div className="list-sub">{t("dl.pausedUntil", { when: relative(h.until) })}</div> : null}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
