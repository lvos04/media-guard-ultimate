"use client";

import { Activity, BarChart3, CircleHelp, Copy, Cpu, RefreshCw, TestTube2 } from "lucide-react";
import { useMemo, useState } from "react";

import { errorText, useEncoder } from "@/components/av1/use-encoder";
import { ChartFrame } from "@/components/mg/chart-frame";
import { BarChart, LineChart, thin } from "@/components/mg/charts";
import { StateView } from "@/components/mg/states";
import { Button, Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { mutation } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { useLiveMetrics } from "@/lib/ws";
import type { Statistics } from "@/types";

interface GpuStatus {
  state?: string; message?: string; severity?: string; active?: boolean;
  kaart?: string | null; niet_gebruikt?: string[];
  details?: { reden?: string; hint?: string | null; pogingen?: number; geprobeerd?: string[] };
}
interface GpuAttempt { node: string; variant: string; env?: string; returncode?: number | null; geslaagd: boolean; uitvoer?: string }
interface GpuDiag { status?: GpuStatus | null; probe?: { geslaagd?: boolean; reden?: string; hint?: string; pogingen?: GpuAttempt[] } | null; kaarten?: unknown[]; render_node?: string; variant?: unknown }

interface Diagnosis {
  gpu?: GpuDiag;
  dependencies: {
    tools?: Record<string, boolean>;
    ffmpeg_version?: string | null;
    libsvtav1?: boolean;
    av1_qsv?: boolean;
    dolbyvision?: boolean;
    hdr10plus?: boolean;
    render_node?: { pad: string; aanwezig: boolean; leesbaar: boolean; schrijfbaar: boolean };
    qsv_gereed?: boolean;
  };
}

function Yes({ ok }: { ok: boolean | undefined }) {
  const { t } = useI18n();
  return ok ? <StatusChip tone="success">{t("av1.hw.yes")}</StatusChip> : <StatusChip tone="neutral" outline>{t("av1.hw.no")}</StatusChip>;
}

export function HardwareCard({ className }: { className?: string }) {
  const { t } = useI18n();
  const { can } = useSession();
  const diag = useEncoder<Diagnosis>("/systeem/diagnose");
  const [result, setResult] = useState<{ tone: "success" | "danger" | "info"; text: string } | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const d = diag.data?.dependencies;
  const node = d?.render_node;
  const hasGpu = Boolean(node?.aanwezig);
  const access = Boolean(node?.leesbaar && node?.schrijfbaar);
  const gpuStatus = (d as { gpu_status?: GpuStatus | null } | undefined)?.gpu_status ?? diag.data?.gpu?.status ?? null;
  const attempts = diag.data?.gpu?.probe?.pogingen ?? [];

  const retest = async () => {
    setPending("qsv");
    try {
      const r = await mutation<{ geslaagd?: boolean; gpu_status?: GpuStatus | null; probe?: { reden?: string } }>("/systeem/qsv-test", "POST");
      setResult(r.geslaagd ? { tone: "success", text: t("av1.hw.retestOk") }
        : { tone: "danger", text: t("av1.hw.retestBad", { reason: r.gpu_status?.details?.reden ?? r.probe?.reden ?? "?" }) });
      await diag.reload();
    } catch (e) {
      setResult({ tone: "danger", text: `${t("av1.hw.qsvFailed")}: ${errorText(e)}` });
    } finally {
      setPending(null);
    }
  };
  const copyDiag = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(diag.data?.gpu ?? { gpu_status: gpuStatus }, null, 2));
      setResult({ tone: "info", text: t("av1.hw.copied") });
    } catch { /* klembord niet beschikbaar: details blijven zichtbaar */ }
  };

  const act = async (key: string, path: string, ok: string, bad: string) => {
    setPending(key);
    try {
      await mutation(path, "POST");
      setResult({ tone: "success", text: ok });
      await diag.reload();
    } catch (e) {
      setResult({ tone: "danger", text: `${bad}: ${errorText(e)}` });
    } finally {
      setPending(null);
    }
  };

  return (
    <Card className={className} title={t("av1.hw.title")} sub={t("av1.hw.sub")} icon={Cpu}
      actions={<Button size="sm" variant="ghost" icon={RefreshCw} pending={diag.loading && Boolean(diag.data)} onClick={() => void diag.reload()}>{t("av1.hw.refresh")}</Button>}>
      {!diag.data && diag.error ? <StateView kind="offline" compact title={t("av1.engineDownTitle")} body={t("av1.engineDownBody")} />
        : !d ? <StateView kind="loading" compact />
          : (
            <div className="stack-sm">
              <ul className="checklist" data-testid="av1-hardware">
                <li><span>{t("av1.hw.gpuNode")}</span><Yes ok={hasGpu} /></li>
                {hasGpu ? <li><span>{t("av1.hw.gpuAccess")}</span><Yes ok={access} /></li> : null}
                <li><span>{t("av1.hw.qsv")}</span><Yes ok={d.qsv_gereed} /></li>
                <li><span>{t("av1.hw.svt")}</span><Yes ok={d.libsvtav1} /></li>
                <li><span>{t("av1.hw.dv")}</span><Yes ok={Boolean(d.dolbyvision && (d.tools?.dovi_tool ?? true))} /></li>
                {d.hdr10plus !== undefined ? <li><span>{t("av1.hw.hdr10plus")}</span><Yes ok={d.hdr10plus} /></li> : null}
              </ul>
              {d.ffmpeg_version ? <p className="hint" style={{ margin: 0 }}>{t("av1.hw.ffmpeg", { version: d.ffmpeg_version })}</p> : null}
              {/* één eerlijke GPU-regel van de engine; 'info' (bv. geen GPU) is neutraal, geen fout */}
              {gpuStatus?.message ? <InlineState tone={gpuStatus.severity === "warning" ? "warning" : gpuStatus.active ? "success" : "info"} role="status" data-testid="gpu-status">
                {gpuStatus.message}
                {gpuStatus.details?.reden ? <><br /><strong>{t("av1.hw.reason", { reason: gpuStatus.details.reden })}</strong></> : null}
                {gpuStatus.details?.hint ? <><br />{gpuStatus.details.hint}</> : null}
                {gpuStatus.kaart ? <><br /><span className="t-2">{t("av1.hw.card", { name: gpuStatus.kaart })}</span></> : null}
                {gpuStatus.niet_gebruikt?.length ? <><br /><span className="t-2">{t("av1.hw.unused", { list: gpuStatus.niet_gebruikt.join(", ") })}</span></> : null}
              </InlineState>
                : !hasGpu ? <InlineState tone="info"><strong>{t("av1.hw.noGpuTitle")}</strong><br />{t("av1.hw.noGpuBody")}</InlineState>
                  : !access ? <InlineState tone="warning"><strong>{t("av1.hw.noAccessTitle")}</strong><br />{t("av1.hw.noAccessBody")}</InlineState> : null}
              {result ? <InlineState tone={result.tone} role="status">{result.text}</InlineState> : null}
              {attempts.length ? (
                <details className="tech-details" data-testid="gpu-attempts"><summary>{t("state.techDetails")} · {t("av1.hw.attempts", { count: attempts.length })}</summary>
                  <ul className="stack-sm" style={{ margin: 0, paddingLeft: 16 }}>
                    {attempts.map((a, i) => (
                      <li key={i}><span className="mono" style={{ fontSize: 14 }}>{a.node.split("/").pop()} · {a.variant} · {a.env ?? ""} · {a.geslaagd ? t("av1.hw.attemptOk") : t("av1.hw.attemptBad")}{a.returncode != null ? ` (${a.returncode})` : ""}</span>
                        {a.uitvoer ? <pre className="mono t-2" style={{ fontSize: 14, whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: "2px 0 0" }}>{a.uitvoer.slice(-600)}</pre> : null}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
              {hasGpu && can("operator") ? (
                <div className="row wrap">
                  <Button size="sm" icon={TestTube2} pending={pending === "qsv"} onClick={() => void retest()}>{gpuStatus && !gpuStatus.active && gpuStatus.severity === "warning" ? t("av1.hw.retest") : t("av1.hw.qsvTest")}</Button>
                  {attempts.length || gpuStatus?.details ? <Button size="sm" variant="ghost" icon={Copy} onClick={() => void copyDiag()}>{t("av1.hw.copyDiag")}</Button> : null}
                  <Button size="sm" variant="ghost" icon={Activity} pending={pending === "gpu"} onClick={() => void act("gpu", "/systeem/gpu-herstart", t("av1.hw.gpuRestarted"), t("av1.hw.gpuRestart"))}>{t("av1.hw.gpuRestart")}</Button>
                </div>
              ) : null}
            </div>
          )}
    </Card>
  );
}

/** Legenda-item: kleurvlak + woord (een kleur staat nooit alleen). */
function LegendItem({ color, label }: { color: string; label: string }) {
  return <span className="legend-item inline-flex items-center gap-1.5 text-sm text-foreground"><span className="legend-swatch inline-block size-3 rounded-sm border border-border" style={{ background: color }} />{label}</span>;
}

/**
 * Belasting (Peil, PEIL_MIGRATIE §7, ADR-0016 punt 10): één y-as 0–100 %, hooguit drie lijnen — GPU (chart-1 =
 * domain-lane-gpu), CPU (chart-2 = domain-lane-cpu), RAM (chart-3), alle doorgetrokken; cache staat in een tweede,
 * kleine grafiek "Geheugen" (RAM + cache). Zelfde gegevens, geen nieuwe API. Tabel + toetsenbord via ChartFrame.
 */
export function LoadChart({ className, unknown }: { className?: string; unknown?: string | null }) {
  const { t, num } = useI18n();
  const metrics = useLiveMetrics();
  const data = useMemo(() => thin(metrics.slice(-720), 240), [metrics]);
  const hasCache = data.some((m) => typeof m.memoryCachePercent === "number");
  const pctOrGap = (v: number | null | undefined) => (typeof v === "number" ? `${num(v)} %` : t("chart.noReading"));
  const series = useMemo(() => [
    { key: "gpuVideo" as const, label: t("av1.chart.gpu"), color: "var(--chart-1)", fill: true },
    { key: "cpu" as const, label: t("av1.chart.cpu"), color: "var(--chart-2)" },
    { key: "memoryPercent" as const, label: t("av1.chart.ram"), color: "var(--chart-3)" },
  ], [t]);
  const memSeries = useMemo(() => [
    { key: "memoryPercent" as const, label: t("av1.chart.ram"), color: "var(--chart-3)" },
    { key: "memoryCachePercent" as const, label: t("memory.cache"), color: "var(--chart-4)" },
  ], [t]);
  const points = data.map((m) => `${m.tijd}: ${series.map((s) => `${s.label} ${pctOrGap(m[s.key])}`).join(", ")}`);
  const memPoints = data.map((m) => `${m.tijd}: ${memSeries.map((s) => `${s.label} ${pctOrGap(m[s.key])}`).join(", ")}`);
  const cols = [t("chart.col.time"), ...series.map((s) => `${s.label} (%)`)];
  return (
    <Card className={className} title={<>{t("av1.chart.load")} <span className="card-sub">{t("av1.chart.loadSub")}</span></>} icon={Activity}
      actions={<div className="legend flex flex-wrap gap-x-3.5 gap-y-1.5">{series.map((s) => <LegendItem key={s.key} color={s.color} label={s.label} />)}</div>}>
      {/* Peil §4.4: een bron die niet antwoordt is Onbekend, geen lege grafiek ("Nog geen metingen" zou niet waar zijn) */}
      {data.length < 2 && unknown ? <StateView kind="unavailable" compact icon={CircleHelp} title={unknown} body="" />
        : data.length < 2 ? <StateView kind="empty" compact title={t("av1.chart.noData")} /> : (
        <div className="flex flex-col gap-5">
          <ChartFrame name="belasting" summary={t("av1.chart.loadAria")} points={points} unit={t("chart.unit.reading")} caption={t("chart.gapNote")}
            table={{ caption: t("av1.chart.load"), columns: cols, numeric: [false, true, true, true],
              rows: data.map((m, i) => ({ key: `${m.tijd}-${i}`, cells: [m.tijd, ...series.map((s) => pctOrGap(m[s.key]))] })) }}>
            {(active) => <LineChart data={data} series={series} xKey="tijd" height={240} active={active} />}
          </ChartFrame>
          {hasCache ? (
            <div>
              <h3 className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-base font-semibold">{t("chart.memory")}
                <span className="flex flex-wrap gap-x-3.5 gap-y-1 font-normal">{memSeries.map((s) => <LegendItem key={s.key} color={s.color} label={s.label} />)}</span></h3>
              <ChartFrame name="geheugen" summary={t("chart.memoryAria")} points={memPoints} unit={t("chart.unit.reading")}
                table={{ caption: t("chart.memory"), columns: [t("chart.col.time"), ...memSeries.map((s) => `${s.label} (%)`)], numeric: [false, true, true],
                  rows: data.map((m, i) => ({ key: `${m.tijd}-${i}`, cells: [m.tijd, ...memSeries.map((s) => pctOrGap(m[s.key]))] })) }}>
                {(active) => <LineChart data={data} series={memSeries} xKey="tijd" height={140} active={active} />}
              </ChartFrame>
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
}

/** Alle dagen van `from` t/m `to` (ISO yyyy-mm-dd), zodat een dag zonder meting een plek op de as houdt. */
export function dayRange(from: string, to: string): string[] {
  const out: string[] = [];
  const start = new Date(`${from}T12:00:00Z`);
  const end = new Date(`${to}T12:00:00Z`);
  if (Number.isNaN(start.valueOf()) || Number.isNaN(end.valueOf()) || end < start) return [from];
  for (let d = start; d <= end && out.length < 400; d = new Date(d.valueOf() + 864e5)) out.push(d.toISOString().slice(0, 10));
  return out;
}

/**
 * Besparing per dag (Peil §7): staven in één tint (chart-1, niet groen), doorlopende datumas: een dag zonder meting
 * is geen staaf van 0 maar een gearceerd vak "Geen meting".
 */
export function SavingChart({ className, statistics, unknown }: { className?: string; statistics: Statistics | null; unknown?: string | null }) {
  const { t, bytes, num, locale } = useI18n();
  const day = (v: unknown) => { const d = new Date(`${String(v)}T12:00:00`); return Number.isNaN(d.valueOf()) ? String(v ?? "") : d.toLocaleDateString(locale, { day: "numeric", month: "short" }); };
  const raw = statistics?.per_dag ?? [];
  const sorted = [...raw].sort((a, b) => a.dag.localeCompare(b.dag));
  const byDay = new Map(sorted.map((r) => [r.dag, r]));
  const days = sorted.length ? dayRange(sorted[0].dag, sorted[sorted.length - 1].dag) : [];
  const rows = days.map((d) => ({ dag: d, row: byDay.get(d) ?? null }));
  const values = rows.map((r) => (r.row && typeof r.row.bespaard_bytes === "number" ? r.row.bespaard_bytes : null));
  const points = rows.map((r) => (r.row ? t("chart.savingPoint", { day: day(r.dag), saved: bytes(r.row.bespaard_bytes), jobs: num(r.row.jobs ?? 0) }) : `${day(r.dag)}: ${t("chart.noReading")}`));
  return (
    <Card className={className} title={t("av1.chart.saving")} icon={BarChart3}>
      {!raw.length && unknown ? <StateView kind="unavailable" compact icon={CircleHelp} title={unknown} body="" />
        : !raw.length ? <StateView kind="empty" compact title={t("av1.chart.noData")} /> : (
        <ChartFrame name="besparing-per-dag" summary={t("av1.chart.savingAria")} points={points} unit={t("chart.unit.day")} caption={t("chart.gapNote")}
          table={{ caption: t("av1.chart.saving"), columns: [t("chart.col.day"), t("av1.chart.saved"), t("chart.col.encodes")], numeric: [false, true, true],
            rows: rows.map((r) => ({ key: r.dag, cells: [day(r.dag), r.row ? bytes(r.row.bespaard_bytes) : t("chart.noReading"), r.row ? num(r.row.jobs ?? 0) : t("chart.noReading")] })) }}>
          {(active) => <BarChart values={values} labels={rows.map((r) => day(r.dag))} active={active} />}
        </ChartFrame>
      )}
    </Card>
  );
}
