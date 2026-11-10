import {
  HardDriveDownload, Inbox, LayoutDashboard, Library, Radar, Route, Settings, type LucideIcon,
} from "lucide-react";

import type { MessageKey } from "@/lib/i18n";

export interface NavItem { key: string; href: string; label: MessageKey; icon: LucideIcon; shortcut?: string; conditional?: boolean; footer?: boolean }
export interface NavGroup { label: MessageKey | null; items: NavItem[] }

/**
 * Vijf hoofdonderdelen: Overzicht · Activiteit · Bibliotheek (met Ontbrekend en Kalender) ·
 * Ruimte (AV1, opslag en Terugzetten) · Aanvragen (Seerr). Instellingen staat los onderaan
 * (telefoon: in het profielmenu) en is één pagina met secties; zie docs/design/2026-11-feature-inventory.md.
 * Migratie verschijnt alleen zolang er een migratie loopt.
 */
export const NAV: NavGroup[] = [
  { label: null, items: [
    { key: "overview", href: "/", label: "nav.overview", icon: LayoutDashboard, shortcut: "g o" },
    { key: "activity", href: "/activiteit", label: "nav.activity", icon: Radar, shortcut: "g n" },
    { key: "library", href: "/bibliotheek", label: "nav.library", icon: Library, shortcut: "g b" },
    { key: "av1", href: "/av1", label: "nav.space", icon: HardDriveDownload, shortcut: "g a" },
    { key: "requests", href: "/aanvragen", label: "nav.requests", icon: Inbox, shortcut: "g r" },
    { key: "migration", href: "/migratie", label: "nav.migration", icon: Route, conditional: true },
    { key: "settings", href: "/instellingen", label: "nav.settings", icon: Settings, shortcut: "g s", footer: true },
  ] },
];

export const ALL_NAV_ITEMS = NAV.flatMap((g) => g.items);

/** Sneltoetsen naar pagina's die geen eigen menu-item meer hebben. */
export const EXTRA_SHORTCUTS: Array<[string, string]> = [["m", "/ontbrekend"], ["j", "/activiteit?tab=geschiedenis"]];

/** Oude menu-adressen die nu onder een ander menu-item vallen (voor de actieve markering). */
const BELONGS_TO: Array<[string, string]> = [
  ["/jobs", "/activiteit"], ["/kalender", "/bibliotheek"], ["/ontbrekend", "/bibliotheek"], ["/dolby-vision", "/instellingen"], ["/quarantaine", "/av1"],
  ["/av1/instellingen", "/instellingen"], ["/instellingen/meldingen", "/instellingen"], ["/hoe-werkt-het", "/instellingen"], ["/migratie", "/instellingen"],
  ["/inrichten", "/instellingen"], ["/systeem", "/instellingen"], ["/downloads", "/instellingen"], ["/seerr", "/instellingen"],
  ["/kwaliteit", "/instellingen"], ["/release-lab", "/instellingen"], ["/anime", "/instellingen"], ["/ai", "/instellingen"], ["/audio", "/instellingen"],
];

export function isActive(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/";
  // AV1-instellingen horen bij Instellingen, niet bij Ruimte
  if (href === "/av1" && pathname.startsWith("/av1/instellingen")) return false;
  if (pathname === href || pathname.startsWith(`${href}/`)) return true;
  return BELONGS_TO.some(([from, to]) => to === href && (pathname === from || pathname.startsWith(`${from}/`)));
}
