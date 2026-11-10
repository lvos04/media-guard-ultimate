"use client";

import { OffsiteCard } from "@/components/notify/offsite-card";
import { UpdateCard } from "@/components/mg/update-card";
import { QuarantineOrphans } from "@/components/mg/quarantine-orphans";
import { BackgroundTasks } from "@/components/mg/background-tasks";
import { DatabaseBackup, Download, KeyRound, HardDrive, HeartPulse, ListChecks, Stethoscope } from "lucide-react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, PageHeader, StatusChip } from "@/components/mg/primitives";
import { Term } from "@/components/mg/term";
import { ResourceView, StateView } from "@/components/mg/states";
import { useShell } from "@/components/shell/app-shell";
import { useAction } from "@/lib/api/actions";
import { CORE_BASE } from "@/lib/api/client";
import type { AdminCoverage, AdminHealth, AdminVersion } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { SettingsTabs } from "@/components/shell/settings-tabs";
import { RowMenu } from "@/components/mg/row-menu";
import { StorageOverview } from "@/components/mg/storage";
import { StorageTrend } from "@/components/mg/storage-trend";
import { FolderTwins, FolderTypos } from "@/components/mg/folder-typos";
import { DownloadCleanup } from "@/components/mg/download-cleanup";
import { SystemPerformance } from "@/components/mg/system-performance";
import { useState } from "react";

interface Backup { name?: string; file?: string; size?: number; created_at?: string; kind?: string; label_nl?: string; restore_mode?: string; staged?: boolean; [key: string]: unknown }
/** core noemt het veld 'file' (oud: 'name'); zonder naam geen kapotte link (H10) */
const backupName = (b: Backup) => b.file ?? b.name ?? "";
/** De 8 nieuwste gewone back-ups plus álle sets van vóór een update: die zakten anders na een
 * week nachtelijke back-ups uit beeld (rapport 17). Nieuwste eerst. */
