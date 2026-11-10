"use client";

import { Archive, CircleHelp, Database, FileArchive, Link2, Play, PlugZap, Plus, Route } from "lucide-react";
import { useState } from "react";

import { Button, Card, Field, InlineState, Modal, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { UploadZone } from "@/components/migration/upload";
import { useAction } from "@/lib/api/actions";
import type { Resource } from "@/lib/api/hooks";
import type { MigRun, MigRunRow, MigSource } from "@/lib/api/migration";
import { useI18n, type Formatters } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export function sourceCounts(s: MigSource, t: Formatters["t"], num: Formatters["num"]): string | null {
  const c = s.detected?.counts;
  if (!c) return null;
  return s.app === "sonarr" ? t("mig.counts.sonarr", { series: num(c.Series ?? 0), episodes: num(c.Episodes ?? 0) }) : t("mig.counts.radarr", { movies: num(c.Movies ?? 0) });
}

export function SourceKindChip({ source }: { source: Pick<MigSource, "kind"> }) {
  const { t } = useI18n();
  return source.kind === "live"
    ? <StatusChip tone="info" icon={PlugZap}>{t("mig.source.live")}</StatusChip>
    : <StatusChip tone="neutral" icon={FileArchive}>{t("mig.source.backup")}</StatusChip>;
}

/** Bronnen (live + back-up-upload) and migration runs (create / resume). */
export function MigrationOverview({ sources, runs, onOpenRun }: { sources: Resource<MigSource[]>; runs: Resource<MigRunRow[]>; onOpenRun: (id: number) => void }) {
  const { t } = useI18n();
  const { can } = useSession();
  return (
    <div className="grid">
      <div className="span-8 stack">
        <Card title={t("mig.sources.upload")} icon={FileArchive} sub={t("mig.sources.uploadSub")}>
          <div className="stack">
            <UploadZone<MigSource>
              path="/migration/sources/upload"
              accept=".zip,application/zip"
              multiple
              disabled={!can("admin")}
              testId="backup-upload"
              label={t("mig.sources.dropLabel")}
              hint={can("admin") ? t("mig.sources.dropHint") : t("mig.adminOnly")}
              onUploaded={() => void sources.reload()}
              describe={(s) => <UploadVerdict source={s} />}
            />
            <details className="raw" style={{ border: "1px solid var(--mg-border-soft)", borderRadius: "var(--mg-r)" }}>
              <summary><CircleHelp className="ico ico-sm" aria-hidden="true" />{t("mig.sources.howTo")}</summary>
              <ol className="t-2" style={{ fontSize: 14, margin: 0, padding: "4px 12px 12px 32px", lineHeight: 1.7 }}>
                <li>{t("mig.sources.howTo1")}</li><li>{t("mig.sources.howTo2")}</li><li>{t("mig.sources.howTo3")}</li><li>{t("mig.sources.howTo4")}</li><li>{t("mig.sources.howTo5")}</li>
              </ol>
            </details>
          </div>
        </Card>
        <SourceList sources={sources} />
        <LiveSourceForm onCreated={() => void sources.reload()} />
      </div>
      <div className="span-4 stack">
        <NewRun sources={sources.data ?? []} onCreated={(r) => { void runs.reload(); onOpenRun(r.id); }} />
        <Card title={t("mig.runs.title")} icon={Route} flush>
          <ResourceView resource={runs} endpoint="GET /api/v1/migration/runs" compact isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("mig.runs.empty")} body={t("mig.runs.emptyBody")} />}>
            {(rows) => (
              <div className="list">{rows.map((r) => (
                <div className="list-row" key={r.id} data-testid={`run-${r.id}`}>
                  <div className="list-main"><div className="list-title">#{r.id} · {r.name}</div><RunMeta row={r} /></div>
                  <Button size="sm" variant={r.status === "active" ? "primary" : "secondary"} onClick={() => onOpenRun(r.id)}>{r.status === "active" ? t("mig.runs.resume") : t("action.open")}</Button>
                </div>
              ))}</div>
            )}
          </ResourceView>
        </Card>
      </div>
    </div>
  );
}

