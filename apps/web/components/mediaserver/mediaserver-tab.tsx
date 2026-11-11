"use client";

import { Captions, Link2, MonitorPlay, Pencil, Plus, RefreshCw, Search, Trash2, Unlink } from "lucide-react";
import { useState, type CSSProperties, type ReactNode } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Segmented, StatusChip, Switch } from "@/components/mg/primitives";
import { ErrorState, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

import { MAX_EXTRA_SERVERS, isResultKey, serverState, sourceKey, suggestions, type MsServer, type ServerKind, type SettingKind } from "./server-state";

interface Features {
  refresh_after_handoff: boolean; playback_test: boolean; playback_fail_action: "notify" | "rollback";
  pause_while_watching: "both" | "gpu" | "off"; skip_recent_watch: boolean; recent_watch_hours: number;
}
interface ServerTest { server_id: number; result: string; detail_nl: string | null; nl_subtitles: string | null; label: string }
interface Test { path: string; result: string; detail_nl: string; done_at: string | null; servers?: ServerTest[] }
interface Action { ts: string; kind: string; ok: number; detail_nl: string; server_id?: number | null }
interface Found { kind: string; url: string; name?: string; label: string }
export interface MsStatus {
  kind: SettingKind; label: string; url: string; url_source: string; key_set: boolean;
  setting_kind: SettingKind; setting_url: string;
  connected: { at: string; name?: string; version?: string } | null;
  discovery: { servers: Found[]; bazarr: Array<{ url: string }> } | null;
  path_map: Array<[string, string]>;
  /** Δ-MG-DUAL: één element per ingestelde mediaserver; ontbreekt bij een core van vóór 1.3. */
  servers?: MsServer[];
  features: Features;
  hold: { active: boolean; reason: string | null };
  sessions: { watching: Array<{ user: string; title: string; paused: boolean }> } | null;
  tests: Test[]; actions: Action[];
  key_help_nl: Record<string, string>;
  subtitles: {
    nl_auto: boolean; nl_wanted: boolean; bazarr_url: string; bazarr_key_set: boolean;
    opensubtitles_enabled: boolean; opensubtitles_key_set: boolean; opensubtitles_daily: number;
    downloads_today: number; source: "bazarr" | "opensubtitles" | null; missing: number;
  };
}
interface TestResult { ok: boolean; message_nl: string }
interface ConfigAnswer { test: TestResult | null; server_id: number | null }

function Row({ title, hint, children }: { title: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="list-row list-row--wrap">
      <div className="list-main"><div className="list-title">{title}</div>{hint ? <div className="list-sub">{hint}</div> : null}</div>
      <span className="list-aside">{children}</span>
    </div>
  );
}

const ACTIONS: CSSProperties = { display: "flex", gap: 8, flexWrap: "wrap" };
// Eén server = één rij met twee lagen: bovenaan feiten en status naast elkaar (onder elkaar als het smal wordt),
// daaronder het formulier en de handelingen over de volle breedte.
const ITEM: CSSProperties = { flexDirection: "column", alignItems: "stretch" };
const ITEM_HEAD: CSSProperties = { display: "flex", flexWrap: "wrap", alignItems: "flex-start", gap: "8px 12px", minWidth: 0 };
// De reden bij een status moet je helemaal kunnen lezen: nooit afkappen met een beletselteken.
const FULL: CSSProperties = { whiteSpace: "normal" };

/** Instellingen → Koppelingen → Mediaservers & ondertitels. Alles in de app in te stellen. */
export function MediaServerTab() {
  const status = useResource<MsStatus>("/mediaserver/status", undefined, 30_000);
  if (status.error && !status.data) return <Card><ErrorState error={status.error} onRetry={() => void status.reload()} endpoint="GET /api/v1/mediaserver/status" /></Card>;
  if (!status.data) return <Card><StateView kind="loading" /></Card>;
  return (
    <div className="stack" data-testid="mediaserver-tab">
      <ServersCard s={status.data} reload={() => void status.reload()} />
      <SubtitlesCard s={status.data} reload={() => void status.reload()} />
    </div>
  );
}

/** Koppeling (ingesteld) en gezondheid (gemeten) van één server: vorm + icoon + woord, en bij Onbekend de reden. */
export function ServerChips({ s }: { s: MsServer }) {
  const { t } = useI18n();
  const st = serverState(s);
  if (st.link === "detached") return <StatusChip tone="unlinked">{t("ms.link.detached")}</StatusChip>;
  if (st.link !== "linked") return <StatusChip tone="unlinked">{t("ms.status.none")}</StatusChip>;
  return (
    <>
      <StatusChip tone="linked">{t("ms.link.linked")}</StatusChip>
      {st.health === "healthy" ? <StatusChip tone="healthy">{t("status.healthy")}</StatusChip>
        : st.health === "down" ? <StatusChip tone="danger">{t("status.offline")}</StatusChip>
          : <StatusChip tone="unknown">{t("status.unknown")}</StatusChip>}
    </>
  );
}

/** Wat er over één server te zeggen valt: eerst wat, dan waarom, dan wat je kunt doen. */
export function ServerFacts({ s }: { s: MsServer }) {
  const { t, relative, num } = useI18n();
  const st = serverState(s);
  const r = s.results;
  const tested = r.ok + r.transcode + r.failed + r.not_found + r.open;
  const kind = s.kind === "auto" ? t("ms.kind.autoShort") : t(`ms.kind.${s.kind}` as MessageKey);
  const why = st.link === "detached" ? t("ms.why.detached")
    : st.link === "noAddress" ? t("ms.why.noAddress")
      : st.link === "noKey" ? t("ms.why.noKey", { label: s.label })
        : st.health === "healthy" ? t("ms.why.healthy", { when: relative(s.contact?.at ?? null) })
          : st.health === "down" ? t("ms.why.down", { error: s.contact?.error || t("ms.why.noAnswer") })
            : st.why === "stale" ? t("ms.why.stale", { when: relative(s.contact?.at ?? null) })
              : t("ms.why.notMeasured");
  return (
    <>
      <div className="list-title" style={FULL}>{s.label}{kind !== s.label ? <span className="list-sub"> · {kind}</span> : null}</div>
      <div className="list-sub mono" style={{ ...FULL, wordBreak: "break-all" }}>{s.url ? `${s.url} · ${t(sourceKey(s.url_source))}` : t("ms.srv.noAddress")}</div>
      <div className="list-sub" style={FULL} data-testid={`ms-why-${s.server_id}`}>{why}</div>
      {st.health === "down" && s.contact?.last_ok_at ? <div className="list-sub" style={FULL}>{t("ms.why.lastOk", { when: relative(s.contact.last_ok_at) })}</div> : null}
      {s.auth ? <div className="list-sub" style={FULL}>{t("ms.srv.auth", { auth: s.auth })}</div> : null}
      {s.hold.active ? <div className="list-sub" style={FULL}>{t("ms.srv.watching")}</div> : null}
      {s.path_map.length ? (
        <div className="list-sub" style={FULL}>{t("ms.paths")}: {s.path_map.map(([a, b]) => <span className="mono" key={a} style={{ display: "block", wordBreak: "break-all" }}>{a} → {b}</span>)}</div>
      ) : s.followed ? <div className="list-sub" style={FULL}>{t("ms.pathsNone")}</div> : null}
      {tested ? (
        <div className="list-sub" style={FULL}>
          {t("ms.srv.results", { ok: num(r.ok), transcode: num(r.transcode), failed: num(r.failed + r.not_found), open: num(r.open) })}
          {r.nl_visible + r.nl_missing ? ` · ${t("ms.srv.nl", { visible: num(r.nl_visible), missing: num(r.nl_missing) })}` : ""}
        </div>
      ) : null}
    </>
  );
}

type Draft = { kind: ServerKind; url: string; key: string; label: string };

/** Soort, naam, adres en sleutel van één server (bestaand of nieuw). De sleutel wordt nooit teruggelezen. */
function ServerForm({ id, legacy, draft, setDraft, keySet, urlHint, admin, found }: {
  id: string; legacy: boolean; draft: Draft; setDraft: (d: Draft) => void; keySet: boolean; urlHint: ReactNode; admin: boolean; found?: Found[];
}) {
  const { t } = useI18n();
  const kinds: ServerKind[] = legacy ? ["auto", "emby", "jellyfin", "plex"] : ["emby", "jellyfin", "plex"];
  const helpKind = draft.kind === "jellyfin" ? "jellyfin" : draft.kind === "plex" ? "plex" : "emby";
  return (
    <div className="stack">
      <Field label={t("ms.kind")} htmlFor={`ms-kind-${id}`}>
        <Segmented<ServerKind> label={t("ms.kind")} value={draft.kind} onChange={(kind) => setDraft({ ...draft, kind })}
          options={kinds.map((k) => ({ value: k, label: t((legacy || k !== "jellyfin" ? `ms.kind.${k}` : "ms.kind.jellyfinLeon") as MessageKey), disabled: !admin }))} />
      </Field>
      {found?.length ? (
        <div className="list-sub">
          {t("ms.add.found")}
          <span style={{ ...ACTIONS, marginTop: 6 }}>
            {found.map((f) => (
              <Button key={`${f.kind}-${f.url}`} size="sm" disabled={!admin}
                onClick={() => setDraft({ ...draft, kind: f.kind === "jellyfin" || f.kind === "plex" ? f.kind : "emby", url: f.url })}>
                {t("ms.add.use", { label: f.label, url: f.url })}
              </Button>
            ))}
          </span>
        </div>
      ) : null}
      {legacy ? null : (
        <Field label={t("ms.name")} htmlFor={`ms-name-${id}`} hint={t("ms.nameHint")}>
          <input id={`ms-name-${id}`} className="input" value={draft.label} maxLength={60} disabled={!admin} onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
        </Field>
      )}
      <Field label={t("ms.url")} htmlFor={`ms-url-${id}`} hint={urlHint}>
        <input id={`ms-url-${id}`} className="input" value={draft.url} placeholder="http://nas.lan:8096" disabled={!admin} onChange={(e) => setDraft({ ...draft, url: e.target.value })} />
      </Field>
      <Field label={t("ms.key")} htmlFor={`ms-key-${id}`} hint={keySet ? t("ms.keySet") : undefined}>
        <input id={`ms-key-${id}`} className="input" type="password" autoComplete="off" value={draft.key} placeholder={t("ms.keyPlaceholder")} disabled={!admin} onChange={(e) => setDraft({ ...draft, key: e.target.value })} />
      </Field>
      <InlineState tone="info"><strong>{t("ms.keyHelpTitle")}</strong> {t(`ms.keyHelp.${helpKind}` as MessageKey)}</InlineState>
    </div>
  );
}

/** Eén mediaserver in de lijst: status, feiten en de handelingen voor die server. */
function ServerItem({ s, status, reload, onResult }: { s: MsServer; status: MsStatus; reload: () => void; onResult: (id: number, r: TestResult | null) => void }) {
  const { t } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const admin = can("admin");
  const st = serverState(s);
  const needsSetup = s.enabled && !s.key_set;
  const [editing, setEditing] = useState(false);
  const open = s.enabled && (editing || needsSetup);
  const initial = (): Draft => ({
    kind: s.legacy && status.setting_kind !== "off" ? status.setting_kind : s.kind,
    url: s.legacy ? status.setting_url || "" : s.url, key: "", label: s.label,
  });
  const [draft, setDraft] = useState<Draft>(initial);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"detach" | "remove" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // server 1 gaat zonder server_id: precies het verzoek van 1.2.2
  const target = s.legacy ? {} : { server_id: s.server_id };
  const run = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name); setError(null);
    try { await fn(); reload(); return true; } catch (e) { const message = e instanceof Error ? e.message : String(e); setError(message); onResult(s.server_id, { ok: false, message_nl: message }); return false; } finally { setBusy(null); }
  };
  const save = () => run("save", async () => {
    const r = await api.post<ConfigAnswer>("/mediaserver/config", {
      ...target, kind: draft.kind, url: draft.url, ...(s.legacy ? {} : { label: draft.label }), ...(draft.key ? { api_key: draft.key } : {}),
    });
    setDraft({ ...draft, key: "" }); setEditing(false);
    if (r.test) onResult(s.server_id, r.test); else toast.push("success", t("ms.saved"));
  });
  const post = (name: string, body: Record<string, unknown>, done: string) => run(name, async () => {
    const r = await api.post<ConfigAnswer>("/mediaserver/config", { ...(s.legacy ? { server_id: 1 } : target), ...body });
    onResult(s.server_id, r.test ?? null);
    setConfirm(null);
    toast.push("success", done);
  });
  const found = status.discovery?.servers ?? [];
  const urlHint = s.legacy
    ? (found.length ? t("ms.found", { list: found.map((x) => `${x.label} ${x.url}`).join(", ") }) : t("ms.urlHint"))
    : undefined;
  return (
    <div className="list-row" style={ITEM} data-testid={`ms-server-${s.server_id}`}>
      <div style={ITEM_HEAD}>
        <div className="list-main" style={{ flex: "1 1 18rem" }}><ServerFacts s={s} /></div>
        <span className="list-aside" style={{ flexWrap: "wrap" }}><ServerChips s={s} /></span>
      </div>
      <div className="stack" style={{ minWidth: 0 }}>
        {open ? <ServerForm id={String(s.server_id)} legacy={s.legacy} draft={draft} setDraft={setDraft} keySet={s.key_set} urlHint={urlHint} admin={admin} /> : null}
        <div style={ACTIONS}>
          {open && admin ? <Button variant="primary" pending={busy === "save"} onClick={() => void save()}>{t("ms.save")}</Button> : null}
          {s.enabled && can("operator") ? (
            <Button icon={RefreshCw} pending={busy === "test"}
              onClick={() => void run("test", async () => onResult(s.server_id, await api.post<TestResult>(s.legacy ? "/mediaserver/test" : `/mediaserver/test?server_id=${s.server_id}`)))}>
              {t("ms.test")}
            </Button>
          ) : null}
          {s.enabled && admin && !open ? <Button icon={Pencil} onClick={() => { setDraft(initial()); setEditing(true); }}>{t("ms.edit", { label: s.label })}</Button> : null}
          {open && editing && !needsSetup ? <Button variant="ghost" onClick={() => setEditing(false)}>{t("action.cancel")}</Button> : null}
          {open && admin && s.key_set ? <Button variant="ghost" pending={busy === "clear"} onClick={() => void post("clear", { clear_key: true }, t("ms.keyRemoved", { label: s.label }))}>{t("ms.removeKey")}</Button> : null}
          {admin && s.enabled && st.link !== "noAddress" ? <Button variant="ghost" icon={Unlink} onClick={() => setConfirm("detach")}>{t("ms.detach", { label: s.label })}</Button> : null}
          {admin && !s.enabled ? <Button variant="primary" icon={Link2} pending={busy === "attach"} onClick={() => void post("attach", { enabled: true }, t("ms.attached", { label: s.label }))}>{t("ms.attach", { label: s.label })}</Button> : null}
          {admin && !s.legacy ? <Button variant="ghost" icon={Trash2} onClick={() => setConfirm("remove")}>{t("ms.remove", { label: s.label })}</Button> : null}
        </div>
      </div>
      <DangerDialog open={confirm === "detach"} onClose={() => setConfirm(null)} icon={Unlink} pending={busy === "detach"} error={error}
        title={t("ms.detach.title", { label: s.label })} description={t("ms.detach.body", { label: s.label })} confirmLabel={t("ms.detach", { label: s.label })}
        onConfirm={() => void post("detach", { enabled: false }, t("ms.detached", { label: s.label }))} />
      <DangerDialog open={confirm === "remove"} onClose={() => setConfirm(null)} icon={Trash2} pending={busy === "remove"} error={error}
        title={t("ms.remove.title", { label: s.label })} description={t("ms.remove.body", { label: s.label })} confirmLabel={t("ms.remove", { label: s.label })}
        onConfirm={() => void post("remove", { remove: true }, t("ms.removed", { label: s.label }))} />
    </div>
  );
}

