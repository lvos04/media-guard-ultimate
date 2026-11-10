"use client";

/**
 * Kleine SVG-grafieken zonder afhankelijkheid (geen recharts; ONTWERPTAAL §10: MediaGuard houdt de eigen SVG).
 * Peil (PEIL_MIGRATIE §7): reekskleuren chart-1…5 (nooit statuskleuren), lijnen doorgetrokken (stippel = geschat),
 * vlak van 10 % alleen onder de eerste reeks, een ontbrekende meting is een gat (lijn breekt, geen staaf maar een
 * gearceerd vak), nooit 0. Labels zijn HTML (≥ 14 px). Lezen per punt gebeurt in ChartFrame (toetsenbord + aanwijzer
 * + tabel); deze componenten tekenen alleen en markeren de actieve positie met een draadkruis.
 */
import { useId, useMemo } from "react";

export interface Series<T> {
  key: keyof T & string;
  label: string;
  color: string;
  fill?: boolean;
  /** stippel = geschat (Peil T-03); gemeten reeksen zijn doorgetrokken */
  estimated?: boolean;
}

const W = 600;

/** Aaneengesloten stukken meetpunten (een ontbrekende meting breekt de lijn; nooit 0). */
function segmentsFor(values: Array<number | null>, height: number, max: number): Array<Array<[number, number]>> {
  const out: Array<Array<[number, number]>> = [];
  let cur: Array<[number, number]> | null = null;
  const n = values.length;
  values.forEach((v, i) => {
    if (v === null || v === undefined || Number.isNaN(v)) { cur = null; return; }
    const x = n > 1 ? (i / (n - 1)) * W : W / 2;
    const y = height - (Math.max(0, Math.min(max, v)) / max) * height;
    if (!cur) { cur = []; out.push(cur); }
    cur.push([x, y]);
  });
  return out;
}

const pt = ([x, y]: [number, number]) => `${x.toFixed(1)},${y.toFixed(1)}`;

/** Gaten waar géén enkele reeks een meting heeft: [x-begin, x-eind] van het laatste punt ervoor tot het eerste erna. */
function gapsFor(rows: Array<Array<number | null>>, n: number): Array<[number, number]> {
  const xOf = (i: number) => (n > 1 ? (i / (n - 1)) * W : W / 2);
  const empty = Array.from({ length: n }, (_, i) => rows.every((vals) => vals[i] === null || vals[i] === undefined || Number.isNaN(vals[i] as number)));
  const out: Array<[number, number]> = [];
  for (let i = 0; i < n; i += 1) {
    if (!empty[i]) continue;
    let j = i;
    while (j + 1 < n && empty[j + 1]) j += 1;
    out.push([xOf(Math.max(0, i - 1)), xOf(Math.min(n - 1, j + 1))]);
    i = j;
  }
  return out;
}

