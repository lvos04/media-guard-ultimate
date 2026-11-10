"use client";

import { useState } from "react";

import { ResourceView, StateView } from "@/components/mg/states";
import type { AuditEvent } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { actorLabel, eventLabel, hasEventLabel, instanceLabel, plainText } from "@/lib/labels";

const INSTANCES = ["sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms"];

function short(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const s = typeof value === "string" ? value : JSON.stringify(value);
  return s.length > 80 ? `${s.slice(0, 77)}…` : s;
}

/** Audit timeline: why / when / who / old / new / undo (Q2.2). */
export function AuditTable({ initialAction = "", instanceId }: { initialAction?: string; instanceId?: string }) {
  const { t, dateTime, locale } = useI18n();
  const [action, setAction] = useState(initialAction);
  const [instance, setInstance] = useState(instanceId ?? "");
  const audit = useResource<AuditEvent[]>("/audit", { limit: 200, action: action || undefined, instance_id: instance || undefined });
  useCoreEvents("settings.changed", () => void audit.reload());
  useCoreEvents("system.", () => void audit.reload());
  return (
    <>
      <div className="toolbar" style={{ padding: "0 16px 12px" }}>
        <input className="input" style={{ maxWidth: 260 }} aria-label={t("audit.filterAction")} placeholder={t("audit.filterAction")} value={action} onChange={(e) => setAction(e.target.value)} />
        <select className="select" style={{ maxWidth: 220 }} aria-label={t("audit.filterInstance")} value={instance} onChange={(e) => setInstance(e.target.value)}>
          <option value="">{t("audit.filterInstance")}</option>
          {INSTANCES.map((id) => <option key={id} value={id}>{instanceLabel(id, null, locale)}</option>)}
        </select>
      </div>
      <ResourceView resource={audit} endpoint="GET /api/v1/audit" isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("audit.empty")} />}>
        {(rows) => {
          // hersteltip alleen tonen als er echt nog een origineel bewaard is (S12)
          const purged = new Set(rows.filter((x) => x.action === "quarantine.purged" || x.action === "downloads.purged").map((x) => x.target_id).filter(Boolean));
          const undoText = (x: (typeof rows)[number]) => (x.undo_hint && /quarantaine|quarantine/i.test(x.undo_hint) && x.target_id && purged.has(x.target_id) ? t("audit.undoGone") : x.undo_hint ?? "—");
          return (
          <div className="table-wrap" style={{ maxHeight: 640 }}>
            <table className="table table--compact table--stack audit-table">
              <thead><tr><th>{t("audit.when")}</th><th>{t("audit.who")}</th><th>{t("audit.what")}</th><th>{t("audit.target")}</th><th>{t("audit.why")}</th><th>{t("audit.old")}</th><th>{t("audit.new")}</th><th>{t("audit.undo")}</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td className="cell-primary num" style={{ whiteSpace: "nowrap" }}>{dateTime(r.ts)}</td>
                    <td data-label={t("audit.who")} className="mono">{(r as { actor_nl?: string | null }).actor_nl ?? actorLabel(r.actor, locale)}</td>
                    <td data-label={t("audit.what")}><span className="tag" title={r.action}>{hasEventLabel(r.action) || locale !== "nl-NL" ? eventLabel(r.action, locale) : (r as { action_nl?: string | null }).action_nl ?? eventLabel(r.action, locale)}</span></td>
                    <td data-label={t("audit.target")} className="audit-target">{r.instance_id ? <div>{instanceLabel(r.instance_id, null, locale)}</div> : null}<div className="cell-sub mono">{r.target_id ?? "—"}</div></td>
                    <td data-label={t("audit.why")} className="audit-why">{r.reason ? plainText(r.reason, locale) : "—"}</td>
                    <td data-label={t("audit.old")} className="mono t-3" title={JSON.stringify(r.old_value)}>{short(r.old_value)}</td>
                    <td data-label={t("audit.new")} className="mono" title={JSON.stringify(r.new_value)}>{short(r.new_value)}</td>
                    <td data-label={t("audit.undo")} className="t-2" style={{ fontSize: 14 }}>{undoText(r)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          );
        }}
      </ResourceView>
    </>
  );
}
