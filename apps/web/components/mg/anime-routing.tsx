"use client";

import { ArrowRight, Hand, MoveRight, Route, Undo2 } from "lucide-react";
import { useState } from "react";

import { Button, Card, Segmented, StatusChip, type Tone } from "@/components/mg/primitives";
import { SettingsLink } from "@/components/mg/settings-link";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/** Row of GET /api/v1/routing/anime/actions (apps/core/mediaguard/routing). */
export interface AnimeRoutingAction {
  id: number;
  source_instance: string;
  target_instance: string;
  kind: "series" | "movie";
  provider_id: number;
  target_provider_id: number | null;
  external_id: string | number | null;
  title: string;
  status: "moved" | "suggested" | "needs_review" | "failed" | "reverted" | "dismissed";
  evidence: string[];
  detail: string | null;
  created_at: string;
}

const TONE: Record<AnimeRoutingAction["status"], Tone> = {
  moved: "success", suggested: "info", needs_review: "warning", failed: "danger", reverted: "neutral", dismissed: "neutral",
};
type Filter = "all" | "open" | "moved";

/** Automatic anime routing: what was moved / proposed, with approve / keep / undo, plus its settings. */
export function AnimeRoutingSection() {
  const { t, relative } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const [filter, setFilter] = useState<Filter>("all");
  const list = useResource<AnimeRoutingAction[]>("/routing/anime/actions");
  useCoreEvents("routing.", () => void list.reload());
  const rows = (list.data ?? []).filter((r) => filter === "all" || (filter === "moved" ? r.status === "moved" : r.status === "suggested" || r.status === "needs_review" || r.status === "failed"));
  const act = async (id: number, action: "approve" | "keep" | "undo", success: MessageKey) => {
    await run(`${action}-${id}`, "post", `/routing/anime/actions/${id}/${action}`, {}, t(success));
    void list.reload();
  };
  const open = (list.data ?? []).filter((r) => r.status === "suggested" || r.status === "needs_review" || r.status === "failed").length;
  return (
    <div className="stack" data-testid="anime-routing">
      <Card title={t("routing.title")} icon={Route} sub={t("routing.sub")} flush
        actions={<Segmented label={t("routing.filter")} value={filter} onChange={setFilter} options={[
          { value: "all", label: t("routing.filter.all"), count: list.data?.length ?? 0 },
          { value: "open", label: t("routing.filter.open"), count: open },
          { value: "moved", label: t("routing.filter.moved") },
        ]} />}>
        <ResourceView resource={list} endpoint="GET /api/v1/routing/anime/actions" compact isEmpty={() => rows.length === 0}
          empty={<StateView kind="empty" compact title={t("routing.empty")} body={t("routing.emptyBody")} />}>
          {() => (
            <div className="list">{rows.map((r) => (
              <div className="list-row" key={r.id} style={{ alignItems: "flex-start" }} data-routing-action={r.id}>
                <div className="list-main stack-sm" style={{ gap: 4 }}>
                  <div className="row wrap" style={{ gap: 8 }}>
                    <span className="list-title" style={{ whiteSpace: "normal" }}>{r.title}</span>
                    <StatusChip tone={TONE[r.status] ?? "neutral"}>{t(`routing.status.${r.status}` as MessageKey)}</StatusChip>
                  </div>
                  <div className="list-sub row wrap" style={{ gap: 6 }}>
                    <span className="tag">{r.source_instance}</span><ArrowRight className="ico ico-sm" aria-hidden="true" /><span className="tag">{r.target_instance}</span>
                    <span>· {t(r.kind === "series" ? "routing.kind.series" : "routing.kind.movie")} · {relative(r.created_at)}</span>
                  </div>
                  {r.evidence.length ? <ul className="t-2" style={{ fontSize: 14, margin: 0, paddingLeft: 18 }}>{r.evidence.map((e) => <li key={e}>{e}</li>)}</ul> : null}
                  {r.detail ? <div className={r.status === "failed" ? "t-danger" : "t-3"} style={{ fontSize: 14 }}>{r.detail}</div> : null}
                </div>
                <div className="row wrap" style={{ justifyContent: "flex-end", gap: 6 }}>
                  {r.status === "suggested" || r.status === "needs_review" || r.status === "failed" ? (
                    <>
                      <Button size="sm" variant="primary" icon={MoveRight} disabled={!can("operator")} pending={pending === `approve-${r.id}`} onClick={() => void act(r.id, "approve", "routing.moved")}>{t("routing.approve")}</Button>
                      <Button size="sm" icon={Hand} disabled={!can("operator")} pending={pending === `keep-${r.id}`} onClick={() => void act(r.id, "keep", "routing.kept")}>{t("routing.keep")}</Button>
                    </>
                  ) : null}
                  {r.status === "moved" ? <Button size="sm" icon={Undo2} disabled={!can("operator")} pending={pending === `undo-${r.id}`} onClick={() => void act(r.id, "undo", "routing.undone")}>{t("routing.undo")}</Button> : null}
                </div>
              </div>
            ))}</div>
          )}
        </ResourceView>
      </Card>
      <SettingsLink sections={["taal"]} />
    </div>
  );
}

/** Global toast when the core moved a new title to the anime instance (SSE `routing.anime_moved`). */
export function AnimeRoutingToasts() {
  const { t } = useI18n();
  const toast = useToast();
  useCoreEvents("routing.anime_moved", (e) => {
    const p = (e.payload ?? {}) as { title?: string; from?: string; to?: string };
    toast.push("info", t("routing.toast", { title: p.title ?? "?", from: p.from ?? "?", to: p.to ?? "?" }));
  });
  return null;
}
