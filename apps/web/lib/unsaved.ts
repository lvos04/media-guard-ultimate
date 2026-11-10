"use client";

import { useEffect, useId } from "react";

/**
 * Niet-opgeslagen wijzigingen (verbeterlijst 24): formulieren melden hier dat ze iets hebben
 * dat nog niet is opgeslagen. De schil vraagt dan eerst of je echt weg wilt (link in de
 * navigatie, zoekresultaat) en de browser waarschuwt bij herladen of sluiten.
 */
const dirty = new Set<string>();

export function hasUnsavedChanges(): boolean {
  return dirty.size > 0;
}

/** Na 'Weggooien en verder': niet nog eens vragen terwijl de pagina wisselt. */
export function forgetUnsavedChanges(): void {
  dirty.clear();
}

/** Meld (zolang `isDirty`) dat dit formulier wijzigingen heeft die nog niet zijn opgeslagen. */
export function useUnsavedChanges(isDirty: boolean): void {
  const id = useId();
  useEffect(() => {
    if (!isDirty) return;
    dirty.add(id);
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // oudere browsers tonen alleen een vraag als returnValue gezet is
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      dirty.delete(id);
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [id, isDirty]);
}

/**
 * Moet een klik op deze link eerst om bevestiging vragen? Nee voor een nieuw tabblad, een
 * download, een externe link en een anker op dezelfde pagina (bv. de inhoudsopgave).
 */
export function linkLeavesPage(anchor: HTMLAnchorElement, event: MouseEvent): string | null {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  if (anchor.target && anchor.target !== "_self") return null;
  if (anchor.hasAttribute("download")) return null;
  const raw = anchor.getAttribute("href");
  if (!raw || raw.startsWith("#") || raw.startsWith("mailto:") || raw.startsWith("tel:")) return null;
  let url: URL;
  try { url = new URL(anchor.href, window.location.href); } catch { return null; }
  if (url.origin !== window.location.origin) return null;
  const here = window.location;
  if (url.pathname === here.pathname && url.search === here.search) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}
