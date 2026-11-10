"use client";

import { Trash2, Undo2 } from "lucide-react";
import { useState } from "react";

import { Button, InlineState } from "@/components/mg/primitives";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

interface TrashItem { id: number; name: string; size: number; reason: string; moved_at: string; purge_after: string }
interface CleanupStatus {
  enabled: boolean;
  interval_hours: number;
  trash_days: number;
  last_run: null | { finished_at?: string | null; started_at: string; summary_nl?: string; refused_roots?: { path: string; reason: string }[] };
  freed_total_bytes: number;
  trash: TrashItem[];
  trash_bytes: number;
}

/**
 * Downloadmappen opruimen (Systeem → Opslag). Gebeurt vanzelf; hier zie je wat er is
 * opgeruimd, wat nog in de prullenbak ligt, en kun je iets terugzetten.
 */
export function DownloadCleanup() {
  const { t, num, bytes, dateTime } = useI18n();
  const { can } = useSession();
  const status = useResource<CleanupStatus>("/downloads/cleanup", undefined, 120_000);
  const toast = useToast();
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const s = status.data;
  // oudere core zonder deze functie: niets tonen
  if (!s) return null;
  const act = async (key: string, path: string, done: string) => {
    setPending(key);
    setError(null);
    try {
      await api.post(path, {});
      toast.push("success", done);
      void status.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
    }
  };
  const last = s.last_run;
  return (
    <div className="list" data-testid="download-cleanup" style={{ marginTop: 12 }}>
      <div className="list-row list-row--wrap">
        <div className="list-main">
          <div className="list-title"><Trash2 className="ico" aria-hidden="true" /> {t("cleanup.title")}</div>
          <div className="list-sub">
            {!s.enabled ? t("cleanup.off")
              : last ? t("cleanup.lastRun", { when: dateTime(last.finished_at ?? last.started_at), summary: last.summary_nl ?? "" })
              : t("cleanup.never", { hours: num(s.interval_hours) })}
          </div>
          <div className="list-sub">{t("cleanup.how", { days: num(s.trash_days) })}</div>
          <div className="list-sub">{t("cleanup.total", { bytes: bytes(s.freed_total_bytes) })} · {t("cleanup.inTrash", { count: num(s.trash.length), bytes: bytes(s.trash_bytes) })}</div>
        </div>
        {can("operator") && s.enabled ? (
          <span className="list-aside">
            <Button size="sm" pending={pending === "run"} onClick={() => void act("run", "/downloads/cleanup/run", t("cleanup.started"))}>{t("cleanup.runNow")}</Button>
          </span>
        ) : null}
      </div>
      {(last?.refused_roots ?? []).map((r) => (
        <div className="list-row" key={r.path}><div className="list-main"><div className="list-sub">{t("cleanup.refused", { path: r.path, reason: r.reason })}</div></div></div>
      ))}
      {error ? <div className="list-row"><InlineState tone="danger" role="alert">{error}</InlineState></div> : null}
      {s.trash.slice(0, 10).map((item) => (
        <div className="list-row list-row--wrap" key={item.id}>
          <div className="list-main">
            <div className="list-title" style={{ wordBreak: "break-word" }}>{item.name}</div>
            <div className="list-sub">{[bytes(item.size), item.reason, t("cleanup.purgeAt", { when: dateTime(item.purge_after) })].join(" · ")}</div>
          </div>
          {can("admin") ? (
            <span className="list-aside">
              <Button size="sm" variant="ghost" icon={Undo2} pending={pending === `r-${item.id}`} onClick={() => void act(`r-${item.id}`, `/downloads/cleanup/trash/${item.id}/restore`, t("cleanup.restored"))}>{t("cleanup.restore")}</Button>
            </span>
          ) : null}
        </div>
      ))}
    </div>
  );
}
