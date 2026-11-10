"use client";

import { AwaitingActivation } from "@/components/av1/awaiting-activation";
import { Av1PlanLink } from "@/components/optimization/av1-plan-link";
import { CircleAlert, Gauge, ListOrdered, PiggyBank } from "lucide-react";
import { Suspense, useEffect, useMemo, useState } from "react";

import { JobDrawer } from "@/components/av1/job-drawer";
import { HardwareCard, LoadChart, SavingChart } from "@/components/av1/system";
import { useEncoder } from "@/components/av1/use-encoder";
import { LaneSection } from "@/components/av1/workers";
import { KpiCard } from "@/components/mg/domain";
import { StorageLedger } from "@/components/mg/storage-ledger";
import { InlineState, StatusChip } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useI18n } from "@/lib/i18n";
import { useLive } from "@/lib/ws";
import { av1Errors, forecastText } from "@/lib/forecast";
import { useHealth } from "@/lib/health";
import type { Lane, LiveWorker, Statistics } from "@/types";

export default function Av1OverviewPage() {
  const { t, bytes, num, relative } = useI18n();
  const health = useHealth();
  const { tick, verbonden } = useLive();
  const stats = useEncoder<Statistics>("/statistieken", 60_000);
  const [open, setOpen] = useState<number | null>(null);

  const lanes = useMemo(() => {
    const by: Record<Lane, LiveWorker[]> = { gpu: [], cpu: [] };
    for (const w of tick?.workers ?? []) by[w.baan]?.push(w);
    for (const lane of ["gpu", "cpu"] as Lane[]) by[lane].sort((a, b) => (a.slot ?? 0) - (b.slot ?? 0));
    return by;
  }, [tick]);

  const q = tick?.wachtrij;
  const s = stats.data;
  const sys = tick?.systeem;
  const stale = stats.stale && stats.fetchedAt ? t("state.updatedAgo", { when: relative(stats.fetchedAt) }) : null;
  // zelfde telling als het Overzicht (gezondheidsmodel); zonder model de live-teller
  const errorCount = health.loaded ? av1Errors(health.breakdown) : (q?.fouten ?? 0);
  const cachePaused = sys?.cache_status === "pauzegrens";
  const cacheWarn = sys && sys.cache_status !== "voldoende" && !cachePaused;
  // Peil: de engine antwoordt niet → Onbekend + reden (niet "Nog niet beschikbaar", dat is een fase)
  const engineDown = !s && Boolean(stats.error) ? t("kpi.engineUnknown") : null;
  const liveDown = !q && engineDown ? engineDown : null;

  return (
    <div className="stack">
      <AwaitingActivation />
      <Av1PlanLink />
      <section className="kpi-grid" aria-label={t("av1.tab.overview")}>
        <KpiCard icon={ListOrdered} label={t("av1.kpi.queue")} value={num(q?.wachtend)} unavailable={!q} unknown={liveDown} source={t("kpi.source.engineLive")}
          meta={t("av1.kpi.queueMeta", { busy: num(q?.bezig ?? 0), done: num(q?.klaar_vandaag ?? 0) })} href="/av1/wachtrij?status=wacht" drill={t("av1.tab.queue")} />
        <KpiCard icon={PiggyBank} label={t("av1.kpi.saved")} value={bytes(s?.totaal.bespaard_bytes ?? 0)} unavailable={!s} unknown={engineDown} staleNote={stale} source={t("kpi.source.engine")} updatedAt={stats.fetchedAt}
          meta={s?.wacht_op_activatie?.jobs
            ? t("av1.kpi.savedPending", { n: num(s.wacht_op_activatie.jobs), bytes: bytes(s.wacht_op_activatie.besparing_bytes) })
            : t("av1.kpi.savedMeta", { pct: num(s?.totaal.gemiddeld ?? 0, 1) })} />
        <KpiCard icon={Gauge} label={t("av1.kpi.throughput")} value={bytes(s?.doorvoer_bytes_per_dag_7d ?? 0)} unit={t("av1.kpi.perDay")} unavailable={!s} unknown={engineDown} staleNote={stale} source={t("kpi.source.engine")} updatedAt={stats.fetchedAt}
          meta={forecastText(t, { ...s, resterend_bytes: (q?.wachtend ?? 1) === 0 && !(q?.bezig) ? 0 : s?.resterend_bytes },
            Math.max(0, ...(tick?.workers ?? []).map((w) => w.eta_seconden ?? 0)))} />
        <KpiCard icon={CircleAlert} label={t("av1.kpi.errors")} value={num(errorCount)} unavailable={!q && !health.loaded} unknown={!q && !health.loaded && health.level === "offline" ? t("health.offline") : null} source={t("kpi.source.health")} badge={errorCount ? <StatusChip tone="danger">{t("status.failed")}</StatusChip> : null}
          meta={health.loaded && health.errors > errorCount ? t("av1.kpi.errorsOfTotal", { total: num(health.errors) }) : t("av1.kpi.errorsMeta")} href="/av1/wachtrij?status=mislukt" drill={t("av1.kpi.errorsDrill")} />
      </section>

      {sys?.opslagfout ? <InlineState tone="danger" role="alert"><strong>{t("av1.notice.storage")}</strong><br />{t("av1.notice.storageBody", { error: sys.opslagfout })}</InlineState> : null}
      {sys && (cachePaused || cacheWarn) ? (
        <InlineState tone={cachePaused ? "warning" : "info"} role="status">
          <strong>{t(cachePaused ? "av1.notice.cache" : "av1.notice.cacheWarn")}</strong><br />
          {t("av1.notice.cacheBody", { free: bytes(sys.schijf_beschikbaar_na_reservering), warn: num(sys.effectieve_cache_waarschuwing_gb), pause: num(sys.effectieve_cache_pauze_gb) })}
        </InlineState>
      ) : null}

      <div className="grid">
        {!tick ? (
          <div className="span-12 card"><div className="card-body">{verbonden ? <StateView kind="loading" compact /> : <InlineState tone="info" role="status">{t("av1.live.connecting")}</InlineState>}</div></div>
        ) : (
          <>
            <LaneSection lane="gpu" workers={lanes.gpu} onOpen={setOpen} status={laneStatus(tick, "gpu")} />
            <LaneSection lane="cpu" workers={lanes.cpu} onOpen={setOpen} status={laneStatus(tick, "cpu")} />
          </>
        )}
        <LoadChart className="span-8" unknown={engineDown} />
        <HardwareCard className="span-4" />
        <SavingChart className="span-12" statistics={s} unknown={engineDown} />
      </div>
      {/* Waar ging de ruimte heen: vroeger Activiteit → Opslag (oude links komen hier uit via #opslag) */}
      <section id="opslag" className="settings-section" aria-label={t("space.storage")}>
        <Suspense><StorageLedger /></Suspense>
      </section>
      <ScrollToHash />
      {open !== null ? <JobDrawer jobId={open} onClose={() => setOpen(null)} /> : null}
    </div>
  );
}

/** #opslag: de pagina bouwt zich live op, dus na een korte pauze nog eens naar de sectie scrollen. */
function ScrollToHash() {
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id) return;
    const timer = window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ block: "start" }), 600);
    return () => window.clearTimeout(timer);
  }, []);
  return null;
}

/** Eén statusregel per baan uit de live-stand (tick.banen); niet per plek herhalen. */
function laneStatus(tick: unknown, lane: "gpu" | "cpu"): string | null {
  const banen = (tick as { banen?: Array<{ baan: string; status?: string }> } | null)?.banen;
  return banen?.find((b) => b.baan === lane)?.status ?? null;
}
