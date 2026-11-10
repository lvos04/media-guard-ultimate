"use client";

import { LockKeyhole, LockKeyholeOpen } from "lucide-react";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, InlineState } from "@/components/mg/primitives";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/**
 * Softwarevergrendeling van de bibliotheek (ADR-011): zolang de overstap niet gedaan is,
 * vervangt MediaGuard geen bestanden. Bij een verse installatie zonder migratie geeft de
 * beheerder de bibliotheek hier vrij — geen TrueNAS-instelling nodig.
 */
export function LibraryLockBanner() {
  const { t } = useI18n();
  const { can } = useSession();
  const readiness = useResource<{ library_locked?: boolean }>("/recovery/readiness", undefined, 60_000);
  useCoreEvents("settings.changed", () => void readiness.reload());
  const { run, pending } = useAction();
  const [open, setOpen] = useState(false);
  if (!readiness.data?.library_locked) return null;
  const unlock = async () => {
    const r = await run("unlock", "post", "/recovery/library-write", { confirm: true }, t("lock.unlocked"));
    if (r !== undefined) { setOpen(false); void readiness.reload(); }
  };
  return (
    <div style={{ marginBottom: 16 }} data-testid="library-lock-banner">
      <InlineState tone="gate" icon={LockKeyhole} role="status">
        {/* Peil GateBlokkade: slot + neutraal-sterk; zeg erbij dat dit geen storing is */}
        <strong>{t("lock.title")}</strong> {t("lock.body")} {t("lock.notAFault")}{" "}
        {can("admin") ? <Button size="sm" icon={LockKeyholeOpen} onClick={() => setOpen(true)} data-testid="library-unlock">{t("lock.action")}</Button> : null}
      </InlineState>
      <DangerDialog
        open={open}
        onClose={() => setOpen(false)}
        onConfirm={() => void unlock()}
        icon={LockKeyholeOpen}
        title={t("lock.dialogTitle")}
        description={t("lock.dialogBody")}
        confirmLabel={t("lock.action")}
        pending={pending === "unlock"}
      />
    </div>
  );
}

/**
 * Bibliotheekslot in Instellingen: geen gewone schakelaar (rapport, item 4) maar de status
 * met de knop ‘Bibliotheek vrijgeven’ (met bevestiging) of ‘Weer vergrendelen’. Alleen voor
 * beheerders; de API weigert library.write_enabled via de gewone instellingen.
 */
export function LibraryWriteControl() {
  const { t } = useI18n();
  const { can } = useSession();
  const readiness = useResource<{ library_locked?: boolean }>("/recovery/readiness", undefined, 60_000);
  useCoreEvents("settings.changed", () => void readiness.reload());
  const { run, pending } = useAction();
  const [dialog, setDialog] = useState<"unlock" | "lock" | null>(null);
  const locked = readiness.data?.library_locked;
  if (locked === undefined) return null;
  const change = async (enabled: boolean) => {
    const r = await run(enabled ? "unlock" : "lock", "post", "/recovery/library-write", enabled ? { confirm: true, enabled: true } : { enabled: false },
      enabled ? t("lock.unlocked") : t("lockctl.locked"));
    if (r !== undefined) { setDialog(null); void readiness.reload(); }
  };
  return (
    <div className="stack" style={{ gap: 6, alignItems: "flex-end" }} data-testid="library-write-control">
      <span className="hint" style={{ margin: 0, textAlign: "right" }}>{locked ? t("lockctl.closed") : t("lockctl.open")}</span>
      {can("admin") ? (locked
        ? <Button size="sm" icon={LockKeyholeOpen} onClick={() => setDialog("unlock")}>{t("lock.action")}</Button>
        : <Button size="sm" icon={LockKeyhole} onClick={() => setDialog("lock")}>{t("lockctl.lock")}</Button>) : null}
      <DangerDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        onConfirm={() => void change(dialog === "unlock")}
        icon={dialog === "unlock" ? LockKeyholeOpen : LockKeyhole}
        title={dialog === "unlock" ? t("lock.dialogTitle") : t("lockctl.lockTitle")}
        description={dialog === "unlock" ? t("lock.dialogBody") : t("lockctl.lockBody")}
        confirmLabel={dialog === "unlock" ? t("lock.action") : t("lockctl.lock")}
        pending={pending === "unlock" || pending === "lock"}
      />
    </div>
  );
}
