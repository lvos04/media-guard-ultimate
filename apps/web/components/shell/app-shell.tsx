"use client";

import {
  Activity, ArrowLeft, Bell, Check, CircleHelp, CirclePause, Film, Info, Keyboard, ListChecks, LoaderCircle, LogOut, Monitor, Moon, OctagonAlert, OctagonX, Radio, Search,
  Settings, Settings2, ShieldCheck, Sun, TriangleAlert, Tv, X, type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Button, Field, Kbd, Modal, SEGMENT_CLASS, StatusChip, Switch } from "@/components/mg/primitives";
import { buttonVariants } from "@/components/ui/button";
import { StateView } from "@/components/mg/states";
import { ToastProvider, useToast } from "@/components/mg/toast";
import { ALL_NAV_ITEMS, EXTRA_SHORTCUTS, isActive, NAV } from "@/components/shell/nav";
import { navigateOrAsk, UnsavedGuard } from "@/components/shell/unsaved-guard";
import { api } from "@/lib/api/client";
import type { EffectiveSetting, LibraryItem, LibraryPage, NotificationRow, SettingDef, SystemStatus } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { EventsProvider, useCoreEvents, useLiveStatus } from "@/lib/events";
import { setShowTech, useShowTech, useTechDetailsSync } from "@/lib/tech-pref";
import { useScrollableRegions } from "@/lib/a11y";
import { HUB_URL_SETTING, hubUrl } from "@/lib/hub";
import { arrMessage } from "@/lib/arr-messages";
import { totalStatus, useTodoRows } from "@/lib/todo";
import { useHealth, type Health } from "@/lib/health";
import { LOCALES, useI18n, type Locale, parseServerDate } from "@/lib/i18n";
import { instanceLabel, plainText } from "@/lib/labels";
import { SessionProvider, useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------- shell data (status)
interface ShellData { status: SystemStatus | null; reloadStatus: () => Promise<void>; statusError: boolean }
const ShellContext = createContext<ShellData>({ status: null, reloadStatus: async () => undefined, statusError: false });
export const useShell = () => useContext(ShellContext);

type ThemeChoice = "dark" | "light" | "system";
let systemListener: ((e: MediaQueryListEvent) => void) | null = null;
function applyTheme(choice: ThemeChoice) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const dark = choice === "dark" || (choice === "system" && mq.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.themeChoice = choice;
  if (systemListener) mq.removeEventListener("change", systemListener);
  systemListener = null;
  if (choice === "system") {
    systemListener = (e) => { document.documentElement.dataset.theme = e.matches ? "dark" : "light"; };
    mq.addEventListener("change", systemListener);
  }
  window.dispatchEvent(new CustomEvent("mg:theme", { detail: choice }));
}
/**
 * Per-user preference: stored server-side (PUT /auth/me/preferences) so it follows the user to
 * every browser; localStorage only caches it for the first paint. Wins over the global ui.theme.
 */
let userChoseTheme = false;
function setUserTheme(choice: ThemeChoice) {
  userChoseTheme = true;
  try { window.localStorage.setItem("mg.theme", choice); } catch { /* ignore */ }
  applyTheme(choice);
  api.put("/auth/me/preferences", { theme: choice }).catch(() => undefined);
}
function useThemeChoice(): ThemeChoice {
  const [choice, setChoice] = useState<ThemeChoice>("system");
  useEffect(() => {
    const read = () => setChoice((document.documentElement.dataset.themeChoice as ThemeChoice | undefined) ?? "system");
    read();
    window.addEventListener("mg:theme", read);
    return () => window.removeEventListener("mg:theme", read);
  }, []);
  return choice;
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <ShellGate>{children}</ShellGate>
    </SessionProvider>
  );
}

function ShellGate({ children }: { children: ReactNode }) {
  const { status, refresh, offlineNote } = useSession();
  const { t } = useI18n();
  // core start opnieuw of migreert: vanzelf opnieuw proberen (elke 15 s)
  useEffect(() => {
    if (status !== "offline") return;
    const id = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(id);
  }, [status, refresh]);
  // Tijdens het controleren van de sessie al de shell en de pagina tonen: pagina-requests lopen
  // parallel met /auth/me (scheelt één rondreis via de tunnel). Een 401 stuurt alsnog naar /login.
  if (status === "offline") {
    return (
      <div className="auth-main" style={{ minHeight: "100vh" }}>
        <StateView kind="offline" title={t("offline.pageTitle")} body={offlineNote ?? t("offline.pageBody")} action={<Button variant="primary" onClick={() => void refresh()}>{t("action.retry")}</Button>} />
      </div>
    );
  }
  return (
    <EventsProvider enabled>
      <ToastProvider>
        <ShellFrame>{children}</ShellFrame>
      </ToastProvider>
    </EventsProvider>
  );
}

