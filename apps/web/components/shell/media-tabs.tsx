"use client";

import { usePathname } from "next/navigation";

import { Tabs } from "@/components/mg/primitives";
import { useI18n } from "@/lib/i18n";

/** Bibliotheek, Ontbrekend en Kalender horen bij elkaar: één tabbalk i.p.v. drie menu-items. */
export function MediaTabs() {
  const { t } = useI18n();
  const pathname = usePathname();
  const items = [
    { value: "/bibliotheek", href: "/bibliotheek", label: t("nav.library") },
    { value: "/ontbrekend", href: "/ontbrekend", label: t("nav.wantedShort") },
    { value: "/kalender", href: "/kalender", label: t("nav.calendar") },
  ];
  const value = items.find((i) => pathname === i.value || pathname.startsWith(`${i.value}/`))?.value ?? "/bibliotheek";
  return <div style={{ marginBottom: 16 }}><Tabs label={t("nav.group.media")} value={value} items={items} /></div>;
}
