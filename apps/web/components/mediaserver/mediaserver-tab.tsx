"use client";

import { Captions, Eye, Link2, MonitorPlay, Pencil, Plus, RefreshCw, Search, Trash2, Unlink } from "lucide-react";
import { useState, type ReactNode } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Modal, Segmented, StatusChip, Switch } from "@/components/mg/primitives";
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

// Peil C-41a (DienstKaart): handelingen onder elkaar over de volle breedte op een telefoon, naast elkaar vanaf sm.
const ACTIONS = "flex flex-col gap-2 sm:flex-row sm:flex-wrap";
const ACTION = "w-full sm:w-auto";
// Tussenkop binnen een kaart (h3 onder de h2 van de kaart).
const SUBHEAD = "text-base font-semibold leading-snug text-foreground";
// Zichtbaar label boven een keuzegroep: dezelfde vorm als het label van een Veld (Peil §20).
const GROUP_LABEL = "label mb-1.5 block text-base font-semibold text-foreground";
// De RolMelding (C-49) is ook de reden bij elke uitgeschakelde schakelaar of keuze (aria-describedby).
const ROLE_NOTE = "ms-role-note";

/** Instellingen → Koppelingen → Mediaservers & ondertitels. Alles in de app in te stellen. */
export function MediaServerTab() {
  const { t } = useI18n();
  const { can } = useSession();
  const status = useResource<MsStatus>("/mediaserver/status", undefined, 30_000);
  if (status.error && !status.data) return <Card><ErrorState error={status.error} onRetry={() => void status.reload()} endpoint="GET /api/v1/mediaserver/status" /></Card>;
  if (!status.data) return <Card><StateView kind="loading" /></Card>;
  return (
    <div className="stack ms-tab" data-testid="mediaserver-tab">
      {can("admin") ? null : (
        <div id={ROLE_NOTE} data-testid="ms-role-note">
          <InlineState tone="info">{can("operator") ? t("ms.adminOnly") : t("state.readOnly")}</InlineState>
        </div>
      )}
      <ServersCard s={status.data} reload={() => void status.reload()} />
      <SubtitlesCard s={status.data} reload={() => void status.reload()} />
    </div>
  );
}

/**
 * Koppeling (ingesteld) en gezondheid (gemeten) van één server: vorm + icoon + woord. Een laatst bekend Gezond dat te
 * oud is, is Verouderd (stippel + arcering, Peil §4.4) en nooit groen; zonder meting is het Onbekend. De reden staat
 * er altijd bij (ServerFacts).
 */
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
          : st.why === "stale" ? <StatusChip tone="stale">{t("state.staleShort")}</StatusChip>
            : <StatusChip tone="unknown">{t("status.unknown")}</StatusChip>}
    </>
  );
}

/**
 * Wat er over één server te zeggen valt, onder de naam en de pillen: eerst waarom de status zo is en wat je kunt doen,
 * dan de feiten als lijst van term en waarde (Peil C-41a: dl met vaste termkolom, onder elkaar als het smal is).
 */
