"use client";

import { CircleAlert, CircleHelp, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card } from "@/components/mg/primitives";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { plainText } from "@/lib/labels";
import { useSession } from "@/lib/session";

export interface AttentionAction { label_nl: string; method: "post"; path: string; body: Record<string, unknown>; confirm_nl?: string | null; role: "operator" | "admin"; primary?: boolean }
/** `actions` kan ontbreken: core stuurt rijen zonder knoppen zonder dit veld (bv. security.twofa_off:
 *  beheerders zonder tweestapsverificatie). Dat liet de Problemen-tab crashen. */
export interface AttentionRow { id: string | number; title: string; detail?: string; href?: string | null; actions?: AttentionAction[] }
export interface AttentionItem { key: string; severity: "error" | "warning" | "info"; title_nl: string; detail_nl: string; href: string; count: number; actions: AttentionAction[]; rows: AttentionRow[]; push?: boolean }
/** dismissed = punten die met 'Bewust zo gekozen' verborgen zijn (terughalen kan) */
interface AttentionResponse { items: AttentionItem[]; count: number; errors: number; dismissed?: number }

/** 'Bewust zo gekozen' verbergt alleen; het is geen handeling die iets oplost. */
export const DISMISS_PATH = "/attention/dismiss";
export const isDismissAction = (a: AttentionAction) => a.path === DISMISS_PATH;

/** 'Wacht op jou' uit core (/attention): alles waar MediaGuard op een beslissing wacht. */
export function useAttention() {
  const res = useResource<AttentionResponse>("/attention", undefined, 60_000);
  useCoreEvents("encoding.", () => void res.reload());
  useCoreEvents("recovery.", () => void res.reload());
  useCoreEvents("instance.", () => void res.reload());
  return res;
}

const ICON = { error: CircleAlert, warning: TriangleAlert, info: CircleHelp } as const;

function ActionButton({ a, onDone, size = "sm" }: { a: AttentionAction; onDone: () => void; size?: "sm" }) {
  const { can } = useSession();
  const { run, pending } = useAction();
  const [open, setOpen] = useState(false);
  if (!can(a.role)) return null;
  const go = async () => {
    const r = await run(a.path, a.method, a.path, a.body, a.label_nl);
    setOpen(false);
    if (r !== undefined) onDone();
  };
  return (
    <>
      <Button size={size} variant={a.primary ? "primary" : "ghost"} pending={pending === a.path}
        onClick={(e) => { e.preventDefault(); if (a.confirm_nl) setOpen(true); else void go(); }}>{a.label_nl}</Button>
      {a.confirm_nl ? (
        <DangerDialog open={open} onClose={() => setOpen(false)} onConfirm={() => void go()} title={a.label_nl}
          description={a.confirm_nl} confirmLabel={a.label_nl} pending={pending === a.path} />
      ) : null}
    </>
  );
}

/** Verborgen punten ('Bewust zo gekozen') met één knop om ze weer te tonen. */
export function DismissedNote({ count, onChange }: { count: number | undefined; onChange: () => void }) {
  const { t, num } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  if (!count) return null;
  return (
    <div className="list-row list-row--wrap" data-testid="attention-dismissed">
      <div className="list-main">
        <div className="list-title">{t("attention.dismissedCount", { count: num(count) })}</div>
        <div className="list-sub">{t("attention.dismissedSub")}</div>
      </div>
      {can("operator") ? (
        <span className="list-aside">
          <Button size="sm" variant="ghost" pending={pending === "restore"}
            onClick={() => void run("restore", "post", "/attention/dismissed/restore", {}, t("attention.restored")).then((r) => { if (r !== undefined) onChange(); })}>
            {t("attention.restore")}
          </Button>
        </span>
      ) : null}
    </div>
  );
}

/** Volledige lijst met per item de rijen en knoppen (Activiteit → Problemen). */
export function AttentionList({ items, onChange }: { items: AttentionItem[]; onChange: () => void }) {
  const { t, num, locale } = useI18n();
  if (!items.length) return null;
  return (
    <div className="stack" data-testid="attention-list">
      {items.map((it) => {
        const Icon = ICON[it.severity] ?? CircleHelp;
        const tone = it.severity === "error" ? "danger" : it.severity === "warning" ? "warning" : "info";
        return (
          <Card key={it.key} title={it.title_nl} icon={Icon} sub={it.count > 1 ? num(it.count) : undefined} flush>
            <div className="list">
              <div className="list-row list-row--wrap">
                <span className={`list-icon list-icon--${tone}`}><Icon className="ico" aria-hidden="true" /></span>
                <div className="list-main"><div className="list-sub" style={{ whiteSpace: "normal" }}>{plainText(it.detail_nl, locale)}</div></div>
                <span className="list-aside" style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
                  {it.actions.map((a) => <ActionButton key={a.path} a={a} onDone={onChange} />)}
                  <Link className="btn btn--sm" href={it.href}>{t("attention.open")}</Link>
                </span>
              </div>
              {it.rows.map((r) => { const rowActions = r.actions ?? []; return (
                <div className="list-row list-row--wrap" key={String(r.id)}>
                  <div className="list-main">
                    <div className="list-title" style={{ wordBreak: "break-word" }}>{r.href ? <Link href={r.href}>{r.title}</Link> : r.title}</div>
                    {r.detail ? <div className="list-sub" style={{ whiteSpace: "normal" }}>{plainText(r.detail, locale)}</div> : null}
                  </div>
                  {rowActions.length ? (
                    <span className="list-aside" style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
                      {rowActions.map((a) => <ActionButton key={a.path} a={a} onDone={onChange} />)}
                    </span>
                  ) : null}
                </div>
              ); })}
              {it.count > it.rows.length && it.rows.length ? (
                <div className="list-row"><Link className="list-sub" href={it.href}>{t("attention.more", { n: num(it.count - it.rows.length) })}</Link></div>
              ) : null}
            </div>
          </Card>
        );
      })}
    </div>
  );
}
