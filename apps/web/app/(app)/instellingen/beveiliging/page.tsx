"use client";

import { SecuritySettings } from "@/components/notify/security-settings";
import { useI18n } from "@/lib/i18n";

export default function SecurityPage() {
  const { t } = useI18n();
  return (
    <div className="stack">
      <div>
        <h2 className="section-title" style={{ margin: 0 }}>{t("sec.title")}</h2>
        <p className="page-sub">{t("sec.sub")}</p>
      </div>
      <SecuritySettings />
    </div>
  );
}
