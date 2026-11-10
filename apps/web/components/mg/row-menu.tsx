"use client";

import { Ellipsis } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useI18n } from "@/lib/i18n";

/**
 * Eén primaire knop per rij; de rest (en alles wat gevaarlijk is) zit achter ⋯ (L4/L5, 8D).
 * Kinderen zijn knoppen; het menu sluit na een klik, op Escape en bij klikken erbuiten.
 */
export function RowMenu({ children, label }: { children: ReactNode; label?: string }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("pointerdown", onDown); };
  }, [open]);
  return (
    <span className="row-menu" ref={ref}>
      <button type="button" className="btn btn--sm btn--ghost btn--icon" aria-haspopup="menu" aria-expanded={open} aria-label={label ?? t("rowMenu.more")} title={label ?? t("rowMenu.more")} onClick={() => setOpen((v) => !v)} data-testid="row-menu">
        <Ellipsis className="ico" aria-hidden="true" />
      </button>
      {open ? <div className="row-menu-list" role="menu" onClick={() => setOpen(false)}>{children}</div> : null}
    </span>
  );
}
