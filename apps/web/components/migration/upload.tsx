"use client";

import { CircleCheck, CircleX, FileArchive, Upload } from "lucide-react";
import { useId, useRef, useState, type ReactNode } from "react";

import { Progress } from "@/components/mg/primitives";
import { ApiError } from "@/lib/api/client";
import { uploadFile, type UploadProgress } from "@/lib/api/migration";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

interface UploadRow { key: string; name: string; size: number; progress: UploadProgress | null; state: "waiting" | "uploading" | "checking" | "done" | "error"; message?: ReactNode }

/**
 * Drop zone + file picker that uploads each chosen file (sequentially, one request per file) with
 * byte progress, then shows the core's verdict per file (success summary or its Dutch error).
 */
export function UploadZone<T>({ path, accept, multiple, fields, label, hint, disabled, onUploaded, describe, testId }: {
  path: string; accept: string; multiple?: boolean; fields?: (file: File) => Record<string, string | null | undefined>;
  label: string; hint?: ReactNode; disabled?: boolean; onUploaded?: (result: T, file: File) => void;
  describe?: (result: T) => ReactNode; testId?: string;
}) {
  const { t, bytes, pct } = useI18n();
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [rows, setRows] = useState<UploadRow[]>([]);
  const [over, setOver] = useState(false);
  const busy = rows.some((r) => r.state === "uploading" || r.state === "checking" || r.state === "waiting");

  const patch = (key: string, next: Partial<UploadRow>) => setRows((old) => old.map((r) => (r.key === key ? { ...r, ...next } : r)));

  const start = async (files: File[]) => {
    if (!files.length) return;
    const batch = files.map((f, i) => ({ key: `${Date.now()}-${i}-${f.name}`, name: f.name, size: f.size, progress: null, state: "waiting" as const }));
    setRows((old) => [...batch, ...old.filter((r) => r.state !== "waiting")].slice(0, 12));
    for (let i = 0; i < files.length; i += 1) {
      const file = files[i];
      const key = batch[i].key;
      patch(key, { state: "uploading" });
      try {
        const result = await uploadFile<T>(path, { ...(fields?.(file) ?? {}), file }, (p) => {
          patch(key, { progress: p, state: p.total && p.loaded >= p.total ? "checking" : "uploading" });
        });
        patch(key, { state: "done", message: describe?.(result) });
        onUploaded?.(result, file);
      } catch (error) {
        const message = error instanceof ApiError ? (error.kind === "offline" ? t("state.offlineTitle") : error.message) : String(error);
        patch(key, { state: "error", message });
      }
    }
  };

  return (
    <div className="stack-sm">
      <label
        htmlFor={inputId}
        className={cn("dropzone", over && "dropzone--over", disabled && "dropzone--disabled")}
        onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); if (!disabled && !busy) void start(Array.from(e.dataTransfer.files)); }}
        data-testid={testId}
      >
        <Upload className="ico ico-lg" aria-hidden="true" />
        <strong>{label}</strong>
        {hint ? <span className="t-3" style={{ fontSize: 14 }}>{hint}</span> : null}
        <span className="btn btn--sm" aria-hidden="true">{t("upload.choose")}</span>
        <input
          ref={input}
          id={inputId}
          type="file"
          className="sr-only"
          accept={accept}
          multiple={multiple}
          disabled={disabled || busy}
          onChange={(e) => { const files = Array.from(e.target.files ?? []); e.target.value = ""; void start(files); }}
        />
      </label>
      {rows.length ? (
        <ul className="list upload-list" aria-live="polite">
          {rows.map((r) => {
            const percent = r.progress?.total ? (r.progress.loaded / r.progress.total) * 100 : null;
            return (
              <li key={r.key} className="list-row" data-upload-state={r.state} style={{ alignItems: "flex-start" }}>
                <span className={cn("list-icon", r.state === "done" && "list-icon--success", r.state === "error" && "list-icon--danger")}>
                  {r.state === "done" ? <CircleCheck className="ico" aria-hidden="true" /> : r.state === "error" ? <CircleX className="ico" aria-hidden="true" /> : <FileArchive className="ico" aria-hidden="true" />}
                </span>
                <div className="list-main stack-sm" style={{ gap: 6 }}>
                  <div className="row-between wrap"><span className="list-title mono" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{r.name}</span><span className="t-3 num" style={{ fontSize: 14 }}>{bytes(r.size)}</span></div>
                  {r.state === "uploading" || r.state === "waiting" || r.state === "checking" ? (
                    <>
                      <Progress value={percent} indeterminate={r.state !== "uploading" || percent === null} thin label={t("upload.progress", { name: r.name })} />
                      <span className="t-3" style={{ fontSize: 14 }}>
                        {r.state === "waiting" ? t("upload.waiting") : r.state === "checking" ? t("upload.checking") : t("upload.sending", { done: bytes(r.progress?.loaded ?? 0), total: bytes(r.size), pct: pct(percent) })}
                      </span>
                    </>
                  ) : null}
                  {r.message ? <div className={r.state === "error" ? "t-danger" : "t-2"} style={{ fontSize: 14 }} role={r.state === "error" ? "alert" : undefined}>{r.message}</div> : null}
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
