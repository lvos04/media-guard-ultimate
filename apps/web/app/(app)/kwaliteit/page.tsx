"use client";

import { Gauge, History, ListTree, RotateCcw, Upload } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button, Card, Field, InlineState, Modal, PageHeader, StatusChip, Tabs } from "@/components/mg/primitives";
import { SettingsLink } from "@/components/mg/settings-link";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { PolicyProfile } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { LibraryOptimize } from "@/components/optimization/library-optimize";
import { OptimizeTab } from "@/components/provisioning/optimize";
import { SettingsTabs } from "@/components/shell/settings-tabs";
import { useSession } from "@/lib/session";

type QTab = "profielen" | "optimaliseren" | "instellingen";
const QTABS: QTab[] = ["profielen", "optimaliseren", "instellingen"];

export default function QualityPage() {
  return <Suspense><Quality /></Suspense>;
}

/** Eén plek voor kwaliteit en optimaliseren (was verspreid over Migratie, Koppelingen en Bibliotheek). */
function Quality() {
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const raw = params.get("tab");
  const tab: QTab = QTABS.includes(raw as QTab) ? (raw as QTab) : "profielen";
  const go = (next: QTab) => {
    const q = new URLSearchParams(next === "optimaliseren" ? params.toString() : "");
    if (next === "profielen") q.delete("tab"); else q.set("tab", next);
    const qs = q.toString();
    router.replace(qs ? `/kwaliteit?${qs}` : "/kwaliteit", { scroll: false });
  };
  const labels: Record<QTab, MessageKey> = { profielen: "quality.tab.profiles", optimaliseren: "quality.tab.optimize", instellingen: "quality.tab.arr" };
  return (
    <>
      <SettingsTabs />
      <PageHeader title={t("nav.quality")} sub={t("quality.pageSub")} />
      <div style={{ marginBottom: 16 }}>
        <Tabs label={t("nav.quality")} value={tab} onSelect={go} items={QTABS.map((v) => ({ value: v, label: t(labels[v]) }))} />
      </div>
      {tab === "profielen" ? <Profiles /> : tab === "optimaliseren" ? <LibraryOptimize params={params} /> : <OptimizeTab />}
    </>
  );
}

function Profiles() {
  const { t, dateTime } = useI18n();
  const { can } = useSession();
  const profiles = useResource<PolicyProfile[]>("/profiles");
  const trash = useResource<Array<Record<string, unknown>>>("/policy/trash");
  const { run, pending } = useAction();
  const [editing, setEditing] = useState<PolicyProfile | null>(null);
  const [json, setJson] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importJson, setImportJson] = useState("");

  const save = async () => {
    if (!editing) return;
    let config: unknown;
    try { config = JSON.parse(json); } catch { setJsonError(t("settings.invalidJson")); return; }
    const r = await run("save", "put", `/profiles/${editing.id}`, { config, name_nl: editing.name_nl, description_nl: editing.description_nl }, t("toast.saved"));
    if (r !== undefined) { setEditing(null); void profiles.reload(); }
  };

  return (
    <>
      <div className="stack">
        <div className="row" style={{ justifyContent: "flex-end" }}><Button icon={Upload} disabled={!can("operator")} onClick={() => setImportOpen(true)}>{t("quality.trashImport")}</Button></div>
        <Card title={t("quality.profiles")} icon={Gauge} flush>
          <ResourceView resource={profiles} endpoint="GET /api/v1/profiles" isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact />}>
            {(rows) => (
              <div className="table-wrap">
                <table className="table table--stack">
                  <thead><tr><th>{t("quality.profile")}</th><th>{t("quality.ranking")}</th><th>{t("quality.version")}</th><th>{t("secrets.updated")}</th><th /></tr></thead>
                  <tbody>{rows.map((p) => (
                    <tr key={p.id}>
                      <td className="cell-primary"><div className="cell-title">{p.name_nl} {p.builtin ? <StatusChip tone="neutral" icon={null} outline>{t("quality.builtin")}</StatusChip> : null}</div><div className="cell-sub" style={{ whiteSpace: "normal", maxWidth: 520 }}>{p.description_nl}</div></td>
                      <td data-label={t("quality.ranking")} style={{ fontSize: 14 }}>{Array.isArray(p.config.ranking_order) ? (p.config.ranking_order as string[]).join(" → ") : "—"}</td>
                      <td data-label={t("quality.version")} className="num">v{p.version}</td>
                      <td data-label={t("secrets.updated")}>{dateTime(p.updated_at)} · {p.updated_by}</td>
                      <td><div className="row" style={{ justifyContent: "flex-end" }}>
                        <Button size="sm" disabled={!can("operator")} onClick={() => { setJsonError(null); setJson(JSON.stringify(p.config, null, 2)); setEditing(p); }}>{t("action.edit")}</Button>
                        <Button size="sm" variant="ghost" icon={RotateCcw} disabled={!can("operator") || !p.builtin} pending={pending === `r-${p.id}`} onClick={() => void run(`r-${p.id}`, "post", `/profiles/${p.id}/reset`, {}, t("settings.reset")).then(() => profiles.reload())}>{t("action.restoreDefault")}</Button>
                      </div></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </ResourceView>
        </Card>
        <Card title={t("quality.trash")} icon={ListTree} flush>
          <ResourceView resource={trash} endpoint="GET /api/v1/policy/trash" compact isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("quality.noTrash")} />}>
            {(rows) => (
              <div className="list">{rows.map((cf, i) => (
                <div className="list-row" key={String(cf.id ?? i)}>
                  <div className="list-main"><div className="list-title">{String(cf.name ?? cf.id)}</div><div className="list-sub mono">{String(cf.trash_id ?? cf.id ?? "")}</div></div>
                  <span className="num t-2">{String(cf.score ?? "")}</span>
                  <Button size="sm" variant="ghost" disabled={!can("operator")} pending={pending === `d-${String(cf.id)}`} onClick={() => void run(`d-${String(cf.id)}`, "del", `/policy/trash/${encodeURIComponent(String(cf.id))}`).then(() => trash.reload())}>{t("action.delete")}</Button>
                </div>
              ))}</div>
            )}
          </ResourceView>
        </Card>
        <SettingsLink sections={["downloads", "taal"]} />
      </div>
      <Modal open={Boolean(editing)} onClose={() => setEditing(null)} title={editing?.name_nl ?? ""} icon={History} description={t("quality.editHint")}
        footer={<><Button onClick={() => setEditing(null)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending === "save"} onClick={() => void save()}>{t("action.save")}</Button></>}>
        <Field label="config" htmlFor="profile-json" error={jsonError}><textarea id="profile-json" className="textarea mono" style={{ minHeight: 320, fontSize: 14 }} value={json} onChange={(e) => { setJson(e.target.value); setJsonError(null); }} /></Field>
        <InlineState>{t("quality.historyNote")}</InlineState>
      </Modal>
      <Modal open={importOpen} onClose={() => setImportOpen(false)} title={t("quality.trashImport")} icon={Upload} description={t("quality.trashImportBody")}
        footer={<><Button onClick={() => setImportOpen(false)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending === "import"} onClick={async () => { let body: unknown; try { body = JSON.parse(importJson); } catch { return; } const r = await run("import", "post", "/policy/trash/import", body, t("toast.saved")); if (r !== undefined) { setImportOpen(false); void trash.reload(); } }}>{t("action.apply")}</Button></>}>
        <textarea className="textarea mono" aria-label={t("quality.trashImport")} style={{ minHeight: 240, fontSize: 14 }} value={importJson} onChange={(e) => setImportJson(e.target.value)} />
      </Modal>
    </>
  );
}
