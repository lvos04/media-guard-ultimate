"use client";

import { Download, FolderCheck, Inbox, Plus, Radar, Save, SlidersHorizontal, Trash2, Wifi } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button, Card, Field, InlineState, Segmented, StatusChip } from "@/components/mg/primitives";
import { useToast } from "@/components/mg/toast";
import { ActionChip } from "@/components/provisioning/overview";
import { useAction } from "@/lib/api/actions";
import { api, ApiError } from "@/lib/api/client";
import type { Resource } from "@/lib/api/hooks";
import type { DownloadClientCfg, IndexerCfg, InstanceCfg, PathCheck, ProvChange, ProvConfig, ProwlarrTest, TestResult } from "@/lib/api/provisioning";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/** Save a partial config (PUT /provisioning/config); merges the public view back into the resource. */
function useSaveConfig(cfg: Resource<ProvConfig>) {
  const { t } = useI18n();
  const toast = useToast();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (patch: Record<string, unknown>): Promise<Partial<ProvConfig> | null> => {
    setPending(true);
    setError(null);
    try {
      const after = await api.put<Partial<ProvConfig>>("/provisioning/config", patch);
      cfg.setData((old) => (old ? { ...old, ...after } : old));
      toast.push("success", t("toast.saved"));
      return after;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      return null;
    } finally {
      setPending(false);
    }
  };
  return { save, pending, error };
}

const strip = <T extends object>(o: T, keys: string[]) => Object.fromEntries(Object.entries(o).filter(([k, v]) => !keys.includes(k) && v !== undefined));

/** Write-only secret: shows whether it is set; empty input = unchanged; "Wissen" clears. */
function SecretInput({ id, label, isSet, value, onChange }: { id: string; label: string; isSet?: boolean; value: string | null | undefined; onChange: (v: string | null | undefined) => void }) {
  const { t } = useI18n();
  const clearing = value === "";
  return (
    <Field label={<span className="row" style={{ gap: 6 }}>{label}{isSet && !clearing ? <StatusChip tone="success" icon={null}>{t("prov.secret.set")}</StatusChip> : null}{clearing ? <StatusChip tone="warning" icon={null}>{t("prov.secret.clearing")}</StatusChip> : null}</span>} htmlFor={id} hint={isSet ? t("prov.secret.keepHint") : undefined}>
      <div className="row" style={{ gap: 6 }}>
        <input id={id} className="input mono" type="password" autoComplete="new-password" value={value ?? ""} placeholder={isSet ? "••••••••" : ""} onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)} />
        {isSet ? <Button size="sm" variant="ghost" onClick={() => onChange(clearing ? undefined : "")}>{clearing ? t("action.undo") : t("prov.secret.clear")}</Button> : null}
      </div>
    </Field>
  );
}

function TestLine({ result }: { result: TestResult | null }) {
  if (!result) return null;
  return <InlineState tone={result.ok ? "success" : "danger"} role="status">{result.message_nl}</InlineState>;
}

function useTest() {
  const [results, setResults] = useState<Record<string, TestResult>>({});
  const [pending, setPending] = useState<string | null>(null);
  const test = async <R extends TestResult>(key: string, path: string, body: unknown): Promise<R | null> => {
    setPending(key);
    try {
      const r = await api.post<R>(path, body);
      setResults((o) => ({ ...o, [key]: r }));
      return r;
    } catch (e) {
      setResults((o) => ({ ...o, [key]: { ok: false, message_nl: e instanceof ApiError ? e.message : String(e) } }));
      return null;
    } finally {
      setPending(null);
    }
  };
  return { test, results, pending };
}

function InstancePicker({ value, onChange, instances, label }: { value: string[] | null; onChange: (v: string[] | null) => void; instances: string[]; label: string }) {
  const { t } = useI18n();
  const all = value === null;
  return (
    <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend className="label">{label}</legend>
      <div className="row wrap" style={{ gap: 12 }}>
        <label className="choice"><input type="checkbox" checked={all} onChange={(e) => onChange(e.target.checked ? null : [...instances])} /><span>{t("prov.allInstances")}</span></label>
        {instances.map((id) => (
          <label key={id} className="choice"><input type="checkbox" disabled={all} checked={all || (value ?? []).includes(id)} onChange={(e) => onChange(e.target.checked ? [...(value ?? []), id] : (value ?? []).filter((x) => x !== id))} /><span className="mono" style={{ fontSize: 14 }}>{id}</span></label>
        ))}
      </div>
    </fieldset>
  );
}

