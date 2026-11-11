"use client";

// Peil fase 2 (docs/ontwerp/PEIL_MIGRATIE.md §6.3): dezelfde exports en props als vóór de omzetting, nu gebouwd op
// de shadcn/ui-componenten in components/ui (Tailwind 4-utilities, Radix Dialog). Hookklassen die tests en CSS
// gebruiken (.btn, .chip, .skeleton, .tab, .dialog, .segmented) blijven op de elementen; data-testid's, rollen en
// toegankelijke namen veranderen niet.
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  Check, ChevronRight, CircleCheck, CircleHelp, CirclePause, ClockAlert, Copy, Info, Link2, Link2Off, Loader, LoaderCircle, Lock, OctagonX,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import {
  Children, cloneElement, isValidElement, useId, useRef, useState, type ButtonHTMLAttributes, type KeyboardEvent, type ReactElement,
  type ReactNode,
} from "react";

import { ErrorBoundary } from "@/components/mg/error-boundary";
import { badgeVariants } from "@/components/ui/badge";
import { buttonVariants, type ButtonVariantProps } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Peil (ONTWERPTAAL §4, PEIL_MIGRATIE §6.2): elke as heeft een eigen vorm; alleen `healthy` is groen en alleen voor
 * GEMETEN gezondheid (of een gemeten controle). Een goede uitkomst die geen gezondheid is ("Afgerond", "Aan",
 * "Binnen") is `success`: een neutraal label met een vinkje.
 *   healthy  → Gezond      (pil zonder vlak, circle-check)      warning → Beperkt (vlak + 2 px rand, triangle-alert)
 *   danger   → Storing     (octagon-x)                           unknown → Onbekend (stippelrand, circle-help)
 *   paused   → Gepauzeerd  (circle-pause, info-tokens)           linked / unlinked → Gekoppeld / Niet gekoppeld
 *   stale    → Verouderd   (stippelrand + arcering, clock-alert): het laatst bekende Gezond is te oud (§4.4)
 *   strong   → neutraal label met sterke rand (bv. "Ontbreekt")  success / neutral / accent / info → neutrale labels
 * `neutral` heeft geen standaardicoon: nooit het pauze-icoon voor "onbekend/uit/rustig" (Peil §7).
 */
export type Tone = "healthy" | "success" | "warning" | "danger" | "info" | "accent" | "neutral" | "strong" | "unknown" | "stale" | "paused" | "linked" | "unlinked";

export const TONE_ICON: Record<Tone, LucideIcon | null> = {
  healthy: CircleCheck,
  success: Check,
  warning: TriangleAlert,
  danger: OctagonX,
  info: Info,
  accent: LoaderCircle,
  neutral: null,
  strong: null,
  unknown: CircleHelp,
  stale: ClockAlert,
  paused: CirclePause,
  linked: Link2,
  unlinked: Link2Off,
};

/** Gezondheidsas voor forced-colors en tests (`[data-status]`); andere assen krijgen geen data-status. */
const TONE_STATUS: Partial<Record<Tone, string>> = {
  healthy: "healthy", warning: "degraded", danger: "down", unknown: "unknown", unlinked: "unknown", stale: "stale", paused: "paused",
};