function ShellFrame({ children }: { children: ReactNode }) {
  const statusRes = useResource<SystemStatus>("/status", undefined, 20_000, { liveSlowdown: true });
  const { reload } = statusRes;
  // events kunnen in stoten komen: één verversing per halve seconde is genoeg
  const debounced = useDebounced(reload, 500);
  useCoreEvents("system.", debounced);
  useCoreEvents("job", debounced);

  // theme: the user's server-side preference, else a choice cached in this browser (migrated to
  // the server once), else the global default setting ui.theme
  const { me } = useSession();
  const serverTheme = me?.preferences?.theme ?? null;
  // standaardthema van de installatie: core stuurt het (nieuw) mee in /auth/me; anders opvragen
  const defaultTheme = (me as { theme?: ThemeChoice; ui_theme?: ThemeChoice } | null)?.theme ?? (me as { ui_theme?: ThemeChoice } | null)?.ui_theme ?? null;
  const meLoaded = me !== null;
  useEffect(() => {
    if (!meLoaded) return;
    let local: ThemeChoice | null = null;
    try { local = window.localStorage.getItem("mg.theme") as ThemeChoice | null; } catch { /* ignore */ }
    // De keuze die op dit apparaat gemaakt is, gaat voor: het serverthema mag een persoonlijke
    // keuze nooit overschrijven (bug: licht werd na het laden weer donker). Op een nieuw apparaat
    // zonder eigen keuze geldt de opgeslagen voorkeur van de gebruiker.
    if (local) {
      if (!userChoseTheme) applyTheme(local);
      if (local !== serverTheme) api.put("/auth/me/preferences", { theme: local }).catch(() => undefined);
      return;
    }
    if (serverTheme) {
      if (userChoseTheme) return;
      applyTheme(serverTheme);
      try { window.localStorage.setItem("mg.theme", serverTheme); } catch { /* ignore */ }
      return;
    }
    if (defaultTheme) { if (!userChoseTheme) applyTheme(defaultTheme); return; }
    api.get<EffectiveSetting[]>("/settings/effective", { keys: "ui.theme" })
      // a choice the user makes while this request is in flight wins
      .then((rows) => { if (!userChoseTheme) applyTheme((rows[0]?.value as ThemeChoice) ?? "system"); })
      .catch(() => { if (!userChoseTheme) applyTheme("system"); });
  }, [serverTheme, defaultTheme, meLoaded]);

  const shell = useMemo<ShellData>(() => ({ status: statusRes.data, reloadStatus: reload, statusError: Boolean(statusRes.error) }), [reload, statusRes.data, statusRes.error]);
  const { t } = useI18n();
  return (
    <ShellContext.Provider value={shell}>
      {/* AppSchil (Peil C-01): zijbalk ≥ 1024 px (lg), daaronder kopbalk + ondernavigatie; skiplink is de eerste tabstop */}
      <a className="skip-link focus-ring fixed top-[-4rem] left-3 z-toast rounded-lg bg-primary px-3 py-2.5 font-semibold text-primary-foreground no-underline focus:top-3" href="#main">{t("app.skipToContent")}</a>
      <div className="app min-h-screen lg:grid lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className="sidebar sticky top-0 hidden h-screen flex-col gap-3 overflow-y-auto overscroll-contain border-r border-border bg-sidebar px-3 pt-4 pb-3 lg:flex" aria-label={t("nav.main")}><SidebarContent /></aside>
        <div className="main flex min-w-0 flex-col">
          <Topbar />
          <EmergencyBanner />
          <main id="main" className="content mx-auto w-full max-w-[95rem] px-4 pt-4 pb-[calc(var(--bottomnav-h)+1.75rem)] outline-none lg:px-6 lg:pt-6 lg:pb-12" tabIndex={-1}>{children}</main>
        </div>
      </div>
      <BottomNav />
      <KeyboardShortcuts />
      <TechSync />
      <EmergencyStopDialog />
      <UnsavedGuard />
    </ShellContext.Provider>
  );
}

function useDebounced(fn: () => Promise<void> | void, ms: number): () => void {
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef(fn);
  useEffect(() => { latest.current = fn; });
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void latest.current(), ms);
  }, [ms]);
}

// ---------------------------------------------------------------- sidebar
function Brand() {
  const { t } = useI18n();
  return (
    <Link href="/" className="brand focus-ring flex min-h-touch items-center gap-2.5 rounded-lg px-2 text-foreground no-underline">
      <span className="brand-mark inline-grid size-8 flex-none place-items-center rounded-lg bg-primary text-primary-foreground"><ShieldCheck className="size-5" aria-hidden="true" /></span>
      <span className="min-w-0"><span className="block text-base leading-tight font-bold">{t("app.name")}</span><span className="block text-sm leading-tight text-muted-foreground">{t("app.tagline")}</span></span>
    </Link>
  );
}

/**
 * "Terug naar de Hub" (Peil O-11, ADR-0016 punt 1): adres uit de instelling system.hub_url (bestaande settings-API).
 * Alleen een geldig adres geeft een link (lib/hub.ts); leeg, ongeldig of niet leesbaar → niets in de DOM. Zelfde
 * tabblad (terugweg, geen deep link). Zijbalk ≥ 1024 px; op kleinere schermen het eerste item van het profielmenu.
 */
function HubLink({ className }: { className?: string }) {
  const { t } = useI18n();
  const setting = useResource<EffectiveSetting[]>("/settings/effective", { keys: HUB_URL_SETTING });
  const href = hubUrl(setting.data?.find((r) => r.key === HUB_URL_SETTING)?.value);
  if (!href) return null;
  return (
    <a href={href} className={cn(buttonVariants({ variant: "secundair" }), "w-full justify-start", className)} data-testid="hub-link">
      <ArrowLeft className="ico" aria-hidden="true" />{t("hub.back")}
    </a>
  );
}

