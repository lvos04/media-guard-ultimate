"use client";

import { FileClock, History } from "lucide-react";
import { useState } from "react";

import { Button, Card, Field, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { UploadZone } from "@/components/migration/upload";
import { useToast } from "@/components/mg/toast";
import { useResource } from "@/lib/api/hooks";
import { uploadFile, type Av1Import, type PathRule } from "@/lib/api/migration";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

interface Provenance { source_instance: string; source_kind: "job" | "bestand"; source_id: number; mediaguard_ref: string | null; instance_id: string | null; status: string; path: string | null; mapped_path: string | null; size: number | null; imported_at: string }
interface Av1ImportResult { import_id: number; sha256: string; counts: Record<string, number> }

/** Import of the old AV1 app's history (POST /migration/av1/import) + provenance overview. */
export function Av1HistoryImport() {
  const { t, num, bytes, dateTime } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const [pathPending, setPathPending] = useState(false);
  const imports = useResource<Av1Import[]>("/migration/av1/imports");
  const prov = useResource<Provenance[]>("/migration/av1/provenance", { limit: 100 });
  const [sourceInstance, setSourceInstance] = useState("av1-ubuntu");
  const [rules, setRules] = useState("");
  const [path, setPath] = useState("");
  const parsedRules = (): PathRule[] => rules.split("\n").map((l) => l.split("=>").map((x) => x.trim())).filter((p) => p.length === 2 && p[0] && p[1]).map(([from, to]) => ({ from, to }));
  const refresh = () => { void imports.reload(); void prov.reload(); };
  const describe = (r: Av1ImportResult) => t("av1import.done", { id: r.import_id, counts: Object.entries(r.counts).map(([k, v]) => `${k} ${num(v)}`).join(" · ") });
  return (
    <div className="grid">
      <Card className="span-5" title={t("av1import.title")} icon={History} sub={t("av1import.sub")}>
        <div className="stack">
          <p className="t-2" style={{ fontSize: 14, margin: 0 }}>{t("av1import.body")}</p>
          <Field label={t("av1import.sourceInstance")} htmlFor="av1-src"><input id="av1-src" className="input mono" value={sourceInstance} onChange={(e) => setSourceInstance(e.target.value)} /></Field>
          <Field label={t("av1import.rules")} htmlFor="av1-rules" hint={t("av1import.rulesHint")}><textarea id="av1-rules" className="textarea mono" rows={3} value={rules} placeholder="/mnt/media => /media" onChange={(e) => setRules(e.target.value)} /></Field>
          <UploadZone<Av1ImportResult> path="/migration/av1/import" accept=".db,.sqlite,.sqlite3,application/octet-stream" disabled={!can("admin")}
            label={t("av1import.drop")} hint={can("admin") ? t("av1import.dropHint") : t("mig.adminOnly")}
            fields={() => ({ source_instance: sourceInstance.trim() || "av1-ubuntu", path_map: JSON.stringify(parsedRules()) })}
            onUploaded={refresh} describe={describe} />
          <Field label={t("av1import.path")} htmlFor="av1-path" hint={t("av1import.pathHint")}><input id="av1-path" className="input mono" value={path} placeholder="/config/av1/av1.db" onChange={(e) => setPath(e.target.value)} /></Field>
          <div><Button size="sm" disabled={!can("admin") || !path.trim().startsWith("/")} pending={pathPending} onClick={() => void importPath()}>{t("av1import.fromPath")}</Button></div>
          <InlineState tone="info">{t("av1import.safe")}</InlineState>
        </div>
      </Card>
      <div className="span-7 stack">
        <Card title={t("av1import.history")} icon={FileClock} flush>
          <ResourceView resource={imports} endpoint="GET /api/v1/migration/av1/imports" compact isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("av1import.none")} />}>
            {(rows) => (
              <div className="list">{rows.map((r) => (
                <div className="list-row" key={r.id}>
                  <div className="list-main"><div className="list-title">#{r.id} · <span className="mono">{r.source_instance}</span></div><div className="list-sub">{dateTime(r.created_at)} · {Object.entries(r.counts).map(([k, v]) => `${k} ${num(v)}`).join(" · ")}</div></div>
                  <span className="t-3 mono" style={{ fontSize: 14 }} title={r.sha256}>{r.sha256.slice(0, 12)}</span>
                </div>
              ))}</div>
            )}
          </ResourceView>
        </Card>
        <Card title={t("av1import.provenance")} flush>
          <ResourceView resource={prov} endpoint="GET /api/v1/migration/av1/provenance" compact isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("av1import.noProvenance")} />}>
            {(rows) => (
              <div className="table-wrap">
                <table className="table table--compact table--stack">
                  <thead><tr><th>{t("av1import.col.file")}</th><th>{t("av1import.col.ref")}</th><th>{t("library.col.status")}</th><th className="num">{t("av1import.col.size")}</th></tr></thead>
                  <tbody>{rows.map((r) => (
                    <tr key={`${r.source_instance}-${r.source_kind}-${r.source_id}`}>
                      <td className="cell-primary"><div className="cell-title mono" style={{ fontSize: 14, whiteSpace: "normal", overflowWrap: "anywhere" }}>{r.mapped_path ?? r.path ?? "—"}</div><div className="cell-sub">{r.source_kind} #{r.source_id}</div></td>
                      <td data-label={t("av1import.col.ref")} className="mono" style={{ fontSize: 14 }}>{r.mediaguard_ref ?? "—"}</td>
                      <td data-label={t("library.col.status")}><StatusChip tone={r.status === "av1_unknown_source" ? "warning" : r.mediaguard_ref ? "success" : "neutral"} icon={null}>{r.status}</StatusChip></td>
                      <td data-label={t("av1import.col.size")} className="num">{bytes(r.size)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </ResourceView>
        </Card>
      </div>
    </div>
  );

  async function importPath() {
    // same multipart route as the upload, with the `path` form field instead of a file
    setPathPending(true);
    try {
      const r = await uploadFile<Av1ImportResult>("/migration/av1/import", { path: path.trim(), source_instance: sourceInstance.trim() || "av1-ubuntu", path_map: JSON.stringify(parsedRules()) });
      toast.push("success", describe(r));
      refresh();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: e instanceof Error ? e.message : String(e) }));
    } finally {
      setPathPending(false);
    }
  }
}