export function ServerFacts({ s }: { s: MsServer }) {
  const { t, relative, num } = useI18n();
  const st = serverState(s);
  const r = s.results;
  const tested = r.ok + r.transcode + r.failed + r.not_found + r.open;
  const why = st.link === "detached" ? t("ms.why.detached")
    : st.link === "noAddress" ? t("ms.why.noAddress")
      : st.link === "noKey" ? t("ms.why.noKey", { label: s.label })
        : st.health === "healthy" ? t("ms.why.healthy", { when: relative(s.contact?.at ?? null) })
          : st.health === "down" ? t("ms.why.down", { error: s.contact?.error || t("ms.why.noAnswer") })
            : st.why === "stale" ? t("ms.why.stale", { when: relative(s.contact?.at ?? null) })
              : t("ms.why.notMeasured");
  const term = "font-semibold text-muted-foreground";
  const pair = "min-w-0 @md:contents";
  return (
    <>
      <div className="flex min-w-0 flex-col gap-1 text-sm text-foreground">
        <p data-testid={`ms-why-${s.server_id}`}>{why}</p>
        {st.health === "down" && s.contact?.last_ok_at ? <p className="text-muted-foreground">{t("ms.why.lastOk", { when: relative(s.contact.last_ok_at) })}</p> : null}
        {s.hold.active ? <p className="flex items-start gap-1.5"><Eye className="ico mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />{t("ms.srv.watching")}</p> : null}
      </div>
      {/* grid-cols-1 is nodig: de bestaande klasse .grid (mg.css) geeft twaalf kolommen, en dan breekt elke regel per letter */}
      <dl className="grid min-w-0 grid-cols-1 gap-x-3 gap-y-2 text-sm text-foreground @md:grid-cols-[minmax(0,11rem)_minmax(0,1fr)] @md:gap-y-1" data-testid={`ms-facts-${s.server_id}`}>
        {/* zonder adres zegt de reden hierboven al "Nog geen adres": geen lege regel in de lijst */}
        {s.url ? (
          <div className={pair}>
            <dt className={term}>{t("ms.url")}</dt>
            <dd className="min-w-0"><span className="font-mono break-all">{s.url}</span> <span className="text-muted-foreground">· {t(sourceKey(s.url_source))}</span></dd>
          </div>
        ) : null}
        {s.auth ? <div className={pair}><dt className={term}>{t("ms.fact.auth")}</dt><dd className="min-w-0 font-mono break-all">{s.auth}</dd></div> : null}
        {s.path_map.length || s.followed ? (
          <div className={pair}>
            <dt className={term}>{t("ms.paths")}</dt>
            <dd className="min-w-0">
              {s.path_map.length
                ? s.path_map.map(([a, b]) => <span className="block font-mono break-all" key={a}>{a} → {b}</span>)
                : t("ms.pathsNone")}
            </dd>
          </div>
        ) : null}
        {tested ? (
          <div className={pair}>
            <dt className={term}>{t("ms.fact.tests")}</dt>
            <dd className="min-w-0">{t("ms.srv.results", { ok: num(r.ok), transcode: num(r.transcode), failed: num(r.failed + r.not_found), open: num(r.open) })}</dd>
          </div>
        ) : null}
        {tested && r.nl_visible + r.nl_missing ? (
          <div className={pair}>
            <dt className={term}>{t("ms.fact.nl")}</dt>
            <dd className="min-w-0">{t("ms.srv.nl", { visible: num(r.nl_visible), missing: num(r.nl_missing) })}</dd>
          </div>
        ) : null}
      </dl>
    </>
  );
}

type Draft = { kind: ServerKind; url: string; key: string; label: string };

/**
 * Soort, naam, adres en sleutel van één server (bestaand of nieuw). De sleutel wordt nooit teruggelezen en het veld
 * start leeg; een veld heeft nooit een voorbeeldwaarde als plaatshouder (Peil §20): de uitleg staat eronder.
 */
function ServerForm({ id, legacy, draft, setDraft, keySet, urlHint, urlRequired, found }: {
  id: string; legacy: boolean; draft: Draft; setDraft: (d: Draft) => void; keySet: boolean; urlHint: ReactNode; urlRequired?: boolean; found?: Found[];
}) {
  const { t } = useI18n();
  const kinds: ServerKind[] = legacy ? ["auto", "emby", "jellyfin", "plex"] : ["emby", "jellyfin", "plex"];
  const helpKind = draft.kind === "jellyfin" ? "jellyfin" : draft.kind === "plex" ? "plex" : "emby";
  const kindId = `ms-kind-${id}`;
  return (
    <div className="stack">
      <div>
        <div className={GROUP_LABEL} id={kindId}>{t("ms.kind")}</div>
        <Segmented<ServerKind> label={t("ms.kind")} labelledBy={kindId} value={draft.kind} onChange={(kind) => setDraft({ ...draft, kind })}
          options={kinds.map((k) => ({ value: k, label: t((legacy || k !== "jellyfin" ? `ms.kind.${k}` : "ms.kind.jellyfinLeon") as MessageKey) }))} />
      </div>
      {found?.length ? (
        <div className="text-sm text-foreground">
          <p>{t("ms.add.found")}</p>
          <div className={`${ACTIONS} mt-1.5`}>
            {found.map((f) => (
              <Button key={`${f.kind}-${f.url}`} size="sm" className={ACTION}
                onClick={() => setDraft({ ...draft, kind: f.kind === "jellyfin" || f.kind === "plex" ? f.kind : "emby", url: f.url })}>
                {t("ms.add.use", { label: f.label, url: f.url })}
              </Button>
            ))}
          </div>
        </div>
      ) : null}
      {legacy ? null : (
        <Field label={t("ms.name")} htmlFor={`ms-name-${id}`} hint={t("ms.nameHint")}>
          <input id={`ms-name-${id}`} className="input" value={draft.label} maxLength={60} onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
        </Field>
      )}
      <Field htmlFor={`ms-url-${id}`} hint={urlHint}
        label={urlRequired ? <>{t("ms.url")} <span className="font-normal text-muted-foreground">{t("ms.required")}</span></> : t("ms.url")}>
        <input id={`ms-url-${id}`} className="input" inputMode="url" autoCapitalize="none" spellCheck={false} required={urlRequired}
          value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} />
      </Field>
      <Field label={t("ms.key")} htmlFor={`ms-key-${id}`} hint={keySet ? t("ms.keySet") : t("ms.keyHint")}>
        <input id={`ms-key-${id}`} className="input" type="password" autoComplete="off" value={draft.key} onChange={(e) => setDraft({ ...draft, key: e.target.value })} />
      </Field>
      <InlineState tone="info"><strong>{t("ms.keyHelpTitle")}</strong> {t(`ms.keyHelp.${helpKind}` as MessageKey)}</InlineState>
    </div>
  );
}

