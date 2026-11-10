"use client";

import { Check, Info, OctagonX, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { buttonVariants } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

type ToastTone = "success" | "danger" | "info";
interface ToastItem { id: number; tone: ToastTone; title: string; body?: string }
interface ToastApi { push: (tone: ToastTone, title: string, body?: string) => void }

const ToastContext = createContext<ToastApi>({ push: () => undefined });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const { t } = useI18n();
  const dismiss = useCallback((id: number) => setItems((all) => all.filter((i) => i.id !== id)), []);
  const push = useCallback((tone: ToastTone, title: string, body?: string) => {
    const id = Date.now() + Math.random();
    setItems((all) => [...all.slice(-3), { id, tone, title, body }]);
    window.setTimeout(() => dismiss(id), tone === "danger" ? 9000 : 5000);
  }, [dismiss]);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-region" aria-live="polite">
        {items.map((item) => {
          // Peil: "gelukt" is geen gezondheid (vinkje, neutraal); mislukt = Storing (octagon-x)
          const Icon = item.tone === "success" ? Check : item.tone === "danger" ? OctagonX : Info;
          return (
            <div key={item.id} className={`toast toast--${item.tone}`} role={item.tone === "danger" ? "alert" : "status"}>
              <Icon className="ico" aria-hidden="true" />
              <div className="grow"><strong>{item.title}</strong>{item.body ? <div className="mt-0.5 text-sm text-muted-foreground">{item.body}</div> : null}</div>
              <button type="button" className={buttonVariants({ variant: "stil", size: "icoon" })} onClick={() => dismiss(item.id)} aria-label={t("action.close")}><X className="ico" aria-hidden="true" /></button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}
