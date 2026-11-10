"use client";

import { ArrowRight, Check, CircleCheck, CircleX, Cpu, Info, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { TableToggle } from "@/components/mg/chart-frame";
import { Button, Field, Modal, Progress, StatusChip, type Tone } from "@/components/mg/primitives";
import type { Instance, InstanceStatus } from "@/lib/api/contracts";
import { DISTRIBUTION_KEYS, type DistributionKey, type EpisodeCellState, type EvidenceView, type JobLane, type QualityInfo, type SeasonRow } from "@/lib/viewmodels";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// ------------------------------------------------------------------ KPI
/**
 * StatKaart (Peil C-05): de waarde staat altijd in inkt (nooit statuskleur); een status komt als badge ernaast.
 * `unknown` = de bron antwoordt niet (Onbekend + reden, stippelpil); `unavailable` = nog geen waarde (laden of een
 * fase). `tone` blijft voor API-compatibiliteit maar kleurt de waarde niet meer.
 */
export function KpiCard({ label, icon: Icon, value, unit, meta, href, drill, tone, spark, unavailable, staleNote, unknown, badge, source, updatedAt }: {
  label: string; icon: LucideIcon; value: ReactNode; unit?: string; meta?: ReactNode; href?: string; drill?: string;
  tone?: "success" | "warning" | "danger"; spark?: number[]; unavailable?: boolean;
  /** Laatst bekende waarde (verversen mislukte): bv. "bijgewerkt 2 min geleden" (R14). */
  staleNote?: string | null;
  /** Bron antwoordt niet: "Onbekend — AV1-engine reageert niet". */
  unknown?: string | null;
  /** Status naast de waarde (StatusBadge), alleen als er iets is. */
  badge?: ReactNode;
  /** Peil C-05/§4.4: waar het getal vandaan komt en wanneer het is opgehaald (Bron · Bijgewerkt). */
  source?: string;
  updatedAt?: number | null;
}) {
  const { t, time, relative } = useI18n();
  const off = unavailable || Boolean(unknown);
  const body = (
    <>
      <span className="kpi-label"><Icon className="ico" aria-hidden="true" />{label}</span>
      {/* Peil §4.4: onbekend of (nog) geen waarde = geen getal, geen 0 en geen "—"; de Onbekend-pil of het skelet
          staat op die plek. */}
      {unknown ? null
        : unavailable ? <span className="kpi-value"><span className="skeleton inline-block h-8 w-20 rounded-md align-middle" aria-hidden="true" /></span>
          : <span className="kpi-value num">{value}{unit ? <small>{unit}</small> : null}</span>}
      {unknown ? <span className="kpi-badge"><StatusChip tone="unknown">{unknown}</StatusChip></span> : badge ? <span className="kpi-badge">{badge}</span> : null}
      <span className="kpi-meta">{unknown ? null : unavailable ? t("state.loading") : meta}{staleNote && !off ? <span className="kpi-stale"> · {staleNote}</span> : null}</span>
      {spark && spark.length > 1 && !off ? <Sparkline values={spark} /> : null}
      {source || updatedAt ? (
        <dl className="kpi-bron">
          {source ? <div><dt>{t("kpi.source")}</dt><dd>{source}</dd></div> : null}
          {updatedAt ? <div><dt>{t("kpi.updated")}</dt><dd><time dateTime={new Date(updatedAt).toISOString()}>{time(updatedAt)} ({relative(updatedAt)})</time></dd></div> : null}
        </dl>
      ) : null}
      {href && drill ? <span className="kpi-drill">{drill} <ArrowRight className="ico ico-sm" aria-hidden="true" /></span> : null}
    </>
  );
  const cls = cn("kpi", tone && !off && `kpi--${tone}`, spark && spark.length > 1 && !off && "kpi--spark");
  return href ? <Link href={href} className={cls}>{body}</Link> : <div className={cls}>{body}</div>;
}

export function Sparkline({ values }: { values: number[] }) {
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 92},${30 - ((v - min) / span) * 26}`).join(" ");
  return <svg className="kpi-spark" viewBox="0 0 92 32" preserveAspectRatio="none" fill="none" aria-hidden="true"><polyline points={pts} vectorEffect="non-scaling-stroke" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

// ------------------------------------------------------------------ Instance
// Peil: gemeten gezondheid van een koppeling (Gezond/Beperkt/Storing/Onbekend); alleen "healthy" is groen
export const INSTANCE_TONE: Record<InstanceStatus, Tone> = { healthy: "healthy", degraded: "warning", offline: "danger", unknown: "unknown" };
const INSTANCE_LABEL: Record<InstanceStatus, MessageKey> = { healthy: "status.healthy", degraded: "status.degraded", offline: "status.offline", unknown: "status.unknown" };

export function InstanceStatusChip({ instance }: { instance: Pick<Instance, "status" | "suspended"> }) {
  const { t } = useI18n();
  if (instance.suspended) return <StatusChip tone="paused">{t("status.paused")}</StatusChip>;
  return <StatusChip tone={INSTANCE_TONE[instance.status]}>{t(INSTANCE_LABEL[instance.status])}</StatusChip>;
}

export function InstanceLogo({ instance }: { instance: Pick<Instance, "app" | "name"> }) {
  const initials = instance.name.split(/\s+/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();
  return <span className={cn("instance-logo", `instance-logo--${instance.app}`)} aria-hidden="true">{initials}</span>;
}

export function InstanceCard({ instance, actions, stats }: { instance: Instance; actions?: ReactNode; stats?: { items?: number; missing?: number } }) {
  const { t, num, relative } = useI18n();
  return (
    <article id={instance.id} className={cn("instance", instance.status === "degraded" && "instance--degraded", instance.status === "offline" && "instance--offline")} data-testid={`instance-${instance.id}`}>
      <div className="instance-head">
        <InstanceLogo instance={instance} />
        <div className="grow"><div className="instance-name">{instance.name}</div><div className="instance-id">{instance.id}</div></div>
        <InstanceStatusChip instance={instance} />
      </div>
      <div className="instance-stats">
        <div><span>{t("instances.items")}</span><strong>{num(stats?.items)}</strong></div>
        <div><span>{t("instances.missing")}</span><strong>{num(stats?.missing)}</strong></div>
        <div><span>{t("instances.checked")}</span><strong>{instance.last_check_at ? relative(instance.last_check_at) : "—"}</strong></div>
      </div>
      <dl className="kv" style={{ fontSize: 14 }}>
        <dt>{t("instances.root")}</dt><dd className="mono">{instance.default_root_folder ?? "—"}</dd>
        <dt>{t("instances.url")}</dt><dd className="mono">{instance.url}</dd>
        <dt>{t("instances.version")}</dt><dd className="mono">{instance.version ?? "—"}</dd>
        <dt>{t("instances.apiKey")}</dt><dd>{instance.api_key_valid === null ? "—" : instance.api_key_valid ? t("instances.apiKeyValid") : t("instances.apiKeyInvalid")}</dd>
      </dl>
      {instance.last_error ? <p className="t-danger" style={{ fontSize: 14 }}>{instance.last_error}</p> : null}
      {actions ? <div className="row wrap">{actions}</div> : null}
    </article>
  );
}

// ------------------------------------------------------------------ Quality ribbon / poster
export function QualityRibbon({ q }: { q?: QualityInfo | null }) {
  const { t } = useI18n();
  if (!q) return null;
  const parts: Array<[string, string | undefined]> = [];
  if (q.resolution) parts.push([q.resolution, undefined]);
  if (q.source) parts.push([q.source, undefined]);
  if (q.codec) parts.push([q.codec, q.codec.toUpperCase() === "AV1" ? "q-av1" : undefined]);
  if (q.dv_profile) parts.push([`DV ${q.dv_profile}`, "q-dv"]);
  else if (q.hdr && q.hdr !== "SDR") parts.push([q.hdr, "q-hdr"]);
  const langs = (q.audio_languages ?? []).map((l) => l.toUpperCase()).join("·");
  if (langs) parts.push([langs, "q-lang"]);
  const label = t("quality.aria", { value: parts.map((p) => p[0]).join(", ") });
  return <span className="qribbon" aria-label={label} title={label}>{parts.map(([text, cls]) => <span key={text} className={cls} aria-hidden="true">{text}</span>)}</span>;
}

function hashHue(text: string): number {
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) % 360;
  return h;
}

/** Kleinste bruikbare variant van een poster-URL (TMDB 'original' is 2000×3000, ~1 MB). */
export function posterSrc(url: string, size?: "md" | "lg" | "xl"): string {
  const tmdb = size === "lg" || size === "xl" ? "w342" : size === "md" ? "w185" : "w92";
  if (/image\.tmdb\.org\/t\/p\/original\//.test(url)) return url.replace("/t/p/original/", `/t/p/${tmdb}/`);
  // TVDB v4-artwork heeft een kleine variant met '_t'; bij een fout valt <img> terug op het origineel
  if (!size && /artworks\.thetvdb\.com\/banners\/v4\/.+\.(jpe?g|png)$/i.test(url) && !/_t\.(jpe?g|png)$/i.test(url)) {
    return url.replace(/\.(jpe?g|png)$/i, "_t.$1");
  }
  return url;
}

const POSTER_PX: Record<string, [number, number]> = { md: [64, 96], lg: [120, 180], xl: [160, 240] };

export function Poster({ title, url, size }: { title: string; url?: string | null; size?: "md" | "lg" | "xl" }) {
  const hue = hashHue(title);
  const [w, h] = POSTER_PX[size ?? ""] ?? [36, 54];
  return (
    <span className={cn("poster", size && `poster--${size}`)} style={{ ["--p1" as string]: `hsl(${hue} 38% 30%)`, ["--p2" as string]: `hsl(${(hue + 30) % 360} 40% 9%)` }}>
      <span className="poster-art" />
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element -- posters komen van TMDB/TVDB; kleine variant via posterSrc
        <img src={posterSrc(url, size)} alt="" loading="lazy" decoding="async" fetchPriority="low" width={w} height={h} style={{ position: "relative" }}
          // tweede mislukking: geen kapot-plaatje-icoon, de gegenereerde posterachtergrond blijft zichtbaar (alleen uiterlijk)
          onError={(e) => { const img = e.currentTarget; if (!img.dataset.retried && img.src !== url) { img.dataset.retried = "1"; img.src = url; } else img.style.visibility = "hidden"; }} />
      ) : size === "lg" || size === "xl" ? <span className="poster-fallback">{title}</span> : null}
    </span>
  );
}

export function MediaRow({ title, sub, poster, href }: { title: string; sub?: ReactNode; poster?: string | null; href?: string }) {
  const content = (
    <div className="media-row">
      <Poster title={title} url={poster} />
      <div className="media-meta"><div className="media-title">{title}</div>{sub ? <div className="media-sub">{sub}</div> : null}</div>
    </div>
  );
  return href ? <Link href={href}>{content}</Link> : content;
}

// ------------------------------------------------------------------ Season heatmap (Episodekaart)
const CELL_LABEL: Record<EpisodeCellState, MessageKey> = {
  ok: "heat.ok", av1: "heat.av1", missing: "heat.missing", searching: "heat.searching", future: "heat.future", unmonitored: "heat.unmonitored", uncertain: "heat.uncertain",
};

/**
 * Episodekaart (Peil §7): toestand per aflevering met kleur ÉN vorm (grafiekslots chart-1…3, geen statuskleuren) —
 * Binnen vol chart-1 · AV1 vol chart-3 · Ontbreekt vol chart-2 · Zoekt rand chart-2 · Verwacht dunne rand ·
 * Niet gevolgd streep · Onzeker stippelrand + "?". De cellen blijven links (Tab); de tabel is de tweeling.
 */
export function SeasonHeatmap({ rows, labelWidth = 64 }: { rows: SeasonRow[]; labelWidth?: number }) {
  const { t, pct } = useI18n();
  return (
    <div data-testid="grafiek-episodekaart">
      <div className="heatmap" role="group" aria-label={t("chart.episodesAria")}>
        {rows.map((row) => (
          <div className="heat-row" key={row.label} style={{ gridTemplateColumns: `${labelWidth}px minmax(0,1fr) 52px` }}>
            <span className="heat-label" title={row.title}>{row.href ? <Link href={row.href}>{row.label}</Link> : row.label}</span>
            <span className="heat-cells">
              {row.cells.map((cell, i) => {
                const aria = `${cell.label} ${t(CELL_LABEL[cell.state])}`;
                return cell.href
                  ? <Link key={i} href={cell.href} className={`heat-cell heat-cell--${cell.state}`} aria-label={aria} title={aria} />
                  : <span key={i} className={`heat-cell heat-cell--${cell.state}`} role="img" aria-label={aria} title={aria} />;
              })}
            </span>
            <span className="heat-pct">{row.percent === null ? "—" : pct(row.percent)}</span>
          </div>
        ))}
      </div>
      <div className="legend mt-3.5 flex flex-wrap gap-x-3.5 gap-y-1.5 text-sm">
        {(Object.keys(CELL_LABEL) as EpisodeCellState[]).map((s) => (
          <span className="legend-item inline-flex items-center gap-1.5" key={s}><span className={`legend-swatch heat-cell heat-cell--${s}`} />{t(CELL_LABEL[s])}</span>
        ))}
      </div>
      <TableToggle testid="grafiek-episodekaart-tabel" table={{
        caption: t("chart.episodesAria"), columns: [t("chart.col.season"), t("chart.col.episode"), t("chart.col.state")],
        rows: rows.flatMap((row) => row.cells.map((cell, i) => ({ key: `${row.label}-${i}`, cells: [row.label, cell.label, t(CELL_LABEL[cell.state])] }))),
      }} />
    </div>
  );
}

// ------------------------------------------------------------------ Evidence / trace / diff
export function Confidence({ value, tone = "success" }: { value: number | null; tone?: "success" | "danger" | "warning" }) {
  const { num } = useI18n();
  if (value === null) return null;
  // Peil T-24: zekerheid is inkt (mini-meter), nooit groen/amber/rood; `tone` blijft alleen voor API-compatibiliteit
  void tone;
  return <span className="confidence">{num(value, 2)}<span className="bar"><span style={{ width: `${Math.round(value * 100)}%`, background: "var(--foreground)" }} /></span></span>;
}

export function EvidenceCard({ item }: { item: EvidenceView }) {
  const { t, relative } = useI18n();
  const verdict = item.verdict === "confirms"
    ? <StatusChip tone="success">{t("evidence.confirms")}</StatusChip>
    : item.verdict === "contradicts" ? <StatusChip tone="danger">{t("evidence.contradicts")}</StatusChip>
      : <StatusChip tone="neutral" icon={Info}>{t("evidence.neutral")}</StatusChip>;
  return (
    <article className="evidence">
      <div className="evidence-head">{verdict}<span className="evidence-source">{item.source}</span><span className="grow" /><Confidence value={item.confidence} tone={item.verdict === "contradicts" ? "danger" : "success"} /></div>
      <dl className="evidence-body">
        {item.matching_id ? <><dt>{t("evidence.matchingId")}</dt><dd className="mono">{item.matching_id}</dd></> : null}
        {item.claim ? <><dt>{t("evidence.claim")}</dt><dd>{item.claim}</dd></> : null}
        {item.method ? <><dt>{t("evidence.method")}</dt><dd>{item.method}</dd></> : null}
        {item.trace?.length ? <><dt>{t("evidence.trace")}</dt><dd><Trace steps={item.trace.map((s, i) => ({ label: s, active: i === item.trace!.length - 1 }))} /></dd></> : null}
        {item.checked_at ? <><dt>{t("evidence.checked")}</dt><dd>{relative(item.checked_at)}</dd></> : null}
      </dl>
      {item.raw !== undefined && item.raw !== null ? (
        <details className="raw"><summary>{t("evidence.raw")}</summary><pre>{JSON.stringify(item.raw, null, 2)}</pre></details>
      ) : null}
    </article>
  );
}

export function Trace({ steps }: { steps: Array<{ label: ReactNode; active?: boolean; unset?: boolean }> }) {
  return (
    <span className="trace">
      {steps.map((s, i) => (
        <span key={i} className="row" style={{ gap: 6 }}>
          {i > 0 ? <span className="trace-arrow" aria-hidden="true">→</span> : null}
          <span className={cn("trace-step", s.active && "trace-step--active", s.unset && "trace-step--unset")} aria-current={s.active ? "true" : undefined}>{s.label}</span>
        </span>
      ))}
    </span>
  );
}

export function DiffViewer({ rows, headers, keyWidth = 120 }: {
  rows: Array<{ key: string; before: ReactNode; after: ReactNode; change?: "changed" | "removed" | "added" | "same" }>;
  headers: [string, string, string]; keyWidth?: number;
}) {
  return (
    <div className="diff" style={{ gridTemplateColumns: `${keyWidth}px 1fr 1fr` }} role="table" aria-label={headers.join(" / ")}>
      {headers.map((h) => <div key={h} className="diff-head" role="columnheader">{h}</div>)}
      {rows.map((r) => {
        const changed = r.change && r.change !== "same";
        return (
          <div key={r.key} style={{ display: "contents" }} role="row">
            <div className="diff-key" role="rowheader">{r.key}</div>
            <div className={cn(changed && r.change === "removed" && "diff-removed", changed && r.change === "changed" && "")} role="cell">{r.before ?? "—"}</div>
            <div className={cn(r.change === "changed" && "diff-changed", r.change === "added" && "diff-added", r.change === "removed" && "diff-added")} role="cell">{r.after ?? "—"}</div>
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Distribution (dry-run)
// Peil §7: vaste slot per deel (de kleur volgt het deel, verspringt nooit): AV1 1 · Vervangen 2 · Zoeken 3 ·
// Behouden 4 · Onzeker 5 · Overslaan = Overig (chart-other + arcering). Geen status- of violette kleuren.
const DIST_COLOR: Record<DistributionKey, string> = {
  AV1: "var(--chart-1)", vervangen: "var(--chart-2)", zoeken: "var(--chart-3)", behouden: "var(--chart-4)", onzeker: "var(--chart-5)", overslaan: "var(--chart-other)",
};
const DIST_LABEL: Record<DistributionKey, MessageKey> = {
  behouden: "dist.keep", AV1: "dist.av1", vervangen: "dist.replace", zoeken: "dist.search", onzeker: "dist.uncertain", overslaan: "dist.skip",
};

/** Verdeling: dunne segmentbalk zonder tekst (aria-hidden) + legenda met aantallen + tabel (Deel · Aantal · Aandeel). */
export function DistributionBar({ distribution }: { distribution: Partial<Record<string, number>> }) {
  const { t, num, pct } = useI18n();
  const sum = DISTRIBUTION_KEYS.reduce((acc, k) => acc + (distribution[k] ?? 0), 0);
  const total = sum || 1;
  return (
    <div data-testid="grafiek-verdeling">
      <div className="stackbar flex h-2.5 gap-0.5 overflow-hidden rounded-full" aria-hidden="true">
        {DISTRIBUTION_KEYS.map((k) => (distribution[k] ?? 0) > 0
          ? <span key={k} className={cn("h-full", k === "overslaan" && "arcering-gat")} style={{ width: `${((distribution[k] ?? 0) / total) * 100}%`, backgroundColor: DIST_COLOR[k] }} />
          : null)}
      </div>
      <div className="legend mt-2.5 flex flex-wrap gap-x-3.5 gap-y-1.5 text-sm">
        {DISTRIBUTION_KEYS.map((k) => <span className="legend-item inline-flex items-center gap-1.5" key={k}><span className={cn("legend-swatch inline-block size-3 rounded-sm border border-border", k === "overslaan" && "arcering-gat")} style={{ backgroundColor: DIST_COLOR[k] }} />{t(DIST_LABEL[k])} <strong className="cijfers">{num(distribution[k] ?? 0)}</strong></span>)}
      </div>
      <TableToggle testid="grafiek-verdeling-tabel" table={{
        caption: t("chart.distAria"), columns: [t("chart.col.part"), t("chart.col.count"), t("chart.col.share")], numeric: [false, true, true],
        rows: DISTRIBUTION_KEYS.map((k) => ({ key: k, cells: [t(DIST_LABEL[k]), num(distribution[k] ?? 0), sum ? pct(((distribution[k] ?? 0) / sum) * 100, 1) : "—"] })),
      }} />
    </div>
  );
}

// ------------------------------------------------------------------ Job lane
export function JobLaneCard({ lane }: { lane: JobLane }) {
  const { t, num, duration } = useI18n();
  const idle = !lane.file;
  return (
    <div className={cn("job-card", idle && "job-card--idle")}>
      <div className="row-between">
        <span className={cn("job-lane", `job-lane--${lane.lane}`)}><Cpu className="ico ico-sm" aria-hidden="true" />{lane.lane.toUpperCase()} · {lane.encoder}</span>
        {!idle ? <span className="num t-2" style={{ fontSize: 14 }}>{num(lane.percent, 1)}%</span> : null}
      </div>
      {idle ? <div className="t-3" style={{ fontSize: 14 }}>{t("jobs.laneIdle")}</div> : (
        <>
          <div className="row-between"><span className="job-file">{lane.file}</span><QualityRibbon q={lane.quality} /></div>
          <Progress value={lane.percent} tone={lane.lane === "cpu" ? "cpu" : undefined} label={lane.file ?? ""} />
          <div className="job-stats">
            <div className="job-stat"><span>FPS</span><strong className={lane.stale ? "t-3" : undefined}>{num(lane.fps, 1)}</strong></div>
            <div className="job-stat"><span>ETA</span><strong>{duration(lane.eta_seconds)}</strong></div>
            <div className="job-stat"><span>{t("jobs.bitrate")}</span><strong>{lane.bitrate_kbps ? `${num(lane.bitrate_kbps / 1000, 1)} Mb/s` : "—"}</strong></div>
            <div className="job-stat"><span>{t("jobs.saving")}</span><strong className="t-success">{lane.saving_percent === null ? "—" : `−${num(lane.saving_percent)}%`}</strong></div>
          </div>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Stepper / timeline
export function Stepper({ steps }: { steps: Array<{ label: string; status: "done" | "current" | "warning" | "blocked" | "todo"; meta?: ReactNode; onClick?: () => void }> }) {
  return (
    <ol className="stepper" style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {steps.map((s, i) => (
        <li key={s.label} className={cn("step", s.status !== "todo" && `step--${s.status}`)} aria-current={s.status === "current" ? "step" : undefined}>
          <span className="step-no">{s.status === "done" ? <Check className="ico ico-sm" aria-hidden="true" /> : i + 1}</span>
          {s.onClick ? <button type="button" className="btn--ghost" style={{ border: 0, background: "none", padding: 0, textAlign: "left", font: "inherit", color: "inherit" }} onClick={s.onClick}>{s.label}</button> : <span>{s.label}</span>}
          <span>{s.meta}</span>
        </li>
      ))}
    </ol>
  );
}

export function Timeline({ items }: { items: Array<{ title: ReactNode; meta?: ReactNode; tone?: "success" | "danger" | "warning" | "accent" | "neutral"; icon?: LucideIcon }> }) {
  return (
    <div className="timeline">
      {items.map((item, i) => {
        const Icon = item.icon ?? (item.tone === "success" ? CircleCheck : item.tone === "danger" ? CircleX : Info);
        return (
          <div className="tl-item" key={i}>
            <span className={cn("tl-dot", item.tone && item.tone !== "neutral" && `tl-dot--${item.tone}`)}><Icon className="ico" aria-hidden="true" /></span>
            <div><div className="tl-title">{item.title}</div>{item.meta ? <div className="tl-meta">{item.meta}</div> : null}</div>
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Danger confirm
export function DangerDialog({ open, onClose, onConfirm, title, description, impact, facts, confirmLabel, confirmWord, pending, error, icon, children }: {
  open: boolean; onClose: () => void; onConfirm: () => void; title: string; description: ReactNode;
  impact?: Array<{ label: string; value: ReactNode }>; facts?: Array<{ label: string; value: ReactNode }>;
  confirmLabel: string; confirmWord?: string; pending?: boolean; error?: string | null; icon?: LucideIcon; children?: ReactNode;
}) {
  const { t } = useI18n();
  const [typed, setTyped] = useState("");
  const ok = !confirmWord || typed.trim() === confirmWord;
  const close = () => { setTyped(""); onClose(); };
  return (
    <Modal
      open={open}
      onClose={close}
      danger
      role="alertdialog"
      icon={icon}
      title={title}
      description={description}
      footer={<><Button onClick={close} data-autofocus>{t("action.cancel")}</Button><Button variant="danger-solid" onClick={onConfirm} disabled={!ok} pending={pending}>{confirmLabel}</Button></>}
    >
      {impact?.length ? <div className="impact">{impact.map((i) => <div key={i.label}><span>{i.label}</span><strong>{i.value}</strong></div>)}</div> : null}
      {facts?.length ? <dl className="kv">{facts.map((f) => <div key={f.label} style={{ display: "contents" }}><dt>{f.label}</dt><dd>{f.value}</dd></div>)}</dl> : null}
      {children}
      {confirmWord ? (
        <div style={{ marginTop: 14 }}>
          <Field label={t("dialog.typeToConfirm", { word: confirmWord })} htmlFor="danger-confirm">
            <input id="danger-confirm" className="input" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </Field>
        </div>
      ) : null}
      {error ? <div className="field-error" role="alert" style={{ marginTop: 12 }}><CircleX className="ico ico-sm" aria-hidden="true" />{error}</div> : null}
    </Modal>
  );
}

/** Button that is disabled when the endpoint it needs is not yet offered by the backend. */
export function PendingAction({ endpoint, children, icon, variant = "secondary", size }: { endpoint: string; children: ReactNode; icon?: LucideIcon; variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" }) {
  const { t } = useI18n();
  return <Button variant={variant} size={size} icon={icon} disabled title={t("state.backendRequired", { endpoint })} aria-disabled="true">{children}</Button>;
}
