"use client";

import { LifeBuoy } from "lucide-react";

import { Card, StatusChip } from "@/components/mg/primitives";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { plainText } from "@/lib/labels";

interface RecoveryItem { name?: string; status?: string; reason_nl?: string; client?: string; at?: string; restored_from_trash?: boolean }
interface Recovery {
  last?: { checked_at?: string; path_issues?: unknown[] } | null;
  imported: number;
  not_recoverable: number;
  pending: number;
  items: RecoveryItem[];
}

const TONE: Record<string, "success" | "accent" | "danger" | "neutral"> = {
  imported: "success", importing: "accent", not_recoverable: "danger", error: "neutral",
};

/**
 * Activiteit → Downloads: 'Hersteld' — voltooide downloads die Sonarr/Radarr nooit
 * importeerden en die MediaGuard alsnog importeerde (of waarom dat niet kon).
 */
export function DownloadRecovery() {
  const { t, relative, num } = useI18n();
  const res = useResource<Recovery>("/downloads/recovery", undefined, 60_000);
  if (!res.data) return null; // oudere core of nog niet geladen: niets tonen
  const d = res.data;
  const label = (s?: string) => s === "imported" ? t("rec.imported") : s === "importing" ? t("rec.importing")
    : s === "not_recoverable" ? t("rec.notRecoverable") : t("rec.error");
  const issues = d.last?.path_issues?.length ?? 0;
  return (
    <Card title={t("rec.title")} icon={LifeBuoy}>
      <p className="list-sub" style={{ marginTop: 0 }}>{t("rec.sub")}</p>
      <p data-testid="recovery-summary" style={{ margin: "8px 0" }}>
        {t("rec.summary", { imported: num(d.imported), pending: num(d.pending), failed: num(d.not_recoverable) })}
        {d.last?.checked_at ? <span className="list-sub"> · {t("rec.lastRun", { when: relative(d.last.checked_at) })}</span> : null}
      </p>
      {issues ? <p className="list-sub">{t("rec.pathIssues", { n: num(issues) })}</p> : null}
      {d.items.length === 0 ? <p className="list-sub">{t("rec.empty")}</p> : (
        <div className="list">
          {d.items.slice(0, 20).map((it, i) => (
            <div className="list-row" key={`${it.name}-${i}`}>
              <div className="list-main" style={{ minWidth: 0 }}>
                <div className="list-title" style={{ overflowWrap: "anywhere" }}>{it.name}</div>
                <div className="list-sub">
                  {[it.client, it.reason_nl ? plainText(it.reason_nl) : null, it.restored_from_trash ? t("rec.fromTrash") : null, it.at ? relative(it.at) : null].filter(Boolean).join(" · ")}
                </div>
              </div>
              <StatusChip tone={TONE[it.status ?? ""] ?? "neutral"}>{label(it.status)}</StatusChip>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
