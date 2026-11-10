"use client";

import { Captions, MonitorPlay, RefreshCw, Search } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Button, Card, Field, InlineState, Segmented, StatusChip, Switch } from "@/components/mg/primitives";
import { ErrorState, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type Kind = "auto" | "emby" | "jellyfin" | "plex" | "off";
interface Features {
  refresh_after_handoff: boolean; playback_test: boolean; playback_fail_action: "notify" | "rollback";
  pause_while_watching: "both" | "gpu" | "off"; skip_recent_watch: boolean; recent_watch_hours: number;
}
interface Test { path: string; result: string; detail_nl: string; done_at: string | null }
interface Action { ts: string; kind: string; ok: number; detail_nl: string }
interface MsStatus {
  kind: Kind; label: string; url: string; url_source: string; key_set: boolean;
  setting_kind: Kind; setting_url: string;
  connected: { at: string; name?: string; version?: string } | null;
  discovery: { servers: Array<{ kind: string; url: string; name?: string; label: string }>; bazarr: Array<{ url: string }> } | null;
  path_map: Array<[string, string]>;
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

function Row({ title, hint, children }: { title: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="list-row list-row--wrap">
      <div className="list-main"><div className="list-title">{title}</div>{hint ? <div className="list-sub">{hint}</div> : null}</div>
      <span className="list-aside">{children}</span>
    </div>
  );
}

/** Instellingen → Koppelingen → Mediaserver & ondertitels. Alles in de app in te stellen. */
export function MediaServerTab() {
  const status = useResource<MsStatus>("/mediaserver/status", undefined, 30_000);
  if (status.error && !status.data) return <Card><ErrorState error={status.error} onRetry={() => void status.reload()} endpoint="GET /api/v1/mediaserver/status" /></Card>;
  if (!status.data) return <Card><StateView kind="loading" /></Card>;
  return (
    <div className="stack" data-testid="mediaserver-tab">
      <ServerCard s={status.data} reload={() => void status.reload()} />
      <SubtitlesCard s={status.data} reload={() => void status.reload()} />
    </div>
  );
}

function ServerCard({ s, reload }: { s: MsStatus; reload: () => void }) {
  const { t, relative } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const admin = can("admin");
  const [kind, setKind] = useState<Kind>(s.setting_kind);
  const [url, setUrl] = useState(s.setting_url || "");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<TestResult | null>(null);
  useEffect(() => { setKind(s.setting_kind); setUrl(s.setting_url || ""); }, [s.setting_kind, s.setting_url]);
  const label = s.label && s.label !== "Uit" ? s.label : "Emby";
  const run = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    try { await fn(); reload(); } catch (e) { setResult({ ok: false, message_nl: e instanceof Error ? e.message : String(e) }); } finally { setBusy(null); }
  };
  const save = (extra: Record<string, unknown> = {}) => run("save", async () => {
    const r = await api.post<{ test: TestResult | null }>("/mediaserver/config", { kind, url, ...(key ? { api_key: key } : {}), ...extra });
    setKey("");
    if (r.test) setResult(r.test); else toast.push("success", t("ms.saved"));
  });
  const feature = (name: keyof Features, value: unknown) => run(`f-${name}`, async () => {
    await api.post("/mediaserver/config", { features: { [name]: value } });
    toast.push("success", t("ms.saved"));
  });
  const f = s.features;
  const state = s.kind === "off" ? <StatusChip tone="neutral">{t("ms.status.off")}</StatusChip>
    : s.connected && s.key_set ? <StatusChip tone="success">{t("ms.status.connected", { label })}</StatusChip>
      : s.url ? <StatusChip tone="warning">{t("ms.status.noKey", { label })}</StatusChip>
        : <StatusChip tone="neutral">{t("ms.status.none")}</StatusChip>;
  const found = s.discovery?.servers ?? [];
  const helpKey = s.kind === "jellyfin" ? "jellyfin" : s.kind === "plex" ? "plex" : "emby";
  return (
    <Card title={t("ms.title")} icon={MonitorPlay} sub={t("ms.sub")} actions={state}>
      <div className="stack">
        <Field label={t("ms.kind")} htmlFor="ms-kind">
          <Segmented<Kind> label={t("ms.kind")} value={kind} onChange={setKind}
            options={(["auto", "emby", "jellyfin", "plex", "off"] as const).map((k) => ({ value: k, label: t(`ms.kind.${k}` as MessageKey), disabled: !admin }))} />
        </Field>
        <Field label={t("ms.url")} htmlFor="ms-url" hint={found.length ? t("ms.found", { list: found.map((x) => `${x.label} ${x.url}`).join(", ") }) : s.url ? `${s.url} · ${s.url_source}` : t("ms.urlHint")}>
          <input id="ms-url" className="input" value={url} placeholder={s.url || "http://nas.lan:8096"} disabled={!admin} onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <Field label={t("ms.key")} htmlFor="ms-key" hint={s.key_set ? t("ms.keySet") : undefined}>
          <input id="ms-key" className="input" type="password" autoComplete="off" value={key} placeholder={t("ms.keyPlaceholder")} disabled={!admin} onChange={(e) => setKey(e.target.value)} />
        </Field>
        <InlineState tone="info"><strong>{t("ms.keyHelpTitle")}</strong> {s.key_help_nl[helpKey]}</InlineState>
        <div className="row-actions" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {admin ? <Button variant="primary" pending={busy === "save"} onClick={() => void save()}>{t("ms.save")}</Button> : null}
          {can("operator") ? <Button icon={RefreshCw} pending={busy === "test"} onClick={() => void run("test", async () => setResult(await api.post<TestResult>("/mediaserver/test")))}>{t("ms.test")}</Button> : null}
          {can("operator") ? <Button icon={Search} pending={busy === "discover"} onClick={() => void run("discover", () => api.post("/mediaserver/discover"))}>{t("ms.search")}</Button> : null}
          {admin && s.key_set ? <Button variant="ghost" pending={busy === "clear"} onClick={() => void run("clear", () => api.post("/mediaserver/config", { clear_key: true }))}>{t("ms.removeKey")}</Button> : null}
        </div>
        {result ? <InlineState tone={result.ok ? "success" : "warning"} role="status">{result.message_nl}</InlineState> : null}
        {!found.length && !s.url ? <InlineState tone="neutral">{t("ms.notFound")}</InlineState> : null}
        {s.hold.active && s.hold.reason ? <InlineState tone="info">{t("ms.hold", { reason: s.hold.reason })}</InlineState> : null}
        {s.sessions?.watching?.length ? <div className="list-sub">{t("ms.watching", { list: s.sessions.watching.map((w) => `${w.user}: ${w.title}`).join(" · ") })}</div> : null}
      </div>
      <div className="list" style={{ marginTop: 12 }}>
        <div className="list-row"><div className="list-main"><div className="list-title">{t("ms.features", { label })}</div></div></div>
        <Row title={t("ms.f.refresh")} hint={t("ms.f.refreshHint", { label })}><Switch label={t("ms.f.refresh")} checked={f.refresh_after_handoff} disabled={!admin} onChange={(v) => void feature("refresh_after_handoff", v)} /></Row>
        <Row title={t("ms.f.test")} hint={t("ms.f.testHint")}><Switch label={t("ms.f.test")} checked={f.playback_test} disabled={!admin} onChange={(v) => void feature("playback_test", v)} /></Row>
        {f.playback_test ? (
          <Row title={t("ms.f.fail")}>
            <Segmented label={t("ms.f.fail")} value={f.playback_fail_action} onChange={(v) => void feature("playback_fail_action", v)}
              options={(["notify", "rollback"] as const).map((v) => ({ value: v, label: t(`ms.f.fail.${v}` as MessageKey), disabled: !admin }))} />
          </Row>
        ) : null}
        <Row title={t("ms.f.pause")} hint={t("ms.f.pauseHint")}>
          <Segmented label={t("ms.f.pause")} value={f.pause_while_watching} onChange={(v) => void feature("pause_while_watching", v)}
            options={(["both", "gpu", "off"] as const).map((v) => ({ value: v, label: t(`ms.f.pause.${v}` as MessageKey), disabled: !admin }))} />
        </Row>
        <Row title={t("ms.f.recent")} hint={t("ms.f.recentHint", { hours: f.recent_watch_hours })}><Switch label={t("ms.f.recent")} checked={f.skip_recent_watch} disabled={!admin} onChange={(v) => void feature("skip_recent_watch", v)} /></Row>
        <div className="list-row list-row--wrap"><div className="list-main">
          <div className="list-title">{t("ms.paths")}</div>
          {s.path_map.length ? s.path_map.map(([a, b]) => <div className="list-sub mono" key={a} style={{ wordBreak: "break-all" }}>{a} → {b}</div>) : <div className="list-sub">{t("ms.pathsNone")}</div>}
        </div></div>
        {s.tests.length ? (
          <div className="list-row list-row--wrap"><div className="list-main">
            <div className="list-title">{t("ms.tests")}</div>
            {s.tests.slice(0, 5).map((x) => (
              <div className="list-sub" key={`${x.path}-${x.done_at}`} style={{ wordBreak: "break-word" }}>
                <StatusChip tone={x.result === "ok" ? "success" : x.result === "failed" ? "danger" : "neutral"}>{t(`ms.result.${x.result}` as MessageKey)}</StatusChip>{" "}
                {x.path.split("/").pop()} · {relative(x.done_at)}
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
