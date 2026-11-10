"use client";

import { ArrowRight, Bot, CircleCheck, Play, TriangleAlert } from "lucide-react";

import { Button, Card, InlineState, Progress } from "@/components/mg/primitives";
import { useWizard } from "@/components/migration/shared";
import { useAction } from "@/lib/api/actions";
import type { MigAutopilotNeed } from "@/lib/api/migration";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const OPEN_STEP: Record<string, string> = {
  open_mapping: "mapping", open_preflight: "preflight", open_backup: "backup", open_shadow_import: "shadow_import",
  open_verify: "verify", open_scan: "scan", open_readiness: "readiness", open_cutover: "cutover",
};

/** Eén blok bovenaan de wizard: wat MediaGuard nu doet en de ene knop die nodig is. */
export function AutopilotPanel() {
  const { t } = useI18n();
  const { can } = useSession();
  const { run: mig, reload, goStep } = useWizard();
  const { run, pending } = useAction();
  const ap = mig.autopilot;
  if (!ap || mig.status !== "active") return null;
  const busy = ap.state === "queued" || ap.state === "running" || ap.state === "waiting";
  const pct = ap.total ? (ap.done / ap.total) * 100 : 0;
  const start = async (body?: Record<string, unknown>) => {
    await run("ap", "post", `/migration/runs/${mig.id}/autopilot`, body ?? {});
    await reload();
  };
  const need: MigAutopilotNeed | undefined = ap.needs[0];
  const title = busy
    ? t("mig.ap.busy", { step: Math.min(ap.done + 1, ap.total), total: ap.total, label: ap.current?.label ?? t("mig.ap.starting") })
    : ap.state === "ready_for_cutover" ? t("mig.ap.ready") : ap.state === "needs_input" ? t("mig.ap.needs") : t("mig.ap.idle");
  const action = (n: MigAutopilotNeed) => {
    if (n.action === "allow_backup_create") {
      return <Button variant="primary" size="sm" icon={Play} disabled={!can("admin")} pending={pending === "ap"} onClick={() => void start({ allow_backup_create: true })}>{t("mig.ap.allowBackup")}</Button>;
    }
    const step = OPEN_STEP[n.action];
    if (step) {
      return (
        <span className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <Button variant="primary" size="sm" onClick={() => goStep(step)}>{t(step === "cutover" ? "mig.ap.toCutover" : "mig.ap.open")} <ArrowRight className="ico" aria-hidden="true" /></Button>
          {step !== "cutover" ? <Button size="sm" disabled={!can("operator")} pending={pending === "ap"} onClick={() => void start()}>{t("mig.ap.continue")}</Button> : null}
        </span>
      );
    }
    return null;
  };
  return (
    <Card className="span-12" data-testid="mig-autopilot">
      <div className="stack-sm">
        <div className="row" style={{ gap: 10, alignItems: "center" }}>
          {ap.state === "ready_for_cutover" ? <CircleCheck className="ico" aria-hidden="true" /> : ap.state === "needs_input" ? <TriangleAlert className="ico" aria-hidden="true" /> : <Bot className="ico" aria-hidden="true" />}
          <strong>{title}</strong>
        </div>
        {(ap as { headline_nl?: string | null }).headline_nl ? <p style={{ margin: 0, fontSize: 14 }} data-testid="mig-headline">{(ap as { headline_nl?: string | null }).headline_nl}</p> : null}
        <Progress value={pct} indeterminate={busy && ap.done === 0} label={t("mig.ap.progress", { done: ap.done, total: ap.total })} tone={ap.state === "ready_for_cutover" ? "success" : ap.state === "needs_input" ? "warning" : undefined} />
        <p className="hint" style={{ margin: 0 }}>{t("mig.ap.progress", { done: ap.done, total: ap.total })}{busy ? ` · ${t("mig.ap.selfNote")}` : ""}</p>
        {ap.needs.filter((n) => n.kind !== "waiting" || busy).map((n, i) => (
          <InlineState key={`${n.kind}-${n.target ?? i}`} tone={n.kind === "cutover" ? "success" : n.kind === "waiting" ? "neutral" : "warning"}>
            <div className="stack-sm">
              <span>{n.message_nl}</span>
              {n.detail ? <span className="hint">{n.detail}</span> : null}
              {action(n)}
            </div>
          </InlineState>
        ))}
        {ap.state === "idle" || ap.state === "stopped" || (!busy && !need) ? (
          <div><Button variant="primary" size="sm" icon={Play} disabled={!can("operator")} pending={pending === "ap"} onClick={() => void start()}>{t("mig.ap.start")}</Button></div>
        ) : null}
      </div>
    </Card>
  );
}
