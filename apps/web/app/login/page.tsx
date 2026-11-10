"use client";

import { ArrowRight } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Button, Field, InlineState } from "@/components/mg/primitives";
import { AuthFrame } from "@/components/shell/auth-frame";
import { api, ApiError } from "@/lib/api/client";
import type { LoginChallenge, LoginResult, SetupStatus } from "@/lib/api/contracts";
import { useI18n } from "@/lib/i18n";

export default function LoginPage() {
  return <AuthFrame><Suspense><LoginForm /></Suspense></AuthFrame>;
}

function LoginForm() {
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");

  const goNext = () => {
    const next = params.get("next");
    router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
  };

  useEffect(() => {
    api.get<SetupStatus>("/auth/setup-status").then((s) => { if (s.needs_setup) router.replace("/setup"); }).catch(() => undefined);
  }, [router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const res = await api.post<LoginResult | LoginChallenge>("/auth/login", { username, password });
      if (res.needs_2fa) { setChallenge(res.challenge); setCode(""); return; }
      goNext();
    } catch (err) {
      setError(err instanceof ApiError && err.kind === "offline" ? t("offline.pageTitle") : t("auth.loginFailed", { message: err instanceof Error ? err.message : String(err) }));
    } finally {
      setPending(false);
    }
  };

  const verify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!challenge) return;
    setPending(true);
    setError(null);
    try {
      await api.post<LoginResult>("/auth/login/2fa", { challenge, code: code.trim() });
      goNext();
    } catch (err) {
      setError(t("auth.twofaFailed", { message: err instanceof Error ? err.message : String(err) }));
    } finally {
      setPending(false);
    }
  };

  if (challenge) {
    return (
      <form className="stack" onSubmit={verify} noValidate data-testid="login-2fa">
        <div>
          <h1 className="page-title">{t("auth.twofaTitle")}</h1>
          <p className="page-sub">{t("auth.twofaSub")}</p>
        </div>
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
        <Field label={t("sec.code")} htmlFor="twofa-code">
          <input id="twofa-code" className="input" inputMode="numeric" autoComplete="one-time-code" autoFocus maxLength={12} value={code} onChange={(ev) => setCode(ev.target.value)} required />
        </Field>
        <Button type="submit" variant="primary" pending={pending} disabled={code.trim().length < 6}>{t("auth.twofaVerify")} <ArrowRight className="ico" aria-hidden="true" /></Button>
        <Button variant="ghost" onClick={() => { setChallenge(null); setError(null); setPassword(""); }}>{t("auth.twofaBack")}</Button>
      </form>
    );
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <div>
        <h1 className="page-title">{t("auth.loginTitle")}</h1>
        <p className="page-sub">{t("auth.loginSub")}</p>
      </div>
      {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      <Field label={t("auth.username")} htmlFor="username">
        <input id="username" name="username" className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </Field>
      <Field label={t("auth.password")} htmlFor="password">
        <input id="password" name="password" type="password" className="input" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </Field>
      <Button type="submit" variant="primary" pending={pending}>{t("auth.login")} <ArrowRight className="ico" aria-hidden="true" /></Button>
    </form>
  );
}
