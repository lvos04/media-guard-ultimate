"use client";

import { Ban, CircleSlash, ClockAlert, Inbox, OctagonX, SearchX, WifiOff, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Button, CopyField, Skeleton } from "@/components/mg/primitives";
import type { ApiError } from "@/lib/api/client";
import type { Resource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type StateKind = "loading" | "empty" | "error" | "offline" | "unavailable" | "forbidden" | "not_found";

// Peil-iconen (ONTWERPTAAL §7): fout/Storing octagon-x · niet beschikbaar circle-slash · offline wifi-off.
const ICONS: Record<Exclude<StateKind, "loading">, LucideIcon> = {
  empty: Inbox,
  error: OctagonX,
  offline: WifiOff,
  unavailable: CircleSlash,
  forbidden: Ban,
  not_found: SearchX,
};

export function StateView({ kind, title, body, action, meta, compact, icon }: {
  kind: StateKind; title?: ReactNode; body?: ReactNode; action?: ReactNode; meta?: ReactNode; compact?: boolean; icon?: LucideIcon;
}) {
  const { t } = useI18n();
  if (kind === "loading") {
    return (
      <div className={cn("state", compact ? "state--compact px-4 py-3" : "px-6 py-8")} role="status" aria-live="polite">
        <span className="sr-only">{t("state.loading")}</span>
        <Skeleton lines={compact ? 2 : 4} />
      </div>
    );
  }
  const Icon = icon ?? ICONS[kind];
  const defaults: Record<Exclude<StateKind, "loading">, [string, string]> = {
    empty: [t("state.emptyTitle"), ""],
    error: [t("state.errorTitle"), t("state.errorBody")],
    offline: [t("state.offlineTitle"), t("state.offlineBody")],
    unavailable: [t("state.unavailableTitle"), t("state.unavailableBody")],
    forbidden: [t("state.forbiddenTitle"), t("state.forbiddenBody")],
    not_found: [t("state.notFoundTitle"), t("state.notFoundBody")],
  };
  // Peil Leeg / Onbekend / Foutpaneel (C-14, C-17): icoon in de vorm van zijn as, titel, reden, wat je kunt doen.
  const iconCls = kind === "error" ? "border-down bg-down-bg text-down-ink"
    : kind === "offline" || kind === "unavailable" ? "border-dashed border-unknown bg-unknown-bg text-unknown-ink"
      : "border-transparent bg-muted text-foreground";
  return (
    <div className={cn("state flex flex-col items-center justify-center gap-2.5 text-center text-muted-foreground", `state--${kind}`, compact ? "state--compact px-4 py-5" : "px-6 py-10")}
      role={kind === "error" ? "alert" : "status"} data-state={kind} data-status={kind === "offline" || kind === "unavailable" ? "unknown" : kind === "error" ? "down" : undefined}>
      <span className={cn("state-icon inline-grid size-11 place-items-center rounded-xl border-2", iconCls)}><Icon className="ico size-6" aria-hidden="true" /></span>
      <div className="state-title text-lg font-semibold text-foreground">{title ?? defaults[kind][0]}</div>
      {(body ?? defaults[kind][1]) ? <div className="state-text max-w-prose text-base">{body ?? defaults[kind][1]}</div> : null}
      {meta ? <div className="state-meta text-sm">{meta}</div> : null}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry, endpoint, compact }: { error: ApiError; onRetry?: () => void; endpoint?: string; compact?: boolean }) {
  const { t } = useI18n();
  const kind: StateKind = error.kind === "unavailable" ? "unavailable"
    : error.kind === "offline" ? "offline"
      : error.kind === "forbidden" ? "forbidden"
        : error.kind === "not_found" ? "not_found" : "error";
  // Peil Foutpaneel (C-14): wat mislukte + waarom + wat je kunt doen; de code en een kopieerbare trace-ID
  // (= correlation_id) staan zichtbaar; HTTP-code en pad onder 'Technische details'.
  const tech = [
    error.status ? `HTTP ${error.status}` : null,
    endpoint ?? `/api/v1${error.path}`,
  ].filter(Boolean).join(" · ");
  const reason = kind === "error" && error.message && error.message !== "network" && error.message !== `HTTP ${error.status}` ? error.message : null;
  const body = kind === "error" ? <>{reason ? <>{reason} </> : null}{t("state.errorBody")}</> : undefined;
  return (
    <StateView
      kind={kind}
      compact={compact}
      body={body}
      meta={(
        <span className="flex flex-col items-center gap-2">
          {kind === "error" && error.status ? <span>{t("state.errorCode", { code: error.code ?? "error", status: String(error.status) })}</span> : null}
          {error.correlationId ? <CopyField label={t("state.traceId")} value={error.correlationId} /> : null}
          <details className="tech-details"><summary>{t("state.techDetails")}</summary><span className="mono">{tech}</span></details>
        </span>
      )}
      action={onRetry && kind !== "forbidden" ? <Button size="sm" onClick={onRetry}>{t("action.retry")}</Button> : undefined}
    />
  );
}

/**
 * Render a Resource: loading → skeleton, error without data → ErrorState, otherwise children(data).
 * When data exists but the latest refresh failed, a stale marker is rendered above the content.
 */
export function ResourceView<T>({ resource, children, endpoint, compact, empty, isEmpty }: {
  resource: Resource<T>; children: (data: T) => ReactNode; endpoint?: string; compact?: boolean;
  empty?: ReactNode; isEmpty?: (data: T) => boolean;
}) {
  const { t, relative } = useI18n();
  if (resource.loading) return <StateView kind="loading" compact={compact} />;
  if (resource.error && resource.data === null) return <ErrorState error={resource.error} onRetry={() => void resource.reload()} endpoint={endpoint} compact={compact} />;
  if (resource.data === null) return <StateView kind="loading" compact={compact} />;
  // Peil: verouderd is altijd zichtbaar — ook als de laatst bekende lijst leeg was ("niets te tonen" kan oud zijn).
  const staleMark = resource.stale ? (
    <div className="px-4 pb-2"><StaleMark>{t("state.stale", { age: relative(resource.fetchedAt) })}</StaleMark></div>
  ) : null;
  if (isEmpty?.(resource.data)) return <>{staleMark}{empty ?? <StateView kind="empty" compact={compact} />}</>;
  return (
    <>
      {staleMark}
      {children(resource.data)}
    </>
  );
}

/** Peil Versheid (T-02): verouderd = stippelrand + arcering + clock-alert + woord; nooit alleen kleur. */
export function StaleMark({ children }: { children: ReactNode }) {
  return (
    <span className="stale-mark arcering inline-flex max-w-full items-center gap-1.5 rounded-full border-2 border-dashed border-stale px-2 py-0.5 text-sm text-stale-ink" role="status" data-status="stale">
      <ClockAlert className="size-4 shrink-0" aria-hidden="true" />{children}
    </span>
  );
}