function RunMeta({ row }: { row: MigRunRow }) {
  const { t, relative } = useI18n();
  return (
    <div className="list-sub row" style={{ gap: 6 }}>
      <StatusChip tone={row.status === "active" ? "accent" : row.status === "completed" ? "success" : "neutral"} icon={null}>{t(`mig.run.status.${row.status}`)}</StatusChip>
      <span>{t("mig.runs.updated", { age: relative(row.updated_at) })}</span>
    </div>
  );
}

function UploadVerdict({ source }: { source: MigSource }) {
  const { t, pct, num } = useI18n();
  const anime = source.detected?.anime?.score;
  return (
    <span data-testid="upload-verdict">
      {t("mig.sources.detected", { app: source.app_name ?? source.app, version: source.version ?? "?", id: source.id })}
      {sourceCounts(source, t, num) ? ` · ${sourceCounts(source, t, num)}` : ""}
      {anime !== undefined ? ` · ${t("mig.sources.animeScore", { score: pct(anime * 100) })}` : ""}
      {source.suggested_target ? <> · {t("mig.sources.suggested")} <span className="tag">{source.suggested_target}</span></> : null}
    </span>
  );
}

function SourceList({ sources }: { sources: Resource<MigSource[]> }) {
  const { t, bytes, dateTime, num } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const [archive, setArchive] = useState<MigSource | null>(null);
  return (
    <Card title={t("mig.sources.title")} icon={Database} flush>
      <ResourceView resource={sources} endpoint="GET /api/v1/migration/sources" compact isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("mig.sources.empty")} body={t("mig.sources.emptyBody")} />}>
        {(rows) => (
          <div className="table-wrap">
            <table className="table table--compact table--stack" data-testid="source-table">
              <thead><tr><th>{t("mig.col.source")}</th><th>{t("mig.col.kind")}</th><th>{t("mig.col.app")}</th><th>{t("mig.col.content")}</th><th>{t("mig.col.backup")}</th><th /></tr></thead>
              <tbody>{rows.map((s) => (
                <tr key={s.id} data-source={s.id}>
                  <td className="cell-primary"><div className="cell-title">{s.name}</div><div className="cell-sub mono">{s.id}{s.url ? ` · ${s.url}` : ""}</div></td>
                  <td data-label={t("mig.col.kind")}><SourceKindChip source={s} /></td>
                  <td data-label={t("mig.col.app")}><span className="mono">{s.app_name ?? s.app} {s.version ?? ""}</span>{s.detected?.anime ? <div className="cell-sub">{t("mig.sources.animeScoreShort", { score: Math.round((s.detected.anime.score ?? 0) * 100) })}</div> : null}</td>
                  <td data-label={t("mig.col.content")}>{sourceCounts(s, t, num) ?? "—"}</td>
                  <td data-label={t("mig.col.backup")}>{s.latest_backup ? (
                    <span title={s.latest_backup.sha256}><StatusChip tone={s.latest_backup.verified ? "success" : "warning"} icon={null}>{s.latest_backup.verified ? t("mig.backup.verified") : t("mig.backup.unverified")}</StatusChip> <span className="cell-sub">{bytes(s.latest_backup.size)} · {dateTime(s.latest_backup.created_at)}</span></span>
                  ) : "—"}{s.last_error ? <div className="t-danger" style={{ fontSize: 14 }}>{s.last_error}</div> : null}</td>
                  <td className="row" style={{ justifyContent: "flex-end", gap: 6 }}>
                    {s.kind === "live" ? <Button size="sm" disabled={!can("operator")} pending={pending === `test-${s.id}`} onClick={() => void run(`test-${s.id}`, "post", `/migration/sources/${encodeURIComponent(s.id)}/test`, {}, t("mig.sources.tested")).then(() => sources.reload())}>{t("action.test")}</Button> : null}
                    <Button size="sm" variant="ghost" icon={Archive} disabled={!can("admin")} onClick={() => setArchive(s)}>{t("mig.sources.archive")}</Button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </ResourceView>
      <Modal open={archive !== null} onClose={() => setArchive(null)} icon={Archive} title={t("mig.sources.archiveTitle", { name: archive?.name ?? "" })} description={t("mig.sources.archiveBody")}
        footer={<><Button onClick={() => setArchive(null)} data-autofocus>{t("action.cancel")}</Button><Button variant="danger" pending={pending === "archive"} onClick={() => { if (!archive) return; void run("archive", "del", `/migration/sources/${encodeURIComponent(archive.id)}`, undefined, t("mig.sources.archived")).then((r) => { if (r !== undefined) { setArchive(null); void sources.reload(); } }); }}>{t("mig.sources.archive")}</Button></>} />
    </Card>
  );
}

function LiveSourceForm({ onCreated }: { onCreated: () => void }) {
  const { t } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  const valid = name.trim().length > 0 && /^https?:\/\//.test(url.trim()) && key.trim().length >= 8;
  const submit = async () => {
    const r = await run("live", "post", "/migration/sources", { name: name.trim(), url: url.trim(), api_key: key.trim() }, t("mig.sources.liveAdded"));
    if (r !== undefined) { setName(""); setUrl(""); setKey(""); onCreated(); }
  };
  return (
    <Card title={t("mig.sources.liveTitle")} icon={Link2} sub={t("mig.sources.liveSub")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); if (valid) void submit(); }}>
        <div className="grid" style={{ gap: 12 }}>
          <div className="span-4"><Field label={t("mig.sources.name")} htmlFor="live-name"><input id="live-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Sonarr anime" disabled={!can("admin")} /></Field></div>
          <div className="span-4"><Field label={t("mig.sources.url")} htmlFor="live-url"><input id="live-url" className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://192.168.1.10:8989" inputMode="url" disabled={!can("admin")} /></Field></div>
          <div className="span-4"><Field label={t("mig.sources.apiKey")} htmlFor="live-key" hint={t("mig.sources.apiKeyHint")}><input id="live-key" className="input mono" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} disabled={!can("admin")} /></Field></div>
        </div>
        <InlineState tone="info">{t("mig.sources.readOnlyNote")}</InlineState>
        <div><Button type="submit" variant="primary" icon={Plus} disabled={!valid || !can("admin")} pending={pending === "live"}>{t("mig.sources.liveAdd")}</Button></div>
      </form>
    </Card>
  );
}

