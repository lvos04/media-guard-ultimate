"use client";

import { CalendarDays, Settings } from "lucide-react";

import { Card, LinkButton, StatusChip } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

import type { DigestRow, DigestStats } from "./types";

/** Activiteit → Wat is er gebeurd: vandaag tot nu toe + eerdere dag-/weekoverzichten. */
export function WhatsHappened() {
  const { t, dateTime } = useI18n();
  const { can } = useSession();
  const data = useResource<{ today: { text_nl: string; stats: DigestStats }; items: DigestRow[] }>("/notify/digests", { limit: 60 }, 120_000);
  return (
    <div className="stack" data-testid="whats-happened">
      <ResourceView resource={data} endpoint="GET /api/v1/notify/digests">
        {(d) => (
          <>
            <Card title={t("happened.today")} icon={CalendarDays}
              actions={can("admin") ? <LinkButton size="sm" href="/instellingen/meldingen" icon={Settings}>{t("happened.settings")}</LinkButton> : undefined}>
              <p style={{ margin: 0 }} data-testid="happened-today">{d.today.text_nl}</p>
              <Items stats={d.today.stats} />
            </Card>
            <Card title={t("happened.history")} sub={t("happened.sub")} flush>
              <div className="list">
                {!d.items.length ? <div className="list-row"><div className="list-sub">{t("happened.empty")}</div></div> : null}
                {d.items.map((row) => (
                  <div key={row.id} className="list-row" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 8 }}>
                    <div className="list-main">
                      <div className="list-title"><StatusChip tone={row.kind === "weekly" ? "accent" : "neutral"}>{row.kind === "weekly" ? t("happened.weekly") : t("happened.daily")}</StatusChip> {dateTime(row.period_start)}</div>
                      <div className="list-sub" style={{ whiteSpace: "normal" }}>{row.text_nl}</div>
                      <Items stats={row.stats} />
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </>
        )}
      </ResourceView>
    </div>
  );
}

function Items({ stats }: { stats: DigestStats }) {
  if (!stats.items?.length) return null;
  return (
    <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
      {stats.items.slice(0, 10).map((i) => (
        <li key={`${i.category}-${i.title}`} className="list-sub" style={{ whiteSpace: "normal" }}>
          {i.category_nl}: {i.title}{i.count > 1 ? ` (${i.count}×)` : ""}
        </li>
      ))}
    </ul>
  );
}