/**
 * Eén mediaserver in de lijst (Peil C-41a, DienstKaart): naam als kop, de pillen eronder, dan de reden en de feiten,
 * dan de handelingen. De knoppen zijn kort; de servernaam staat in hun toegankelijke naam ("Wijzigen: Jelly Leon").
 * Ontkoppelen is omkeerbaar en dus geen gevaar (C-65): gewone bevestiging. Verwijderen wist gegevens: gevaardialoog.
 */
function ServerItem({ s, status, result, reload, onResult }: {
  s: MsServer; status: MsStatus; result: TestResult | null | undefined; reload: () => void; onResult: (id: number, r: TestResult | null) => void;
}) {
  const { t } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const admin = can("admin");
  const st = serverState(s);
  const needsSetup = s.enabled && !s.key_set;
  const [editing, setEditing] = useState(false);
  // het formulier is er alleen voor wie het mag opslaan; een kijker of operator leest de reden in de RolMelding
  const open = admin && s.enabled && (editing || needsSetup);
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
  // Een verzoek dat mislukt (geweigerde invoer, geen rechten, core onbereikbaar) is geen meting: de fout staat als
  // melding (role=alert) bij de server of in de open dialoog, nooit als "mislukt" in de live-regio van het TestResultaat.
  const run = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name); setError(null);
    try { await fn(); reload(); return true; } catch (e) { setError(e instanceof Error ? e.message : String(e)); return false; } finally { setBusy(null); }
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
    // Ontkoppelen is een keuze, geen meting. De core test server 1 daarna toch ("koppeling staat uit"); die uitslag is
    // geen gemeten falen en hoort dus niet als "mislukt" in de live-regio. De toast zegt wat er gebeurde.
    onResult(s.server_id, name === "detach" ? null : r.test ?? null);
    setConfirm(null);
    toast.push("success", done);
  });
  const ask = (which: "detach" | "remove") => { setError(null); setConfirm(which); };
  const found = status.discovery?.servers ?? [];
  const urlHint = s.legacy
    ? (found.length ? t("ms.found", { list: found.map((x) => `${x.label} ${x.url}`).join(", ") }) : t("ms.urlHint"))
    : t("ms.add.urlHint");
  const kind = s.kind === "auto" ? t("ms.kind.autoShort") : t(`ms.kind.${s.kind}` as MessageKey);
  const nameId = `ms-server-name-${s.server_id}`;
  // zichtbaar kort, in de toegankelijke naam met de server erbij (de zichtbare tekst staat vooraan: WCAG 2.5.3)
  const named = (action: string) => t("ms.actionFor", { action, label: s.label });
  return (
    <article className="flex min-w-0 flex-col gap-3 border-t border-border py-4 first:border-t-0 first:pt-1" aria-labelledby={nameId} data-testid={`ms-server-${s.server_id}`}>
      <div className="flex min-w-0 flex-col gap-2">
        <h3 id={nameId} className={`${SUBHEAD} [overflow-wrap:anywhere]`}>
          {s.label}{kind !== s.label ? <span className="font-normal text-muted-foreground"> · {kind}</span> : null}
        </h3>
        <div className="flex flex-wrap gap-2"><ServerChips s={s} /></div>
      </div>
      <ServerFacts s={s} />
      {open ? <ServerForm id={String(s.server_id)} legacy={s.legacy} draft={draft} setDraft={setDraft} keySet={s.key_set} urlHint={urlHint} /> : null}
      <div>
        <div className={ACTIONS}>
          {open ? <Button variant="primary" className={ACTION} pending={busy === "save"} aria-label={named(t("ms.save"))} onClick={() => void save()}>{t("ms.save")}</Button> : null}
          {s.enabled && can("operator") ? (
            <Button icon={RefreshCw} className={ACTION} pending={busy === "test"} aria-label={named(t("ms.test"))}
              onClick={() => void run("test", async () => onResult(s.server_id, await api.post<TestResult>(s.legacy ? "/mediaserver/test" : `/mediaserver/test?server_id=${s.server_id}`)))}>
              {t("ms.test")}
            </Button>
          ) : null}
          {s.enabled && admin && !open ? <Button icon={Pencil} className={ACTION} aria-label={named(t("ms.edit"))} onClick={() => { setDraft(initial()); setEditing(true); }}>{t("ms.edit")}</Button> : null}
          {open && editing && !needsSetup ? <Button variant="ghost" className={ACTION} aria-label={named(t("action.cancel"))} onClick={() => setEditing(false)}>{t("action.cancel")}</Button> : null}
          {open && s.key_set ? <Button variant="ghost" className={ACTION} pending={busy === "clear"} aria-label={named(t("ms.removeKey"))} onClick={() => void post("clear", { clear_key: true }, t("ms.keyRemoved", { label: s.label }))}>{t("ms.removeKey")}</Button> : null}
          {admin && s.enabled && st.link !== "noAddress" ? <Button variant="ghost" className={ACTION} icon={Unlink} aria-label={named(t("ms.detach"))} onClick={() => ask("detach")}>{t("ms.detach")}</Button> : null}
          {admin && !s.enabled ? <Button variant="primary" className={ACTION} icon={Link2} pending={busy === "attach"} aria-label={named(t("ms.attach"))} onClick={() => void post("attach", { enabled: true }, t("ms.attached", { label: s.label }))}>{t("ms.attach")}</Button> : null}
          {admin && !s.legacy ? <Button variant="ghost" className={ACTION} icon={Trash2} aria-label={named(t("ms.remove"))} onClick={() => ask("remove")}>{t("ms.remove")}</Button> : null}
        </div>
        {/* TestResultaat (C-58): één zin in een vaste live-regio; geslaagd is neutraal, mislukt is gemeten falen */}
        <div role="status" className="[&:not(:empty)]:mt-3" data-testid={`ms-result-${s.server_id}`}>
          {result ? <InlineState tone={result.ok ? "success" : "danger"}>{result.message_nl}</InlineState> : null}
        </div>
        {/* met een open dialoog staat de fout daar, niet twee keer */}
        {error && !confirm ? <div className="mt-3"><InlineState tone="danger" role="alert">{error}</InlineState></div> : null}
      </div>
      <Modal open={confirm === "detach"} onClose={() => setConfirm(null)} role="alertdialog" icon={Unlink}
        title={t("ms.detach.title", { label: s.label })} description={t("ms.detach.body", { label: s.label })}
        footer={<>
          <Button onClick={() => setConfirm(null)} data-autofocus>{t("action.cancel")}</Button>
          <Button variant="primary" pending={busy === "detach"} onClick={() => void post("detach", { enabled: false }, t("ms.detached", { label: s.label }))}>{t("ms.detach.confirm")}</Button>
        </>}>
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      </Modal>
      <DangerDialog open={confirm === "remove"} onClose={() => setConfirm(null)} icon={Trash2} pending={busy === "remove"} error={error}
        title={t("ms.remove.title", { label: s.label })} description={t("ms.remove.body", { label: s.label })} confirmLabel={t("ms.remove.confirm")}
        onConfirm={() => void post("remove", { remove: true }, t("ms.removed", { label: s.label }))} />
    </article>
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
  // "Toevoegen en testen" kan pas met een adres: de reden staat zichtbaar onder de knop en hangt eraan (aria-describedby)
  const needUrl = !first && !draft.url.trim();
  if (!shown) {
    return (
      <div className="list-row list-row--wrap">
        <div className="list-main"><p className="text-sm text-muted-foreground">{full ? t("ms.add.full", { max: MAX_EXTRA_SERVERS }) : t("ms.add.hint")}</p></div>
        <span className="list-aside"><Button icon={Plus} disabled={full} onClick={() => setOpen(true)} data-testid="ms-add-open">{t("ms.add")}</Button></span>
      </div>
    );
  }
  return (
    <section className="flex min-w-0 flex-col gap-4 border-t border-border py-4 first:border-t-0 first:pt-1" aria-labelledby="ms-add-title" data-testid="ms-add-form">
      <div className="flex min-w-0 flex-col gap-1">
        <h3 id="ms-add-title" className={SUBHEAD}>{first ? t("ms.add.firstTitle") : t("ms.add.title")}</h3>
        {first ? null : <p className="text-sm text-muted-foreground">{t("ms.add.keepsFirst")}</p>}
      </div>
      <ServerForm id="new" legacy={first} draft={draft} setDraft={setDraft} keySet={false} urlHint={first ? t("ms.urlHint") : t("ms.add.urlHint")} urlRequired={!first} found={found} />
      <div className="flex min-w-0 flex-col gap-2">
        <div className={ACTIONS}>
          <Button variant="primary" className={ACTION} pending={busy} disabled={needUrl} aria-describedby={needUrl ? "ms-add-need-url" : undefined}
            onClick={() => void add()} data-testid="ms-add-save">{first ? t("ms.save") : t("ms.add.save")}</Button>
          {servers.length ? <Button variant="ghost" className={ACTION} onClick={() => { setOpen(false); setDraft(empty()); setError(null); }}>{t("action.cancel")}</Button> : null}
        </div>
        {needUrl ? <p id="ms-add-need-url" className="text-sm text-muted-foreground">{t("ms.add.needUrl")}</p> : null}
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      </div>
    </section>
  );
}