function shownBackups(rows: Backup[]): Backup[] {
  const named = rows.filter((b) => backupName(b));
  const regular = named.filter((b) => b.kind !== "pre-update").slice(0, 8);
  const preUpdate = named.filter((b) => b.kind === "pre-update");
  return [...regular, ...preUpdate].sort((a, b) => String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")));
}

export default function SystemPage() {
  const { t, dateTime, num, bytes } = useI18n();
  const { can } = useSession();
  const { status } = useShell();
  const health = useResource<AdminHealth>("/admin/health", undefined, 30_000);
  const version = useResource<AdminVersion>("/admin/version");
  const [details, setDetails] = useState(false);
  const coverage = useResource<AdminCoverage>(details ? "/admin/coverage" : null);
  const backups = useResource<Backup[]>("/backups");
  const { run, pending } = useAction();
  const [restore, setRestore] = useState<Backup | null>(null);

  return (
    <>
      <SettingsTabs />
      <PageHeader title={t("set.link.system")} sub={t("system.sub")} actions={<>
        <a className="btn" href={`${CORE_BASE}/admin/support-bundle`} download><Download className="ico" aria-hidden="true" />{t("system.supportBundle")}</a>
      </>} />
      <div style={{ marginBottom: 16 }}><SystemPerformance /></div>
      <div className="grid">
        <Card className="span-6" id="opslag" title={t("system.storage")} icon={HardDrive}>
          <StorageOverview />
          <StorageTrend />
          <FolderTypos />
          <FolderTwins />
          <DownloadCleanup />
        </Card>
        <Card className="span-6" id="back-ups" title={t("system.backups")} icon={DatabaseBackup} flush actions={<Button size="sm" variant="primary" disabled={!can("admin")} pending={pending === "backup"} onClick={() => void run("backup", "post", "/backups", {}, t("system.backupDone")).then(() => backups.reload())}>{t("system.backupNow")}</Button>}>
          <ResourceView resource={backups} endpoint="GET /api/v1/backups" compact isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("system.noBackups")} />}>
            {(rows) => (
              <div className="list">{shownBackups(rows).map((b) => (
                <div className="list-row list-row--wrap" key={backupName(b)}>
                  <div className="list-main"><div className="list-title">{b.kind === "pre-update" && b.label_nl ? b.label_nl : t("system.backupOf", { when: dateTime(b.created_at) })}</div><div className="list-sub">{[b.kind === "manual" ? t("system.backupManual") : b.kind === "pre-update" ? `${t("system.backupPreUpdate")} · ${dateTime(b.created_at)}` : t("system.backupAuto"), b.size ? bytes(b.size) : null, typeof b.verified_nl === "string" ? b.verified_nl : null, b.restore_mode === "handmatig" && typeof b.restore_nl === "string" ? b.restore_nl : null].filter(Boolean).join(" · ")}</div>
                    {b.staged ? <StatusChip tone="warning">{t("system.backupStaged")}</StatusChip> : null}
                    <details className="tech-details"><summary>{t("state.techDetails")}</summary><span className="mono">{backupName(b)}</span></details></div>
                  <span className="list-aside row" style={{ gap: 6 }}>
                    <a className="btn btn--sm" href={typeof b.download_path === "string" ? b.download_path : `${CORE_BASE}/backups/${encodeURIComponent(backupName(b))}/download`} data-testid="backup-download">{t("system.download")}</a>
                    {b.restore_mode === "handmatig" ? null : <RowMenu><Button size="sm" variant="danger" disabled={!can("admin")} onClick={() => setRestore(b)}>{t("system.restore")}</Button></RowMenu>}
                  </span>
                </div>
              ))}</div>
            )}
          </ResourceView>
        </Card>
        <BundleCard />
        <UpdateCard />
        <OffsiteCard />
        <QuarantineOrphans />
        <BackgroundTasks />
        <div className="span-12">
          <Button size="sm" variant="ghost" onClick={() => setDetails((v) => !v)} aria-expanded={details}>{t(details ? "system.hideDetails" : "system.showDetails")}</Button>
        </div>
        {details ? (
          <>
        <Card className="span-4" title={t("system.core")} icon={HeartPulse}>
          <ResourceView resource={version} endpoint="GET /api/v1/admin/version" compact>
            {(v) => (
              <dl className="kv">
                <dt>{t("instances.version")}</dt><dd className="mono">{v.version}</dd>
                <dt>{t("system.role")}</dt><dd className="mono">{status?.role ?? "—"}</dd>
                <dt>{t("system.serverTime")}</dt><dd>{dateTime(status?.time)}</dd>
                <dt>{t("system.schema")}</dt><dd className="mono" style={{ fontSize: 14 }}>{v.schema_versions.join(", ")}</dd>
                <dt>{t("system.jobs")}</dt><dd className="mono">{status ? Object.entries(status.jobs).map(([k, n]) => `${k} ${n}`).join(" · ") || "—" : "—"}</dd>
              </dl>
            )}
          </ResourceView>
        </Card>
        <Card className="span-8" title={t("system.components")} icon={Stethoscope} flush>
          <ResourceView resource={health} endpoint="GET /api/v1/admin/health" compact>
            {(h) => (
              <div className="list">{Object.entries(h.components).map(([id, c]) => (
                <div className="list-row" key={id}>
                  <div className="list-main"><div className="list-title mono">{id}</div><div className="list-sub">{[c.version, c.status, c.detail].filter(Boolean).join(" · ") || "—"}</div></div>
                  <StatusChip tone={c.ok ? "healthy" : c.suspended ? "paused" : "danger"}>{c.ok ? t("status.healthy") : c.suspended ? t("status.paused") : t("status.offline")}</StatusChip>
                  <Button size="sm" disabled={!can("operator")} pending={pending === id} onClick={() => void run(id, "post", `/admin/diagnostics/${encodeURIComponent(id.split(":")[0])}`, {}, t("system.diagQueued"))}>{t("action.diagnose")}</Button>
                </div>
              ))}</div>
            )}
          </ResourceView>
        </Card>
        <Card className="span-12" title={t("system.coverage")} icon={ListChecks} flush>
          <ResourceView resource={coverage} endpoint="GET /api/v1/admin/coverage" compact>
            {(c) => (
              <>
                <div className="card-body"><p style={{ fontSize: 14 }}>{t("system.coverageValue", { covered: num(c.settings.filter((s) => s.ui_control).length), total: num(c.total) })} — {t("system.coverageNote")}</p></div>
                <div className="table-wrap" style={{ maxHeight: 360 }}>
                  <table className="table table--compact table--stack">
                    <thead><tr><th>{t("system.key")}</th><th>{t("system.category")}</th><th>{t("system.type")}</th><th>{t("system.control")}</th><th>{t("settings.scope")}</th></tr></thead>
                    <tbody>{c.settings.map((s) => (
                      <tr key={s.key}><td className="cell-primary"><div className="cell-title">{s.label_nl}</div><div className="cell-sub mono">{s.key}</div></td><td data-label={t("system.category")}>{s.category}</td><td data-label={t("system.type")} className="mono">{s.type}</td><td data-label={t("system.control")}>{s.integrity_locked ? <StatusChip tone="neutral">{t("status.locked")}</StatusChip> : s.ui_control}</td><td data-label={t("settings.scope")} className="mono" style={{ fontSize: 14 }}>{s.scopes.join(", ")}</td></tr>
                    ))}</tbody>
                  </table>
                </div>
              </>
            )}
          </ResourceView>
        </Card>
          </>
        ) : null}
      </div>
      <DangerDialog open={Boolean(restore)} onClose={() => setRestore(null)} icon={DatabaseBackup} pending={pending === "restore"} title={t("system.restoreTitle", { name: restore ? (restore.label_nl ?? dateTime(restore.created_at)) : "" })}
        description={restore?.restore_mode === "bij-stoppen" ? t("system.restorePreUpdateBody") : t("system.restoreBody")}
        confirmWord={restore?.name} confirmLabel={restore?.restore_mode === "bij-stoppen" ? t("system.restorePreUpdate") : t("system.restore")}
        onConfirm={async () => { if (!restore) return; const r = await run("restore", "post", `/backups/${encodeURIComponent(backupName(restore))}/restore`, { confirm: backupName(restore) }, restore.restore_mode === "bij-stoppen" ? t("system.staged") : t("system.restored")); if (r !== undefined) { setRestore(null); void backups.reload(); } }} />
    </>
  );
}


