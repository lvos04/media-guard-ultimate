"use client";

import { ListChecks } from "lucide-react";
import Link from "next/link";

import type { OptimizationPlan } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";

/** Korte verwijzing naar het laatste optimalisatieplan vanaf Ruimte → AV1 (verborgen zonder plan). */
export function Av1PlanLink() {
  const { t, num } = useI18n();
  const plan = useResource<OptimizationPlan>("/optimization/plan", { limit: 1 });
  const p = plan.data;
  if (!p) return null;
  const av1 = p.summary.AV1 ?? 0;
  return (
    <div className="list-row" data-testid="av1-plan-link" style={{ border: "1px solid var(--border)", borderRadius: 10 }}>
      <ListChecks className="ico" aria-hidden="true" />
      <div className="list-main">
        <div className="list-title">{t("av1.plan.title", { id: p.id, count: num(av1) })}</div>
        <div className="list-sub">{t("av1.plan.sub")}</div>
      </div>
      <Link className="btn btn--sm" href="/kwaliteit?tab=optimaliseren#av1-items">{t("av1.plan.open")}</Link>
    </div>
  );
}
