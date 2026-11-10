"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { nl, type MessageKey } from "./messages";

export type Locale = "nl-NL" | "en-US";
export const LOCALES: Locale[] = ["nl-NL", "en-US"];
export type { MessageKey };

type Vars = Record<string, string | number>;

// Engels wordt pas geladen als iemand het kiest (scheelt ~31 KB gz voor Nederlandse gebruikers).
const dictionaries: Partial<Record<Locale, Record<MessageKey, string>>> = { "nl-NL": nl };
let enLoading: Promise<void> | null = null;
function loadLocale(locale: Locale): Promise<void> | null {
  if (dictionaries[locale]) return null;
  if (locale === "en-US") {
    enLoading ??= import("./messages/en").then((m) => { dictionaries["en-US"] = m.en; });
    return enLoading;
  }
  return null;
}

function interpolate(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (_, name: string) => (name in vars ? String(vars[name]) : `{${name}}`));
}

// "ding(en)", "film(s)", "oorza(a)k(en)" → enkelvoud of meervoud op basis van {count} (N21).
const PLURAL_RE = /([A-Za-zÀ-ÿ]+)\((en|s|n|e|'s)\)/g;
function countOf(vars?: Vars): number | null {
  const raw = vars?.count ?? vars?.n;
  if (raw === undefined) return null;
  const n = typeof raw === "number" ? raw : Number(String(raw).replace(/[^\d-]/g, ""));
  return Number.isFinite(n) ? n : null;
}
export function resolvePlurals(template: string, count: number | null): string {
  if (!template.includes("(")) return template;
  const one = count === 1;
  return template
    .replace(/oorza\(a\)k\(en\)/g, one ? "oorzaak" : "oorzaken")
    .replace(PLURAL_RE, (_, word: string, suffix: string) => (one ? word : word + suffix));
}

export function translate(locale: Locale, key: MessageKey, vars?: Vars): string {
  return interpolate(resolvePlurals(dictionaries[locale]?.[key] ?? nl[key] ?? key, countOf(vars)), vars);
}

export interface Formatters {
  locale: Locale;
  t: (key: MessageKey, vars?: Vars) => string;
  /** Plural helper: picks `${key}.one` / `${key}.other`. */
  tp: (key: string, count: number, vars?: Vars) => string;
  num: (value: number | null | undefined, digits?: number) => string;
  pct: (value: number | null | undefined, digits?: number) => string;
  bytes: (value: number | null | undefined) => string;
  date: (value: string | number | Date | null | undefined) => string;
  dateTime: (value: string | number | Date | null | undefined) => string;
  time: (value: string | number | Date | null | undefined) => string;
  relative: (value: string | number | Date | null | undefined) => string;
  duration: (seconds: number | null | undefined) => string;
  money: (value: number | null | undefined, currency?: string) => string;
}

const DASH = "—";

/** Tijdstempels zonder tijdzone (SQLite "2026-11-05 10:50:00", ISO zonder Z) zijn UTC (S18). */
export function parseServerDate(value: string | number | Date): Date | null {
  if (value instanceof Date) return Number.isNaN(value.valueOf()) ? null : value;
  if (typeof value === "string") {
    const v = value.trim();
    const m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/.exec(v);
    if (m) value = `${m[1]}T${m[2]}Z`;
  }
  const d = new Date(value);
  return Number.isNaN(d.valueOf()) ? null : d;
}
const toDate = parseServerDate;
const TZ = "Europe/Amsterdam";

export function makeFormatters(locale: Locale): Formatters {
  const t = (key: MessageKey, vars?: Vars) => translate(locale, key, vars);
  const num = (value: number | null | undefined, digits = 0) =>
    value === null || value === undefined || Number.isNaN(value)
      ? DASH
      : value.toLocaleString(locale, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  return {
    locale,
    t,
    tp: (key, count, vars) => {
      const k = `${key}.${count === 1 ? "one" : "other"}` as MessageKey;
      return translate(locale, k, { count: num(count), ...vars });
    },
    num,
    pct: (value, digits = 0) => (value === null || value === undefined ? DASH : `${num(value, digits)}%`),
    bytes: (value) => {
      if (value === null || value === undefined) return DASH;
      const units = ["B", "KB", "MB", "GB", "TB", "PB"];
      let v = Math.abs(value);
      let i = 0;
      while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i += 1;
      }
      return `${value < 0 ? "−" : ""}${v.toLocaleString(locale, { maximumFractionDigits: v >= 100 ? 0 : 1 })} ${units[i]}`;
    },
    date: (value) => {
      if (value === null || value === undefined) return DASH;
      const d = toDate(value);
      return d ? d.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric", timeZone: TZ }) : DASH;
    },
    dateTime: (value) => {
      if (value === null || value === undefined) return DASH;
      const d = toDate(value);
      return d ? d.toLocaleString(locale, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: TZ }) : DASH;
    },
    time: (value) => {
      if (value === null || value === undefined) return DASH;
      const d = toDate(value);
      return d ? d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", timeZone: TZ }) : DASH;
    },
    relative: (value) => {
      if (value === null || value === undefined) return DASH;
      const d = toDate(value);
      if (!d) return DASH;
      const seconds = Math.round((d.valueOf() - Date.now()) / 1000);
      const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
      const abs = Math.abs(seconds);
      if (abs < 60) return rtf.format(seconds, "second");
      if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
      if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
      return rtf.format(Math.round(seconds / 86400), "day");
    },
    duration: (seconds) => {
      if (seconds === null || seconds === undefined) return DASH;
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = Math.floor(seconds % 60);
      const hu = locale === "nl-NL" ? "u" : "h";
      return h > 0 ? `${h}${hu} ${m}m` : `${m}m ${s}s`;
    },
    money: (value, currency = "EUR") =>
      value === null || value === undefined ? DASH : value.toLocaleString(locale, { style: "currency", currency, maximumFractionDigits: 3 }),
  };
}

const I18nContext = createContext<Formatters>(makeFormatters("nl-NL"));

export function I18nProvider({ locale, children }: { locale: Locale; children: ReactNode }) {
  const [, setLoaded] = useState(0);
  useEffect(() => {
    const pending = loadLocale(locale);
    if (pending) void pending.then(() => setLoaded((n) => n + 1));
  }, [locale]);
  const ready = Boolean(dictionaries[locale]);
  const value = useMemo(() => makeFormatters(locale), [locale, ready]); // eslint-disable-line react-hooks/exhaustive-deps
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): Formatters {
  return useContext(I18nContext);
}

export function useT() {
  const { t } = useContext(I18nContext);
  return useCallback((key: MessageKey, vars?: Vars) => t(key, vars), [t]);
}

export function isLocale(value: unknown): value is Locale {
  return value === "nl-NL" || value === "en-US";
}
