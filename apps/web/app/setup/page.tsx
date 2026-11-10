"use client";

import { ArrowRight, Monitor, Moon, Route, Sun, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, Field, InlineState, LinkButton, SEGMENT_CLASS } from "@/components/mg/primitives";
import { AuthFrame } from "@/components/shell/auth-frame";
import { api, ApiError, setCsrfToken } from "@/lib/api/client";
import type { LoginResult, SetupStatus } from "@/lib/api/contracts";
import { LOCALES, useI18n, type Locale } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export default function SetupPage() {
  const [locale, setLocale] = useState<Locale>("nl-NL");
  return <AuthFrame locale={locale}><SetupWizard locale={locale} onLocale={setLocale} /></AuthFrame>;
}

function SetupWizard({ locale, onLocale }: { locale: Locale; onLocale: (l: Locale) => void }) {
  const { t } = useI18n();
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [closed, setClosed] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [touched, setTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Peil: standaardthema Systeem (ADR-0016 punt 5); dezelfde drie keuzes als in het profielmenu
  const [theme, setTheme] = useState<"dark" | "light" | "system">("system");

  useEffect(() => {
    api.get<SetupStatus>("/auth/setup-status").then((s) => { if (!s.needs_setup && step === 0) setClosed(true); }).catch(() => undefined);
  }, [step]);

  const userError = touched && username.trim().length < 2 ? t("auth.usernameTooShort") : null;
  const passError = touched && password.length < 12 ? t("auth.passwordTooShort") : null;
  const repeatError = touched && repeat !== password ? t("auth.passwordMismatch") : null;

  const createAdmin = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (username.trim().length < 2 || password.length < 12 || repeat !== password) return;
    setPending(true);
    setError(null);
    try {
      const result = await api.post<LoginResult>("/auth/setup", { username: username.trim(), password });
      setCsrfToken(result.csrf_token);
      setStep(1);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setClosed(true);
      else setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };

  const saveLanguage = async () => {
    setPending(true);
    setError(null);
    try {
      await api.put("/auth/me/locale", { locale });
      await api.patch("/settings/scoped", { changes: [{ scope_type: "global", scope_id: "", key: "ui.theme", value: theme }], reason: "Eerste installatie" });
      try { window.localStorage.setItem("mg.locale", locale); } catch { /* ignore */ }
      setStep(2);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };

  const chooseTheme = (value: "dark" | "light" | "system") => {
    setTheme(value);
    const dark = value === "dark" || (value === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.dataset.themeChoice = value;
  };

  if (closed && step === 0) {
    return (
      <div className="stack">
        <h1 className="page-title">{t("auth.setupTitle")}</h1>
        <InlineState tone="info">{t("auth.setupDone")}</InlineState>
        <LinkButton href="/login" variant="primary">{t("auth.goLogin")}</LinkButton>
      </div>
    );
  }

  const steps = [t("auth.setupStepAdmin"), t("auth.setupStepLanguage"), t("auth.setupStepNext")];
  return (
    <div className="stack">
      <ol className="hstepper" aria-label={t("auth.setupTitle")} style={{ listStyle: "none", margin: "0 0 8px", padding: 0 }}>
        {steps.map((label, i) => (
          <li key={label} className="hstep" style={i === step ? { color: "var(--mg-text)" } : undefined} aria-current={i === step ? "step" : undefined}>
            {i > 0 ? <span className="hstep-line" aria-hidden="true" /> : null}
            <span className={cn("step-no")} style={i <= step ? { borderColor: "var(--primary)", color: "var(--foreground)" } : undefined}>{i + 1}</span>{label}
          </li>
        ))}
      </ol>

      {step === 0 ? (
        <form className="stack" onSubmit={createAdmin} noValidate>
          <div><h1 className="page-title">{t("auth.setupTitle")}</h1><p className="page-sub">{t("auth.setupSub")}</p></div>
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          <Field label={t("auth.username")} htmlFor="setup-username" error={userError}>
            <input id="setup-username" className="input" autoComplete="username" value={username} aria-invalid={Boolean(userError)} onChange={(e) => setUsername(e.target.value)} />
          </Field>
          <Field label={t("auth.password")} htmlFor="setup-password" hint={t("auth.passwordHint")} error={passError}>
            <input id="setup-password" type="password" className="input" autoComplete="new-password" value={password} aria-invalid={Boolean(passError)} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Field label={t("auth.passwordRepeat")} htmlFor="setup-repeat" error={repeatError}>
            <input id="setup-repeat" type="password" className="input" autoComplete="new-password" value={repeat} aria-invalid={Boolean(repeatError)} onChange={(e) => setRepeat(e.target.value)} />
          </Field>
          <Button type="submit" variant="primary" pending={pending}>{t("auth.createAccount")} <ArrowRight className="ico" aria-hidden="true" /></Button>
        </form>
      ) : null}

      {step === 1 ? (
        <div className="stack">
          <div><h1 className="page-title">{t("auth.languageTitle")}</h1><p className="page-sub">{t("auth.languageSub")}</p></div>
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          <div>
            <span className="label">{t("topbar.language")}</span>
            <div className="segmented inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-border bg-muted p-1" role="group" aria-label={t("topbar.language")}>
              {LOCALES.map((l) => <button key={l} type="button" className={SEGMENT_CLASS} aria-pressed={locale === l} onClick={() => onLocale(l)}>{t(`locale.${l}`)}</button>)}
            </div>
          </div>
          <div>
            <span className="label">{t("topbar.theme")}</span>
            <div className="segmented inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-border bg-muted p-1" role="group" aria-label={t("topbar.theme")}>
              <button type="button" className={SEGMENT_CLASS} aria-pressed={theme === "dark"} onClick={() => chooseTheme("dark")}><Moon className="ico" aria-hidden="true" />{t("theme.dark")}</button>
              <button type="button" className={SEGMENT_CLASS} aria-pressed={theme === "light"} onClick={() => chooseTheme("light")}><Sun className="ico" aria-hidden="true" />{t("theme.light")}</button>
              <button type="button" className={SEGMENT_CLASS} aria-pressed={theme === "system"} onClick={() => chooseTheme("system")}><Monitor className="ico" aria-hidden="true" />{t("theme.system")}</button>
            </div>
          </div>
          <Button variant="primary" pending={pending} onClick={() => void saveLanguage()}>{t("auth.continue")} <ArrowRight className="ico" aria-hidden="true" /></Button>
        </div>
      ) : null}

      {step === 2 ? (
        <div className="stack">
          <div><h1 className="page-title">{t("auth.nextTitle")}</h1><p className="page-sub">{t("auth.nextSub")}</p></div>
          <Button variant="primary" icon={Route} onClick={() => router.replace("/migratie")}>{t("auth.startMigration")}</Button>
          <Button icon={Wand2} onClick={() => router.replace("/inrichten")}>{t("auth.startProvisioning")}</Button>
          <Link href="/" className="link-more" style={{ justifyContent: "center" }}>{t("auth.later")}</Link>
        </div>
      ) : null}
    </div>
  );
}