function ServersCard({ s, reload }: { s: MsStatus; reload: () => void }) {
  const { t, relative } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const admin = can("admin");
  const why = admin ? undefined : ROLE_NOTE;
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
      <div className="flex min-w-0 flex-col" data-testid="ms-servers">
        {servers.map((x) => <ServerItem key={x.server_id} s={x} status={s} result={results[x.server_id]} reload={reload} onResult={onResult} />)}
        {admin ? <AddServer status={s} servers={servers} reload={reload} onResult={onResult} /> : null}
        {!admin && !servers.length ? <p className="py-2 text-sm text-muted-foreground">{t("ms.noneViewer")}</p> : null}
      </div>
      <div className="stack mt-3">
        {can("operator") ? (
          <div className={ACTIONS}>
            <Button icon={Search} className={ACTION} pending={busy === "discover"} onClick={() => void run("discover", () => api.post("/mediaserver/discover"))}>{t("ms.search")}</Button>
          </div>
        ) : null}
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
        {!found.length && !servers.some((x) => x.url) ? <InlineState tone="neutral">{t("ms.notFound")}</InlineState> : null}
        {s.hold.active && s.hold.reason ? <InlineState tone="info">{t("ms.hold", { reason: s.hold.reason })}</InlineState> : null}
        {s.sessions?.watching?.length ? <p className="text-sm text-muted-foreground">{t("ms.watching", { list: s.sessions.watching.map((w) => `${w.user}: ${w.title}`).join(" · ") })}</p> : null}
      </div>
      <section className="mt-4 border-t border-border pt-4" aria-labelledby="ms-features-title">
        <h3 id="ms-features-title" className={SUBHEAD}>{many ? t("ms.featuresAll") : t("ms.features", { label })}</h3>
        <div className="list mt-1">
          <Row title={t("ms.f.refresh")} hint={many ? t("ms.f.refreshHintAll") : t("ms.f.refreshHint", { label })}><Switch label={t("ms.f.refresh")} checked={f.refresh_after_handoff} disabled={!admin} describedBy={why} onChange={(v) => void feature("refresh_after_handoff", v)} /></Row>
          <Row title={t("ms.f.test")} hint={many ? t("ms.f.testHintAll") : t("ms.f.testHint")}><Switch label={t("ms.f.test")} checked={f.playback_test} disabled={!admin} describedBy={why} onChange={(v) => void feature("playback_test", v)} /></Row>
          {f.playback_test ? (
            <Row title={t("ms.f.fail")}>
              <Segmented label={t("ms.f.fail")} describedBy={why} value={f.playback_fail_action} onChange={(v) => void feature("playback_fail_action", v)}
                options={(["notify", "rollback"] as const).map((v) => ({ value: v, label: t(`ms.f.fail.${v}` as MessageKey), disabled: !admin }))} />
            </Row>
          ) : null}
          <Row title={t("ms.f.pause")} hint={many ? t("ms.f.pauseHintAll") : t("ms.f.pauseHint")}>
            <Segmented label={t("ms.f.pause")} describedBy={why} value={f.pause_while_watching} onChange={(v) => void feature("pause_while_watching", v)}
              options={(["both", "gpu", "off"] as const).map((v) => ({ value: v, label: t(`ms.f.pause.${v}` as MessageKey), disabled: !admin }))} />
          </Row>
          <Row title={t("ms.f.recent")} hint={t("ms.f.recentHint", { hours: f.recent_watch_hours })}><Switch label={t("ms.f.recent")} checked={f.skip_recent_watch} disabled={!admin} describedBy={why} onChange={(v) => void feature("skip_recent_watch", v)} /></Row>
        </div>
      </section>
      {s.tests.length ? (
        <section className="mt-4 border-t border-border pt-4" aria-labelledby="ms-tests-title">
          <h3 id="ms-tests-title" className={SUBHEAD}>{t("ms.tests")}</h3>
          <ul className="mt-2 flex flex-col gap-2 text-sm text-foreground">
            {s.tests.slice(0, 5).map((x) => (
              <li key={`${x.path}-${x.done_at}`} className="min-w-0 [overflow-wrap:anywhere]">
                <StatusChip tone={x.result === "ok" ? "success" : x.result === "failed" ? "danger" : "neutral"}>{isResultKey(x.result) ? t(`ms.result.${x.result}` as MessageKey) : x.result}</StatusChip>{" "}
                {x.path.split("/").pop()} <span className="text-muted-foreground">· {relative(x.done_at)}</span>
                {(x.servers?.length ?? 0) > 1 ? x.servers?.map((r) => (
                  <span key={r.server_id} className="block text-muted-foreground">
                    {r.label}: {isResultKey(r.result) ? t(`ms.result.${r.result}` as MessageKey) : r.result}
                    {r.nl_subtitles === "visible" ? ` · ${t("ms.nl.visible")}` : r.nl_subtitles === "missing" ? ` · ${t("ms.nl.missing")}` : ""}
                  </span>
                )) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {s.actions.length ? (
        <section className="mt-4 border-t border-border pt-4" aria-labelledby="ms-actions-title">
          <h3 id="ms-actions-title" className={SUBHEAD}>{t("ms.recent")}</h3>
          <ul className="mt-2 flex flex-col gap-1 text-sm text-foreground">
            {s.actions.slice(0, 6).map((a, i) => <li key={i} className="min-w-0 [overflow-wrap:anywhere]"><span className="text-muted-foreground">{relative(a.ts)} ·</span> {a.detail_nl}</li>)}
          </ul>
        </section>
      ) : null}
    </Card>
  );
}

function SubtitlesCard({ s, reload }: { s: MsStatus; reload: () => void }) {
  const { t, num } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const admin = can("admin");
  const why = admin ? undefined : ROLE_NOTE;
  // een veld dat uit staat voor wie geen beheerder is, noemt naast zijn eigen hulptekst ook de reden (RolMelding)
  const off = (hintId?: string) => (why ? [hintId, why].filter(Boolean).join(" ") : undefined);
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
  const bazarrHint = s.discovery?.bazarr?.length ? t("ms.found", { list: s.discovery.bazarr.map((b) => b.url).join(", ") })
    : admin ? t("subs.bazarrUrlHint") : undefined;
  return (
    <Card title={t("subs.title")} icon={Captions} sub={t("subs.sub")}
      // een telling van wat ontbreekt is een feit, geen gezondheid: neutraal label met sterke rand, nooit amber of groen
      actions={<StatusChip tone={sub.missing ? "strong" : "success"}>{sub.missing ? t("subs.missing", { n: num(sub.missing) }) : t("subs.missingNone")}</StatusChip>}>
      <div className="stack" data-testid="subtitles-card">
        {!sub.nl_wanted ? <InlineState tone="neutral">{t("subs.notWanted")}</InlineState> : null}
        <div className="list">
          <Row title={t("subs.auto")} hint={t("subs.autoHint")}><Switch label={t("subs.auto")} checked={sub.nl_auto} disabled={!admin} describedBy={why} onChange={(v) => void feature("nl_auto", v)} /></Row>
        </div>
        <p className="text-sm text-muted-foreground">{source}</p>
        <h3 className={SUBHEAD}>{t("subs.bazarr")}</h3>
        <Field label={t("subs.bazarrUrl")} htmlFor="subs-bazarr-url" hint={bazarrHint}>
          <input id="subs-bazarr-url" className="input" inputMode="url" autoCapitalize="none" spellCheck={false} value={bazarrUrl} disabled={!admin}
            aria-describedby={off(bazarrHint ? "subs-bazarr-url-hint" : undefined)} onChange={(e) => setBazarrUrl(e.target.value)} />
        </Field>
        <Field label={t("subs.bazarrKey")} htmlFor="subs-bazarr-key" hint={sub.bazarr_key_set ? t("ms.keySet") : t("subs.bazarrHelp")}>
          <input id="subs-bazarr-key" className="input" type="password" autoComplete="off" value={bazarrKey} disabled={!admin}
            aria-describedby={off("subs-bazarr-key-hint")} onChange={(e) => setBazarrKey(e.target.value)} />
        </Field>
        <h3 className={SUBHEAD}>{t("subs.os")}</h3>
        <div className="list">
          <Row title={t("subs.osEnabled")} hint={t("subs.osHint")}><Switch label={t("subs.osEnabled")} checked={sub.opensubtitles_enabled} disabled={!admin} describedBy={why} onChange={(v) => void feature("opensubtitles_enabled", v)} /></Row>
        </div>
        {sub.opensubtitles_enabled ? (
          <>
            <Field label={t("subs.osKey")} htmlFor="subs-os-key" hint={sub.opensubtitles_key_set ? t("ms.keySet") : undefined}>
              <input id="subs-os-key" className="input" type="password" autoComplete="off" value={os.key} disabled={!admin}
                aria-describedby={off(sub.opensubtitles_key_set ? "subs-os-key-hint" : undefined)} onChange={(e) => setOs({ ...os, key: e.target.value })} />
            </Field>
            <Field label={t("subs.osUser")} htmlFor="subs-os-user">
              <input id="subs-os-user" className="input" autoComplete="off" value={os.user} disabled={!admin} aria-describedby={off()} onChange={(e) => setOs({ ...os, user: e.target.value })} />
            </Field>
            <Field label={t("subs.osPass")} htmlFor="subs-os-pass">
              <input id="subs-os-pass" className="input" type="password" autoComplete="new-password" value={os.pass} disabled={!admin} aria-describedby={off()} onChange={(e) => setOs({ ...os, pass: e.target.value })} />
            </Field>
          </>
        ) : null}
        <div>
          <div className={ACTIONS}>
            {admin ? <Button variant="primary" className={ACTION} pending={busy === "save"} onClick={() => void save()}>{t("subs.save")}</Button> : null}
            {can("operator") && sub.source ? <Button className={ACTION} pending={busy === "runs"} onClick={() => void run("runs", () => api.post("/mediaserver/subtitles/run"))}>{t("subs.runNow")}</Button> : null}
            {admin && sub.opensubtitles_key_set ? <Button variant="ghost" className={ACTION} pending={busy === "clearos"} onClick={() => void save({ clear_opensubtitles: true })}>{t("subs.removeOs")}</Button> : null}
          </div>
          {/* TestResultaat (C-58): vaste live-regio; geslaagd is neutraal, mislukt is gemeten falen */}
          <div role="status" className="[&:not(:empty)]:mt-3" data-testid="subs-result">
            {result ? <InlineState tone={result.ok ? "success" : "danger"}>{result.message_nl}</InlineState> : null}
          </div>
        </div>
      </div>
    </Card>
  );
}