function SaveBar({ dirty, pending, error, onSave, onReset }: { dirty: boolean; pending: boolean; error: string | null; onSave: () => void; onReset: () => void }) {
  const { t } = useI18n();
  const { can } = useSession();
  return (
    <div className="stack-sm">
      {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <Button size="sm" variant="ghost" disabled={!dirty} onClick={onReset}>{t("action.discard")}</Button>
        <Button size="sm" variant="primary" icon={Save} disabled={!dirty || !can("admin")} pending={pending} onClick={onSave} data-testid="prov-save">{t("action.save")}</Button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ downloads
const DC_IMPL: DownloadClientCfg["implementation"][] = ["Sabnzbd", "Nzbget", "QBittorrent", "Transmission", "Deluge"];
const DC_PORT: Record<DownloadClientCfg["implementation"], number> = { Sabnzbd: 8080, Nzbget: 6789, QBittorrent: 8080, Transmission: 9091, Deluge: 8112 };

export function DownloadsTab({ cfg }: { cfg: Resource<ProvConfig> & { data: ProvConfig } }) {
  const { t } = useI18n();
  const { can } = useSession();
  const initial = cfg.data.download_clients;
  const [list, setList] = useState<DownloadClientCfg[]>(initial);
  const { save, pending, error } = useSaveConfig(cfg);
  const { test, results, pending: testing } = useTest();
  const instances = Object.keys(cfg.data.defaults);
  const dirty = JSON.stringify(list) !== JSON.stringify(initial);
  const set = (i: number, patch: Partial<DownloadClientCfg>) => setList((l) => l.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  const payload = (d: DownloadClientCfg) => strip(d, ["api_key_set", "password_set", "protocol"]);
  const onSave = async () => { const after = await save({ download_clients: list.map(payload) }); if (after?.download_clients) setList(after.download_clients); };
  return (
    <Card title={t("prov.dc.title")} icon={Download} sub={t("prov.dc.sub")}>
      <div className="stack">
        {list.length === 0 ? <InlineState tone="info">{t("prov.dc.empty")}</InlineState> : null}
        {list.map((d, i) => (
          <section key={i} className="prov-item" data-testid={`dc-${i}`}>
            <div className="grid" style={{ gap: 12 }}>
              <div className="span-3"><Field label={t("prov.name")} htmlFor={`dc-name-${i}`}><input id={`dc-name-${i}`} className="input" value={d.name} onChange={(e) => set(i, { name: e.target.value })} /></Field></div>
              <div className="span-3"><Field label={t("prov.dc.kind")} htmlFor={`dc-impl-${i}`}><select id={`dc-impl-${i}`} className="select" value={d.implementation} onChange={(e) => { const impl = e.target.value as DownloadClientCfg["implementation"]; set(i, { implementation: impl, port: DC_PORT[impl] }); }}>{DC_IMPL.map((x) => <option key={x} value={x}>{x}</option>)}</select></Field></div>
              <div className="span-3"><Field label={t("prov.host")} htmlFor={`dc-host-${i}`}><input id={`dc-host-${i}`} className="input mono" value={d.host} placeholder="sabnzbd" onChange={(e) => set(i, { host: e.target.value })} /></Field></div>
              <div className="span-3"><Field label={t("prov.port")} htmlFor={`dc-port-${i}`}><input id={`dc-port-${i}`} className="input num" inputMode="numeric" value={d.port} onChange={(e) => set(i, { port: Number(e.target.value.replace(/\D/g, "")) || 0 })} /></Field></div>
              <div className="span-3"><Field label={t("prov.urlBase")} htmlFor={`dc-ub-${i}`}><input id={`dc-ub-${i}`} className="input mono" value={d.url_base} placeholder="/sabnzbd" onChange={(e) => set(i, { url_base: e.target.value })} /></Field></div>
              <div className="span-3"><Field label={t("prov.username")} htmlFor={`dc-user-${i}`}><input id={`dc-user-${i}`} className="input" autoComplete="off" value={d.username} onChange={(e) => set(i, { username: e.target.value })} /></Field></div>
              <div className="span-3"><SecretInput id={`dc-key-${i}`} label={t("prov.apiKey")} isSet={d.api_key_set} value={d.api_key} onChange={(v) => set(i, { api_key: v })} /></div>
              <div className="span-3"><SecretInput id={`dc-pass-${i}`} label={t("prov.password")} isSet={d.password_set} value={d.password} onChange={(v) => set(i, { password: v })} /></div>
            </div>
            <div className="row wrap" style={{ gap: 16 }}>
              <label className="choice"><input type="checkbox" checked={d.use_ssl} onChange={(e) => set(i, { use_ssl: e.target.checked })} /><span>{t("prov.ssl")}</span></label>
              <label className="choice"><input type="checkbox" checked={d.enabled} onChange={(e) => set(i, { enabled: e.target.checked })} /><span>{t("prov.enabled")}</span></label>
            </div>
            <InstancePicker label={t("prov.dc.instances")} value={d.instances} instances={instances} onChange={(v) => set(i, { instances: v })} />
            <div className="grid" style={{ gap: 12 }}>
              {instances.filter((id) => d.instances === null || d.instances.includes(id)).map((id) => (
                <div key={id} className="span-3"><Field label={t("prov.dc.category", { instance: id })} htmlFor={`dc-cat-${i}-${id}`}><input id={`dc-cat-${i}-${id}`} className="input mono" value={d.categories[id] ?? ""} placeholder={cfg.data.defaults[id]?.category} onChange={(e) => set(i, { categories: { ...d.categories, [id]: e.target.value } })} /></Field></div>
              ))}
            </div>
            <div className="row wrap">
              <Button size="sm" icon={Wifi} disabled={!can("operator") || !d.host} pending={testing === `dc-${i}`} onClick={() => void test(`dc-${i}`, "/provisioning/downloadclients/test", { download_client: payload(d) })}>{t("action.test")}</Button>
              <Button size="sm" variant="ghost" icon={Trash2} onClick={() => setList((l) => l.filter((_, j) => j !== i))}>{t("action.delete")}</Button>
            </div>
            <TestLine result={results[`dc-${i}`] ?? null} />
          </section>
        ))}
        <div><Button size="sm" icon={Plus} onClick={() => setList((l) => [...l, { name: l.length ? `Client ${l.length + 1}` : "SABnzbd", implementation: "Sabnzbd", enabled: true, host: "", port: 8080, use_ssl: false, url_base: "", username: "", priority: 1, instances: null, categories: {} }])}>{t("prov.dc.add")}</Button></div>
        <SaveBar dirty={dirty} pending={pending} error={error} onSave={() => void onSave()} onReset={() => setList(initial)} />
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ indexers
export function IndexersTab({ cfg }: { cfg: Resource<ProvConfig> & { data: ProvConfig } }) {
  const { t } = useI18n();
  const { can } = useSession();
  const init = { mode: cfg.data.indexer_mode, prowlarr: cfg.data.prowlarr, indexers: cfg.data.indexers };
  const [mode, setMode] = useState(init.mode);
  const [pr, setPr] = useState(init.prowlarr);
  const [list, setList] = useState<IndexerCfg[]>(init.indexers);
  const { save, pending, error } = useSaveConfig(cfg);
  const { test, results, pending: testing } = useTest();
  const [prTest, setPrTest] = useState<ProwlarrTest | null>(null);
  const dirty = JSON.stringify({ mode, prowlarr: pr, indexers: list }) !== JSON.stringify(init);
  const setIdx = (i: number, patch: Partial<IndexerCfg>) => setList((l) => l.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  const onSave = async () => {
    const after = await save({ indexer_mode: mode, prowlarr: strip(pr, ["api_key_set"]), indexers: list.map((x) => strip(x, ["api_key_set", "effective_scope"])) });
    if (after?.prowlarr && after.indexers) { setPr(after.prowlarr); setList(after.indexers); }
  };
  const scopes = ["normal", "anime", "both"] as const;
  return (
    <Card title={t("prov.idx.title")} icon={Radar} sub={t("prov.idx.sub")}>
      <div className="stack">
        <Segmented label={t("prov.idx.mode")} value={mode} onChange={setMode} options={[{ value: "none", label: t("prov.idx.mode.none") }, { value: "prowlarr", label: "Prowlarr" }, { value: "direct", label: t("prov.idx.mode.direct") }]} />
        {mode === "none" ? <InlineState tone="warning">{t("prov.idx.noneHint")}</InlineState> : null}
        {mode === "prowlarr" ? (
          <section className="prov-item">
            <div className="grid" style={{ gap: 12 }}>
              <div className="span-4"><Field label={t("prov.idx.prUrl")} htmlFor="pr-url" hint={t("prov.idx.prUrlHint")}><input id="pr-url" className="input mono" value={pr.url} placeholder="http://prowlarr:9696" onChange={(e) => setPr({ ...pr, url: e.target.value })} /></Field></div>
              <div className="span-4"><Field label={t("prov.idx.prUrlArr")} htmlFor="pr-url-arr" hint={t("prov.idx.prUrlArrHint")}><input id="pr-url-arr" className="input mono" value={pr.url_from_arr} placeholder="http://prowlarr:9696" onChange={(e) => setPr({ ...pr, url_from_arr: e.target.value })} /></Field></div>
              <div className="span-4"><SecretInput id="pr-key" label={t("prov.apiKey")} isSet={pr.api_key_set} value={pr.api_key} onChange={(v) => setPr({ ...pr, api_key: v })} /></div>
            </div>
            <label className="choice"><input type="checkbox" checked={pr.manage_indexer_tags} onChange={(e) => setPr({ ...pr, manage_indexer_tags: e.target.checked })} /><span>{t("prov.idx.manageTags")}</span></label>
            <div className="row wrap"><Button size="sm" icon={Wifi} disabled={!can("operator")} pending={testing === "pr"} onClick={() => void test<ProwlarrTest>("pr", "/provisioning/prowlarr/test", { url: pr.url || null, api_key: pr.api_key || null }).then((r) => setPrTest(r))}>{t("action.test")}</Button></div>
            <TestLine result={results.pr ?? null} />
            {prTest?.indexers?.length ? (
              <div className="table-wrap">
                <table className="table table--compact table--stack">
                  <thead><tr><th>{t("prov.idx.indexer")}</th><th>{t("prov.idx.protocol")}</th><th>{t("prov.idx.scope")}</th></tr></thead>
                  <tbody>{prTest.indexers.map((x) => (
                    <tr key={x.id}><td className="cell-primary">{x.name}</td><td data-label={t("prov.idx.protocol")}>{x.protocol}</td>
                      <td data-label={t("prov.idx.scope")}><select className="select input--sm" aria-label={t("prov.idx.scopeFor", { name: x.name })} value={pr.indexer_scopes[String(x.id)] ?? x.suggested_scope} onChange={(e) => setPr({ ...pr, indexer_scopes: { ...pr.indexer_scopes, [String(x.id)]: e.target.value as "normal" | "anime" | "both" } })}>{scopes.map((s) => <option key={s} value={s}>{t(`prov.scope.${s}` as MessageKey)}</option>)}</select></td></tr>
                  ))}</tbody>
                </table>
              </div>
            ) : null}
          </section>
        ) : null}
        {mode === "direct" ? (
          <>
            {list.map((x, i) => (
              <section key={i} className="prov-item">
                <div className="grid" style={{ gap: 12 }}>
                  <div className="span-3"><Field label={t("prov.name")} htmlFor={`ix-name-${i}`}><input id={`ix-name-${i}`} className="input" value={x.name} onChange={(e) => setIdx(i, { name: e.target.value })} /></Field></div>
                  <div className="span-3"><Field label={t("prov.idx.kind")} htmlFor={`ix-impl-${i}`}><select id={`ix-impl-${i}`} className="select" value={x.implementation} onChange={(e) => setIdx(i, { implementation: e.target.value as IndexerCfg["implementation"] })}><option value="Newznab">Newznab</option><option value="Torznab">Torznab</option></select></Field></div>
                  <div className="span-3"><Field label={t("prov.url")} htmlFor={`ix-url-${i}`}><input id={`ix-url-${i}`} className="input mono" value={x.base_url} placeholder="https://indexer.example" onChange={(e) => setIdx(i, { base_url: e.target.value })} /></Field></div>
                  <div className="span-3"><Field label={t("prov.idx.apiPath")} htmlFor={`ix-path-${i}`}><input id={`ix-path-${i}`} className="input mono" value={x.api_path} onChange={(e) => setIdx(i, { api_path: e.target.value })} /></Field></div>
                  <div className="span-3"><SecretInput id={`ix-key-${i}`} label={t("prov.apiKey")} isSet={x.api_key_set} value={x.api_key} onChange={(v) => setIdx(i, { api_key: v })} /></div>
                  <div className="span-3"><Field label={t("prov.idx.scope")} htmlFor={`ix-scope-${i}`} hint={x.effective_scope ? t("prov.idx.effective", { scope: t(`prov.scope.${x.effective_scope}` as MessageKey) }) : undefined}>
                    <select id={`ix-scope-${i}`} className="select" value={x.scope ?? "auto"} onChange={(e) => setIdx(i, { scope: e.target.value === "auto" ? null : e.target.value as IndexerCfg["scope"] })}><option value="auto">{t("prov.scope.auto")}</option>{scopes.map((s) => <option key={s} value={s}>{t(`prov.scope.${s}` as MessageKey)}</option>)}</select>
                  </Field></div>
                </div>
                <div className="row wrap">
                  <Button size="sm" icon={Wifi} disabled={!can("operator") || !x.base_url} pending={testing === `ix-${i}`} onClick={() => void test(`ix-${i}`, "/provisioning/indexers/test", { indexer: strip(x, ["api_key_set", "effective_scope"]) })}>{t("action.test")}</Button>
                  <Button size="sm" variant="ghost" icon={Trash2} onClick={() => setList((l) => l.filter((_, j) => j !== i))}>{t("action.delete")}</Button>
                </div>
                <TestLine result={results[`ix-${i}`] ?? null} />
              </section>
            ))}
            <div><Button size="sm" icon={Plus} onClick={() => setList((l) => [...l, { name: `Indexer ${l.length + 1}`, implementation: "Newznab", base_url: "", api_path: "/api", scope: null, instances: null, enabled: true, priority: 25 }])}>{t("prov.idx.add")}</Button></div>
          </>
        ) : null}
        <SaveBar dirty={dirty} pending={pending} error={error} onSave={() => void onSave()} onReset={() => { setMode(init.mode); setPr(init.prowlarr); setList(init.indexers); }} />
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ paths & categories
const EMPTY_INST: InstanceCfg = { root_folder: null, category: null, mode: "auto", full_manage: [], disabled_sections: [] };

export function PathsTab({ cfg }: { cfg: Resource<ProvConfig> & { data: ProvConfig } }) {
  const { t, bytes } = useI18n();
  const { can } = useSession();
  const ids = Object.keys(cfg.data.defaults);
  const init = Object.fromEntries(ids.map((id) => [id, { ...EMPTY_INST, ...(cfg.data.instances[id] ?? {}) }]));
  const [state, setState] = useState<Record<string, InstanceCfg>>(init);
  const { save, pending, error } = useSaveConfig(cfg);
  const [checks, setChecks] = useState<Record<string, PathCheck | { error: string }>>({});
  const [checking, setChecking] = useState<string | null>(null);
  const dirty = JSON.stringify(state) !== JSON.stringify(init);
  const set = (id: string, patch: Partial<InstanceCfg>) => setState((s) => ({ ...s, [id]: { ...s[id], ...patch } }));
  const toggle = (list: string[], v: string, on: boolean) => (on ? [...new Set([...list, v])] : list.filter((x) => x !== v));
  const validate = async (id: string) => {
    setChecking(id);
    try { const r = await api.post<PathCheck>("/provisioning/paths/validate", { instance_id: id, path: state[id].root_folder || null }); setChecks((c) => ({ ...c, [id]: r })); }
    catch (e) { setChecks((c) => ({ ...c, [id]: { error: e instanceof ApiError ? e.message : String(e) } })); }
    finally { setChecking(null); }
  };
  const sections = cfg.data.sections.filter((s) => s.id !== "prowlarr" && s.id !== "seerr");
  return (
    <div className="stack">
      {ids.map((id) => {
        const s = state[id];
        const c = checks[id];
        return (
          <Card key={id} title={id} icon={FolderCheck} sub={cfg.data.defaults[id]?.profiles.join(" · ")}>
            <div className="stack">
              <div className="grid" style={{ gap: 12, alignItems: "end" }}>
                <div className="span-5"><Field label={t("prov.paths.root")} htmlFor={`root-${id}`}><input id={`root-${id}`} className="input mono" value={s.root_folder ?? ""} placeholder={cfg.data.defaults[id]?.root_folder} onChange={(e) => set(id, { root_folder: e.target.value || null })} /></Field></div>
                <div className="span-3"><Field label={t("prov.paths.category")} htmlFor={`cat-${id}`}><input id={`cat-${id}`} className="input mono" value={s.category ?? ""} placeholder={cfg.data.defaults[id]?.category} onChange={(e) => set(id, { category: e.target.value || null })} /></Field></div>
                <div className="span-2"><Field label={t("prov.paths.mode")} htmlFor={`mode-${id}`}><select id={`mode-${id}`} className="select" value={s.mode} onChange={(e) => set(id, { mode: e.target.value as InstanceCfg["mode"] })}>{(["auto", "manage", "hold"] as const).map((m) => <option key={m} value={m}>{t(`prov.mode.${m}` as MessageKey)}</option>)}</select></Field></div>
                <div className="span-2"><Button size="sm" icon={FolderCheck} disabled={!can("operator")} pending={checking === id} onClick={() => void validate(id)}>{t("prov.paths.check")}</Button></div>
              </div>
              {c ? ("error" in c ? <InlineState tone="danger">{c.error}</InlineState> : <InlineState tone={c.exists ? "success" : c.exists === false ? "danger" : "warning"}>{c.message_nl} <span className="mono">{c.path}</span>{c.free_space !== null ? ` · ${t("prov.paths.free", { free: bytes(c.free_space) })}` : ""}</InlineState>) : null}
              <details className="raw" style={{ border: "1px solid var(--mg-border-soft)", borderRadius: "var(--mg-r)" }}>
                <summary><SlidersHorizontal className="ico ico-sm" aria-hidden="true" />{t("prov.paths.sections")}</summary>
                <div className="table-wrap" style={{ padding: "0 12px 12px" }}>
                  <table className="table table--compact table--stack">
                    <thead><tr><th>{t("prov.col.section")}</th><th className="center">{t("prov.paths.managed")}</th><th className="center">{t("prov.paths.full")}</th></tr></thead>
                    <tbody>{sections.map((sec) => (
                      <tr key={sec.id}><td className="cell-primary">{t(`prov.section.${sec.id}` as MessageKey)}</td>
                        <td data-label={t("prov.paths.managed")} className="center"><input type="checkbox" aria-label={t("prov.paths.managedAria", { section: t(`prov.section.${sec.id}` as MessageKey) })} checked={!s.disabled_sections.includes(sec.id)} onChange={(e) => set(id, { disabled_sections: toggle(s.disabled_sections, sec.id, !e.target.checked) })} /></td>
                        <td data-label={t("prov.paths.full")} className="center">{sec.full_manage_possible ? <input type="checkbox" aria-label={t("prov.paths.fullAria", { section: t(`prov.section.${sec.id}` as MessageKey) })} checked={s.full_manage.includes(sec.id)} onChange={(e) => set(id, { full_manage: toggle(s.full_manage, sec.id, e.target.checked) })} /> : <span className="t-3">—</span>}</td></tr>
                    ))}</tbody>
                  </table>
                  <p className="hint">{t("prov.paths.fullHint")}</p>
                </div>
              </details>
            </div>
          </Card>
        );
      })}
      <SaveBar dirty={dirty} pending={pending} error={error} onSave={() => void save({ instances: state }).then((after) => { if (after?.instances) setState(Object.fromEntries(ids.map((id) => [id, { ...EMPTY_INST, ...(after.instances?.[id] ?? {}) }]))); })} onReset={() => setState(init)} />
    </div>
  );
}

// ------------------------------------------------------------------ media, quality, delay
export function MediaTab({ cfg }: { cfg: Resource<ProvConfig> & { data: ProvConfig } }) {
  const { t } = useI18n();
  const init = { media: cfg.data.media, quality: cfg.data.quality, delay: cfg.data.delay };
  const [m, setM] = useState(init.media);
  const [q, setQ] = useState(init.quality);
  const [d, setD] = useState(init.delay);
  const { save, pending, error } = useSaveConfig(cfg);
  const dirty = JSON.stringify({ media: m, quality: q, delay: d }) !== JSON.stringify(init);
  const check = (label: MessageKey, value: boolean, onChange: (v: boolean) => void) => <label className="choice"><input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} /><span>{t(label)}</span></label>;
  const score = (tpl: string, cf: string, base: number) => q.score_overrides[tpl]?.[cf] ?? base;
  return (
    <div className="stack">
      <Card title={t("prov.media.title")} icon={SlidersHorizontal}>
        <div className="stack">
          <div className="row wrap" style={{ gap: 18 }}>
            {check("prov.media.hardlinks", m.hardlinks, (v) => setM({ ...m, hardlinks: v }))}
            {check("prov.media.rename", m.rename, (v) => setM({ ...m, rename: v }))}
            {check("prov.media.unmonitorDeleted", m.unmonitor_deleted, (v) => setM({ ...m, unmonitor_deleted: v }))}
            {check("prov.media.extraFiles", m.import_extra_files, (v) => setM({ ...m, import_extra_files: v }))}
          </div>
          <div className="grid" style={{ gap: 12 }}>
            <div className="span-4"><Field label={t("prov.media.propers")} htmlFor="propers"><select id="propers" className="select" value={m.propers} onChange={(e) => setM({ ...m, propers: e.target.value as typeof m.propers })}>{(["preferAndUpgrade", "doNotUpgrade", "doNotPrefer"] as const).map((x) => <option key={x} value={x}>{t(`prov.propers.${x}` as MessageKey)}</option>)}</select></Field></div>
            <div className="span-4"><Field label={t("prov.media.extensions")} htmlFor="ext"><input id="ext" className="input mono" value={m.extra_file_extensions} onChange={(e) => setM({ ...m, extra_file_extensions: e.target.value })} /></Field></div>
            <div className="span-4"><Field label={t("prov.media.recycle")} htmlFor="recycle" hint={t("prov.media.recycleHint")}><input id="recycle" className="input mono" value={m.recycle_bin} onChange={(e) => setM({ ...m, recycle_bin: e.target.value })} /></Field></div>
          </div>
        </div>
      </Card>
      <Card title={t("prov.quality.title")} icon={SlidersHorizontal}>
        <div className="stack">
          <div className="row wrap" style={{ gap: 18 }}>
            {check("prov.quality.enabled", q.enabled, (v) => setQ({ ...q, enabled: v }))}
            {check("prov.quality.definitions", q.quality_definitions, (v) => setQ({ ...q, quality_definitions: v }))}
          </div>
          <div style={{ maxWidth: 320 }}><Field label={t("prov.quality.language")} htmlFor="lang"><select id="lang" className="select" value={q.language_films} onChange={(e) => setQ({ ...q, language_films: e.target.value as "original" | "any" })}><option value="original">{t("prov.quality.lang.original")}</option><option value="any">{t("prov.quality.lang.any")}</option></select></Field></div>
          {Object.entries(cfg.data.templates).map(([key, tpl]) => (
            <details key={key} className="raw" style={{ border: "1px solid var(--mg-border-soft)", borderRadius: "var(--mg-r)" }}>
              <summary>{tpl.name} <span className="t-3">· {tpl.label_nl}</span></summary>
              <div style={{ padding: "0 12px 12px" }} className="stack-sm">
                <p className="t-3" style={{ fontSize: 14, margin: 0 }}>{t("prov.quality.allowed", { list: tpl.allowed.join(", "), cutoff: tpl.cutoff })}</p>
                <div className="grid" style={{ gap: 10 }}>{Object.entries(tpl.scores).map(([cf, base]) => (
                  <div key={cf} className="span-3 md-span-6"><Field label={cf} htmlFor={`score-${key}-${cf}`}><input id={`score-${key}-${cf}`} className="input num" inputMode="numeric" value={score(key, cf, base)} onChange={(e) => { const v = Number(e.target.value.replace(/[^\d-]/g, "")) || 0; setQ({ ...q, score_overrides: { ...q.score_overrides, [key]: { ...(q.score_overrides[key] ?? {}), [cf]: v } } }); }} /></Field></div>
                ))}</div>
              </div>
            </details>
          ))}
        </div>
      </Card>
      <Card title={t("prov.delay.title")} icon={SlidersHorizontal}>
        <div className="grid" style={{ gap: 12, alignItems: "end" }}>
          <div className="span-4"><Field label={t("prov.delay.protocol")} htmlFor="proto"><select id="proto" className="select" value={d.preferred_protocol} onChange={(e) => setD({ ...d, preferred_protocol: e.target.value as typeof d.preferred_protocol })}>{(["auto", "usenet", "torrent"] as const).map((x) => <option key={x} value={x}>{t(`prov.delay.proto.${x}` as MessageKey)}</option>)}</select></Field></div>
          <div className="span-3"><Field label={t("prov.delay.usenet")} htmlFor="ud"><input id="ud" className="input num" inputMode="numeric" value={d.usenet_delay} onChange={(e) => setD({ ...d, usenet_delay: Number(e.target.value.replace(/\D/g, "")) || 0 })} /></Field></div>
          <div className="span-3"><Field label={t("prov.delay.torrent")} htmlFor="td"><input id="td" className="input num" inputMode="numeric" value={d.torrent_delay} onChange={(e) => setD({ ...d, torrent_delay: Number(e.target.value.replace(/\D/g, "")) || 0 })} /></Field></div>
          <div className="span-12">{check("prov.delay.bypass", d.bypass_if_highest_quality, (v) => setD({ ...d, bypass_if_highest_quality: v }))}</div>
        </div>
      </Card>
      <SaveBar dirty={dirty} pending={pending} error={error} onSave={() => void save({ media: m, quality: q, delay: d }).then((after) => { if (after?.media && after.quality && after.delay) { setM(after.media); setQ(after.quality); setD(after.delay); } })} onReset={() => { setM(init.media); setQ(init.quality); setD(init.delay); }} />
    </div>
  );
}

// ------------------------------------------------------------------ Seerr
export function SeerrTab({ cfg }: { cfg: Resource<ProvConfig> & { data: ProvConfig } }) {
  const { t } = useI18n();
  const { can } = useSession();
  const { run, pending: acting } = useAction();
  const [url, setUrl] = useState(cfg.data.seerr_connection.url);
  const [key, setKey] = useState<string | null | undefined>(undefined);
  const [opts, setOpts] = useState(cfg.data.seerr);
  const { save, pending, error } = useSaveConfig(cfg);
  const [preview, setPreview] = useState<{ requires_confirmation?: boolean; changes: ProvChange[]; message_nl?: string } | null>(null);
  const dirty = url !== cfg.data.seerr_connection.url || key !== undefined || JSON.stringify(opts) !== JSON.stringify(cfg.data.seerr);
  const showChanges = (changes: ProvChange[]): ReactNode => (
    <ul className="list">{changes.map((c, i) => (
      <li key={i} className="list-row" style={{ alignItems: "flex-start" }}>
        <ActionChip action={c.action} />
        <div className="list-main"><div className="list-title" style={{ whiteSpace: "normal" }}>{c.title_nl}</div>{c.message_nl ? <div className="list-sub" style={{ whiteSpace: "normal" }}>{c.message_nl}</div> : null}
          {c.diff.length ? <dl className="kv" style={{ fontSize: 14 }}>{c.diff.map((dl) => <div key={dl.field} style={{ display: "contents" }}><dt>{dl.label_nl}</dt><dd className="mono">{String(dl.old ?? "—")} → {String(dl.new ?? "—")}</dd></div>)}</dl> : null}
        </div>
      </li>
    ))}</ul>
  );
  return (
    <Card title={t("prov.seerr.title")} icon={Inbox} sub={t("prov.seerr.sub")}>
      <div className="stack">
        <div className="grid" style={{ gap: 12 }}>
          <div className="span-6"><Field label={t("prov.url")} htmlFor="seerr-url" hint={t("prov.seerr.verifyHint")}><input id="seerr-url" className="input mono" value={url} placeholder="http://seerr:5055" onChange={(e) => setUrl(e.target.value)} /></Field></div>
          <div className="span-6"><SecretInput id="seerr-key" label={t("prov.apiKey")} isSet={cfg.data.seerr_connection.api_key_set} value={key} onChange={setKey} /></div>
        </div>
        <div className="row wrap" style={{ gap: 18 }}>
          <label className="choice"><input type="checkbox" checked={opts.enabled} onChange={(e) => setOpts({ ...opts, enabled: e.target.checked })} /><span>{t("prov.seerr.enabled")}</span></label>
          <label className="choice"><input type="checkbox" checked={opts.scan} onChange={(e) => setOpts({ ...opts, scan: e.target.checked })} /><span>{t("prov.seerr.scan")}</span></label>
        </div>
        <SaveBar dirty={dirty} pending={pending} error={error} onSave={() => void save({ seerr_connection: url ? { url, api_key: key ?? null } : undefined, seerr: opts }).then((after) => { if (after) setKey(undefined); })} onReset={() => { setUrl(cfg.data.seerr_connection.url); setKey(undefined); setOpts(cfg.data.seerr); }} />
        <InlineState tone="info">{t("prov.seerr.oldNote")}</InlineState>
        <div className="row wrap">
          <Button size="sm" disabled={!can("admin")} pending={acting === "sp"} onClick={() => void run<typeof preview>("sp", "post", "/provisioning/seerr/apply", { confirm: false, public_host: window.location.hostname }).then((r) => { if (r) setPreview(r); })}>{t("prov.seerr.preview")}</Button>
          <Button size="sm" variant="primary" disabled={!can("admin") || !preview?.requires_confirmation} pending={acting === "sa"} onClick={() => void run<{ changes: ProvChange[] }>("sa", "post", "/provisioning/seerr/apply", { confirm: true, public_host: window.location.hostname }, t("prov.seerr.applied")).then((r) => { if (r) setPreview({ changes: r.changes }); })}>{t("prov.seerr.apply")}</Button>
        </div>
        {preview ? <div data-testid="seerr-preview">{preview.message_nl ? <p className="t-3" style={{ fontSize: 14 }}>{preview.message_nl}</p> : null}{showChanges(preview.changes)}</div> : null}
      </div>
    </Card>
  );
}
