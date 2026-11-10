"use client";

import { Check, Server, ShieldCheck, SearchX } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { I18nProvider, isLocale, useI18n, type Locale } from "@/lib/i18n";

/** Split-screen frame for login and first-run setup (brand panel hidden on mobile). */
export function AuthFrame({ children, locale: forced }: { children: ReactNode; locale?: Locale }) {
  const [locale, setLocale] = useState<Locale>("nl-NL");
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem("mg.locale");
      if (isLocale(stored)) setLocale(stored);
    } catch { /* ignore */ }
  }, []);
  return <I18nProvider locale={forced ?? locale}><Frame>{children}</Frame></I18nProvider>;
}

function Frame({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="auth">
      <aside className="auth-side">
        <div className="brand flex items-center gap-2.5"><span className="brand-mark inline-grid size-8 flex-none place-items-center rounded-lg bg-primary text-primary-foreground"><ShieldCheck className="size-5" aria-hidden="true" /></span><span className="min-w-0"><span className="block text-base leading-tight font-bold">{t("app.fullName")}</span><span className="block text-sm leading-tight text-muted-foreground">{t("auth.sideTitle")}</span></span></div>
        <div className="auth-points">
          <div className="auth-point"><span className="list-icon"><Server className="ico" aria-hidden="true" /></span><span><strong>{t("auth.point1Title")}</strong>{t("auth.point1Body")}</span></div>
          <div className="auth-point"><span className="list-icon"><SearchX className="ico" aria-hidden="true" /></span><span><strong>{t("auth.point2Title")}</strong>{t("auth.point2Body")}</span></div>
          <div className="auth-point"><span className="list-icon"><Check className="ico" aria-hidden="true" /></span><span><strong>{t("auth.point3Title")}</strong>{t("auth.point3Body")}</span></div>
        </div>
        <p className="text-sm text-muted-foreground">{t("auth.footer")}</p>
      </aside>
      <main className="auth-main" id="main">
        <div className="auth-card">{children}</div>
      </main>
    </div>
  );
}