function NewRun({ sources, onCreated }: { sources: MigSource[]; onCreated: (run: MigRun) => void }) {
  const { t, date } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [follow, setFollow] = useState<"exact" | "follow">("exact");
  const chosen = sources.filter((s) => picked[s.id] ?? true);
  const submit = async () => {
    const r = await run<MigRun>("run", "post", "/migration/runs", {
      name: name.trim() || t("mig.runs.defaultName", { date: date(new Date()) }),
      source_ids: chosen.map((s) => s.id),
      options: { follow_new_seasons: follow === "follow" ? true : null },
    }, t("mig.runs.created"));
    if (r) onCreated(r);
  };
  return (
    <Card title={t("mig.runs.new")} icon={Play}>
      {sources.length === 0 ? <p className="t-3" style={{ fontSize: 14, margin: 0 }}>{t("mig.runs.needSources")}</p> : (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          <Field label={t("mig.runs.name")} htmlFor="run-name"><input id="run-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("mig.runs.defaultName", { date: date(new Date()) })} /></Field>
          <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="label">{t("mig.runs.sources")}</legend>
            {sources.map((s) => (
              <label key={s.id} className="check"><input type="checkbox" checked={picked[s.id] ?? true} onChange={(e) => setPicked((o) => ({ ...o, [s.id]: e.target.checked }))} /><span>{s.name} <span className="t-3 mono" style={{ fontSize: 14 }}>({s.app})</span></span></label>
            ))}
          </fieldset>
          <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="label">{t("mig.opt.newSeasons")}</legend>
            <label className="choice"><input type="radio" name="follow" checked={follow === "exact"} onChange={() => setFollow("exact")} /><span>{t("mig.opt.exact")}</span></label>
            <label className="choice"><input type="radio" name="follow" checked={follow === "follow"} onChange={() => setFollow("follow")} /><span>{t("mig.opt.follow")}</span></label>
          </fieldset>
          <Button type="submit" variant="primary" icon={Play} disabled={!can("operator") || chosen.length === 0} pending={pending === "run"}>{t("mig.runs.start")}</Button>
        </form>
      )}
    </Card>
  );
}
