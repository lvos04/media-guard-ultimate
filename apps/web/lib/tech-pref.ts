"use client";

import { useEffect, useSyncExternalStore } from "react";

/** Per kijker: technische details (JSON, ID's, paden, codes) standaard ingeklapt of uitgeklapt. */
const KEY = "mg.showTech";
const EVENT = "mg:tech-pref";

function read(): boolean {
  try { return window.localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export function setShowTech(on: boolean): void {
  try { window.localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* privévenster: alleen deze sessie */ }
  memory = on;
  window.dispatchEvent(new Event(EVENT));
}

let memory: boolean | null = null;
const subscribe = (cb: () => void) => {
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => { window.removeEventListener(EVENT, cb); window.removeEventListener("storage", cb); };
};

export function useShowTech(): boolean {
  return useSyncExternalStore(subscribe, () => memory ?? read(), () => false);
}

/** Klapt elk `details.tech-details` op de pagina open of dicht volgens de voorkeur, ook later toegevoegde. */
export function useTechDetailsSync(): void {
  const on = useShowTech();
  useEffect(() => {
    const apply = (root: ParentNode) => root.querySelectorAll<HTMLDetailsElement>("details.tech-details").forEach((d) => {
      if (d.dataset.techPref !== String(on)) { d.open = on; d.dataset.techPref = String(on); }
    });
    apply(document);
    const obs = new MutationObserver((muts) => {
      for (const m of muts) m.addedNodes.forEach((n) => { if (n instanceof Element) { if (n.matches("details.tech-details")) apply(n.parentNode ?? document); else apply(n); } });
    });
    obs.observe(document.body, { childList: true, subtree: true });
    return () => obs.disconnect();
  }, [on]);
}
