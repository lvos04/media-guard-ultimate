"use client";

import { Download, FolderTree, Play, RefreshCw, Route, Rss } from "lucide-react";
import { useState } from "react";

import { Button, Card, InlineState, StatusChip, Switch } from "@/components/mg/primitives";
import { SeerrReport } from "@/components/mg/seerr-report";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { useAction } from "@/lib/api/actions";
import { arrMessage } from "@/lib/arr-messages";
import { api } from "@/lib/api/client";
import type { ArrResource, Instance, SeerrRouteReport, SeerrRouting } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/**
 * Live toestand in de Sonarr/Radarr-instanties zelf (voorheen de losse pagina's /downloads en
 * /seerr). De instellingen zelf staan in de tabbladen erboven; hier alleen status en testen.
 */
function useInstancePick() {
  const instances = useResource<Instance[]>("/instances");
  const [picked, setPicked] = useState("");
  const active = picked || instances.data?.[0]?.id || "";
  return { instances, active, setPicked };
}

function InstanceSwitch({ instances, active, onChange }: { instances: Instance[] | null | undefined; active: string; onChange: (v: string) => void }) {
  const { t } = useI18n();
  if (!instances?.length) return null;
  // keuzelijst: past ook op een telefoon met vier instanties
  return (
    <select className="select" style={{ maxWidth: 320 }} aria-label={t("library.instance")} value={active} onChange={(e) => onChange(e.target.value)}>
      {instances.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
    </select>
  );
}

/** Indexers of downloadclients zoals ze nu in de gekozen instantie staan: aan/uit en testen. */
export function ArrLive({ kind }: { kind: "indexers" | "download-clients" }) {
  const { t } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const { instances, active, setPicked } = useInstancePick();
  const list = useResource<ArrResource[]>(active ? `/instances/${active}/${kind}` : null);
  const { run, pending } = useAction();
  const title = t(kind === "indexers" ? "live.indexers" : "live.clients");
  // 'Alles testen' = elke regel apart testen met de bestaande per-regel-test (H9); resultaat per regel
  const [results, setResults] = useState<Record<string, { ok: boolean; message?: string }>>({});
  const [testingAll, setTestingAll] = useState(false);
  const testOne = async (id: number) => {
    try {
      await api.post(`/instances/${active}/${kind}/${id}/test`, {});
      setResults((r) => ({ ...r, [`${active}:${id}`]: { ok: true } }));
      return true;
    } catch (e) {
      setResults((r) => ({ ...r, [`${active}:${id}`]: { ok: false, message: e instanceof Error ? e.message : String(e) } }));
      return false;
    }
  };
  const testAll = async () => {
    const rows = (list.data ?? []).filter((r) => r.enable !== false);
    setTestingAll(true);
    let ok = 0;
    const parts: string[] = [];
    for (const r of rows) {
      const passed = await testOne(r.id);
      if (passed) ok += 1;
      parts.push(`${r.name} ${passed ? "✓" : "✗"}`);
    }
    setTestingAll(false);
    toast.push(ok === rows.length ? "success" : "danger", t("live.testAllResult", { ok, total: rows.length }), parts.join(", "));
  };
  return (
    <Card title={title} sub={t("live.sub")} icon={kind === "indexers" ? Rss : Download} flush id={`live-${kind}`}
      actions={<Button size="sm" disabled={!can("operator") || !active || !list.data?.length} pending={testingAll} onClick={() => void testAll()} data-testid={`test-all-${kind}`}>{t("downloads.testAll")}</Button>}>
      <div className="card-body" style={{ paddingBottom: 0 }}><InstanceSwitch instances={instances.data} active={active} onChange={setPicked} /></div>
      <ResourceView resource={list} compact isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("downloads.none")} />}>
        {(rows) => (
          <div className="list">{rows.map((r) => (
            <div className="list-row" key={r.id}>
              <div className="list-main"><div className="list-title">{r.name}</div><div className="list-sub">{[r.implementationName ?? r.implementation, r.protocol, r.priority !== undefined ? t("live.priority", { n: r.priority }) : null].filter(Boolean).join(" · ")}</div>
                {results[`${active}:${r.id}`] ? <div data-testid={`test-result-${r.id}`} style={{ marginTop: 4 }}><StatusChip tone={results[`${active}:${r.id}`].ok ? "success" : "danger"}>{results[`${active}:${r.id}`].ok ? t("live.testPassed") : t("live.testFailed")}</StatusChip>{results[`${active}:${r.id}`].message ? <span className="list-sub" style={{ marginLeft: 6, whiteSpace: "normal" }}>{arrMessage(results[`${active}:${r.id}`].message)}</span> : null}</div> : null}</div>
              <Switch checked={r.enable !== false} disabled={!can("operator")} label={r.name} onChange={(v) => void run(`en-${r.id}`, "patch", `/instances/${active}/${kind}/${r.id}/enable`, { enable: v }, t("toast.saved")).then(() => list.reload())} />
              <Button size="sm" disabled={!can("operator")} pending={pending === `t-${r.id}`} onClick={() => void testOne(r.id)}>{t("action.test")}</Button>
            </div>
          ))}</div>
        )}
      </ResourceView>
    </Card>
  );
}

