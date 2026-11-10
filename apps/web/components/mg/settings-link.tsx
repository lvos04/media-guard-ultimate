"use client";

import Link from "next/link";
import { ArrowRight, SlidersHorizontal } from "lucide-react";

import { useI18n, type MessageKey } from "@/lib/i18n";

export type SettingsSectionId = "prestaties" | "av1" | "originelen" | "downloads" | "taal" | "algemeen";

/** Link naar de juiste sectie van Instellingen (alle instellingen staan op één plek). */
export function settingsHref(section: SettingsSectionId): string {
  // 'Systeem & prestaties' staat onder de tab Systeem (M15)
  if (section === "prestaties") return "/systeem?open=prestaties#prestaties";
  return `/instellingen?open=${section}#${section}`;
}

export function SettingsLink({ sections, className }: { sections: SettingsSectionId[]; className?: string }) {
  const { t } = useI18n();
  return (
    <div className={`settings-link-row ${className ?? ""}`} data-testid="settings-link">
      <SlidersHorizontal className="ico ico-sm t-3" aria-hidden="true" />
      <span className="t-2">{t("settingsLink.lead")}</span>
      {sections.map((s) => (
        <Link key={s} className="link-arrow" href={settingsHref(s)}>
          {t(`settingsLink.section.${s}` as MessageKey)}<ArrowRight className="ico ico-sm" aria-hidden="true" />
        </Link>
      ))}
    </div>
  );
}
