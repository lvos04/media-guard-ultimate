"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";

import { useEncoder } from "@/components/av1/use-encoder";
import { Button, LinkButton, StatusChip } from "@/components/mg/primitives";
import type { EffectiveSetting, Instance, SystemStatus } from "@/lib/api/contracts";
import { useResource, type Resource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import type { Health } from "@/lib/health";
import { parseServerDate, useI18n } from "@/lib/i18n";
import type { ToDoRow } from "@/lib/todo";
import { cn } from "@/lib/utils";

const STATUS = {
  healthy: "status.healthy", warning: "status.degraded", danger: "status.offline",
  unknown: "status.unknown", stale: "state.staleShort", paused: "status.paused", unlinked: "overview.status.unlinked",
} as const;
type Status = keyof typeof STATUS;
interface Row {
  id: string;
  name: string;
  href: string;
  tone: Status;
  reason: string;
  at: number | null;
  stale?: boolean;
}

export const OVERVIEW_REFRESH_MS = 30_000;
export const OVERVIEW_MAX_AGE_MS = 2 * OVERVIEW_REFRESH_MS;

export function OverviewStatus({ now, core, health, todo }: { now: number; core: Resource<SystemStatus>; health: Health; todo: ToDoRow[] }) {
  const { t, num, time, relative } = useI18n();
  const encoder = useEncoder<{ status?: string }>("/health", OVERVIEW_REFRESH_MS);
  const instances = useResource<Instance[]>("/instances", undefined, OVERVIEW_REFRESH_MS);
  const configured = instances.data?.filter((instance) => !instance.archived) ?? [];
  const interval = useResource<EffectiveSetting[]>(configured.length ? "/settings/effective" : null,
    { keys: "instances.health_interval_seconds" }, 60_000);
  const seconds = interval.data?.[0]?.value;
  const instanceMaxAge = typeof seconds === "number" && seconds > 0 ? 2_000 * seconds : 120_000;
  useCoreEvents("instance.", () => void instances.reload());

  const old = (at: number | null, age = OVERVIEW_MAX_AGE_MS) => at !== null && now - at > age;
  const coreStale = core.stale || old(core.fetchedAt);
  const encoderStale = encoder.stale || old(encoder.fetchedAt);
  const instancesStale = instances.stale || old(instances.fetchedAt);
  const rows: Row[] = [{
    id: "core", name: t("system.core"), href: "/systeem",
    tone: coreStale ? "stale" : core.data ? "healthy" : "unknown",
    reason: t(core.error ? "overview.status.refreshFailed" : coreStale ? "overview.status.tooOld"
      : core.data ? "overview.status.coreReply" : "overview.status.noReading"),
    at: core.fetchedAt, stale: coreStale,
  }, {
    id: "encoder", name: t("overview.status.encoder"), href: "/av1",
    tone: encoderStale && encoder.data?.status !== "degraded" ? "stale"
      : encoder.data?.status === "ok" ? "healthy" : encoder.data?.status === "degraded" ? "warning" : "unknown",
    reason: encoderStale || !encoder.data ? t(encoder.error || encoderStale ? "overview.status.refreshFailed" : "overview.status.noReading")
      : t(encoder.data.status === "ok" ? "overview.status.encoderReply" : encoder.data.status === "degraded" ? "overview.status.encoderLimited" : "overview.status.noReading"),
    at: encoder.fetchedAt, stale: encoderStale,
  }];

  const checks = health.measurement;
  const checksStale = checks.stale || old(checks.at);
  const localErrors = checks.errors === null ? null : Math.max(0, checks.errors
    - (health.breakdown.encoder_degraded ?? 0) - (health.breakdown.instances_unreachable ?? 0));
  rows.push({
    id: "checks", name: t("overview.status.checks"), href: "/activiteit?tab=problemen",
    tone: localErrors ? "warning" : checksStale ? "stale" : localErrors === 0 && checks.complete ? "healthy" : "unknown",
    reason: localErrors !== null && (localErrors > 0 || checks.complete)
      ? t("overview.status.errorsReported", { count: num(localErrors) })
      : t(checks.failed ? "overview.status.refreshFailed" : "overview.status.noFullReading"),
    at: checks.at, stale: checksStale,
  });

  if (!configured.length) rows.push({
    id: "instances", name: t("overview.status.instances"), href: "/instellingen/instanties",
    tone: instancesStale ? "stale" : instances.data ? "unlinked" : "unknown",
    reason: t(instances.error ? "overview.status.refreshFailed" : instancesStale ? "overview.status.tooOld"
      : instances.data ? "overview.status.noneLinked" : "overview.status.noReading"),
    at: instances.fetchedAt, stale: instancesStale,
  });
  for (const instance of configured) {
    const at = instance.suspended ? instances.fetchedAt : parseServerDate(instance.last_check_at ?? "")?.valueOf() ?? null;
    // Na twee verwachte controles zonder nieuwe meting is de uitkomst verouderd.
    const stale = instancesStale || (!instance.suspended && old(at, instanceMaxAge));
    const tone: Status = stale && (instance.suspended || instance.status === "healthy") ? "stale"
      : instance.suspended ? "paused" : at === null ? "unknown"
        : instance.status === "offline" ? "danger" : instance.status === "degraded" ? "warning"
          : instance.status === "healthy" ? "healthy" : "unknown";
    rows.push({ id: instance.id, name: instance.name, href: `/instellingen/instanties#${encodeURIComponent(instance.id)}`, tone, at, stale,
      reason: stale ? t(instances.error ? "overview.status.refreshFailed" : "overview.status.tooOld")
        : tone === "paused" ? t("overview.status.paused") : tone === "unknown" ? t("overview.status.noReading")
          : instance.last_error || t("overview.status.instanceCheck") });
  }

  const count = (tone: Status) => rows.filter((row) => (row.stale ? "stale" : row.tone) === tone).length;
  const total: Status = !core.data || coreStale ? "unknown" : count("danger") ? "danger"
    : count("warning") ? "warning"
      : rows.some((row) => row.stale || row.tone === "unknown") ? "unknown" : "healthy";
  const totals = (["danger", "warning", "unknown", "stale", "healthy", "paused", "unlinked"] as const)
    .flatMap((tone) => count(tone) ? [`${num(count(tone))} ${t(STATUS[tone]).toLocaleLowerCase()}`] : []);
  const previousProblem = rows.find((row) => row.stale && (row.tone === "danger" || row.tone === "warning"));
  const needsRefresh = coreStale || encoderStale || instancesStale || checksStale || checks.failed
    || Boolean(core.error || encoder.error || instances.error);
  const refresh = () => void Promise.all([core.reload(), encoder.reload(), instances.reload(), checks.reload()]);
  const offline = core.error?.kind === "offline";

  return (
    <section aria-labelledby="overview-system-status" data-testid="status-hero" data-status={total === "warning" ? "degraded" : total === "danger" ? "down" : total}
      className={cn("min-w-0 rounded-lg border bg-card text-card-foreground",
        total === "warning" && "border-2 border-degraded", total === "danger" && "border-2 border-down",
        total === "unknown" && "border-2 border-dashed border-unknown")}>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 pt-4 pb-2">
        <h2 className="text-lg font-semibold" id="overview-system-status">{t("overview.status.title")}</h2>
        <div className="flex flex-wrap items-center gap-2">
          <span role="status">
            <span className="sr-only">{t("hero.total")} </span>
            <StatusChip tone={total} size="lg">{t(offline ? "health.offline" : STATUS[total])}</StatusChip>
          </span>
          <Button size="sm" onClick={refresh}>{t(needsRefresh ? "action.retry" : "action.reload")}</Button>
        </div>
      </div>
      <div className="space-y-1 px-4 pb-2 text-sm text-muted-foreground">
        {todo.length ? <p>{t("hero.waiting", { count: num(todo.length) })}
          {todo.some((row) => row.stale) ? ` · ${t("state.staleShort")}` : null}</p> : null}
        {totals.length ? <p>{totals.join(" · ")}</p> : null}
        {total === "unknown" && previousProblem?.at ? <p>{t("overview.status.lastProblem", { when: time(previousProblem.at), name: previousProblem.name, status: t(STATUS[previousProblem.tone]) })}</p> : null}
        <p data-testid="hero-sub">{core.data?.emergency_stop?.active
          ? t(coreStale ? "overview.status.stopLastKnown" : "hero.estop") : null}</p>
      </div>
      <ul className="divide-y divide-border border-t border-border">
        {rows.map((row) => (
          <li key={row.id}>
            <Link href={row.href} className="inline-grid min-h-touch w-full grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-3 gap-y-1 px-4 py-2 hover:bg-muted sm:grid-cols-[8rem_auto_minmax(0,1fr)_auto]">
              <span className="font-semibold [overflow-wrap:anywhere]">{row.name}</span>
              <span><StatusChip tone={row.tone}>{t(STATUS[row.tone])}</StatusChip></span>
              <span className="col-span-2 col-start-1 row-start-2 min-w-0 text-sm text-muted-foreground [overflow-wrap:anywhere] sm:col-span-1 sm:col-start-3 sm:row-start-1">
                {row.reason}
                {row.at !== null ? <> · {t(row.stale ? "overview.status.lastKnown" : "kpi.updated")} <time dateTime={new Date(row.at).toISOString()}>{time(row.at)} ({relative(row.at)})</time></> : null}
              </span>
              <ChevronRight className="col-start-3 row-start-1 size-4 sm:col-start-4" aria-hidden="true" />
            </Link>
          </li>
        ))}
      </ul>
      {todo.length ? <div className="border-t border-border px-4 py-2"><LinkButton href="#todo" variant="secondary">{t("hero.toList")}</LinkButton></div> : null}
    </section>
  );
}
