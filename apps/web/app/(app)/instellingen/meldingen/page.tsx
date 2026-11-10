"use client";

import { NotifySettings } from "@/components/notify/notify-settings";
import { useI18n } from "@/lib/i18n";

export default function NotificationsPage() {
  const { t } = useI18n();
  return (
    <div className="stack">
      <div>
        <h2 className="section-title" style={{ margin: 0 }}>{t("notify.title")}</h2>
        <p className="page-sub">{t("notify.sub")}</p>
      </div>
      <NotifySettings />
    </div>
  );
}
