"use client";

import { FolderX } from "lucide-react";
import Link from "next/link";

import { Card, InlineState } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";

interface Orphan { path: string; size?: number; bytes?: number; item_id?: number | null; reason_nl?: string | null; removed?: boolean }
interface OrphanReport { checked_at?: string | null; items?: Orphan[]; removed?: number; left?: number; left_bytes?: number; freed_bytes?: number }

/**
 * Onderhoud: mappen/bestanden in de quarantainemap zonder actief item (rapport 6, N9).
 * De recovery-worker ruimt aantoonbaar overbodige wezen zelf op; hier staat de uitkomst.
 */
export function QuarantineOrphans({ className = "span-6" }: { className?: string }) {
  const { t, bytes, num, dateTime } = useI18n();
  const res = useResource<OrphanReport>("/quarantine/orphans", undefined, 300_000);
  // oudere core: endpoint bestaat niet → kaart blijft weg
  if (res.error && (res.error.status === 404 || res.error.status === 405)) return null;
  const r = res.data;
  const left = (r?.items ?? []).filter((o) => !o.removed);
  const leftCount = r?.left ?? left.length;
  const leftBytes = r?.left_bytes ?? left.reduce((s, o) => s + (o.size ?? o.bytes ?? 0), 0);
  return (
    <Card id="quarantaine-wezen" className={className} title={t("orphans.title")} icon={FolderX} sub={r?.checked_at ? t("orphans.checkedAt", { when: dateTime(r.checked_at) }) : undefined}>
      <div data-testid="quarantine-orphans" className="stack-sm">
        {res.loading && !r ? <StateView kind="loading" compact />
          : res.error && !r ? <InlineState tone="warning">{res.error.message}</InlineState>
            : <>
              {r?.removed ? <InlineState tone="success">{t("orphans.autoRemoved", { count: num(r.removed), size: bytes(r.freed_bytes ?? 0) })}</InlineState> : null}
              {leftCount === 0 ? <p className="t-2" style={{ margin: 0, fontSize: 14 }}>{t("orphans.none")}</p> : (
                <>
                  <InlineState tone="warning">{t("orphans.found", { count: num(leftCount), size: bytes(leftBytes) })} {t("orphans.leftWhy")}</InlineState>
                  <ul className="stack-sm" style={{ margin: 0, paddingLeft: 16, fontSize: 14 }}>
                    {left.slice(0, 20).map((o) => (
                      <li key={o.path}><span className="mono" style={{ overflowWrap: "anywhere" }}>{o.path}</span> · {bytes(o.size ?? o.bytes ?? 0)}{o.reason_nl ? <span className="t-2"> · {o.reason_nl}</span> : null}</li>
                    ))}
                  </ul>
                </>
              )}
            </>}
      </div>
    </Card>
  );
}

/**
 * Eén regel met de uitkomst van de weescontrole, ook als er niets over is (rapport 7, N9):
 * voor Terugzetten en Ruimte → Instellingen → Onderhoud, met een link naar de kaart op Systeem.
 */
export function OrphansSummaryLine() {
  const { t, num, bytes, dateTime } = useI18n();
  const res = useResource<OrphanReport>("/quarantine/orphans", undefined, 300_000);
  if (res.error && (res.error.status === 404 || res.error.status === 405)) return null;
  const r = res.data;
  const left = r?.left ?? (r?.items ?? []).filter((o) => !o.removed).length;
  return (
    <p className="t-2" style={{ margin: 0, fontSize: 14 }} data-testid="orphans-line">
      {r?.checked_at ? t("q37.orphansLine", { left: num(left), when: dateTime(r.checked_at) }) : t("q37.orphansNever")}
      {r?.freed_bytes ? ` · ${t("q37.orphansFreed", { size: bytes(r.freed_bytes) })}` : ""}{" "}
      <Link className="link-more" href="/systeem#quarantaine-wezen">{t("q37.orphansLink")}</Link>
    </p>
  );
}