/** Hooguit `max` punten (houdt grafieken met 720 metingen goedkoop); de laatste meting blijft altijd. */
export function thin<T>(rows: T[], max: number): T[] {
  if (rows.length <= max) return rows;
  const step = rows.length / max;
  const out: T[] = [];
  for (let i = 0; i < max; i += 1) out.push(rows[Math.floor(i * step)]);
  out.push(rows[rows.length - 1]);
  return out;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Lijngrafiek met één y-as (0…max); rijen moeten al gedund zijn (zelfde rijen als de tabel). */
export function LineChart<T extends object>({ data, series, xKey, height = 180, max = 100, active = null, formatX }: {
  data: T[]; series: Array<Series<T>>; xKey: keyof T & string; height?: number; max?: number; active?: number | null;
  formatX?: (v: unknown) => string;
}) {
  const inner = height - 26;
  // Peil §10: de lijn breekt bij een ontbrekende meting; het vlak (10 %) volgt elk stuk afzonderlijk tot de basislijn
  // (vroeger sloot één vlak alle stukken samen en tekende het een wig over het gat heen); waar geen enkele reeks een
  // meting heeft, staat een gearceerd vak (arcering-gat).
  const paths = useMemo(() => series.map((s, i) => {
    const segs = segmentsFor(data.map((r) => num(r[s.key])), inner, max);
    const line = segs.map((seg) => seg.map((p, j) => `${j ? "L" : "M"}${pt(p)}`).join("")).join("");
    const area = i === 0 && s.fill && segs.length
      ? segs.filter((seg) => seg.length > 1).map((seg) => `M${seg[0][0].toFixed(1)},${inner}${seg.map((p) => `L${pt(p)}`).join("")}L${seg[seg.length - 1][0].toFixed(1)},${inner}Z`).join("")
      : null;
    return { s, line, area: area || null };
  }), [data, inner, max, series]);
  const gaps = useMemo(() => gapsFor(series.map((s) => data.map((r) => num(r[s.key]))), data.length), [data, series]);
  const gatId = `mg-lijn-gat-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const label = (r: T | undefined) => (r === undefined ? "" : formatX ? formatX(r[xKey]) : String(r[xKey] ?? ""));
  const ticks = data.length > 1 ? [data[0], data[Math.floor(data.length / 2)], data[data.length - 1]] : data;
  const x = active !== null && data.length > 1 ? (active / (data.length - 1)) * W : null;
  return (
    <div className="mg-chart" style={{ height }}>
      <svg viewBox={`0 0 ${W} ${inner}`} preserveAspectRatio="none" width="100%" height={inner} aria-hidden="true" className="block">
        {[0.25, 0.5, 0.75].map((f) => <line key={f} x1="0" x2={W} y1={inner * f} y2={inner * f} stroke="var(--chart-grid)" strokeWidth="1" vectorEffect="non-scaling-stroke" />)}
        <line x1="0" x2={W} y1={inner} y2={inner} stroke="var(--chart-axis)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {gaps.length ? <defs><pattern id={gatId} width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="7" stroke="var(--chart-gap)" strokeWidth="2" /></pattern></defs> : null}
        {gaps.map(([a, b], i) => <rect key={`g-${i}`} x={a} y={0} width={Math.max(2, b - a)} height={inner} fill={`url(#${gatId})`} data-gat="1" />)}
        {paths.map(({ s, area }) => area ? <path key={`a-${s.key}`} d={area} fill={s.color} fillOpacity={0.1} stroke="none" /> : null)}
        {paths.map(({ s, line }) => line ? <path key={s.key} d={line} fill="none" stroke={s.color} strokeWidth="2" strokeDasharray={s.estimated ? "4 3" : undefined} vectorEffect="non-scaling-stroke" strokeLinejoin="round" /> : null)}
        {x !== null ? <line x1={x} x2={x} y1="0" y2={inner} stroke="var(--foreground)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" /> : null}
      </svg>
      <div className="flex justify-between gap-2 pt-1 text-sm text-muted-foreground cijfers">{ticks.map((r, i) => <span key={i}>{label(r)}</span>)}</div>
    </div>
  );
}

/** Staafgrafiek, één tint (chart-1). `null` = geen meting: geen staaf maar een gearceerd vak (nooit 0). */
export function BarChart({ values, labels, height = 200, active = null, color = "var(--chart-1)" }: {
  values: Array<number | null>; labels: string[]; height?: number; active?: number | null; color?: string;
}) {
  const inner = height - 26;
  const max = Math.max(1, ...values.map((v) => v ?? 0));
  const bw = W / Math.max(1, values.length);
  const gap = Math.min(bw * 0.25, 6);
  return (
    <div className="mg-chart" style={{ height }}>
      <svg viewBox={`0 0 ${W} ${inner}`} preserveAspectRatio="none" width="100%" height={inner} aria-hidden="true" className="block">
        <defs>
          <pattern id="mg-gat" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="7" stroke="var(--chart-gap)" strokeWidth="2" />
          </pattern>
        </defs>
        <line x1="0" x2={W} y1={inner} y2={inner} stroke="var(--chart-axis)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {values.map((v, i) => {
          if (v === null) return <rect key={i} x={i * bw + gap / 2} y={0} width={bw - gap} height={inner} fill="url(#mg-gat)" data-gat="1" />;
          const h = (v / max) * inner;
          return <rect key={i} x={i * bw + gap / 2} y={inner - h} width={bw - gap} height={h} rx="2" fill={color} stroke={active === i ? "var(--foreground)" : "none"} strokeWidth={active === i ? 2 : 0} vectorEffect="non-scaling-stroke" />;
        })}
      </svg>
      <div className="flex justify-between gap-2 pt-1 text-sm text-muted-foreground cijfers">
        {labels.length ? (labels.length === 1 ? [labels[0]] : [labels[0], labels[labels.length - 1]]).map((l, i) => <span key={i}>{l}</span>) : null}
      </div>
    </div>
  );
}
