"use client";

import { useEffect } from "react";

/**
 * WCAG 2.1.1 (axe scrollable-region-focusable): een tabelvak (`.table-wrap`) dat horizontaal of verticaal scrolt,
 * moet met het toetsenbord bereikbaar zijn. Zodra zo'n vak echt overloopt krijgt het tabindex=0, role=region en een
 * naam (de dichtstbijzijnde kaarttitel of tabelkop); loopt het niet (meer) over, dan gaat dat er weer af. Alleen
 * uiterlijk/toegankelijkheid: inhoud, testids en gedrag van de tabel veranderen niet.
 */
export function useScrollableRegions(): void {
  useEffect(() => {
    let frame = 0;
    const label = (el: HTMLElement): string => {
      const caption = el.querySelector("caption")?.textContent?.trim();
      if (caption) return caption;
      const card = el.closest("section[aria-labelledby]");
      const id = card?.getAttribute("aria-labelledby");
      const title = id ? document.getElementById(id)?.textContent?.trim() : null;
      return title || document.querySelector("main h1")?.textContent?.trim() || "Tabel";
    };
    const apply = () => {
      frame = 0;
      document.querySelectorAll<HTMLElement>(".table-wrap").forEach((el) => {
        const scrolls = el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
        const ours = el.dataset.scrollRegion === "1";
        if (scrolls && !el.hasAttribute("tabindex")) {
          el.tabIndex = 0;
          el.setAttribute("role", "region");
          el.setAttribute("aria-label", label(el));
          el.dataset.scrollRegion = "1";
        } else if (!scrolls && ours) {
          el.removeAttribute("tabindex");
          el.removeAttribute("role");
          el.removeAttribute("aria-label");
          delete el.dataset.scrollRegion;
        }
      });
    };
    const schedule = () => { if (!frame) frame = window.requestAnimationFrame(apply); };
    schedule();
    const mo = new MutationObserver(schedule);
    mo.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", schedule);
    return () => { mo.disconnect(); window.removeEventListener("resize", schedule); if (frame) window.cancelAnimationFrame(frame); };
  }, []);
}