/** Een mediaserver erbij. Staat server 1 niet in de lijst (uitgezet, niets bewaard), dan vult dit formulier die plek. */
function AddServer({ status, servers, reload, onResult }: { status: MsStatus; servers: MsServer[]; reload: () => void; onResult: (id: number, r: TestResult | null) => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const first = !servers.some((s) => s.legacy);
  const empty = (): Draft => ({ kind: first ? "auto" : "jellyfin", url: "", key: "", label: "" });
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(empty);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const full = servers.filter((s) => !s.legacy).length >= MAX_EXTRA_SERVERS;
  const shown = open || servers.length === 0;
  const found = suggestions(status.discovery?.servers ?? [], servers);
  const add = async () => {
    setBusy(true); setError(null);
    try {
      const r = await api.post<ConfigAnswer>("/mediaserver/config", first
        ? { kind: draft.kind, url: draft.url, ...(draft.key ? { api_key: draft.key } : {}) }
        : { add: true, kind: draft.kind, url: draft.url, ...(draft.label.trim() ? { label: draft.label.trim() } : {}), ...(draft.key ? { api_key: draft.key } : {}) });
      setDraft(empty()); setOpen(false);
      if (r.server_id != null) onResult(r.server_id, r.test);
      if (!r.test) toast.push("success", t("ms.saved"));
      reload();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  if (!shown) {
    return (
      <div className="list-row list-row--wrap">
        <div className="list-main"><div className="list-sub" style={FULL}>{full ? t("ms.add.full", { max: MAX_EXTRA_SERVERS }) : t("ms.add.hint")}</div></div>
        <span className="list-aside"><Button icon={Plus} disabled={full} onClick={() => setOpen(true)} data-testid="ms-add-open">{t("ms.add")}</Button></span>
      </div>
    );
  }
  return (
    <div className="list-row list-row--wrap" data-testid="ms-add-form">
      <div className="stack" style={{ flex: "1 1 100%", minWidth: 0 }}>
        <div className="list-title">{first ? t("ms.add.firstTitle") : t("ms.add.title")}</div>
        {first ? null : <div className="list-sub" style={FULL}>{t("ms.add.keepsFirst")}</div>}
        <ServerForm id="new" legacy={first} draft={draft} setDraft={setDraft} keySet={false} urlHint={first ? t("ms.urlHint") : t("ms.add.urlHint")} admin found={found} />
        <div style={ACTIONS}>
          <Button variant="primary" pending={busy} disabled={!first && !draft.url.trim()} onClick={() => void add()} data-testid="ms-add-save">{first ? t("ms.save") : t("ms.add.save")}</Button>
          {servers.length ? <Button variant="ghost" onClick={() => { setOpen(false); setDraft(empty()); setError(null); }}>{t("action.cancel")}</Button> : null}
        </div>
        {error ? <InlineState tone="warning" role="alert">{error}</InlineState> : null}
      </div>
    </div>
  );
}

function ServersCard({ s, reload }: { s: MsStatus; reload: () => void }) {
  const { t, relative } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const admin = can("admin");
  const servers = s.servers ?? [];
  const [busy, setBusy] = useState<string | null>(null);
  const [results, setResults] = useState<Record<number, TestResult | null>>({});
  const [error, setError] = useState<string | null>(null);
  const onResult = (id: number, r: TestResult | null) => setResults((prev) => ({ ...prev, [id]: r }));
  const run = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name); setError(null);
    try { await fn(); reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  };
  const feature = (name: keyof Features, value: unknown) => run(`f-${name}`, async () => {
    await api.post("/mediaserver/config", { features: { [name]: value } });
    toast.push("success", t("ms.saved"));
  });
  const f = s.features;
  const followed = servers.filter((x) => x.followed);
  // één server: noem hem bij naam, zoals voorheen; meer dan één: de teksten gelden voor elke gekoppelde server
  const only = servers.length === 1 ? servers[0] : undefined;
  const label = only ? only.label : s.label && s.label !== "Uit" ? s.label : "Emby";
  const many = servers.length > 1;
  const found = s.discovery?.servers ?? [];
  const state = followed.length
    ? <StatusChip tone="linked">{t("ms.count.linked", { n: followed.length })}</StatusChip>
    : <StatusChip tone="unlinked">{t("ms.status.none")}</StatusChip>;
  return (
    <Card title={t("ms.title")} icon={MonitorPlay} sub={t("ms.sub")} actions={state}>
      <div className="list" data-testid="ms-servers">
        {servers.map((x) => (
          <div key={x.server_id}>
            <ServerItem s={x} status={s} reload={reload} onResult={onResult} />
            {results[x.server_id] ? (
              <div className="list-row"><div className="list-main">
                <InlineState tone={results[x.server_id]?.ok ? "success" : "warning"} role="status">{results[x.server_id]?.message_nl}</InlineState>
              </div></div>
            ) : null}
          </div>
        ))}
        {admin ? <AddServer status={s} servers={servers} reload={reload} onResult={onResult} /> : null}
        {!admin && !servers.length ? <div className="list-row"><div className="list-main"><div className="list-sub">{t("ms.noneViewer")}</div></div></div> : null}
      </div>
      <div className="stack" style={{ marginTop: 12 }}>
        <div style={ACTIONS}>
          {can("operator") ? <Button icon={Search} pending={busy === "discover"} onClick={() => void run("discover", () => api.post("/mediaserver/discover"))}>{t("ms.search")}</Button> : null}
        </div>
        {error ? <InlineState tone="warning" role="alert">{error}</InlineState> : null}
        {!found.length && !servers.some((x) => x.url) ? <InlineState tone="neutral">{t("ms.notFound")}</InlineState> : null}
        {s.hold.active && s.hold.reason ? <InlineState tone="info">{t("ms.hold", { reason: s.hold.reason })}</InlineState> : null}
        {s.sessions?.watching?.length ? <div className="list-sub">{t("ms.watching", { list: s.sessions.watching.map((w) => `${w.user}: ${w.title}`).join(" · ") })}</div> : null}
      </div>
      <div className="list" style={{ marginTop: 12 }}>
        <div className="list-row"><div className="list-main"><div className="list-title">{many ? t("ms.featuresAll") : t("ms.features", { label })}</div></div></div>
        <Row title={t("ms.f.refresh")} hint={many ? t("ms.f.refreshHintAll") : t("ms.f.refreshHint", { label })}><Switch label={t("ms.f.refresh")} checked={f.refresh_after_handoff} disabled={!admin} onChange={(v) => void feature("refresh_after_handoff", v)} /></Row>
        <Row title={t("ms.f.test")} hint={many ? t("ms.f.testHintAll") : t("ms.f.testHint")}><Switch label={t("ms.f.test")} checked={f.playback_test} disabled={!admin} onChange={(v) => void feature("playback_test", v)} /></Row>
        {f.playback_test ? (
          <Row title={t("ms.f.fail")}>
            <Segmented label={t("ms.f.fail")} value={f.playback_fail_action} onChange={(v) => void feature("playback_fail_action", v)}
              options={(["notify", "rollback"] as const).map((v) => ({ value: v, label: t(`ms.f.fail.${v}` as MessageKey), disabled: !admin }))} />
          </Row>
        ) : null}
        <Row title={t("ms.f.pause")} hint={many ? t("ms.f.pauseHintAll") : t("ms.f.pauseHint")}>
          <Segmented label={t("ms.f.pause")} value={f.pause_while_watching} onChange={(v) => void feature("pause_while_watching", v)}
            options={(["both", "gpu", "off"] as const).map((v) => ({ value: v, label: t(`ms.f.pause.${v}` as MessageKey), disabled: !admin }))} />
        </Row>
        <Row title={t("ms.f.recent")} hint={t("ms.f.recentHint", { hours: f.recent_watch_hours })}><Switch label={t("ms.f.recent")} checked={f.skip_recent_watch} disabled={!admin} onChange={(v) => void feature("skip_recent_watch", v)} /></Row>
        {s.tests.length ? (
          <div className="list-row list-row--wrap"><div className="list-main">
            <div className="list-title">{t("ms.tests")}</div>
            {s.tests.slice(0, 5).map((x) => (
              <div className="list-sub" key={`${x.path}-${x.done_at}`} style={{ wordBreak: "break-word" }}>
                <StatusChip tone={x.result === "ok" ? "success" : x.result === "failed" ? "danger" : "neutral"}>{isResultKey(x.result) ? t(`ms.result.${x.result}` as MessageKey) : x.result}</StatusChip>{" "}
                {x.path.split("/").pop()} · {relative(x.done_at)}
                {(x.servers?.length ?? 0) > 1 ? x.servers?.map((r) => (
                  <span key={r.server_id} style={{ display: "block" }}>
                    {r.label}: {isResultKey(r.result) ? t(`ms.result.${r.result}` as MessageKey) : r.result}
                    {r.nl_subtitles === "visible" ? ` · ${t("ms.nl.visible")}` : r.nl_subtitles === "missing" ? ` · ${t("ms.nl.missing")}` : ""}
                  </span>
                )) : null}
              </div>
            ))}
          </div></div>
        ) : null}
        {s.actions.length ? (
          <div className="list-row list-row--wrap"><div className="list-main">
            <div className="list-title">{t("ms.recent")}</div>
            {s.actions.slice(0, 6).map((a, i) => <div className="list-sub" key={i} style={{ wordBreak: "break-word" }}>{relative(a.ts)} · {a.detail_nl}</div>)}
          </div></div>
        ) : null}
      </div>
    </Card>
  );
}

function SubtitlesCard({ s, reload }: { s: MsStatus; reload: () => void }) {
  const { t, num } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const admin = can("admin");
  const sub = s.subtitles;
  const [bazarrUrl, setBazarrUrl] = useState(sub.bazarr_url || "");
  const [bazarrKey, setBazarrKey] = useState("");
  const [os, setOs] = useState({ key: "", user: "", pass: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<TestResult | null>(null);
  const run = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    try { await fn(); reload(); } catch (e) { setResult({ ok: false, message_nl: e instanceof Error ? e.message : String(e) }); } finally { setBusy(null); }
  };
  const save = (extra: Record<string, unknown> = {}) => run("save", async () => {
    const r = await api.post<{ test: TestResult }>("/mediaserver/subtitles/config", {
      bazarr_url: bazarrUrl, ...(bazarrKey ? { bazarr_api_key: bazarrKey } : {}),
      ...(os.key ? { opensubtitles_api_key: os.key } : {}), ...(os.user ? { opensubtitles_username: os.user } : {}),
      ...(os.pass ? { opensubtitles_password: os.pass } : {}), ...extra,
    });
    setBazarrKey(""); setOs({ key: "", user: "", pass: "" });
    setResult(r.test);
  });
  const feature = (name: string, value: unknown) => run(`f-${name}`, async () => {
    await api.post("/mediaserver/subtitles/config", { features: { [name]: value } });
    toast.push("success", t("ms.saved"));
  });
  const source = sub.source === "bazarr" ? t("subs.source.bazarr")
    : sub.source === "opensubtitles" ? t("subs.source.opensubtitles", { n: num(sub.downloads_today), max: num(sub.opensubtitles_daily) })
      : t("subs.source.none");
  return (
    <Card title={t("subs.title")} icon={Captions} sub={t("subs.sub")}
      actions={<StatusChip tone={sub.missing ? "warning" : "success"}>{sub.missing ? t("subs.missing", { n: num(sub.missing) }) : t("subs.missingNone")}</StatusChip>}>
      <div className="stack" data-testid="subtitles-card">
        {!sub.nl_wanted ? <InlineState tone="neutral">{t("subs.notWanted")}</InlineState> : null}
        <div className="list">
          <Row title={t("subs.auto")} hint={t("subs.autoHint")}><Switch label={t("subs.auto")} checked={sub.nl_auto} disabled={!admin} onChange={(v) => void feature("nl_auto", v)} /></Row>
        </div>
        <div className="list-sub">{source}</div>
        <div className="list-title">{t("subs.bazarr")}</div>
        <Field label={t("subs.bazarrUrl")} htmlFor="subs-bazarr-url" hint={s.discovery?.bazarr?.length ? t("ms.found", { list: s.discovery.bazarr.map((b) => b.url).join(", ") }) : undefined}>
          <input id="subs-bazarr-url" className="input" value={bazarrUrl} placeholder="http://nas.lan:6767" disabled={!admin} onChange={(e) => setBazarrUrl(e.target.value)} />
        </Field>
        <Field label={t("subs.bazarrKey")} htmlFor="subs-bazarr-key" hint={sub.bazarr_key_set ? t("ms.keySet") : t("subs.bazarrHelp")}>
          <input id="subs-bazarr-key" className="input" type="password" autoComplete="off" value={bazarrKey} disabled={!admin} onChange={(e) => setBazarrKey(e.target.value)} />
        </Field>
        <div className="list-title">{t("subs.os")}</div>
        <div className="list">
          <Row title={t("subs.osEnabled")} hint={t("subs.osHint")}><Switch label={t("subs.osEnabled")} checked={sub.opensubtitles_enabled} disabled={!admin} onChange={(v) => void feature("opensubtitles_enabled", v)} /></Row>
        </div>
        {sub.opensubtitles_enabled ? (
          <>
            <Field label={t("subs.osKey")} htmlFor="subs-os-key" hint={sub.opensubtitles_key_set ? t("ms.keySet") : undefined}>
              <input id="subs-os-key" className="input" type="password" autoComplete="off" value={os.key} disabled={!admin} onChange={(e) => setOs({ ...os, key: e.target.value })} />
            </Field>
            <Field label={t("subs.osUser")} htmlFor="subs-os-user">
              <input id="subs-os-user" className="input" autoComplete="off" value={os.user} disabled={!admin} onChange={(e) => setOs({ ...os, user: e.target.value })} />
            </Field>
            <Field label={t("subs.osPass")} htmlFor="subs-os-pass">
              <input id="subs-os-pass" className="input" type="password" autoComplete="new-password" value={os.pass} disabled={!admin} onChange={(e) => setOs({ ...os, pass: e.target.value })} />
            </Field>
          </>
        ) : null}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {admin ? <Button variant="primary" pending={busy === "save"} onClick={() => void save()}>{t("subs.save")}</Button> : null}
          {can("operator") && sub.source ? <Button pending={busy === "runs"} onClick={() => void run("runs", () => api.post("/mediaserver/subtitles/run"))}>{t("subs.runNow")}</Button> : null}
          {admin && sub.opensubtitles_key_set ? <Button variant="ghost" pending={busy === "clearos"} onClick={() => void save({ clear_opensubtitles: true })}>{t("subs.removeOs")}</Button> : null}
        </div>
        {result ? <InlineState tone={result.ok ? "success" : "warning"} role="status">{result.message_nl}</InlineState> : null}
      </div>
    </Card>
  );
}
