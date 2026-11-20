"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";

import { useEncoder } from "@/components/av1/use-encoder";
import { Button, LinkButton, StatusChip } from "@/components/mg/primitives";
import type { EffectiveSetting, Instance, SystemStatus } from "@/lib/api/contracts";
import { useResource, type Resource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import type { Health } from "@/lib/health";
import { ReadingMark } from "@/components/mg/reading-mark";
import { useI18n } from "@/lib/i18n";
import { useTodoLabel, type TodoList } from "@/lib/todo";
import { OVERVIEW_REFRESH_MS, sourceReading } from "@/lib/reading";
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

export function OverviewStatus({ now, core, health, todo }: { now: number; core: Resource<SystemStatus>; health: Health; todo: TodoList }) {
  const { t, num, time, relative } = useI18n();
  const encoder = useEncoder<{ status?: string }>("/health", OVERVIEW_REFRESH_MS);
  const instances = useResource<Instance[]>("/instances", undefined, OVERVIEW_REFRESH_MS);
  const configured = instances.data?.filter((instance) => !instance.archived) ?? [];
  const interval = useResource<EffectiveSetting[]>(configured.length ? "/settings/effective" : null,
    { keys: "instances.health_interval_seconds" }, 60_000);
  const seconds = sourceReading(interval, now, undefined, 120_000).state === "fresh" ? interval.data?.[0]?.value : null;
  const instanceMaxAge = typeof seconds === "number" && seconds > 0 ? 2_000 * seconds : 120_000;
  useCoreEvents("instance.", () => void instances.reload());

  const coreReading = sourceReading(core, now, core.data?.time ?? null);
  const encoderReading = sourceReading(encoder, now);
  const instanceList = sourceReading(instances, now);
  const coreStale = coreReading.state !== "fresh";
  const encoderStale = encoderReading.state !== "fresh";
  const instancesStale = instanceList.state !== "fresh";
  const todoText = useTodoLabel(todo, t("hero.waiting", { count: num(todo.rows.length) }));
  const rows: Row[] = [{
    id: "core", name: t("system.core"), href: "/systeem",
    tone: coreReading.state === "fresh" ? "healthy" : coreReading.state,
    reason: t(core.error ? "overview.status.refreshFailed" : coreStale ? "overview.status.tooOld"
      : core.data ? "overview.status.coreReply" : "overview.status.noReading"),
    at: coreReading.at, stale: coreReading.state === "stale",
  }, {
    id: "encoder", name: t("overview.status.encoder"), href: "/av1",
    tone: encoderReading.state === "unknown" ? "unknown" : encoderStale && encoder.data?.status !== "degraded" ? "stale"
      : encoder.data?.status === "ok" ? "healthy" : encoder.data?.status === "degraded" ? "warning" : "unknown",
    reason: encoderStale || !encoder.data ? t(encoder.error ? "overview.status.refreshFailed" : encoder.data ? "overview.status.tooOld" : "overview.status.noReading")
      : t(encoder.data.status === "ok" ? "overview.status.encoderReply" : encoder.data.status === "degraded" ? "overview.status.encoderLimited" : "overview.status.noReading"),
    at: encoderReading.at, stale: encoderReading.state === "stale",
  }];

  if (!configured.length) rows.push({
    id: "instances", name: t("overview.status.instances"), href: "/instellingen/instanties",
    tone: instanceList.state === "fresh" ? "unlinked" : instanceList.state,
    reason: t(instances.error ? "overview.status.refreshFailed" : instancesStale ? "overview.status.tooOld"
      : instances.data ? "overview.status.noneLinked" : "overview.status.noReading"),
    at: instanceList.at, stale: instanceList.state === "stale",
  });
  for (const instance of configured) {
    const reading = instance.suspended ? instanceList : sourceReading(instances, now, instance.last_check_at ?? null, instanceMaxAge);
    const at = reading.at;
    const stale = reading.state === "stale" || instanceList.state === "stale";
    const tone: Status = stale && (instance.suspended || instance.status === "healthy") ? "stale"
      : instance.suspended ? "paused" : at === null ? "unknown"
        : instance.status === "offline" ? "danger" : instance.status === "degraded" ? "warning"
          : instance.status === "healthy" ? "healthy" : "unknown";
    rows.push({ id: instance.id, name: instance.name, href: `/instellingen/instanties#${encodeURIComponent(instance.id)}`, tone, at, stale,
      reason: stale ? t(instances.error ? "overview.status.refreshFailed" : "overview.status.tooOld")
        : tone === "paused" ? t("overview.status.paused") : tone === "unknown" ? t("overview.status.noReading")
          : instance.last_error || t("overview.status.instanceCheck") });
  }

  const checks = health.measurement;
  const checksStale = checks.state !== "fresh";
  // Een downloadclient kan in dezelfde foutgroep zitten zonder eigen instantierij.
  const represented = new Set(rows.filter(row => !row.stale && row.tone === "danger").map(row => row.id));
  const matched = new Set(health.unreachableIds?.filter(id => represented.has(id)));
  const encoderFault = !encoderStale && encoder.data?.status === "degraded" ? 1 : 0;
  const localErrors = checks.errors === null ? null : Math.max(0, checks.errors
    - Math.min(health.breakdown.encoder_degraded ?? 0, encoderFault) - Math.min(health.breakdown.instances_unreachable ?? 0, matched.size));
  rows.splice(2, 0, {
    id: "checks", name: t("overview.status.checks"), href: "/activiteit?tab=problemen",
    tone: localErrors ? "warning" : checksStale ? (checks.at === null ? "unknown" : "stale") : localErrors === 0 && checks.complete ? "healthy" : "unknown",
    reason: localErrors !== null && (localErrors > 0 || checks.complete)
      ? t("overview.status.errorsReported", { count: num(localErrors) })
      : t(checks.failed ? "overview.status.refreshFailed" : "overview.status.noFullReading"),
    at: checks.at, stale: checks.state === "stale",
  });

  const count = (tone: Status) => rows.filter((row) => (row.stale ? "stale" : row.tone) === tone).length;
  const total: Status = count("danger") ? "danger"
    : count("warning") ? "warning"
      : rows.some((row) => row.stale || row.tone === "unknown") || coreStale ? "unknown" : "healthy";
  const totals = (["danger", "warning", "unknown", "stale", "healthy", "paused", "unlinked"] as const)
    .flatMap((tone) => count(tone) ? [`${num(count(tone))} ${t(STATUS[tone]).toLocaleLowerCase()}`] : []);
  const previousProblem = rows.find((row) => row.stale && (row.tone === "danger" || row.tone === "warning"));
  const needsRefresh = rows.some(row => row.stale) || checksStale || checks.failed
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
            <StatusChip tone={total} size="lg">{t(offline && total === "unknown" ? "health.offline" : STATUS[total])}</StatusChip>
          </span>
          <Button size="sm" onClick={refresh}>{t(needsRefresh ? "action.retry" : "action.reload")}</Button>
        </div>
      </div>
      <div className="space-y-1 px-4 pb-2 text-sm text-muted-foreground">
        {todo.rows.length || todo.missing.length ? <p data-testid="hero-todo">{todoText}</p> : null}
        {totals.length ? <p>{totals.join(" · ")}</p> : null}
        {total === "unknown" && previousProblem?.at ? <p>{t("overview.status.lastProblem", { when: time(previousProblem.at), name: previousProblem.name, status: t(STATUS[previousProblem.tone]) })}</p> : null}
        <p data-testid="hero-sub">{coreStale
          ? <>{t(core.data?.emergency_stop?.active ? "overview.status.stopLastKnown" : "overview.stopState")} · <ReadingMark reading={coreReading} /></>
          : core.data?.emergency_stop?.active ? t("hero.estop") : null}</p>
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
      {todo.rows.length || todo.missing.length ? <div className="border-t border-border px-4 py-2"><LinkButton href="#todo" variant="secondary">{t("hero.toList")}</LinkButton></div> : null}
    </section>
  );
}
