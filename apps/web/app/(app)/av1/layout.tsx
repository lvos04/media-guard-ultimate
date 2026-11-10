"use client";

import { Settings, WifiOff } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { LinkButton, InlineState, PageHeader } from "@/components/mg/primitives";
import { Term } from "@/components/mg/term";
import { SettingsTabs } from "@/components/shell/settings-tabs";
import { SpaceTabs } from "@/components/shell/space-tabs";
import { useI18n } from "@/lib/i18n";
import { LiveProvider, useLive } from "@/lib/ws";

export default function Av1Layout({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const pathname = usePathname();
  // de AV1-engine-instellingen horen bij Instellingen: daar geen Ruimte-tabs, wel de weg terug
  if (pathname.startsWith("/av1/instellingen")) {
    return (
      <LiveProvider>
        <SettingsTabs />
        <PageHeader title={t("set.link.av1")} sub={t("set.link.av1.sub")} />
        <EngineOffline />
        {children}
      </LiveProvider>
    );
  }
  return (
    <LiveProvider>
      <PageHeader title={t("space.title")} sub={<>{t("av1.sub")} · <Term k="baan" /> · <Term k="autopilot" /> · <Term k="ondergrens" /></>}
        actions={<LinkButton href="/av1/instellingen" size="sm" icon={Settings}>{t("nav.settingsFooter")}</LinkButton>} />
      <SpaceTabs />
      <EngineOffline />
      {children}
    </LiveProvider>
  );
}

/** Waarschuwing als de engine niet antwoordt: afgeleid van de live-verbinding (geen apart pollen). */
function EngineOffline() {
  const { t } = useI18n();
  const { verbonden } = useLive();
  const [grace, setGrace] = useState(true);
  useEffect(() => { const id = window.setTimeout(() => setGrace(false), 8_000); return () => window.clearTimeout(id); }, []);
  if (verbonden || grace) return null;
  return <div style={{ marginBottom: 16 }}><InlineState tone="unknown" icon={WifiOff} role="status">{t("av1.encoderOffline")}</InlineState></div>;
}
