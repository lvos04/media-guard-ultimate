"use client";

import { TriangleAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, Modal } from "@/components/mg/primitives";
import { useI18n } from "@/lib/i18n";
import { forgetUnsavedChanges, hasUnsavedChanges, linkLeavesPage } from "@/lib/unsaved";

/** Vraag om bevestiging vóór een andere pagina zolang er iets niet is opgeslagen (punt 24). */
export function UnsavedGuard() {
  const { t } = useI18n();
  const router = useRouter();
  const [target, setTarget] = useState<string | null>(null);
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!hasUnsavedChanges()) return;
      const anchor = (event.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!anchor) return;
      const href = linkLeavesPage(anchor, event);
      if (!href) return;
      // vóór de klik van Next.js-Link: die navigeert niet als de standaardactie al is geweigerd
      event.preventDefault();
      event.stopPropagation();
      setTarget(href);
    };
    const onLeave = (event: Event) => {
      const href = (event as CustomEvent<string>).detail;
      if (typeof href === "string") setTarget(href);
    };
    window.addEventListener("click", onClick, true);
    window.addEventListener("mg:leave", onLeave);
    return () => {
      window.removeEventListener("click", onClick, true);
      window.removeEventListener("mg:leave", onLeave);
    };
  }, []);
  const leave = () => {
    const href = target;
    setTarget(null);
    if (!href) return;
    forgetUnsavedChanges();
    router.push(href);
  };
  return (
    <Modal open={Boolean(target)} onClose={() => setTarget(null)} role="alertdialog" icon={TriangleAlert}
      title={t("unsaved.title")} description={t("unsaved.body")}
      footer={<>
        <Button onClick={() => setTarget(null)} data-autofocus data-testid="unsaved-stay">{t("unsaved.stay")}</Button>
        <Button variant="danger-solid" onClick={leave} data-testid="unsaved-leave">{t("unsaved.leave")}</Button>
      </>} />
  );
}

/** Voor navigatie vanuit code (zoekresultaat): eerst vragen als er iets niet is opgeslagen. */
export function navigateOrAsk(href: string, go: () => void): void {
  if (hasUnsavedChanges()) window.dispatchEvent(new CustomEvent("mg:leave", { detail: href }));
  else go();
}
