"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import { Card, PageHeader, Tabs } from "@/components/mg/primitives";
import { SettingsLink, settingsHref } from "@/components/mg/settings-link";
import { ErrorState, StateView } from "@/components/mg/states";
import { DownloadsTab, IndexersTab, MediaTab, PathsTab, SeerrTab } from "@/components/provisioning/config";
import { ArrLive, PathsLive, SeerrLive } from "@/components/provisioning/live-status";
import { RenameTab } from "@/components/provisioning/optimize";
import { MediaServerTab } from "@/components/mediaserver/mediaserver-tab";
import { ProvOverview } from "@/components/provisioning/overview";
import { useResource, type Resource } from "@/lib/api/hooks";
import type { ProvConfig, ProvStatusView } from "@/lib/api/provisioning";
import { SettingsTabs } from "@/components/shell/settings-tabs";
import { useI18n, type MessageKey } from "@/lib/i18n";

const TABS = ["overzicht", "downloads", "indexers", "paden", "media", "seerr", "mediaserver", "hernoemen"] as const;
type Tab = (typeof TABS)[number];

export default function ProvisioningPage() {
  return <Suspense><Provisioning /></Suspense>;
}

function Provisioning() {
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const raw = params.get("tab");
  // het oude tabblad 'instellingen' staat nu op de centrale Instellingen-pagina
  useEffect(() => {
    if (raw === "instellingen") router.replace(settingsHref("algemeen"));
    // optimaliseren staat nu op één plek: Instellingen → Kwaliteit
    if (raw === "optimaliseren") router.replace("/kwaliteit?tab=instellingen");
  }, [raw, router]);
  const tab: Tab = TABS.includes(raw as Tab) ? (raw as Tab) : "overzicht";
  const go = (next: string) => router.replace(next === "overzicht" ? "/inrichten" : `/inrichten?tab=${next}`, { scroll: false });
  const status = useResource<ProvStatusView>("/provisioning/status");
  const cfg = useResource<ProvConfig>("/provisioning/config");
  const needs = status.data?.needs.filter((n) => n.severity === "required").length ?? 0;
  return (
    <>
      <SettingsTabs />
      <PageHeader title={t("set.link.connections")} sub={t("prov.sub")} />
      <div style={{ marginBottom: 16 }}>
        <Tabs label={t("nav.provisioning")} value={tab} onSelect={go} items={TABS.map((v) => ({ value: v, label: t(`prov.tab.${v}` as MessageKey), count: v === "overzicht" && needs ? needs : undefined }))} />
      </div>
      {tab === "overzicht" ? <ProvOverview status={status} onTab={go} />
        : tab === "hernoemen" ? <RenameTab />
          : tab === "mediaserver" ? <MediaServerTab />
          : <ConfigTab tab={tab} cfg={cfg} />}
      {tab === "overzicht" ? <div style={{ marginTop: 16 }}><SettingsLink sections={["algemeen"]} /></div> : null}
    </>
  );
}

function ConfigTab({ tab, cfg }: { tab: Tab; cfg: Resource<ProvConfig> }) {
  if (cfg.error && !cfg.data) return <Card><ErrorState error={cfg.error} onRetry={() => void cfg.reload()} endpoint="GET /api/v1/provisioning/config" /></Card>;
  if (!cfg.data) return <Card><StateView kind="loading" /></Card>;
  const ready = cfg as Resource<ProvConfig> & { data: ProvConfig };
  // re-key on the saved config so drafts restart from the server state after a save elsewhere
  const key = `${tab}`;
  // instellen bovenaan, daaronder hoe het nu echt in Sonarr/Radarr/Seerr staat (testen)
  switch (tab) {
    case "downloads": return <div className="stack"><DownloadsTab key={key} cfg={ready} /><ArrLive kind="download-clients" /><SettingsLink sections={["downloads"]} /></div>;
    case "indexers": return <div className="stack"><IndexersTab key={key} cfg={ready} /><ArrLive kind="indexers" /></div>;
    case "paden": return <div className="stack"><PathsTab key={key} cfg={ready} /><PathsLive /></div>;
    case "media": return <MediaTab key={key} cfg={ready} />;
    case "seerr": return <div className="stack"><SeerrTab key={key} cfg={ready} /><SeerrLive /><SettingsLink sections={["algemeen"]} /></div>;
    default: return null;
  }
}
