"use client";

import { HardDrive, HeartPulse } from "lucide-react";
import Link from "next/link";

import { ChartFrame } from "@/components/mg/chart-frame";
import { LineChart } from "@/components/mg/charts";
import { InlineState, Progress, StatusChip } from "@/components/mg/primitives";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";

interface CountDay { day: string; free_bytes?: number | null }
/** Prognose uit core (GET /storage/forecast, rapport 5 M6); alle velden mogen ontbreken. */
interface ForecastDisk { path: string; free: number; total: number; trend_per_day?: number | null; days_left?: number | null; days_left_nl?: string | null; history?: Array<{ day: string; free: number }>; enough_data?: boolean }
interface TrueNasPool { name: string; status?: string; healthy?: boolean; scrub_state?: string | null; scrub_errors?: number | null; scrub_end?: string | null }
interface StorageForecast {
  disks?: ForecastDisk[];
  soonest_days?: number | null;
  soonest_nl?: string | null;
  basis_nl?: string | null;
  truenas?: { ok?: boolean; error_nl?: string | null; pools?: TrueNasPool[] } | null;
}

const DAY = 86_400_000;

/** Lineaire trend (bytes per dag) over de laatste punten; null bij te weinig data. */
export function freeTrend(points: Array<{ t: number; free: number }>): number | null {
  if (points.length < 3) return null;
  const n = points.length;
  const mx = points.reduce((s, p) => s + p.t, 0) / n;
  const my = points.reduce((s, p) => s + p.free, 0) / n;
  let num = 0, den = 0;
  for (const p of points) { num += (p.t - mx) * (p.free - my); den += (p.t - mx) ** 2; }
  return den ? (num / den) * DAY : null;
}

/**
 * Vrije ruimte over tijd met een schatting 'schijf vol over ± N dagen' (rapport 5, M6).
 * Gebruikt core's eigen prognose als die er is, anders de dagelijkse metingen van de
 * bibliotheek (library-count-changes). TrueNAS-gezondheid alleen als core die levert.
 */
