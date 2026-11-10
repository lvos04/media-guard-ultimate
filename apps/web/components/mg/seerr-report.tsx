"use client";

import { CircleCheck, CircleHelp, CircleX } from "lucide-react";

import { StatusChip } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import type { SeerrRouteReport } from "@/lib/api/contracts";
import { useI18n } from "@/lib/i18n";
import { instanceLabel } from "@/lib/labels";

/** Renders POST /seerr/route-test (or GET /seerr/mapping): per instance the individual checks. */
export function SeerrReport({ report }: { report: SeerrRouteReport | null }) {
  const { t, dateTime, locale } = useI18n();
  if (!report) return <StateView kind="empty" compact title={t("seerr.notTested")} body={t("instancesPage.routeHint")} />;
  return (
    <div className="list">
      {(report.instances ?? []).map((r) => (
        <details key={r.instance_id} className="list-row" style={{ display: "block" }}>
          <summary className="row" style={{ cursor: "pointer", listStyle: "none" }}>
            <span className={`list-icon list-icon--${r.ok ? "success" : r.ok === null ? "info" : "warning"}`}>{r.ok ? <CircleCheck className="ico" aria-hidden="true" /> : r.ok === null ? <CircleHelp className="ico" aria-hidden="true" /> : <CircleX className="ico" aria-hidden="true" />}</span>
            <div className="list-main"><div className="list-title">{instanceLabel(r.instance_id, null, locale)}</div><div className="list-sub">{r.server?.name ?? r.message_nl ?? ""}</div></div>
            <StatusChip tone={r.ok ? "success" : r.ok === null ? "neutral" : "warning"}>{r.ok ? t("status.ok") : r.ok === null ? t("status.unknown") : t("instancesPage.override")}</StatusChip>
          </summary>
          <div className="stack-sm" style={{ padding: "8px 0 4px 42px" }}>
            {r.checks.map((c) => (
              <div key={c.check} className="row" style={{ fontSize: 14, alignItems: "flex-start" }}>
                {c.ok ? <CircleCheck className="ico ico-sm t-success" aria-label={t("status.ok")} /> : c.ok === null ? <CircleHelp className="ico ico-sm t-3" aria-label={t("status.unknown")} /> : <CircleX className="ico ico-sm t-danger" aria-label={t("status.error")} />}
                <span><span className="mono t-3">{c.check}</span> — {c.message_nl}</span>
              </div>
            ))}
          </div>
        </details>
      ))}
      {report.checked_at ? <div className="card-footer"><span>{t("seerr.checkedAt", { at: dateTime(report.checked_at) })}</span></div> : null}
    </div>
  );
}
