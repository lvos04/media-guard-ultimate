"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { api, ApiError, beginSessionLoad, endSessionLoad, onUnauthorized, setCsrfToken } from "@/lib/api/client";
import type { Me, Role, SetupStatus } from "@/lib/api/contracts";
import { I18nProvider, isLocale, type Locale } from "@/lib/i18n";

type SessionStatus = "loading" | "authenticated" | "offline";

interface SessionValue {
  me: Me | null;
  status: SessionStatus;
  /** waarom core onbereikbaar is (migratie, herstartlus), als de server het weet */
  offlineNote: string | null;
  locale: Locale;
  role: Role | null;
  can: (min: Role) => boolean;
  refresh: () => Promise<void>;
  setLocale: (locale: Locale) => Promise<void>;
  logout: () => Promise<void>;
}

const RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 };
const SessionContext = createContext<SessionValue | null>(null);

/** De webserver legt uit waarom core niet antwoordt (codes uit server.mjs). */
export function offlineExplanation(error: ApiError): string | null {
  if (error.kind !== "offline" || error.status !== 502) return null;
  return /^MediaGuard/.test(error.message) ? error.message : null;
}

function preferredLocale(): Locale {
  try {
    const stored = window.localStorage.getItem("mg.locale");
    if (isLocale(stored)) return stored;
  } catch {
    // storage may be blocked
  }
  return "nl-NL";
}

/** Guards the authenticated app: resolves /auth/me, redirects to /setup or /login, keeps CSRF. */
export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [offlineNote, setOfflineNote] = useState<string | null>(null);
  const [locale, setLocaleState] = useState<Locale>("nl-NL");

  const redirecting = useRef(false);
  const redirectToEntry = useCallback(async () => {
    // veel parallelle 401's (pagina's laden nu tegelijk met /auth/me) → één doorverwijzing
    if (redirecting.current) return;
    redirecting.current = true;
    try {
      const setup = await api.get<SetupStatus>("/auth/setup-status");
      const next = encodeURIComponent(pathname || "/");
      router.replace(setup.needs_setup ? "/setup" : `/login?next=${next}`);
    } catch {
      setStatus("offline");
    } finally {
      window.setTimeout(() => { redirecting.current = false; }, 2_000);
    }
  }, [pathname, router]);

  // Al bij de eerste render (vóór de pagina's renderen): hun effecten en snelle klikken lopen
  // anders vóór het effect hieronder, en een wijziging zou dan zonder CSRF-token vertrekken.
  useState(() => {
    if (typeof window !== "undefined") beginSessionLoad();
    return true;
  });

  const refresh = useCallback(async () => {
    beginSessionLoad();
    try {
      const value = await api.get<Me>("/auth/me");
      setCsrfToken(value.csrf_token);
      endSessionLoad();
      setMe(value);
      const loc = isLocale(value.locale) ? value.locale : preferredLocale();
      setLocaleState(loc);
      try { window.localStorage.setItem("mg.locale", loc); } catch { /* ignore */ }
      setStatus("authenticated");
      setOfflineNote(null);
    } catch (error) {
      endSessionLoad();
      if (error instanceof ApiError && error.kind === "unauthorized") {
        await redirectToEntry();
      } else {
        setLocaleState(preferredLocale());
        setOfflineNote(error instanceof ApiError ? offlineExplanation(error) : null);
        setStatus("offline");
      }
    }
  }, [redirectToEntry]);

  useEffect(() => {
    void refresh();
    // only on mount: navigation inside the app keeps the session
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => onUnauthorized(() => {
    setMe(null);
    setCsrfToken(null);
    void redirectToEntry();
  }), [redirectToEntry]);

  useEffect(() => {
    // verplichte tweestapsverificatie: eerst instellen, dan pas de rest van de app
    if (me?.twofa_required && pathname !== "/instellingen/beveiliging") router.replace("/instellingen/beveiliging");
  }, [me?.twofa_required, pathname, router]);

  useEffect(() => {
    document.documentElement.lang = locale === "en-US" ? "en" : "nl";
  }, [locale]);

  const setLocale = useCallback(async (next: Locale) => {
    setLocaleState(next);
    try { window.localStorage.setItem("mg.locale", next); } catch { /* ignore */ }
    await api.put("/auth/me/locale", { locale: next });
  }, []);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); } catch { /* session may already be gone */ }
    setCsrfToken(null);
    setMe(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo<SessionValue>(() => ({
    me,
    status,
    offlineNote,
    locale,
    role: me?.role ?? null,
    can: (min: Role) => (me ? RANK[me.role] >= RANK[min] : false),
    refresh,
    setLocale,
    logout,
  }), [locale, logout, me, offlineNote, refresh, setLocale, status]);

  return (
    <SessionContext.Provider value={value}>
      <I18nProvider locale={locale}>{children}</I18nProvider>
    </SessionContext.Provider>
  );
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession outside SessionProvider");
  return value;
}