export function StorageTrend() {
  const { t, bytes, num, date } = useI18n();
  const history = useResource<{ days: CountDay[] }>("/library-count-changes", { days: 30 }, 600_000);
  const forecast = useResource<StorageForecast>("/storage/forecast", undefined, 600_000);
  const pts = (history.data?.days ?? [])
    .filter((d) => typeof d.free_bytes === "number")
    .map((d) => ({ day: d.day, t: new Date(`${d.day}T12:00:00Z`).valueOf(), free: d.free_bytes as number }));
  const f = forecast.data;
  // per schijf de eigen geschiedenis uit core; anders de dagelijkse totalen van de bibliotheek
  const disk = f?.disks?.find((d) => d.history && d.history.length >= 2) ?? null;
  const series = disk ? disk.history!.map((h) => ({ day: h.day, free: h.free })) : pts;
  const slope = freeTrend(series.slice(-14).map((p) => ({ t: new Date(`${p.day}T12:00:00Z`).valueOf(), free: p.free })));
  const latest = series.length ? series[series.length - 1].free : null;
  const local = slope !== null && slope < 0 && latest !== null ? latest / -slope : null;
  const daysFull = f ? (f.soonest_days ?? null) : local;
  const perDay = disk?.trend_per_day != null ? -disk.trend_per_day : slope !== null ? -slope : null;
  const tone = daysFull === null || daysFull === undefined ? "neutral" : daysFull < 30 ? "danger" : daysFull < 90 ? "warning" : "success";
  const maxTb = Math.max(1, ...series.map((p) => p.free / 1e12)) * 1.1;
  const chart = series.map((p) => ({ x: date(p.day), free: p.free / 1e12 }));
  return (
    <section id="opslag-trend" style={{ marginTop: 16 }}>
      <h3 style={{ fontSize: 14, margin: "0 0 2px", display: "flex", gap: 6, alignItems: "center" }}><HardDrive className="ico ico-sm" aria-hidden="true" />{t("storage.trendTitle")}</h3>
      <p className="hint" style={{ margin: "0 0 8px" }}>{t("storage.trendSub")} <Link href="/av1#opslag" data-testid="ledger-link">{t("ledger.trendLink")}</Link></p>
      <div data-testid="storage-trend" style={{ display: "grid", gap: 12 }}>
        <div className="row-between" style={{ flexWrap: "wrap", gap: 8 }}>
          <strong>
            {daysFull === null || daysFull === undefined
              ? (f?.soonest_nl ?? (series.length < 3 ? t("storage.trendTooFew") : t("storage.trendStable")))
              : f?.soonest_nl ?? (daysFull > 3650 ? t("storage.fullNever") : t("storage.fullIn", { n: num(Math.round(daysFull)) }))}
          </strong>
          {daysFull !== null && daysFull !== undefined ? <StatusChip tone={tone}>{perDay && perDay > 0 ? t("storage.perDay", { size: bytes(perDay) }) : t("storage.trendStable")}</StatusChip> : null}
        </div>
        {/* Peil §7: lijn "Vrije ruimte" in chart-1 (niet groen) met vlak; tabel + toetsenbord via ChartFrame */}
        {chart.length >= 2
          ? <ChartFrame name="opslagtrend" summary={t("chart.trendAria")} unit={t("chart.unit.day")} caption={t("chart.gapNote")}
              points={chart.map((p) => `${p.x}: ${t("storage.free")} ${num(p.free, 1)} TB`)}
              table={{ caption: t("storage.trendTitle"), columns: [t("chart.col.day"), `${t("chart.col.free")} (TB)`], numeric: [false, true],
                rows: chart.map((p, i) => ({ key: `${p.x}-${i}`, cells: [p.x, num(p.free, 1)] })) }}>
              {(active) => <LineChart data={chart} xKey="x" max={maxTb} active={active}
                series={[{ key: "free", label: t("storage.free"), color: "var(--chart-1)", fill: true }]} />}
            </ChartFrame>
          : <p className="hint" style={{ margin: 0 }}>{t("storage.trendNoData")}</p>}
        {f?.disks?.length ? (
          <div style={{ display: "grid", gap: 8 }}>
            {f.disks.map((d) => {
              const used = d.total ? 1 - d.free / d.total : null;
              return (
                <div key={d.path}>
                  <div className="row-between" style={{ fontSize: 14, gap: 8 }}>
                    <span style={{ overflowWrap: "anywhere" }}>{d.path}</span>
                    <span className="t-3">{t("storage.freeOf", { free: bytes(d.free), total: bytes(d.total) })}{d.days_left_nl ? ` · ${d.days_left_nl}` : typeof d.days_left === "number" ? ` · ${t("storage.fullIn", { n: num(Math.round(d.days_left)) })}` : ""}</span>
                  </div>
                  <Progress value={used === null ? null : used * 100} thin tone={used !== null && used > 0.9 ? "danger" : used !== null && used > 0.8 ? "warning" : undefined} label={d.path} />
                </div>
              );
            })}
          </div>
        ) : null}
        {f?.basis_nl ? <p className="hint" style={{ margin: 0 }}>{f.basis_nl}</p> : null}
        {f?.truenas ? <TrueNasHealth info={f.truenas} /> : <p className="hint" style={{ margin: 0 }}>{t("storage.truenasHint")} <Link href="/instellingen?q=truenas">{t("storage.truenasSetup")}</Link></p>}
      </div>
    </section>
  );
}

function TrueNasHealth({ info }: { info: NonNullable<StorageForecast["truenas"]> }) {
  const { t, relative } = useI18n();
  if (info.ok === false) return <InlineState tone="warning" icon={HeartPulse}>{info.error_nl ?? t("storage.truenasOff")}</InlineState>;
  return (
    <div style={{ display: "grid", gap: 6 }} data-testid="truenas-health">
      <strong style={{ fontSize: 14 }}><HeartPulse className="ico ico-sm" aria-hidden="true" /> {t("storage.truenasTitle")}</strong>
      {(info.pools ?? []).map((p) => (
        <div key={p.name} className="row-between" style={{ fontSize: 14, gap: 8, flexWrap: "wrap" }}>
          <span>{p.name}</span>
          <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            {p.scrub_end ? <span className="t-3">{t("storage.scrub", { when: relative(p.scrub_end) })}{p.scrub_errors ? ` · ${t("storage.scrubErrors", { count: p.scrub_errors })}` : ""}</span> : null}
            <StatusChip tone={p.healthy === false || (p.scrub_errors ?? 0) > 0 ? "danger" : "success"}>{p.healthy === false ? (p.status ?? t("storage.poolBad")) : t("storage.poolOk")}</StatusChip>
          </span>
        </div>
      ))}
    </div>
  );
}