/** StatusBadge / Label (C-03): status = vorm + icoon + woord (nooit alleen kleur). */
export function StatusChip({ tone, children, icon, pending = false, size, outline = false, title }: {
  tone: Tone; children: ReactNode; icon?: LucideIcon | null; pending?: boolean; size?: "lg"; outline?: boolean; title?: string;
}) {
  // Een statusvorm (gezondheid, onbekend, gepauzeerd) heeft altijd zijn icoon, ook als de aanroeper icon={null} gaf
  // (Peil §4.2: status nooit alleen kleur + woord). Alleen neutrale labels mogen zonder icoon.
  const Icon = icon === null ? (TONE_STATUS[tone] ? TONE_ICON[tone] : null) : icon ?? (pending ? LoaderCircle : TONE_ICON[tone]);
  return (
    <span className={cn(badgeVariants({ tone, size: size === "lg" ? "groot" : "standaard", outline }), pending && "chip--pending")} title={title} data-status={TONE_STATUS[tone]}>
      {Icon ? <Icon className={cn("ico", pending && "motion-safe:animate-spin")} aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function LockedChip() {
  const { t } = useI18n();
  return <StatusChip tone="neutral" icon={Lock}>{t("status.locked")}</StatusChip>;
}

/** Decoratieve stip naast een woord; `healthy` is de enige groene (gemeten gezond), `unknown` een stippelring. */
export function Dot({ tone }: { tone: "healthy" | "success" | "warning" | "danger" | "accent" | "neutral" | "unknown" }) {
  const cls = tone === "healthy" ? "bg-healthy" : tone === "warning" ? "bg-degraded" : tone === "danger" ? "bg-down"
    : tone === "unknown" ? "border-2 border-dashed border-unknown bg-transparent" : "bg-foreground";
  return <span className={cn("dot inline-block size-2.5 shrink-0 rounded-full shadow-none", cls)} aria-hidden="true" />;
}

/** Card (C-02): 1 px rand in plaats van schaduw; de titel is een h2 die de sectie benoemt; foutgrens per paneel. */
export function Card({ title, icon: Icon, sub, actions, children, className, bodyClass, footer, flush, id, tone }: {
  title?: ReactNode; icon?: LucideIcon; sub?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string;
  bodyClass?: string; footer?: ReactNode; flush?: boolean; id?: string; tone?: "danger" | "warning";
}) {
  const autoId = useId();
  const headingId = id ?? `card-${autoId}`;
  return (
    <section
      className={cn("card @container min-w-0 rounded-lg border bg-card text-card-foreground shadow-none",
        tone === "danger" ? "card--danger border-2 border-down" : tone === "warning" ? "card--warning border-2 border-degraded" : "border-border", className)}
      aria-labelledby={title ? headingId : undefined}>
      {title || actions ? (
        <div className="card-header flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 pt-4 pb-2">
          {title ? (
            <h2 className="card-title flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-lg font-semibold leading-snug" id={headingId}>
              {Icon ? <Icon className="ico size-5 shrink-0 text-muted-foreground" aria-hidden="true" /> : null}{title}
              {sub ? <span className="card-sub text-sm font-normal text-muted-foreground">{sub}</span> : null}
            </h2>
          ) : <span />}
          {actions ? <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      {/* foutgrens per paneel: een kapot onderdeel toont een melding met 'Opnieuw proberen' */}
      <ErrorBoundary>{flush ? children : <div className={cn("card-body px-4 pt-1 pb-4", bodyClass)}>{children}</div>}</ErrorBoundary>
      {footer ? <div className="card-footer flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3 text-sm text-muted-foreground">{footer}</div> : null}
    </section>
  );
}

/** PageHeader: kruimelpad (chevron-right, 44 px-links), één h1 (text-2xl), omschrijving max 65ch, acties. */
export function PageHeader({ title, sub, actions, crumbs }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; crumbs?: Array<{ label: string; href?: string }> }) {
  return (
    <div className="page-header mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0 max-w-full">
        {crumbs?.length ? (
          <nav className="crumbs mb-1 flex flex-wrap items-center gap-1 text-sm text-muted-foreground" aria-label="breadcrumb">
            {crumbs.map((c, i) => (
              <span key={`${c.label}-${i}`} className="inline-flex items-center gap-1">
                {i > 0 ? <ChevronRight className="size-4" aria-hidden="true" /> : null}
                {c.href ? <Link href={c.href} className="inline-flex min-h-touch items-center text-link">{c.label}</Link> : <span>{c.label}</span>}
              </span>
            ))}
          </nav>
        ) : null}
        <h1 className="page-title text-2xl font-bold leading-tight text-balance">{title}</h1>
        {sub ? <p className="page-sub mt-1 max-w-prose text-base text-muted-foreground">{sub}</p> : null}
      </div>
      {actions ? <div className="page-actions flex max-w-full flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

type BtnVariant = "primary" | "secondary" | "ghost" | "danger" | "danger-solid";
const VARIANT: Record<BtnVariant, NonNullable<ButtonVariantProps["variant"]>> = {
  primary: "primair", secondary: "secundair", ghost: "stil", danger: "gevaar", "danger-solid": "gevaar-vol",
};

/** Button (shadcn, Peil-codemod): ≥ 44 px; `pending` → aria-busy + draaiend icoon; label ongewijzigd. */
export function Button({ variant = "secondary", size, icon: Icon, pending, children, className, iconOnly, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant; size?: "sm"; icon?: LucideIcon; pending?: boolean; iconOnly?: boolean;
}) {
  return (
    <button
      type="button"
      {...rest}
      aria-busy={pending || undefined}
      disabled={rest.disabled || pending}
      className={cn(buttonVariants({ variant: VARIANT[variant], size: iconOnly ? "icoon" : size === "sm" ? "klein" : "standaard" }), className)}
    >
      {pending ? <Loader className="ico motion-safe:animate-spin" aria-hidden="true" /> : Icon ? <Icon className="ico" aria-hidden="true" /> : null}
      {iconOnly ? <span className="sr-only">{children}</span> : children}
    </button>
  );
}

export function LinkButton({ href, variant = "secondary", size, icon: Icon, children }: { href: string; variant?: BtnVariant; size?: "sm"; icon?: LucideIcon; children: ReactNode }) {
  return (
    <Link href={href} className={buttonVariants({ variant: VARIANT[variant], size: size === "sm" ? "klein" : "standaard" })}>
      {Icon ? <Icon className="ico" aria-hidden="true" /> : null}{children}
    </Link>
  );
}

/** Switch (role=switch): 44 px raakvlak rond een spoor van 2,75 × 1,5 rem; uit = rand, aan = primary. */
export function Switch({ checked, onChange, label, disabled, id, describedBy }: {
  checked: boolean; onChange?: (v: boolean) => void; label: string; disabled?: boolean; id?: string;
  /** id van de tekst die zegt waarom de schakelaar uit staat of wat hij doet (aria-describedby) */
  describedBy?: string;
}) {
  return (
    <button id={id} type="button" role="switch" aria-checked={checked} aria-label={label} aria-describedby={describedBy} disabled={disabled} onClick={() => onChange?.(!checked)}
      className="switch group focus-ring inline-flex min-h-touch min-w-touch shrink-0 cursor-pointer items-center justify-center rounded-full border-0 bg-transparent p-0 disabled:cursor-not-allowed">
      <span aria-hidden="true" className={cn(
        "relative inline-flex h-6 w-11 items-center rounded-full border-2 transition-colors",
        checked ? "border-primary bg-primary" : "border-input bg-card",
        "group-disabled:border-dashed group-disabled:border-border-strong group-disabled:bg-muted",
      )}>
        <span className={cn("absolute size-4 rounded-full transition-[left] motion-reduce:transition-none",
          checked ? "left-[1.375rem] bg-primary-foreground" : "left-0.5 bg-foreground", "group-disabled:bg-disabled-foreground")} />
      </span>
    </button>
  );
}

/** ToggleGroup-vorm (shadcn) met aria-pressed-knoppen in role=group; loopt om op smalle schermen, 44 px per knop. */
export function Segmented<T extends string>({ value, options, onChange, label, labelledBy, describedBy }: {
  value: T; options: Array<{ value: T; label: ReactNode; count?: number | string; disabled?: boolean; title?: string }>; onChange: (v: T) => void; label: string;
  /** id van een zichtbaar label: dan benoemt dat de groep (anders `label` als aria-label) */
  labelledBy?: string;
  /** id van de tekst die zegt waarom een uitgeschakelde keuze uit staat (aria-describedby op die knoppen) */
  describedBy?: string;
}) {
  return (
    <div className="segmented inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-border bg-muted p-1" role="group"
      {...(labelledBy ? { "aria-labelledby": labelledBy } : { "aria-label": label })}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} disabled={o.disabled} title={o.title} onClick={() => onChange(o.value)}
          aria-describedby={o.disabled ? describedBy : undefined}
          className={SEGMENT_CLASS}>
          {o.label}{o.count !== undefined ? <span className="cijfers font-normal">{o.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

/** Klassen van één segmentknop; ook voor segmenten die buiten Segmented worden opgebouwd (taal, thema). */
export const SEGMENT_CLASS = cn(
  "focus-ring inline-flex min-h-touch cursor-pointer items-center gap-1.5 rounded-md border-2 border-transparent bg-transparent px-3 text-sm font-semibold text-muted-foreground",
  "hover:text-foreground aria-pressed:border-border-strong aria-pressed:bg-card aria-pressed:text-foreground",
  "disabled:cursor-not-allowed disabled:border-dashed disabled:border-border-strong disabled:text-disabled-foreground",
  "[&_svg]:size-[1.125rem]",
);

const TAB_CLASS = cn(
  "tab focus-ring relative inline-flex min-h-touch cursor-pointer items-center gap-1.5 border-0 bg-transparent px-3 text-base font-semibold text-muted-foreground no-underline",
  "hover:text-foreground aria-selected:text-foreground aria-[current=page]:text-foreground",
  "after:absolute after:inset-x-2 after:-bottom-px after:h-1 after:rounded-full after:bg-transparent aria-selected:after:bg-primary aria-[current=page]:after:bg-primary",
);
const CHIP_TAB_CLASS = cn(
  "tab focus-ring inline-flex min-h-touch cursor-pointer items-center gap-1.5 rounded-full border-2 border-border-strong bg-card px-3 text-sm font-semibold text-foreground no-underline",
  "hover:bg-accent aria-selected:border-primary aria-selected:bg-primary aria-selected:text-primary-foreground",
);

/**
 * Tabs (COMPONENTEN §21): route-tabs (links met aria-current) of toestand-tabs (role=tablist, rovende tabindex,
 * ←/→ en Home/End). De rij loopt om in plaats van tabs achter een horizontale scroll te verstoppen.
 * `variant="chips"`: filterknoppen in plaats van een tabbalk (Activiteit, Aanvragen).
 */
export function Tabs<T extends string>({ items, value, onSelect, label, variant }: {
  items: Array<{ value: T; label: ReactNode; count?: number | string; href?: string }>; value: T; onSelect?: (v: T) => void; label: string;
  variant?: "chips";
}) {
  const ref = useRef<HTMLDivElement>(null);
  const onKey = (e: KeyboardEvent) => {
    if (!onSelect || !["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const idx = items.findIndex((i) => i.value === value);
    const nextIdx = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (idx + (e.key === "ArrowRight" ? 1 : items.length - 1)) % items.length;
    onSelect(items[nextIdx].value);
    window.setTimeout(() => ref.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus(), 0);
  };
  const chips = variant === "chips";
  const cls = chips ? CHIP_TAB_CLASS : TAB_CLASS;
  const count = (c?: number | string) => (c !== undefined ? <span className={cn("count cijfers rounded-full px-2 text-sm font-normal", chips ? "bg-transparent" : "bg-muted text-foreground")}>{c}</span> : null);
  return (
    <div className={cn("tabs flex flex-wrap gap-1", chips ? "tabs--chips gap-2" : "border-b border-border")} role={onSelect ? "tablist" : undefined} aria-label={label} ref={ref} onKeyDown={onKey}>
      {items.map((item) =>
        item.href ? (
          <Link key={item.value} href={item.href} className={cls} aria-current={item.value === value ? "page" : undefined}>
            {item.label}{count(item.count)}
          </Link>
        ) : (
          <button key={item.value} type="button" role="tab" className={cls} aria-selected={item.value === value} tabIndex={item.value === value ? 0 : -1} onClick={() => onSelect?.(item.value)}>
            {item.label}{count(item.count)}
          </button>
        ),
      )}
    </div>
  );
}

/**
 * Field: label boven het veld, hulptekst via aria-describedby, veldfout met octagon-x + aria-invalid.
 * De ids gaan naar één element-kind (cloneElement), tenzij dat ze zelf zet.
 */
export function Field({ label, hint, error, htmlFor, children }: { label: ReactNode; hint?: ReactNode; error?: ReactNode; htmlFor: string; children: ReactNode }) {
  const describedBy = error ? `${htmlFor}-error` : hint ? `${htmlFor}-hint` : undefined;
  let control = children;
  const only = Children.count(children) === 1 ? Children.toArray(children)[0] : null;
  if (describedBy && isValidElement(only) && typeof only.type === "string") {
    const el = only as ReactElement<Record<string, unknown>>;
    const extra: Record<string, unknown> = {};
    if (el.props["aria-describedby"] === undefined) extra["aria-describedby"] = describedBy;
    if (error && el.props["aria-invalid"] === undefined) extra["aria-invalid"] = true;
    control = cloneElement(el, extra);
  }
  return (
    <div>
      <label className="label mb-1.5 block text-base font-semibold text-foreground" htmlFor={htmlFor}>{label}</label>
      {control}
      {error ? <div className="field-error mt-1.5 flex items-center gap-1.5 text-sm font-semibold text-down-ink" id={`${htmlFor}-error`} role="alert"><OctagonX className="ico size-4" aria-hidden="true" />{error}</div>
        : hint ? <p className="hint mt-1.5 text-sm text-muted-foreground" id={`${htmlFor}-hint`}>{hint}</p> : null}
    </div>
  );
}

/** Melding (Peil). `success` = iets ging zoals gevraagd (neutraal met vinkje), geen gezondheid. */
export function InlineState({ tone = "info", icon: Icon, children, role }: { tone?: "info" | "warning" | "danger" | "success" | "neutral" | "gate" | "unknown"; icon?: LucideIcon; children: ReactNode; role?: "status" | "alert" }) {
  const I = Icon ?? (tone === "danger" ? OctagonX : tone === "warning" ? TriangleAlert : tone === "success" ? Check : tone === "gate" ? Lock : tone === "unknown" ? CircleHelp : Info);
  const cls = {
    info: "border-info bg-info-bg text-info-ink [&_strong]:text-info-ink",
    warning: "border-degraded bg-degraded-bg text-degraded-ink [&_strong]:text-degraded-ink",
    danger: "border-down bg-down-bg text-down-ink [&_strong]:text-down-ink",
    success: "border-border bg-card text-foreground",
    neutral: "border-border bg-muted text-foreground",
    // GateBlokkade (Peil): een wachtende voorwaarde, geen storing — slot, neutraal-sterk
    gate: "border-gate bg-gate-bg text-gate-ink [&_strong]:text-gate-ink [&>svg]:text-gate",
    // Onbekend (Peil §4.3): een bron antwoordt niet — stippelrand, nooit amber of groen
    unknown: "border-dashed border-unknown bg-unknown-bg text-unknown-ink [&_strong]:text-unknown-ink [&>svg]:text-unknown",
  }[tone];
  return (
    <div className={cn("inline-state flex items-start gap-2.5 rounded-lg border-2 px-3 py-2.5 text-sm", tone !== "neutral" && `inline-state--${tone}`, cls)} role={role} data-tone={tone} data-status={tone === "unknown" ? "unknown" : undefined}>
      <I className="ico mt-0.5 size-[1.125rem] shrink-0" aria-hidden="true" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/**
 * Progress (role=progressbar): één neutrale vulling (chart-1) op het meterspoor — 100 % is niet "groen" en een status
 * staat nooit alleen in de kleur (waarde en grens staan altijd als tekst ernaast). `tone` blijft alleen voor
 * API-compatibiliteit. Onbepaald = gearceerd "bezig"-blok.
 */
export function Progress({ value, thin, label, indeterminate }: { value: number | null | undefined; tone?: "success" | "warning" | "danger" | "cpu"; thin?: boolean; label: string; indeterminate?: boolean }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className={cn("progress relative overflow-hidden rounded-full bg-chart-meter-track", thin ? "progress--thin h-1.5" : "h-2", indeterminate && "progress--indeterminate")} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={indeterminate ? undefined : Math.round(v)}>
      <span className={cn("absolute inset-y-0 left-0 rounded-full bg-chart-1", indeterminate && "arcering-geschat w-1/3 text-chart-1")} style={indeterminate ? undefined : { width: `${v}%` }} />
    </div>
  );
}

/** Skeleton: alleen bij de eerste keer laden; geen beweging bij reduced motion. De `.skeleton`-haak blijft (e2e). */
export function Skeleton({ lines = 3, height }: { lines?: number; height?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton skeleton-line my-2 h-3.5 rounded-md bg-muted after:hidden motion-safe:animate-pulse" style={{ width: `${90 - i * 12}%`, height }} />
      ))}
    </div>
  );
}

/**
 * Peil KopieerVeld (C-15): mono, selecteerbaar, loopt om (nooit afgekapt). Het klembord alleen in een secure context;
 * anders wordt de tekst geselecteerd en zegt de app dat je via het browsermenu kopieert. Terugkoppeling via aria-live.
 */
export function CopyField({ label, value }: { label: string; value: string }) {
  const { t } = useI18n();
  const ref = useRef<HTMLElement>(null);
  const [msg, setMsg] = useState("");
  const copy = async () => {
    if (window.isSecureContext && navigator.clipboard) {
      try { await navigator.clipboard.writeText(value); setMsg(t("copy.done")); return; } catch { /* terugval: selecteren */ }
    }
    const sel = window.getSelection();
    if (ref.current && sel) { const range = document.createRange(); range.selectNodeContents(ref.current); sel.removeAllRanges(); sel.addRange(range); }
    setMsg(t("copy.selected"));
  };
  return (
    <span className="copyfield inline-flex max-w-full flex-wrap items-center justify-center gap-x-2 gap-y-1.5" data-testid="copy-field">
      <span className="text-sm text-muted-foreground">{label}</span>
      <code ref={ref} className="rounded-sm border border-border bg-muted px-1.5 py-0.5 font-mono text-sm text-foreground select-all [overflow-wrap:anywhere]">{value}</code>
      <Button size="sm" icon={Copy} onClick={() => void copy()}>{t("copy.button")}<span className="sr-only"> {label}</span></Button>
      <span className="text-sm text-muted-foreground empty:hidden" aria-live="polite">{msg}</span>
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd inline-grid h-6 min-w-6 place-items-center rounded-sm border border-b-2 border-border-strong px-1.5 font-mono text-sm font-semibold text-muted-foreground">{children}</kbd>;
}

/**
 * Dialog / AlertDialog (shadcn op Radix Dialog): portal, focusval, Escape en klik buiten sluiten, focus terug naar het
 * element dat hem opende. Eerste focus op `[data-autofocus]` als dat er is (nooit een gevaarknop), anders het eerste
 * focusbare element; opnieuw renderen verplaatst de focus nooit. `role="alertdialog"` blijft voor bevestigingen (e2e)
 * en `aria-modal` blijft (de sneltoetsen zwijgen zolang een dialoog open is). Telefoon: sheet van onder over de volle
 * breedte; desktop: gecentreerd, max 100dvh − 2 rem, de inhoud scrolt.
 */
export function Modal({ open, onClose, title, children, footer, danger, icon: Icon, labelledBy, role = "dialog", description, wide }: {
  open: boolean; onClose: () => void; title: ReactNode; children?: ReactNode; footer?: ReactNode; danger?: boolean; icon?: LucideIcon;
  labelledBy?: string; role?: "dialog" | "alertdialog"; description?: ReactNode; wide?: boolean;
}) {
  // Radix geeft de focus terug aan een Dialog.Trigger; MediaGuard opent dialogen vanuit gewone knoppen, dus de opener
  // zelf onthouden.
  const opener = useRef<HTMLElement | null>(null);
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="overlay fixed inset-0 z-overlay bg-black/50" />
        <DialogPrimitive.Content
          role={role}
          aria-modal="true"
          {...(labelledBy ? { "aria-labelledby": labelledBy } : {})}
          {...(description ? {} : { "aria-describedby": undefined })}
          onOpenAutoFocus={(e) => {
            opener.current = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
            const target = (e.currentTarget as HTMLElement | null)?.querySelector<HTMLElement>("[data-autofocus]");
            if (target) { e.preventDefault(); target.focus(); }
          }}
          onCloseAutoFocus={(e) => {
            if (opener.current?.isConnected) { e.preventDefault(); opener.current.focus(); }
            opener.current = null;
          }}
          className={cn(
            "dialog fixed z-overlay flex flex-col overflow-hidden border-2 bg-popover text-popover-foreground shadow-lg outline-none",
            "inset-x-0 bottom-0 max-h-[calc(100dvh-1rem)] rounded-t-xl",
            "sm:inset-auto sm:top-1/2 sm:left-1/2 sm:max-h-[calc(100dvh-2rem)] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-xl",
            wide ? "sm:w-[min(55rem,calc(100vw-2rem))]" : "sm:w-[min(35rem,calc(100vw-2rem))]",
            danger ? "dialog--danger border-destructive" : "border-border",
          )}
        >
          <div className="dialog-head flex flex-none items-start gap-3 px-5 pt-5 pb-2">
            {Icon ? (
              <span className={cn("inline-grid size-10 shrink-0 place-items-center rounded-lg", danger ? "bg-down-bg text-down-ink" : "bg-muted text-foreground")}>
                <Icon className="ico size-5" aria-hidden="true" />
              </span>
            ) : null}
            <div className="min-w-0">
              <DialogPrimitive.Title className="dialog-title text-xl font-bold leading-snug">{title}</DialogPrimitive.Title>
              {description ? <DialogPrimitive.Description className="mt-1 text-base text-muted-foreground">{description}</DialogPrimitive.Description> : null}
            </div>
          </div>
          {children ? <div className="dialog-body min-h-0 flex-auto overflow-y-auto overscroll-contain px-5 pt-1.5 pb-4 text-base text-foreground">{children}</div> : null}
          {footer ? <div className="dialog-foot flex flex-none flex-wrap justify-end gap-2 border-t border-border px-5 pt-3 pb-4">{footer}</div> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