/** Mediamappen en padvertalingen zoals de gekozen instantie ze nu ziet. */
export function PathsLive() {
  const { t, bytes } = useI18n();
  const { instances, active, setPicked } = useInstancePick();
  const maps = useResource<Array<{ id: number; host: string; remotePath: string; localPath: string }>>(active ? `/instances/${active}/remote-path-mappings` : null);
  const roots = useResource<Array<{ id: number; path: string; accessible?: boolean; freeSpace?: number }>>(active ? `/instances/${active}/root-folders` : null);
  return (
    <Card title={t("live.paths")} sub={t("live.sub")} icon={FolderTree} flush id="live-paths">
      <div className="card-body" style={{ paddingBottom: 0 }}><InstanceSwitch instances={instances.data} active={active} onChange={setPicked} /></div>
      <ResourceView resource={roots} compact isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("downloads.none")} />}>
        {(rows) => <div className="list">{rows.map((r) => <div className="list-row" key={r.id}><div className="list-main"><div className="list-title mono" style={{ overflowWrap: "anywhere" }}>{r.path}</div><div className="list-sub">{bytes(r.freeSpace)} {t("system.free")}</div></div><StatusChip tone={r.accessible === false ? "danger" : "success"}>{r.accessible === false ? t("status.offline") : t("status.ok")}</StatusChip></div>)}</div>}
      </ResourceView>
      <ResourceView resource={maps} compact isEmpty={(r) => r.length === 0} empty={null}>
        {(rows) => (
          <div className="list">{rows.map((m) => <div className="list-row" key={m.id}><div className="list-main"><div className="list-title mono" style={{ overflowWrap: "anywhere" }}>{m.remotePath} → {m.localPath}</div><div className="list-sub">{t("live.mapping", { host: m.host })}</div></div></div>)}</div>
        )}
      </ResourceView>
    </Card>
  );
}

const ROLE: Record<string, MessageKey> = { default: "live.role.default", anime: "live.role.anime", "4k": "live.role.4k", "4k-anime": "live.role.4kAnime", manual: "live.role.manual" };

/** Seerr: welke instantie bij welke Seerr-server hoort, routetest en aanvragen bijwerken. */
export function SeerrLive() {
  const { t } = useI18n();
  const { can } = useSession();
  const routing = useResource<SeerrRouting[]>("/seerr/routing");
  const instances = useResource<Instance[]>("/instances");
  const { run, pending } = useAction();
  const [report, setReport] = useState<SeerrRouteReport | null>(null);
  const name = (id: string) => instances.data?.find((i) => i.id === id)?.name ?? id;
  const test = async () => { const r = await run<SeerrRouteReport>("routes", "post", "/seerr/route-test", {}); if (r) setReport(r); };
  return (
    <Card title={t("live.seerr")} sub={t("live.seerrSub")} icon={Route} flush id="live-seerr"
      actions={<div className="row wrap" style={{ gap: 8 }}>
        <Button size="sm" icon={RefreshCw} disabled={!can("operator")} pending={pending === "sync"} onClick={() => void run("sync", "post", "/seerr/requests/sync", {}, t("seerr.synced"))}>{t("seerr.syncRequests")}</Button>
        <Button size="sm" variant="primary" icon={Play} disabled={!can("operator")} pending={pending === "routes"} onClick={() => void test()}>{t("live.routeTest")}</Button>
      </div>}>
      <ResourceView resource={routing} compact isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("downloads.none")} />}>
        {(rows) => (
          <div className="list">{rows.map((r) => {
            const server = r.routing.seerr_server_id !== null
              ? t("live.server", { kind: r.routing.seerr_kind === "radarr" ? "Radarr" : "Sonarr", id: r.routing.seerr_server_id })
              : t("live.serverNone");
            const flags = [r.routing.expected_is_default ? t("live.flag.default") : null, r.routing.expected_is_4k ? "4K" : null].filter(Boolean).join(" · ");
            return (
              <div className="list-row" key={r.instance_id}>
                <div className="list-main"><div className="list-title">{name(r.instance_id)}</div><div className="list-sub">{[server, flags].filter(Boolean).join(" · ")}</div></div>
                <StatusChip tone="neutral" icon={null}>{t(ROLE[r.routing.role] ?? "live.role.default")}</StatusChip>
              </div>
            );
          })}</div>
        )}
      </ResourceView>
      {report ? <SeerrReport report={report} /> : <div className="card-body"><InlineState>{t("seerr.note")}</InlineState></div>}
    </Card>
  );
}