function NavList({ onNavigate, only }: { onNavigate?: () => void; only?: (key: string) => boolean }) {
  const pathname = useNavPath();
  const { t, num } = useI18n();
  const health = useHealth();
  const todo = useTodoRows().length;
  // Migratie alleen tonen zolang er een migratie loopt (of als je er nu bent)
  const runs = useResource<Array<{ status: string }>>("/migration/runs");
  const migrating = (runs.data ?? []).some((r) => r.status === "active") || isActive("/migratie", pathname);
  const counts: Record<string, { value: number; tone?: "danger" | "warning" } | undefined> = {
    // zelfde telling als banner, zijbalk en kopbalk: de lijst 'Wacht op jou' (N13)
    activity: todo > 0 ? { value: todo, tone: health.errors > 0 ? "danger" : "warning" } : undefined,
  };
  return (
    <nav className="nav flex flex-col gap-0.5" aria-label={t("nav.main")}>
      {NAV.map((group) => (
        <div key={group.label ?? "root"} className="contents">
          {group.label ? <div className="nav-group mt-3 mb-1 px-3 text-sm font-semibold text-muted-foreground">{t(group.label)}</div> : null}
          {group.items.filter((item) => (!item.conditional || (item.key === "migration" && migrating)) && (only ? only(item.key) : !item.footer)).map((item) => {
            const active = isActive(item.href, pathname);
            const count = counts[item.key];
            return (
              // actief = bg-accent + 4 px balk + aria-current (Peil C-01); teller in inkt, nooit rood
              <Link key={item.key} href={item.href} aria-current={active ? "page" : undefined} onClick={onNavigate} prefetch={false}
                className={cn("nav-item focus-ring relative flex min-h-touch items-center gap-3 rounded-lg px-3 text-base text-foreground no-underline hover:bg-accent",
                  "aria-[current=page]:bg-accent aria-[current=page]:font-semibold",
                  "aria-[current=page]:before:absolute aria-[current=page]:before:inset-y-2 aria-[current=page]:before:-left-3 aria-[current=page]:before:w-1 aria-[current=page]:before:rounded-r-full aria-[current=page]:before:bg-primary")}>
                <item.icon className="ico size-5 shrink-0" aria-hidden="true" />
                <span className="nav-label min-w-0 flex-1 break-words">{t(item.label)}</span>
                {count ? <span className="nav-count cijfers min-w-6 rounded-full bg-foreground px-1.5 text-center text-sm font-semibold text-background">{num(count.value)}</span> : null}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useI18n();
  const { status } = useShell();
  const live = useLiveStatus();
  const { locale } = useSession();
  const health = useHealth();
  return (
    <>
      <Brand />
      <HubLink />
      <NavList onNavigate={onNavigate} />
      <div className="sidebar-spacer flex-1" />
      <NavList onNavigate={onNavigate} only={(k) => ALL_NAV_ITEMS.find((i) => i.key === k)?.footer === true} />
      <div className="sidebar-footer mt-auto flex flex-col gap-2 border-t border-border px-2 pt-2.5 text-sm">
        <SidebarHealth health={health} />
        {/* Peil: live/verbonden is geen gezondheid (neutraal radio-icoon); onderbroken = Onbekend (stippelpil) */}
        {live === "offline"
          ? <div className="health-line flex items-center gap-2"><StatusChip tone="unknown">{t("live.offline")}</StatusChip></div>
          : <div className="health-line flex items-center gap-2 text-foreground" title={t("live.title")}>{live === "live" ? <Radio className="ico size-[1.125rem]" aria-hidden="true" /> : <LoaderCircle className="ico size-[1.125rem]" aria-hidden="true" />}<span>{t(`live.${live}`)}</span></div>}
        <div className="version-line flex justify-between text-muted-foreground"><span>{status ? t("health.version", { version: status.version }) : "—"}</span><span>{locale}</span></div>
      </div>
    </>
  );
}

/**
 * Zijbalkvoet (Peil): totaalstatus als StatusBadge (zelfde regel als Overzicht) + een aparte link naar wat op jou
 * wacht (inktteller). Laden = skelet, nooit een gekleurde stip.
 */
function SidebarHealth({ health }: { health: Health }) {
  const { t } = useI18n();
  // zelfde telling als 'Wat actie nodig heeft' op Overzicht
  const rows = useTodoRows();
  const todo = rows.length;
  if (health.level === "offline") return <div className="health-line flex items-center gap-2" data-testid="sidebar-health"><StatusChip tone="unknown">{t("health.offline")}</StatusChip></div>;
  if (health.level === "unknown") return <div className="health-line" data-testid="sidebar-health" role="status"><span className="sr-only">{t("state.loading")}</span><span className="skeleton block h-3.5 w-3/4 rounded-md bg-muted" aria-hidden="true" /></div>;
  const total = totalStatus(health, rows);
  const badge = <StatusChip tone={total}>{total === "warning" ? t("status.degraded") : t("health.allGood")}</StatusChip>;
  if (!todo) return <div className="health-line flex items-center gap-2" data-testid="sidebar-health">{badge}</div>;
  return (
    <div className="health-line flex flex-col items-start gap-1.5" data-testid="sidebar-health">
      <Link className="focus-ring inline-flex min-h-touch items-center gap-2 rounded-lg font-semibold text-foreground no-underline hover:underline" href="/#todo"><ListChecks className="ico size-[1.125rem]" aria-hidden="true" />{t("health.attention", { count: todo })}</Link>
      {badge}
    </div>
  );
}

/** Onderste balk op de telefoon: de vijf hoofdonderdelen. Instellingen en de rest staan in het profielmenu. */
const BOTTOM_KEYS = ["overview", "activity", "library", "av1", "requests"];
/** Op de telefoon in het profielmenu (op de computer staan ze in de zijbalk of als chip). */
const MENU_LINKS: Array<[string, "nav.settingsFooter" | "nav.wantedShort" | "nav.calendar" | "space.tab.restore" | "nav.system"]> = [
  ["/instellingen", "nav.settingsFooter"], ["/ontbrekend", "nav.wantedShort"], ["/kalender", "nav.calendar"], ["/quarantaine", "space.tab.restore"], ["/systeem", "nav.system"],
];

function BottomNav() {
  const pathname = useNavPath();
  const { t } = useI18n();
  const items = BOTTOM_KEYS.map((k) => ALL_NAV_ITEMS.find((i) => i.key === k)!);
  return (
    // items zo breed als hun woord (14 px, Peil-minimum): op 360–390 px past elk woord heel; pas op 320 px lopen ze om
    <nav className="bottomnav fixed inset-x-0 bottom-0 z-nav flex border-t border-border bg-card pb-[env(safe-area-inset-bottom)] lg:hidden" aria-label={t("nav.mobile")}>
      {items.map((item) => (
        <Link key={item.key} href={item.href} aria-current={isActive(item.href, pathname) ? "page" : undefined} prefetch={false}
          className="focus-ring relative flex min-h-bottomnav min-w-0 flex-auto flex-col items-center justify-center gap-0.5 px-0.5 text-center text-sm leading-tight font-semibold text-muted-foreground no-underline aria-[current=page]:text-foreground aria-[current=page]:before:absolute aria-[current=page]:before:inset-x-3 aria-[current=page]:before:top-0 aria-[current=page]:before:h-1 aria-[current=page]:before:rounded-b-full aria-[current=page]:before:bg-primary">
          <item.icon className="ico size-6" aria-hidden="true" /><span className="max-w-full break-words">{t(item.label)}</span>
        </Link>
      ))}
    </nav>
  );
}

// ---------------------------------------------------------------- topbar
/** Kopbalk: zoeken · één statuspil · meldingen · profiel (thema en noodstop in het profielmenu). */
function Topbar() {
  const [searchOpen, setSearchOpen] = useState(false);
  const { t } = useI18n();
  return (
    <header className="topbar sticky top-0 z-nav flex min-h-header items-center gap-1 border-b border-border bg-background px-3 lg:gap-3 lg:px-6">
      <span className="hidden min-w-0 flex-[0_1_28rem] lg:flex"><GlobalSearch /></span>
      <div className="topbar-spacer flex-1" />
      <div className="topbar-cluster flex shrink-0 items-center gap-1 lg:gap-2">
        <button type="button" className={cn(buttonVariants({ variant: "stil", size: "icoon" }), "icon-btn lg:hidden")} aria-label={t("topbar.searchLabel")} onClick={() => setSearchOpen(true)} data-testid="mobile-search"><Search className="ico" aria-hidden="true" /></button>
        <StatusPill />
        <Link href="/hoe-werkt-het" className={cn(buttonVariants({ variant: "stil", size: "icoon" }), "icon-btn help-link hidden min-[30rem]:inline-flex")} aria-label={t("help.title")} title={t("help.title")} data-testid="help-link"><CircleHelp className="ico" aria-hidden="true" /></Link>
        <Notifications />
        <ProfileMenu />
      </div>
      {searchOpen ? (
        <div className="search-sheet fixed inset-0 z-overlay bg-background p-3" role="dialog" aria-modal="true" aria-label={t("topbar.searchLabel")}>
          <div className="flex items-center gap-2">
            <GlobalSearch autoFocus onDone={() => setSearchOpen(false)} />
            <button type="button" className={cn(buttonVariants({ variant: "stil", size: "icoon" }), "icon-btn")} aria-label={t("action.close")} onClick={() => setSearchOpen(false)}><X className="ico" aria-hidden="true" /></button>
          </div>
        </div>
      ) : null}
    </header>
  );
}

/** Pad voor de menumarkering: 'Waarom deze release?' vanuit een aflevering/film hoort bij Bibliotheek (N20). */
function useNavPath(): string {
  const pathname = usePathname();
  const [fromLibrary, setFromLibrary] = useState(false);
  useEffect(() => { setFromLibrary(/[?&](episode|movie)=/.test(window.location.search)); }, [pathname]);
  return pathname === "/release-lab" && fromLibrary ? "/bibliotheek" : pathname;
}

/** Eén statuspil uit het gedeelde gezondheidsmodel: "● 1 bezig", klik = Activiteit. */
function StatusPill() {
  const { t, num } = useI18n();
  const h = useHealth();
  const todo = useTodoRows().length;
  // Peil: core onbereikbaar = Onbekend (stippel), nooit rood; rood alleen voor gemeten fouten
  const tone = h.level === "action" ? "danger" : h.level === "warning" ? "warning" : null;
  const offline = h.level === "offline";
  const parts = [
    h.activeParts.downloads ? t("health.part.downloads", { count: h.activeParts.downloads }) : null,
    h.activeParts.searches ? t("health.part.searches", { count: h.activeParts.searches }) : null,
    h.activeParts.encodes ? t("health.part.encodes", { count: h.activeParts.encodes }) : null,
    h.activeParts.tasks ? t("health.part.tasks", { count: h.activeParts.tasks }) : null,
  ].filter(Boolean).join(" · ");
  const title = [parts || null, todo ? t("health.attentionPlain", { count: todo }) : null].filter(Boolean).join(" — ") || t("topbar.activeTitle");
  return (
    <Link className={cn("top-pill focus-ring hidden min-h-touch items-center gap-1.5 rounded-full border-2 border-border-strong px-3 text-sm font-semibold whitespace-nowrap text-foreground no-underline hover:bg-accent md:inline-flex", tone && `top-pill--${tone}`, offline && "border-dashed border-unknown bg-unknown-bg text-unknown-ink")} href={todo ? "/activiteit?tab=problemen" : "/activiteit"} title={title} data-testid="status-pill">
      {/* Peil: inktteller met tekst; nooit een rode of groene stip */}
      {todo ? <ListChecks className="ico" aria-hidden="true" /> : h.active ? <Activity className="ico" aria-hidden="true" /> : offline ? <CircleHelp className="ico" aria-hidden="true" /> : null}
      {todo ? t("health.attentionPlain", { count: todo }) : h.active ? t("topbar.busy", { count: num(h.active) }) : h.loaded ? t("topbar.allQuiet")
        : offline ? t("status.unknown") : <><span className="sr-only">{t("state.loading")}</span><span className="skeleton inline-block h-3.5 w-10 rounded-md bg-muted" aria-hidden="true" /></>}
    </Link>
  );
}

/** Sluit een uitklapmenu bij Escape, klik buiten het menu en bij een andere pagina (M14). */
function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  const pathname = usePathname();
  const latest = useRef(close);
  useEffect(() => { latest.current = close; });
  useEffect(() => { latest.current(); }, [pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") latest.current(); };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) latest.current(); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("pointerdown", onDown); };
  }, [open]);
  return ref;
}

interface SearchHit { href: string; title: string; sub?: string; group: string; icon: typeof Search; poster?: string | null }

/** Live zoeken tijdens het typen, gegroepeerd (Series, Films, Instellingen). Enter opent het eerste resultaat. */
function GlobalSearch({ autoFocus, onDone }: { autoFocus?: boolean; onDone?: () => void }) {
  const { t, locale: lang } = useI18n();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<SettingDef[]>([]);
  const [media, setMedia] = useState<LibraryItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [cursor, setCursor] = useState(0);
  const { locale } = useSession();
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setSettings([]); setMedia([]); return; }
    let live = true;
    setBusy(true);
    const id = window.setTimeout(() => {
      Promise.allSettled([
        api.get<LibraryPage>("/library", { q: term, page_size: 8 }),
        api.get<SettingDef[]>("/settings/search", { q: term }),
      ]).then(([lib, set]) => {
        if (!live) return;
        setMedia(lib.status === "fulfilled" ? lib.value.items ?? [] : []);
        setSettings(set.status === "fulfilled" ? set.value.slice(0, 4) : []);
        setCursor(0);
        setBusy(false);
      });
    }, 180);
    return () => { live = false; window.clearTimeout(id); };
  }, [q]);
  const hits: SearchHit[] = [
    ...media.filter((m) => m.kind === "series").map((m) => ({ href: `/bibliotheek/${m.instance_id}/${m.id}`, title: m.title + (m.year ? ` (${m.year})` : ""), sub: instanceLabel(m.instance_id, m.instance_name, lang), group: t("search.group.series"), icon: Tv, poster: m.poster_url })),
    ...media.filter((m) => m.kind === "movie").map((m) => ({ href: `/bibliotheek/${m.instance_id}/${m.id}`, title: m.title + (m.year ? ` (${m.year})` : ""), sub: instanceLabel(m.instance_id, m.instance_name, lang), group: t("search.group.movies"), icon: Film, poster: m.poster_url })),
    ...settings.map((h) => ({ href: `/instellingen?q=${encodeURIComponent(h.key)}`, title: locale === "en-US" ? h.label_en : h.label_nl, group: t("search.group.settings"), icon: Settings2 })),
  ];
  const term = q.trim();
  const all: SearchHit[] = term ? [...hits, { href: `/bibliotheek?q=${encodeURIComponent(term)}`, title: t("topbar.searchLibrary", { q: term }), group: t("search.group.more"), icon: Search }] : [];
  const go = (href: string) => { setOpen(false); setQ(""); onDone?.(); navigateOrAsk(href, () => router.push(href)); };
  let lastGroup = "";
  return (
    <div style={{ position: "relative", flex: "1 1 auto", minWidth: 0, display: "flex" }}>
      <label className="topbar-search flex min-h-touch min-w-0 flex-1 items-center gap-2 rounded-lg border-2 border-input bg-card px-2.5 text-muted-foreground focus-within:border-ring">
        <Search className="ico size-5 shrink-0" aria-hidden="true" />
        <span className="sr-only">{t("topbar.searchLabel")}</span>
        <input
          data-global-search
          value={q}
          autoFocus={autoFocus}
          placeholder={t("topbar.search")}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, all.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
            if (e.key === "Enter" && all.length) go(all[Math.min(cursor, all.length - 1)].href);
            if (e.key === "Escape") { (e.target as HTMLInputElement).blur(); onDone?.(); }
          }}
          role="combobox"
          aria-expanded={open && Boolean(term)}
          aria-controls="global-search-results"
          aria-autocomplete="list"
        />
        <span className="hidden lg:inline-grid"><Kbd>/</Kbd></span>
      </label>
      {open && term ? (
        <div className="popover search-results absolute inset-x-0 top-full z-overlay mt-1" id="global-search-results" role="listbox" data-testid="search-results">
          <div className="list">
            {busy && !hits.length ? <div className="list-row"><span className="t-3">{t("state.loading")}</span></div> : null}
            {!busy && term.length >= 2 && !hits.length ? <div className="list-row"><span className="t-3">{t("search.none", { q: term })}</span></div> : null}
            {all.map((h, i) => {
              const header = h.group !== lastGroup ? <div className="px-4 pt-2 pb-1 text-sm font-semibold text-muted-foreground">{h.group}</div> : null;
              lastGroup = h.group;
              return (
                <div key={`${h.href}-${i}`} style={{ display: "contents" }}>
                  {header}
                  <button type="button" role="option" aria-selected={i === cursor} className={cn("list-row list-row--hover min-h-touch w-full border-0 text-left", i === cursor ? "bg-accent" : "bg-transparent")} onMouseDown={() => go(h.href)}>
                    {/* eslint-disable-next-line @next/next/no-img-element -- kleine poster via core-proxy */}
                    {h.poster ? <img src={h.poster} alt="" width={28} height={42} style={{ borderRadius: 4, objectFit: "cover", flex: "none" }} loading="lazy" /> : <span className="list-icon"><h.icon className="ico" aria-hidden="true" /></span>}
                    <div className="list-main"><div className="list-title">{h.title}</div>{h.sub ? <div className="list-sub">{h.sub}</div> : null}</div>
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Oude/verkeerde links uit meldingen naar pagina's die bestaan. */
function notificationHref(link: string | null): string | null {
  if (!link || !link.startsWith("/")) return null;
  if (link.startsWith("/instellingen/anime-routering")) return "/anime";
  if (link.startsWith("/instellingen/instanties/")) return "/instellingen/instanties";
  if (link.startsWith("/jobs")) return link.replace("/jobs", "/activiteit?tab=geschiedenis").replace("?tab=geschiedenis?", "?tab=geschiedenis&");
  return link;
}

/** Gelijke meldingen samenvoegen ("4×") in plaats van ze onder elkaar te herhalen (M3). */
function groupNotifications(rows: NotificationRow[]): Array<{ n: NotificationRow; count: number }> {
  const out = new Map<string, { n: NotificationRow; count: number }>();
  for (const n of rows) {
    // zelfde titel + zelfde soort link = één regel (bv. 7× "Inrichting wijkt af"), N15
    const key = `${n.title}|${(n.link ?? "").split("?")[0]}`;
    const hit = out.get(key);
    // core voegt gelijke meldingen zelf al samen ('count'); hier ook nog dubbele regels
    const own = typeof (n as { count?: number }).count === "number" ? (n as { count?: number }).count! : 1;
    if (hit) {
      hit.count += own;
      // de nieuwste melding van de groep bepaalt tekst en tijd (S14)
      if ((parseServerDate(n.ts)?.valueOf() ?? 0) > (parseServerDate(hit.n.ts)?.valueOf() ?? 0)) hit.n = n;
    } else out.set(key, { n, count: own });
  }
  return [...out.values()].sort((a, b) => (parseServerDate(b.n.ts)?.valueOf() ?? 0) - (parseServerDate(a.n.ts)?.valueOf() ?? 0));
}

/** ErnstLabel (Peil): icoon + woord per ernst (het woord voor schermlezers); "success" is neutraal. */
const SEVERITY_ICON: Record<string, LucideIcon> = { error: OctagonX, warning: TriangleAlert, success: Check, info: Info };
function SeverityIcon({ severity }: { severity: string }) {
  const { t } = useI18n();
  const key = severity === "error" || severity === "warning" || severity === "success" ? severity : "info";
  const Icon = SEVERITY_ICON[key];
  return (
    <span className={cn("inline-grid size-8 flex-none place-items-center rounded-lg", key === "error" ? "bg-down-bg text-down-ink" : key === "warning" ? "bg-degraded-bg text-degraded-ink" : "bg-muted text-foreground")}>
      <Icon className="ico" aria-hidden="true" /><span className="sr-only">{t(`notif.severity.${key}`)}: </span>
    </span>
  );
}

function Notifications() {
  const { t, relative, locale } = useI18n();
  const { status, reloadStatus } = useShell();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [limit, setLimit] = useState(30);
  const list = useResource<NotificationRow[]>(open ? "/notifications" : null, { limit });
  // het getal blijft staan terwijl de status opnieuw laadt (N15)
  const [unread, setUnread] = useState(0);
  const fresh = status?.unread_notifications;
  useEffect(() => { if (typeof fresh === "number") setUnread(fresh); }, [fresh]);
  // T12: de bel toont core's teller (ongelezen + wat op jou wacht); zonder die route de eigen lijst
  const counts = useResource<{ unread?: number; attention?: number; badge?: number }>("/notifications/count", undefined, 60_000);
  useCoreEvents("notification", () => void counts.reload());
  const todoCount = useTodoRows().length;
  const waiting = unread ? 0 : (counts.data?.attention ?? todoCount);
  const badge = counts.data?.badge ?? (unread || waiting);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // core stuurt standaard een samenvatting (≤ 120 tekens); de volledige tekst pas bij uitklappen
  const [full, setFull] = useState<Record<number, string>>({});
  const toggle = (n: NotificationRow) => {
    setExpanded((old) => { const nx = new Set(old); if (nx.has(n.id)) nx.delete(n.id); else nx.add(n.id); return nx; });
    if (full[n.id] === undefined) void api.get<NotificationRow>(`/notifications/${n.id}`).then((r) => setFull((f) => ({ ...f, [n.id]: r.body ?? "" }))).catch(() => undefined);
  };
  useCoreEvents("notification", () => { void reloadStatus(); if (open) void list.reload(); });
  // een mislukte klik meldt zich (punt 39); niet stil laten falen
  const markRead = async () => {
    try {
      await api.post("/notifications/read-all");
      await reloadStatus();
      await list.reload();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: e instanceof Error ? e.message : String(e) }));
    }
  };
  const ref = useDismiss(open, () => setOpen(false));
  return (
    <div className="notif-wrap static lg:relative" ref={ref}>
      <button type="button" className={cn(buttonVariants({ variant: "stil", size: "icoon" }), "icon-btn relative")} aria-label={unread ? t("topbar.notificationsUnread", { count: unread }) : waiting ? t("help.bellWaiting", { count: waiting }) : t("topbar.notifications")} aria-expanded={open} onClick={() => setOpen((v) => !v)} data-testid="bell">
        <Bell className="ico" aria-hidden="true" />{badge ? <span className="dot-badge cijfers absolute -top-0.5 -right-0.5 min-w-5 rounded-full bg-foreground px-1 text-center text-sm leading-5 font-bold text-background" data-testid="bell-badge">{badge > 99 ? "99+" : badge}</span> : null}
      </button>
      {open ? (
        // op de telefoon over de hele breedte onder de kopbalk, nooit links buiten beeld (punt 24)
        <div className="popover notif-popover absolute top-full right-3 left-3 z-overlay mt-1 w-auto lg:left-auto lg:right-0 lg:w-[min(24rem,calc(100vw-1.5rem))]" role="dialog" aria-label={t("topbar.notifications")} data-testid="notif-panel">
          <div className="card-header flex flex-wrap items-center justify-between gap-2 px-4 pt-3 pb-2"><strong>{t("topbar.notifications")}</strong><Button size="sm" variant="ghost" onClick={() => void markRead()} disabled={!unread}>{t("topbar.markAllRead")}</Button></div>
          {waiting ? <Link href="/#todo" className="list-row list-row--hover" onClick={() => setOpen(false)} data-testid="bell-waiting"><ListChecks className="ico" aria-hidden="true" /><div className="list-main"><div className="list-title">{t("help.bellWaiting", { count: waiting })}</div></div></Link> : null}
          <div className="list notif-list">
            {list.loading && !list.data ? <StateView kind="loading" compact /> : list.data?.length ? groupNotifications(list.data).slice(0, limit).map(({ n, count }) => {
              const isOpen = expanded.has(n.id);
              const summary = (n as { summary?: string }).summary;
              const body = plainText(arrMessage(isOpen && full[n.id] ? full[n.id] : n.body, locale), locale);
              const long = body.length > 120 || (summary !== undefined && summary !== n.body) || (summary !== undefined && body.endsWith("…"));
              const row = (
                <>
                  <SeverityIcon severity={n.severity} />
                  <div className="list-main"><div className="list-title" style={{ whiteSpace: "normal" }}>{n.title}{count > 1 ? <span className="chip ml-1.5">{count}×</span> : null}</div>
                    <div className="list-sub" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{long && !isOpen ? `${body.slice(0, 117).trimEnd()}…` : body}
                      {long ? <button type="button" className="link-more ml-1 border-0 bg-transparent p-0" onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggle(n); }}>{isOpen ? t("notif.less") : t("notif.more")}</button> : null}</div></div>
                  <span className="cijfers text-sm text-muted-foreground">{relative(n.ts)}</span>
                </>
              );
              const href = notificationHref(n.link);
              return href
                ? <Link key={n.id} href={href} className="list-row list-row--hover" onClick={() => setOpen(false)}>{row}</Link>
                : <div key={n.id} className="list-row">{row}</div>;
            }) : <StateView kind="empty" compact title={t("topbar.noNotifications")} />}
            {list.data && list.data.length >= limit ? <div className="list-row"><Button size="sm" variant="ghost" pending={list.loading} onClick={() => setLimit((l) => l + 30)}>{t("notif.loadMore")}</Button></div> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Knop (profielmenu, telefoonmenu): opent de noodstop-dialoog, die los van het menu leeft. */
function EmergencyStopButton({ onOpen }: { onOpen?: () => void }) {
  const { t } = useI18n();
  const { status } = useShell();
  const { can } = useSession();
  const active = Boolean(status?.emergency_stop?.active);
  return (
    // de knop blijft destructive (ADR-0016 punt 9); het woord staat altijd zichtbaar (anders geen naam op de telefoon)
    <button type="button" className={cn(buttonVariants({ variant: active ? "gevaar-vol" : "gevaar" }), "estop w-full justify-start")} aria-pressed={active} onClick={() => { onOpen?.(); window.dispatchEvent(new CustomEvent("mg:estop")); }} disabled={!can("operator")} title={!can("operator") ? t("state.readOnly") : undefined} data-testid="estop-button">
      <OctagonAlert className="ico" aria-hidden="true" /><span className="estop-label">{active ? t("estop.active") : t("estop.button")}</span>
    </button>
  );
}

function EmergencyStopDialog() {
  const { t } = useI18n();
  const { status, reloadStatus } = useShell();
  const toast = useToast();
  const active = Boolean(status?.emergency_stop?.active);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const on = () => setOpen(true);
    window.addEventListener("mg:estop", on);
    return () => window.removeEventListener("mg:estop", on);
  }, []);
  const submit = async () => {
    setPending(true);
    setError(null);
    try {
      const res = await api.post<EstopResult>("/system/emergency-stop", { active: !active, reason });
      await reloadStatus();
      toast.push(active ? "success" : "danger", active ? resumedText(t, res) : t("estop.done"));
      setOpen(false);
      setReason("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  };
  return (
    <Modal
      open={open}
      onClose={() => setOpen(false)}
      danger={!active}
      role="alertdialog"
      icon={OctagonAlert}
      title={active ? t("estop.releaseTitle") : t("estop.confirmTitle")}
      description={active ? t("estop.releaseBody") : t("estop.confirmBody")}
      footer={<><Button onClick={() => setOpen(false)} data-autofocus>{t("action.cancel")}</Button><Button variant={active ? "primary" : "danger-solid"} pending={pending} onClick={() => void submit()} data-testid="estop-confirm">{active ? t("estop.releaseConfirm") : t("estop.confirm")}</Button></>}
    >
      <Field label={t("estop.reasonLabel")} htmlFor="estop-reason" error={error}>
        <input id="estop-reason" className="input" value={reason} maxLength={500} placeholder={t("estop.reasonPlaceholder")} onChange={(e) => setReason(e.target.value)} />
      </Field>
    </Modal>
  );
}

/** Wat core na het opheffen hervat (rapport 6, N1); oudere core geeft niets terug. */
type EstopResult = { resumed?: { handoffs?: number; encodes?: number } | null; resumed_nl?: string | null } | null | undefined;
function resumedText(t: ReturnType<typeof useI18n>["t"], res: EstopResult): string {
  if (res?.resumed_nl) return `${t("estop.releasedPrefix")} ${res.resumed_nl}`;
  const r = res?.resumed;
  return r && (typeof r.handoffs === "number" || typeof r.encodes === "number")
    ? t("estop.resumed", { handoffs: r.handoffs ?? 0, encodes: r.encodes ?? 0 })
    : t("estop.resumedGeneric");
}

function EmergencyBanner() {
  const { t, dateTime } = useI18n();
  const toast = useToast();
  const { status, reloadStatus } = useShell();
  const { can } = useSession();
  const stop = status?.emergency_stop;
  const [pending, setPending] = useState(false);
  if (!stop?.active) return null;
  const release = async () => {
    setPending(true);
    try {
      const res = await api.post<EstopResult>("/system/emergency-stop", { active: false, reason: "" });
      await reloadStatus();
      toast.push("success", resumedText(t, res));
    } catch (e) {
      // de noodstop blijft dan staan: zeg dat (punt 39)
      toast.push("danger", t("estop.releaseFailed"), e instanceof Error ? e.message : String(e));
    } finally { setPending(false); }
  };
  return (
    // Peil (ADR-0016 punt 9): noodstop actief = bewuste stop → Gepauzeerd-vorm (circle-pause), geen storing
    <div className="banner flex flex-wrap items-center gap-2.5 border-b-2 border-paused bg-paused-bg px-4 py-2 text-base text-paused-ink" role="alert" data-testid="estop-banner" data-status="paused">
      <CirclePause className="ico size-5 shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1"><strong>{t("estop.bannerTitle")}</strong> {t("estop.bannerBody", { since: dateTime(stop.since), by: stop.by ?? "—" })}{stop.reason ? ` ${t("estop.bannerReason", { reason: stop.reason })}` : ""}</span>
      {can("operator") ? <span className="banner-actions flex gap-2"><Button size="sm" pending={pending} onClick={() => void release()}>{t("estop.release")}</Button></span> : null}
    </div>
  );
}

function ProfileMenu() {
  const { t } = useI18n();
  const { me, logout, setLocale, locale } = useSession();
  const [open, setOpen] = useState(false);
  const theme = useThemeChoice();
  const showTech = useShowTech();
  const chooseTheme = (choice: ThemeChoice) => setUserTheme(choice);
  const initials = (me?.username ?? "?").slice(0, 2).toUpperCase();
  const ref = useDismiss(open, () => setOpen(false));
  return (
    <div className="relative" ref={ref}>
      {/* WCAG 2.5.3: de zichtbare initialen staan in de toegankelijke naam ("BE profiel en thema") */}
      <button type="button" className="avatar focus-ring inline-grid size-touch cursor-pointer place-items-center rounded-full border-2 border-border-strong bg-muted text-sm font-bold text-foreground" aria-expanded={open} onClick={() => setOpen((v) => !v)} data-testid="profile-button">{initials}<span className="sr-only"> {t("topbar.profileName")}</span></button>
      {open ? (
        <div className="popover absolute top-full right-0 z-overlay mt-1 max-h-[calc(100dvh-5rem)] w-[min(20rem,calc(100vw-1.5rem))] overflow-y-auto" role="dialog" aria-label={t("topbar.profile")} data-testid="profile-menu">
          <div className="flex flex-col gap-2.5 p-3.5">
            {/* "Terug naar de Hub" als eerste item op < 1024 px (op de computer staat hij in de zijbalk) */}
            <HubLink className="lg:hidden" />
            <div><strong>{me?.username}</strong><div className="text-sm text-muted-foreground">{t("topbar.role", { role: me ? t(`role.${me.role}`) : "—" })}</div></div>
            <hr className="border-border" />
            <span className="text-sm font-semibold">{t("topbar.language")}</span>
            <div className="segmented inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-border bg-muted p-1" role="group" aria-label={t("topbar.language")}>
              {LOCALES.map((l: Locale) => <button key={l} type="button" className={SEGMENT_CLASS} aria-pressed={locale === l} onClick={() => void setLocale(l)}>{t(`locale.${l}`)}</button>)}
            </div>
            <span className="text-sm font-semibold">{t("topbar.theme")}</span>
            <div className="segmented inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-border bg-muted p-1" role="group" aria-label={t("topbar.theme")} data-testid="theme-choice">
              <button type="button" className={SEGMENT_CLASS} aria-pressed={theme === "dark"} onClick={() => chooseTheme("dark")}><Moon className="ico" aria-hidden="true" />{t("theme.dark")}</button>
              <button type="button" className={SEGMENT_CLASS} aria-pressed={theme === "light"} onClick={() => chooseTheme("light")}><Sun className="ico" aria-hidden="true" />{t("theme.light")}</button>
              <button type="button" className={SEGMENT_CLASS} aria-pressed={theme === "system"} onClick={() => chooseTheme("system")}><Monitor className="ico" aria-hidden="true" />{t("theme.system")}</button>
            </div>
            <p className="hint m-0 text-sm text-muted-foreground">{t("topbar.themeHint2")}</p>
            <hr className="border-border" />
            <div className="flex items-center justify-between gap-3" data-testid="tech-toggle">
              <span><strong className="text-base">{t("tech.toggle")}</strong><div className="text-sm text-muted-foreground">{t("tech.toggleHint")}</div></span>
              <Switch checked={showTech} onChange={setShowTech} label={t("tech.toggle")} />
            </div>
            <hr className="border-border" />
            <nav className="flex flex-col gap-1" role="menu" aria-label={t("nav.moreLinks")} data-testid="profile-links">
              {MENU_LINKS.map(([href, key], i) => (
                <Link key={href} href={href} role="menuitem" className={cn(buttonVariants({ variant: "stil" }), "justify-start", i > 0 && "lg:hidden")} onClick={() => setOpen(false)} prefetch={false}>
                  {i === 0 ? <Settings className="ico" aria-hidden="true" /> : null}{t(key)}
                </Link>
              ))}
            </nav>
            <hr className="border-border" />
            <Link href="/hoe-werkt-het" className={cn(buttonVariants({ variant: "stil" }), "justify-start")} onClick={() => setOpen(false)}><CircleHelp className="ico" aria-hidden="true" />{t("help.title")}</Link>
            <EmergencyStopButton onOpen={() => setOpen(false)} />
            <Button variant="ghost" icon={LogOut} onClick={() => void logout()}>{t("topbar.logout")}</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function TechSync() {
  useTechDetailsSync();
  useScrollableRegions();
  return null;
}

// ---------------------------------------------------------------- keyboard
function KeyboardShortcuts() {
  const { t } = useI18n();
  const router = useRouter();
  const [help, setHelp] = useState(false);
  const pendingG = useRef(0);
  const goMap = useMemo(() => ({ ...Object.fromEntries(EXTRA_SHORTCUTS), ...Object.fromEntries(ALL_NAV_ITEMS.filter((i) => i.shortcut).map((i) => [i.shortcut!.split(" ")[1], i.href])) }), []);
  const onKey = useCallback((event: KeyboardEvent) => {
    const target = event.target as HTMLElement | null;
    if (target?.closest("input, textarea, select, [contenteditable=true], [contenteditable=''], [role=dialog], [role=alertdialog]") || event.metaKey || event.ctrlKey || event.altKey || event.isComposing) return;
    // nooit sneltoetsen zolang een dialoog open is (rapport 6, N7)
    if (document.querySelector('[aria-modal="true"]')) return;
    if (event.key === "/") { event.preventDefault(); document.querySelector<HTMLInputElement>("[data-global-search]")?.focus(); return; }
    if (event.key === "?") { setHelp((v) => !v); return; }
    if (event.key === "g") { pendingG.current = Date.now(); return; }
    if (Date.now() - pendingG.current < 1200 && goMap[event.key]) { pendingG.current = 0; const href = goMap[event.key]; navigateOrAsk(href, () => router.push(href)); }
  }, [goMap, router]);
  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);
  const rows: Array<[string, string]> = [
    ["/", t("keyboard.search")], ["?", t("keyboard.help")], ["Esc", t("keyboard.close")], ["j / k", t("keyboard.rows")],
    ["g o", t("keyboard.goOverview")], ["g b", t("keyboard.goLibrary")], ["g m", t("keyboard.goWanted")], ["g a", t("keyboard.goAv1")], ["g j", t("keyboard.goJobs")], ["g s", t("keyboard.goSettings")],
  ];
  return (
    <Modal open={help} onClose={() => setHelp(false)} title={t("keyboard.title")} icon={Keyboard}>
      <dl className="inline-grid w-full grid-cols-[5rem_minmax(0,1fr)] gap-x-4 gap-y-2.5 text-base">
        {rows.map(([k, label]) => <div key={k} className="contents"><dt><Kbd>{k}</Kbd></dt><dd className="m-0">{label}</dd></div>)}
      </dl>
    </Modal>
  );
}
