"use client";

import { Activity } from "lucide-react";

import { Button, Card, StatusChip } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

interface TaskRow {
  name: string; label_nl: string; last_ok_at?: string | null; last_error?: string | null;
  failing_since?: string | null; restarts?: number; stale?: boolean;
  /** null = taak zonder vaste ronde (wacht op gebeurtenissen): geen 'laatst gelukt' */
  quiet_minutes?: number | null;
}
interface MonitorStatus { enabled: boolean; last?: { at?: string } | null; tasks: TaskRow[] }

/**
 * Achtergrondtaken (verbeterlijst 9): per taak wanneer hij voor het laatst lukte, de laatste
 * fout en hoe vaak MediaGuard hem opnieuw startte. Een taak die te lang niets deed staat
 * bovenaan en geeft ook een melding (bewaking op de achtergrond, lijst 1).
 */
export function BackgroundTasks() {
  const { t, relative, num } = useI18n();
  const { can } = useSession();
  const res = useResource<MonitorStatus>("/monitor", undefined, 60_000);
  const { run, pending } = useAction();
  const check = () => void run("monitor", "post", "/monitor/run", {}, t("system.monitorDone")).then(() => res.reload());
  return (
    <Card className="span-12" id="taken" title={t("system.tasks")} sub={t("system.tasksSub")} icon={Activity} flush
      actions={can("operator") ? <Button size="sm" pending={pending === "monitor"} onClick={check}>{t("system.monitorRun")}</Button> : null}>
      <ResourceView resource={res} endpoint="GET /api/v1/monitor" compact>
        {(m) => (
          <div className="list" data-testid="background-tasks">
            <div className="list-row">
              <div className="list-main"><div className="list-sub">
                {!m.enabled ? t("system.monitorOff") : m.last?.at ? t("system.monitorLast", { when: relative(m.last.at) }) : t("system.monitorNever")}
              </div></div>
            </div>
            {m.tasks.map((task) => {
              const failing = Boolean(task.failing_since);
              const sub = [
                task.last_ok_at ? t("system.taskLastOk", { when: relative(task.last_ok_at) })
                  : task.quiet_minutes == null ? t("system.taskEvent") : t("system.taskNeverOk"),
                task.restarts ? t("system.taskRestarts", { count: num(task.restarts) }) : null,
                failing && task.last_error ? t("system.taskError", { error: task.last_error }) : null,
              ].filter(Boolean).join(" · ");
              return (
                <div className="list-row list-row--wrap" key={task.name}>
                  <div className="list-main">
                    <div className="list-title">{task.label_nl}</div>
                    <div className="list-sub" style={{ whiteSpace: "normal", wordBreak: "break-word" }}>{sub}</div>
                  </div>
                  <span className="list-aside">
                    <StatusChip tone={task.stale ? "danger" : failing ? "warning" : "success"}>
                      {task.stale ? t("system.taskStale") : failing ? t("system.taskFailing") : t("system.taskOk")}
                    </StatusChip>
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