interface BundleStatus { enabled: boolean; fingerprint?: string | null; created_at?: string | null; bundles: { file: string; size: number }[] }

/** Versleutelde herstelbundel: databases + sleutels, alleen te openen met de herstelcode. */
function BundleCard() {
  const { t, bytes } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const st = useResource<BundleStatus>("/backups/bundle");
  const [code, setCode] = useState<string | null>(null);
  const [confirmNew, setConfirmNew] = useState(false);
  const setup = async (confirm: boolean) => {
    const r = await run<{ recovery_code?: string }>("bundle-setup", "post", "/backups/bundle/setup", { confirm });
    if (r?.recovery_code) setCode(r.recovery_code);
    setConfirmNew(false);
    void st.reload();
  };
  return (
    <Card className="span-6" title={t("system.bundle.title")} sub={<Term k="herstelbundel">{t("help.whatIsThis")}</Term>} icon={KeyRound} flush>
      <ResourceView resource={st} endpoint="GET /api/v1/backups/bundle" compact>
        {(b) => (
          <div className="list">
            <div className="list-row"><div className="list-main">
              <div className="list-title">{b.enabled ? t("system.bundle.on") : t("system.bundle.off")}</div>
              <div className="list-sub">{t("system.bundle.explain")}</div>
            </div></div>
            {code ? (
              <div className="list-row" role="alert"><div className="list-main">
                <div className="list-title">{t("system.bundle.codeTitle")}</div>
                <div className="list-title mono" style={{ fontSize: "1.05rem", userSelect: "all", wordBreak: "break-all" }}>{code}</div>
                <div className="list-sub">{t("system.bundle.codeHelp")}</div>
              </div></div>
            ) : null}
            <div className="list-row" style={{ gap: 8, flexWrap: "wrap" }}>
              {!b.enabled ? (
                <Button size="sm" variant="primary" disabled={!can("admin")} pending={pending === "bundle-setup"} onClick={() => void setup(false)}>{t("system.bundle.setup")}</Button>
              ) : confirmNew ? (
                <Button size="sm" variant="danger" disabled={!can("admin")} pending={pending === "bundle-setup"} onClick={() => void setup(true)}>{t("system.bundle.newConfirm")}</Button>
              ) : (
                <>
                  <Button size="sm" variant="primary" disabled={!can("admin")} pending={pending === "bundle-now"} onClick={() => void run("bundle-now", "post", "/backups/bundle", {}, t("system.bundle.made")).then(() => st.reload())}>{t("system.bundle.now")}</Button>
                  <Button size="sm" variant="ghost" disabled={!can("admin")} onClick={() => setConfirmNew(true)}>{t("system.bundle.new")}</Button>
                </>
              )}
            </div>
            {b.bundles.slice(0, 5).map((f) => (
              <div className="list-row" key={f.file}>
                <div className="list-main"><div className="list-title mono">{f.file}</div><div className="list-sub">{bytes(f.size)}</div></div>
                <a className="btn btn--sm" href={`${CORE_BASE}/backups/bundle/${encodeURIComponent(f.file)}/download`}>{t("system.download")}</a>
              </div>
            ))}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
