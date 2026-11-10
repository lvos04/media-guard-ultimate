"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { glossary, type TermKey } from "@/lib/glossary";
import { useI18n } from "@/lib/i18n";

/**
 * Vakterm met stippellijn; tik of focus toont een korte uitleg met een link naar
 * 'Hoe werkt MediaGuard' (rapport 5, M10). Werkt op telefoon (tik) en met toetsenbord.
 */
export function Term({ k, children }: { k: TermKey; children?: ReactNode }) {
  const { locale, t } = useI18n();
  const entry = glossary(locale)[k];
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <span className="term" ref={ref}>
      <button type="button" className="term-btn" aria-expanded={open} aria-describedby={open ? id : undefined}
        onClick={() => setOpen((v) => !v)} data-testid={`term-${k}`}>
        {children ?? entry.term}
      </button>
      {open ? (
        <span className="term-pop" role="tooltip" id={id}>
          <strong>{entry.term}</strong>
          <span>{entry.short}</span>
          <Link href={`/hoe-werkt-het#${k}`} onClick={() => setOpen(false)}>{t("help.more")}</Link>
        </span>
      ) : null}
    </span>
  );
}
