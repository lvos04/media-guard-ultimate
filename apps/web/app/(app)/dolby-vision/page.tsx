"use client";

import { Aperture, CheckCheck, FlaskConical, ListTree, Lock, Play, Server, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Trace } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Modal, PageHeader, Segmented, StatusChip, Switch } from "@/components/mg/primitives";
import { SettingsLink } from "@/components/mg/settings-link";
import { SettingsSection } from "@/components/mg/settings-section";
import { TitlePicker } from "@/components/mg/title-picker";
import { SettingsTabs } from "@/components/shell/settings-tabs";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { DvCapabilities, DvPolicyResponse, PlaybackConfirmation } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

// één hoofdschakelaar (Instellingen) plus uitzonderingen per titel; de andere niveaus blijven in core
// bestaan maar zijn hier niet meer te kiezen (rapport §7: 7 niveaus was te veel)
const SCOPES = ["global", "series", "movie"] as const;
const MODES: Array<{ value: string; label: MessageKey; help: MessageKey }> = [
  { value: "keep_original", label: "dv.mode.keep", help: "dv.mode.keepHelp" },
  { value: "trial_copy", label: "dv.mode.trial", help: "dv.mode.trialHelp" },
  { value: "auto_after_verify", label: "dv.mode.auto", help: "dv.mode.autoHelp" },
  { value: "experimental_keep_source", label: "dv.mode.experimental", help: "dv.mode.experimentalHelp" },
];
const PROFILES = ["8.1", "8.4", "7_mel", "7_fel", "5"];

