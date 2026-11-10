"use client";

import { usePathname } from "next/navigation";

import { PageHeader } from "@/components/mg/primitives";
import { SettingsTabs } from "@/components/shell/settings-tabs";
import { useI18n, type MessageKey } from "@/lib/i18n";

/** Subpagina's van Instellingen: eigen kop + één weg terug naar de instellingenpagina. */
const SUB: Record<string, [MessageKey, MessageKey]> = {
  "/instellingen/instanties": ["set.link.instances", "set.link.instances.sub"],
  "/instellingen/meldingen": ["set.link.notify", "set.link.notify.sub"],
  "/instellingen/beveiliging": ["set.link.security", "set.link.security.sub"],
  "/instellingen/gebruikers": ["set.link.users", "set.link.users.sub"],
  "/instellingen/api-sleutels": ["set.link.apikeys", "set.link.apikeys.sub"],
  "/instellingen/geheimen": ["set.link.secrets", "set.link.secrets.sub"],
  "/instellingen/audit": ["set.link.audit", "set.link.audit.sub"],
};

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const pathname = usePathname();
  const sub = SUB[pathname];
  return (
    <>
      <SettingsTabs />
      {sub ? <PageHeader title={t(sub[0])} sub={t(sub[1])} /> : <PageHeader title={t("set.title")} sub={t("set.sub")} />}
      {children}
    </>
  );
}
