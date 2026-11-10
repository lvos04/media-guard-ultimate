"use client";

import { usePathname } from "next/navigation";

import { Tabs } from "@/components/mg/primitives";
import { useI18n, type MessageKey } from "@/lib/i18n";

/**
 * Ruimte = AV1-besparing + Terugzetten (quarantaine): één tabbalk i.p.v. twee menu-items.
 * De AV1-engine-instellingen staan onder Instellingen (knop rechtsboven op Ruimte).
 */
export const SPACE_TABS: Array<[string, MessageKey]> = [
  ["/av1", "space.tab.saving"],
  ["/av1/wachtrij", "av1.tab.queue"],
  ["/av1/kwaliteit", "av1.tab.quality"],
  ["/quarantaine", "space.tab.restore"],
];

export function SpaceTabs() {
  const { t } = useI18n();
  const pathname = usePathname();
  const active = [...SPACE_TABS].reverse().find(([href]) => pathname === href || (href !== "/av1" && pathname.startsWith(href)))?.[0] ?? "/av1";
  return (
    <div style={{ marginBottom: 16 }}>
      <Tabs label={t("space.tabs")} value={active} items={SPACE_TABS.map(([href, key]) => ({ value: href, href, label: t(key) }))} />
    </div>
  );
}
