"use client";

import { RotateCcw } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo } from "react";

import { Button, Card, PageHeader, Tabs } from "@/components/mg/primitives";
import { ErrorState, StateView } from "@/components/mg/states";
import { Av1HistoryImport } from "@/components/migration/av1-import";
import { MigrationOverview } from "@/components/migration/overview";
import { WizardProvider } from "@/components/migration/shared";
import { WIZARD_STEPS, WizardLayout, type WizardStep } from "@/components/migration/wizard";
import type { Instance } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { MigRun, MigRunRow, MigSource } from "@/lib/api/migration";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";

type Tab = "wizard" | "av1";

export default function MigrationPage() {
  return <Suspense><Migration /></Suspense>;
}

function Migration() {
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const tabParam = params.get("tab");
  // optimaliseren staat nu op één plek (Instellingen → Kwaliteit); oude links sturen door
  const legacyOptimize = tabParam === "optimize" || tabParam === "optimalisatie";
  useEffect(() => {
    if (!legacyOptimize) return;
    const q = new URLSearchParams(params.toString());
    q.set("tab", "optimaliseren");
    router.replace(`/kwaliteit?${q.toString()}`);
  }, [legacyOptimize, params, router]);
  const tab: Tab = tabParam === "av1" ? "av1" : "wizard";
  const runId = Number(params.get("run")) || null;
  const stepParam = params.get("step") as WizardStep | null;
  const go = useCallback((next: { tab?: Tab; run?: number | null; step?: WizardStep | null }) => {
    const q = new URLSearchParams();
    const nt = next.tab ?? tab;
    if (nt !== "wizard") q.set("tab", nt);
    const nr = next.run === undefined ? runId : next.run;
    if (nt === "wizard" && nr) q.set("run", String(nr));
    const ns = next.step === undefined ? stepParam : next.step;
    if (nt === "wizard" && nr && ns) q.set("step", ns);
    const qs = q.toString();
    router.replace(qs ? `/migratie?${qs}` : "/migratie", { scroll: false });
  }, [router, runId, stepParam, tab]);
  return (
    <>
      <PageHeader title={t("nav.migration")} sub={t("mig.sub")} actions={runId && tab === "wizard" ? (
        <Button icon={RotateCcw} onClick={() => go({ step: "cutover" })}>{t("migration.rollbackPlan")}</Button>
      ) : null} />
      <div style={{ marginBottom: 16 }}>
        <Tabs label={t("nav.migration")} value={tab} onSelect={(v) => go({ tab: v })} items={[
          { value: "wizard", label: t("migration.tab.wizard") },
          { value: "av1", label: t("mig.tab.av1") },
        ]} />
      </div>
      {tab === "wizard" ? (runId ? <RunView key={runId} runId={runId} step={stepParam} go={go} /> : <Overview onOpen={(id) => go({ run: id, step: null })} />)
        : <Av1HistoryImport />}
    </>
  );
}

function Overview({ onOpen }: { onOpen: (id: number) => void }) {
  const sources = useResource<MigSource[]>("/migration/sources");
  const runs = useResource<MigRunRow[]>("/migration/runs");
  useCoreEvents("migration", () => void runs.reload());
  return <MigrationOverview sources={sources} runs={runs} onOpenRun={onOpen} />;
}

function RunView({ runId, step, go }: { runId: number; step: WizardStep | null; go: (n: { tab?: Tab; run?: number | null; step?: WizardStep | null }) => void }) {
  const router = useRouter();
  const run = useResource<MigRun>(`/migration/runs/${runId}`);
  const sources = useResource<MigSource[]>("/migration/sources", { include_archived: true });
  const instances = useResource<Instance[]>("/instances");
  const { reload } = run;
  const reloadSources = sources.reload;
  useCoreEvents("migration", (e) => { if ((e.payload as { run_id?: number } | undefined)?.run_id === runId) void reload(); });
  const reloadAll = useCallback(async () => { await Promise.all([reload(), reloadSources()]); }, [reload, reloadSources]);
  const ctx = useMemo(() => run.data ? {
    run: run.data,
    reload: reloadAll,
    sources: Object.fromEntries((sources.data ?? []).map((s) => [s.id, s])),
    instances: instances.data ?? [],
    targets: run.data.mappings.filter((m) => m.status === "confirmed" && m.target_instance_id).map((m) => m.target_instance_id as string),
    goStep: (s: string) => (s === "optimize-tab" ? router.push("/kwaliteit?tab=optimaliseren") : go({ step: s as WizardStep })),
  } : null, [go, instances.data, reloadAll, router, run.data, sources.data]);
  if (!ctx) {
    if (run.error) return <Card><ErrorState error={run.error} onRetry={() => void reload()} endpoint={`GET /api/v1/migration/runs/${runId}`} /></Card>;
    return <Card><StateView kind="loading" /></Card>;
  }
  const current: WizardStep = step && WIZARD_STEPS.includes(step) ? step : (ctx.run.current_step ?? "post_cutover");
  return (
    <WizardProvider value={ctx}>
      <WizardLayout step={current} onStep={(s) => go({ step: s })} onLeave={() => go({ run: null, step: null })} />
    </WizardProvider>
  );
}
