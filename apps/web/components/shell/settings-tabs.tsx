"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useI18n } from "@/lib/i18n";

/**
 * Instellingen is één pagina met secties. Subpagina's (Koppelingen, Systeem, Kwaliteit, …)
 * tonen geen tabbalk meer maar één weg terug, zodat er nooit twee rijen tabs op een pagina staan.
 */
export function SettingsTabs() {
  const { t } = useI18n();
  const pathname = usePathname();
  if (pathname === "/instellingen") return null;
  return (
    <nav aria-label={t("settings.tabs")} style={{ marginBottom: 12 }}>
      <Link href="/instellingen" className="back-link" data-testid="settings-back">
        <ArrowLeft className="ico" aria-hidden="true" />{t("nav.back", { page: t("set.title") })}
      </Link>
    </nav>
  );
}