export default function DolbyVisionPage() {
  const { t } = useI18n();
  const { can } = useSession();
  const [scope, setScope] = useState<(typeof SCOPES)[number]>("global");
  const [scopeId, setScopeId] = useState("");
  const [scopeLabel, setScopeLabel] = useState("");
  const policy = useResource<DvPolicyResponse>(scope === "global" || scopeId ? "/dolby-vision/policies" : null, scope !== "global" && scopeId ? { [scope]: scopeId } : undefined);
  const caps = useResource<DvCapabilities>("/dolby-vision/capabilities");
  const confirmations = useResource<PlaybackConfirmation[]>("/dolby-vision/playback-confirmations");
  useCoreEvents("dv", () => { void policy.reload(); void confirmations.reload(); });
  const { run, pending } = useAction();
  const [draft, setDraft] = useState<{ allow: boolean; mode: string; allowed_profiles: string[] } | null>(null);
  const [testOpen, setTestOpen] = useState(false);
  const [testPath, setTestPath] = useState("");
  const [testResult, setTestResult] = useState<unknown>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [conf, setConf] = useState({ client: "", server: "", dv_profile: "8.1", note: "" });
  const eff = policy.data?.effective;
  useEffect(() => { if (eff) setDraft({ allow: eff.allow, mode: eff.mode, allowed_profiles: eff.allowed_profiles }); }, [eff]);
  const dirty = Boolean(eff && draft && (draft.allow !== eff.allow || draft.mode !== eff.mode || draft.allowed_profiles.join() !== eff.allowed_profiles.join()));

  const save = async () => {
    if (!draft) return;
    await run("save", "patch", "/dolby-vision/policies", { scope_type: scope, scope_id: scope === "global" ? "" : scopeId, ...draft }, t("toast.saved"));
    await policy.reload();
  };
  const levels = eff?.trace.allow?.levels ?? [];

  return (
    <>
      <SettingsTabs />
      <PageHeader title={t("nav.dv")} sub={t("dv.exceptionsHint")} actions={caps.data ? (
        <StatusChip tone={caps.data.available ? "success" : "unknown"} icon={caps.data.available ? Server : undefined} size="lg">{caps.data.available ? t("dv.toolchainOk") : t("dv.toolchainOffline")}</StatusChip>
      ) : null} />
      <div className="grid">
        <Card className="span-7" title={t("dv.allow")} icon={Aperture} actions={
          <Segmented label={t("settings.scope")} value={scope} onChange={(v) => { setScope(v); setScopeId(""); setScopeLabel(""); }} options={SCOPES.map((s) => ({ value: s, label: t(`scope.${s}`) }))} />}>
          {scope !== "global" ? (
            <div style={{ marginBottom: 12 }}>
              <TitlePicker key={scope} mediaKind={scope === "series" ? "series" : "movie"} onPick={(p) => { setScopeId(`${p.instance}:${p.item.provider_id}`); setScopeLabel(p.label); }} />
              {scopeId ? <p className="hint">{t("dv.exceptionFor", { title: scopeLabel })}</p> : null}
            </div>
          ) : null}
          {scope !== "global" && !scopeId ? <StateView kind="empty" compact title={t("dv.pickTitle")} /> : (
            <ResourceView resource={policy} endpoint="GET /api/v1/dolby-vision/policies" compact>
              {(p) => draft ? (
                <div className="stack">
                  <div className="row-between" style={{ flexWrap: "wrap", gap: 8 }}>
                    <div style={{ minWidth: 0, flex: "1 1 200px" }}><div className="setting-name">{t("dv.allowFor", { scope: t(`scope.${scope}`) })}</div><div className="setting-help">{t("dv.allowHelp")}</div></div>
                    {scope === "global"
                      ? <span className="row wrap" style={{ gap: 8, minWidth: 0, maxWidth: "100%" }}><StatusChip tone={draft.allow ? "success" : "neutral"}>{draft.allow ? t("settings.yes") : t("settings.no")}</StatusChip><Link className="link-more" href="/instellingen?q=dv.allow_av1">{t("dv.changeInSettings")}</Link></span>
                      : <Switch checked={draft.allow} onChange={(v) => setDraft({ ...draft, allow: v })} label={t("dv.allow")} disabled={!can("operator")} />}
                  </div>
                  {levels.length ? (
                    <div><div className="eyebrow" style={{ marginBottom: 6 }}>{t("settings.trace")}</div>
                      <Trace steps={levels.map((l) => ({ label: `${l.label_nl ?? t(`scope.${l.scope}` as MessageKey)}${l.set ? ` · ${l.value ? t("settings.yes") : t("settings.no")}` : ""}`, active: l.scope === p.effective.trace.allow.source, unset: !l.set }))} />
                      <p className="hint">{t("dv.effective", { value: p.effective.allow ? t("settings.yes") : t("settings.no"), source: p.effective.trace.allow.source_label_nl ?? p.effective.trace.allow.source })}</p></div>
                  ) : null}
                  <fieldset style={{ border: 0, padding: 0, margin: 0 }} disabled={!can("operator")}>
                    <legend className="label">{t("dv.mode")}</legend>
                    <div className="grid" style={{ gap: 8 }}>
                      {MODES.map((m) => {
                        const checked = draft.mode === m.value;
                        return (
                          <label key={m.value} className={cn("card md-span-6 span-6")} style={{ padding: 12, display: "flex", gap: 10, cursor: "pointer", background: checked ? "var(--mg-accent-tint)" : "var(--mg-sunken)", borderColor: checked ? "var(--mg-accent)" : undefined }}>
                            <input type="radio" name="dv-mode" className="check" checked={checked} onChange={() => setDraft({ ...draft, mode: m.value })} />
                            <span><strong style={{ fontSize: 14 }}>{t(m.label)}</strong><br /><span className="t-2" style={{ fontSize: 14 }}>{t(m.help)}</span></span>
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>
                  <div>
                    <span className="label">{t("dv.allowedProfiles")}</span>
                    <div className="row wrap">{PROFILES.map((pr) => (
                      <button key={pr} type="button" className="filter-chip" disabled={!can("operator")} aria-pressed={draft.allowed_profiles.includes(pr)} onClick={() => setDraft({ ...draft, allowed_profiles: draft.allowed_profiles.includes(pr) ? draft.allowed_profiles.filter((x) => x !== pr) : [...draft.allowed_profiles, pr] })}>DV {pr.replace("_", " ").toUpperCase()}</button>
                    ))}</div>
                    <p className="hint">{t("dv.profilesHint")}</p>
                  </div>
                  <InlineState icon={Lock}><strong>{t("dv.lockedTitle")}</strong> {t("dv.lockedBody")}</InlineState>
                  <div className="row-between wrap">
                    <span className="t-3" style={{ fontSize: 14 }}>{dirty ? t("settings.dirty") : ""}</span>
                    <span className="row"><Button size="sm" variant="ghost" disabled={!dirty} onClick={() => eff && setDraft({ allow: eff.allow, mode: eff.mode, allowed_profiles: eff.allowed_profiles })}>{t("action.discard")}</Button><Button variant="primary" size="sm" pending={pending === "save"} disabled={!can("operator") || !dirty} onClick={() => void save()}>{t("action.save")}</Button></span>
                  </div>
                </div>
              ) : <StateView kind="loading" compact />}
            </ResourceView>
          )}
        </Card>

        <Card className="span-5" title={t("dv.profiles")} icon={ListTree}>
          <ResourceView resource={caps} endpoint="GET /api/v1/dolby-vision/capabilities" compact>
            {(c) => (
              <div className="stack-sm">
                {!c.available ? <InlineState tone="unknown">{t("dv.encoderUnavailable", { error: c.error ?? "—" })}</InlineState> : null}
                {Object.keys(c.routes).length ? (
                  <dl className="kv">{Object.entries(c.routes).map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt className="mono">{k}</dt><dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>)}</dl>
                ) : null}
                <span className="eyebrow">{t("dv.statusLegend")}</span>
                {/* legenda van mogelijke toestanden: neutrale labels (Peil: kleur volgt nooit de volgorde en een legenda is geen status) */}
                <div className="row wrap" style={{ gap: 4 }}>{c.statuses.map((s) => <StatusChip key={s} tone="neutral" outline icon={null}>{s}</StatusChip>)}</div>
              </div>
            )}
          </ResourceView>
        </Card>

        <Card className="span-6" title={t("dv.tests")} icon={FlaskConical} actions={<Button size="sm" variant="primary" icon={Play} disabled={!can("operator")} onClick={() => setTestOpen(true)}>{t("dv.testFile")}</Button>}>
          <p className="t-2" style={{ fontSize: 14 }}>{t("dv.testBody")}</p>
          {testResult ? <details className="raw" open style={{ marginTop: 12 }}><summary>{t("migration.stepResult")}</summary><pre>{JSON.stringify(testResult, null, 2)}</pre></details> : null}
        </Card>
        <Card className="span-6" title={t("dv.playbackChains")} icon={CheckCheck} flush actions={<Button size="sm" disabled={!can("operator")} onClick={() => setConfirmOpen(true)}>{t("dv.confirmClient")}</Button>}>
          <ResourceView resource={confirmations} endpoint="GET /api/v1/dolby-vision/playback-confirmations" compact isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" compact title={t("dv.noConfirmations")} body={t("dv.confirmationsHint")} />}>
            {(rows) => (
              <div className="list">{rows.map((c) => (
                <div className="list-row" key={c.id}>
                  <div className="list-main"><div className="list-title">{c.client} · {c.server}</div><div className="list-sub">DV {c.dv_profile} · {c.codec}/{c.container}{c.note ? ` · ${c.note}` : ""}</div></div>
                  <Button size="sm" variant="ghost" iconOnly icon={Trash2} disabled={!can("operator")} pending={pending === `del-${c.id}`} onClick={() => void run(`del-${c.id}`, "del", `/dolby-vision/playback-confirmations/${c.id}`).then(() => confirmations.reload())}>{t("action.delete")}</Button>
                </div>
              ))}</div>
            )}
          </ResourceView>
        </Card>

        {/* rapport 7, §4.1/§4.3: routes (HDR10+ en Dolby Vision 8.1 via GPU als HDR10, of behouden op de CPU) */}
        <div className="span-12 stack-sm" id="routes" data-testid="dv-routes">
          <InlineState tone="info">{t("dv37.routesIntro")}</InlineState>
          <SettingsSection title={t("dv37.routesTitle")} prefixes={["dv."]}
            exclude={["dv.allow_av1", "dv.mode", "dv.allowed_profiles", "dv.show_playback_warnings"]} />
        </div>

        <div className="span-12"><SettingsLink sections={["av1"]} /></div>
      </div>

      <Modal open={testOpen} onClose={() => setTestOpen(false)} title={t("dv.testFile")} icon={FlaskConical} description={t("dv.testBody")}
        footer={<><Button onClick={() => setTestOpen(false)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending === "test"} disabled={!testPath.trim()} onClick={async () => { const r = await run("test", "post", "/dolby-vision/test-encode", { path: testPath.trim() }, t("dv.testQueued")); if (r !== undefined) { setTestResult(r); setTestOpen(false); } }}>{t("dv.startTest")}</Button></>}>
        <Field label={t("dv.testPath")} htmlFor="dv-path" hint={t("dv.testHint")}><input id="dv-path" className="input mono" value={testPath} onChange={(e) => setTestPath(e.target.value)} /></Field>
      </Modal>
      <Modal open={confirmOpen} onClose={() => setConfirmOpen(false)} title={t("dv.confirmClient")} icon={CheckCheck} description={t("dv.confirmBody")}
        footer={<><Button onClick={() => setConfirmOpen(false)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending === "conf"} disabled={!conf.client || !conf.server} onClick={async () => { const r = await run("conf", "post", "/dolby-vision/playback-confirmations", conf, t("dv.playbackSaved")); if (r !== undefined) { setConfirmOpen(false); void confirmations.reload(); } }}>{t("action.save")}</Button></>}>
        <div className="stack">
          <Field label={t("dv.client")} htmlFor="dv-client"><input id="dv-client" className="input" value={conf.client} placeholder="Nvidia Shield Pro" onChange={(e) => setConf({ ...conf, client: e.target.value })} /></Field>
          <Field label={t("dv.server")} htmlFor="dv-server"><input id="dv-server" className="input" value={conf.server} placeholder="Jellyfin 10.10" onChange={(e) => setConf({ ...conf, server: e.target.value })} /></Field>
          <Field label="DV-profiel" htmlFor="dv-prof"><select id="dv-prof" className="select" value={conf.dv_profile} onChange={(e) => setConf({ ...conf, dv_profile: e.target.value })}>{["8.1", "8.4", "7", "5", "10.1"].map((p) => <option key={p} value={p}>{p}</option>)}</select></Field>
          <Field label={t("dv.note")} htmlFor="dv-note"><input id="dv-note" className="input" value={conf.note} onChange={(e) => setConf({ ...conf, note: e.target.value })} /></Field>
        </div>
      </Modal>
    </>
  );
}
