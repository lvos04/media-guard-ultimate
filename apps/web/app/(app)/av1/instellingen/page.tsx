"use client";

import Link from "next/link";
import { Archive, Gauge, HardDriveDownload, Layers, LifeBuoy, Puzzle, RefreshCw, Save, Trash2, Users } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { GpuBenchmarkSection } from "@/components/av1/gpu-benchmark";
import { TuningSection } from "@/components/av1/tuning";
import { OrphansSummaryLine } from "@/components/mg/quarantine-orphans";
import { errorText, useEncoder } from "@/components/av1/use-encoder";
import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, StatusChip, Switch } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { mutation } from "@/lib/api";
import { useResource } from "@/lib/api/hooks";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import type { Lane, Profile, WorkerConfig } from "@/types";

interface EngineSettings {
  log_niveau: "debug" | "info" | "warning" | "error";
  cpu_max_percent: number;
  cpu_max_temperatuur: number;
  max_gelijktijdige_encodes: number;
  cache_waarschuwing_gb: number;
  cache_pauze_gb: number;
  hardlink_modus: "overslaan" | "verbreek_na_bevestiging";
  meld_browser_fouten: boolean;
  meld_browser_batches: boolean;
}
interface Feature { sleutel: string; ingeschakeld: boolean; uitleg: string; configuratie: Record<string, unknown>; laatste_fout: string | null }
interface QueueRecovery { actieve_jobs: number[]; weesjobs: number[]; tijdelijke_jobs: number[]; kan_herstellen: boolean; uitleg: string; onveilige_paden: string[] }
interface Recovery { id: number; job_id: number; status: string; bron_pad: string; toelichting: string | null }
interface Backup { id: number; grootte: number; sha256: string; gemaakt_op: string; gecontroleerd_op: string | null }

/** Ubuntu-only or MediaGuard-managed features that make no sense in the TrueNAS container
 * (MediaGuard decides and submits the jobs, so the engine's own library scans, proof batches
 * and integrations are not offered here). */
// beleid (wat/hoe er geëncodeerd wordt) stelt MediaGuard in; de engine weigert die schakelaars toch
const HIDDEN_FEATURES = new Set(["externe_toegang", "ai_controller", "jellyfin_pauze", "radarr", "sonarr", "canary_limiet",
  "dv_experimenteel", "hardlink_verbreken", "proefdraai"]);
/** Nette Nederlandse namen voor de onderdelen van de engine ("dv_experimenteel" → "Dolby Vision (experimenteel)"). */
const FEATURE_NAMES: Record<string, string> = {
  dv_experimenteel: "Dolby Vision (experimenteel)", gpu_monitor: "Videokaart bewaken", hardlink_verbreken: "Hardlinks verbreken",
  vmaf: "Kwaliteitsmeting (VMAF)", proefbatch: "Proefbatch", notificaties: "Meldingen", meldingen: "Meldingen", thumbnails: "Voorbeeldbeelden",
  hdr10plus: "HDR10+ behouden", ondertitels: "Ondertitels", audio_normalisatie: "Geluid gelijktrekken",
};
const featureName = (key: string) => FEATURE_NAMES[key] ?? (key.charAt(0).toUpperCase() + key.slice(1).replaceAll("_", " "));
const RISKY: Record<string, { enabled: boolean; word: string }> = {
  hardlink_verbreken: { enabled: true, word: "HARDLINKS VERBREKEN" },
  dv_experimenteel: { enabled: true, word: "EXPERIMENTEEL DOLBY VISION" },
  canary_limiet: { enabled: false, word: "CANARY-LIMIET UITZETTEN" },
};
const SECTIONS: Array<[string, MessageKey]> = [
  ["workers", "av1.set.workers"], ["grenzen", "av1.set.limits"], ["profielen", "av1.set.profiles"],
["onderdelen", "av1.set.features"], ["uitvoering", "tuning.title"], ["onderhoud", "av1.set.maintenance"],
];

function useAct() {
  const { t } = useI18n();
  const toast = useToast();
  const [pending, setPending] = useState<string | null>(null);
  const act = async (key: string, fn: () => Promise<string | void>): Promise<boolean> => {
    setPending(key);
    try {
      const msg = await fn();
      toast.push("success", msg || t("av1.saved"));
      return true;
    } catch (e) {
      toast.push("danger", t("av1.failed", { message: errorText(e) }));
      return false;
    } finally {
      setPending(null);
    }
  };
  return { pending, act };
}

function Section({ id, children }: { id: string; children: ReactNode }) {
  return <div id={id} className="span-12" style={{ scrollMarginTop: 80 }}>{children}</div>;
}

// ------------------------------------------------------------------ workers
function WorkersSection() {
  const { t } = useI18n();
  const workers = useEncoder<WorkerConfig[]>("/workers");
  return (
    <Card title={t("av1.set.workers")} sub={t("av1.set.workersManaged")} icon={Users}
      actions={<Link className="btn btn--primary btn--sm" href="/systeem?open=prestaties#prestaties" data-testid="av1-resources-link">{t("av1.set.workersEdit")}</Link>}>
      {!workers.data ? <StateView kind={workers.error ? "offline" : "loading"} compact /> : (
        <div className="grid">
          {[...workers.data].sort((a, b) => (a.baan === "gpu" ? -1 : b.baan === "gpu" ? 1 : 0)).map((w) => {
            const lane = w.baan as Lane;
            return (
              <div key={lane} className="span-6 stack-sm" style={{ padding: 14, border: "1px solid var(--mg-border-soft)", borderRadius: "var(--mg-r-lg)", background: "var(--mg-sunken)" }}>
                <div className="row-between">
                  <span className="lane-title"><span className={`lane-dot lane-dot--${lane}`} aria-hidden="true" />{t(lane === "gpu" ? "av1.lane.gpu" : "av1.lane.cpu")}<small>{t(lane === "gpu" ? "av1.lane.gpuEngine" : "av1.lane.cpuEngine")}</small></span>
                  {w.gepauzeerd ? <StatusChip tone="warning">{t("av1.lane.paused")}</StatusChip> : null}
                </div>
                <dl className="kv">
                  <dt>{t("av1.set.count")}</dt><dd>{w.aantal}</dd>
                  <dt>{t("av1.set.quietFrom")} – {t("av1.set.quietTo")}</dt><dd>{w.stilte_start && w.stilte_einde ? `${w.stilte_start} – ${w.stilte_einde}` : "—"}</dd>
                </dl>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ limits
/** Grenzen worden door MediaGuard beheerd (Instellingen → Systeem & prestaties) en naar de
 * engine doorgezet; hier alleen wat er nu geldt. Browsermeldingen blijven per engine. */
function LimitsSection({ settings, onSaved, browserOn }: { settings: EngineSettings; onSaved: () => void; browserOn: boolean }) {
  const { t, num } = useI18n();
  const { can } = useSession();
  const { pending, act } = useAct();
  const [d, setD] = useState<EngineSettings>(settings);
  useEffect(() => setD(settings), [settings]);
  // één bron van waarheid: de waarde uit MediaGuard (Instellingen → Systeem); de engine volgt (H11)
  const desired = useResource<{ desired?: { cpu_percent?: number }; cpu_percent?: number; in_sync?: boolean; sync_nl?: string | null; cores_nl?: string | null; engine_cpu_percent?: number | null }>("/encoding/resources");
  const want = desired.data?.cpu_percent ?? desired.data?.desired?.cpu_percent;
  const lagging = desired.data?.in_sync === false || (desired.data?.in_sync === undefined && typeof want === "number" && want !== settings.cpu_max_percent);
  const dirty = d.meld_browser_fouten !== settings.meld_browser_fouten || d.meld_browser_batches !== settings.meld_browser_batches || d.log_niveau !== settings.log_niveau;
  const editable = can("operator");
  const notify = async () => {
    if (!("Notification" in window)) return t("av1.set.notifyUnsupported");
    const p = await Notification.requestPermission();
    return p === "granted" ? t("av1.set.notifyGranted") : t("av1.set.notifyDenied");
  };
  return (
    <div className="grid">
      <div className="span-6">
        <Card title={t("av1.set.limits")} sub={t("av1.set.limitsManaged")} icon={Gauge}
          actions={<Link className="btn btn--primary btn--sm" href="/systeem?open=prestaties#prestaties" data-testid="av1-limits-link">{t("av1.set.limitsEdit")}</Link>}>
          <dl className="kv">
            <dt>{t("av1.set.cpuMax")}</dt><dd data-testid="av1-cpu-max">{num(want ?? settings.cpu_max_percent)} %{desired.data?.cores_nl ? <span className="t-3"> ({desired.data.cores_nl})</span> : null}{lagging ? <div className="t-warning" style={{ fontSize: 14 }}>{desired.data?.sync_nl ?? t("av1.set.engineLagging", { value: num(desired.data?.engine_cpu_percent ?? settings.cpu_max_percent) })}</div> : null}</dd>
            <dt>{t("av1.set.tempMax")}</dt><dd>{num(settings.cpu_max_temperatuur)} °C</dd>
            <dt>{t("av1.set.cacheWarn")}</dt><dd>{num(settings.cache_waarschuwing_gb)} GB</dd>
            <dt>{t("av1.set.cachePause")}</dt><dd>{num(settings.cache_pauze_gb)} GB</dd>
          </dl>
        </Card>
      </div>
      <div className="span-6">
        <Card title={t("av1.set.notifyTitle")} icon={Save}
          actions={editable ? <Button size="sm" variant="primary" icon={Save} disabled={!dirty} pending={pending === "limits"} onClick={() => void act("limits", async () => {
            const { log_niveau, meld_browser_fouten, meld_browser_batches } = d;
            await mutation("/instellingen", "PATCH", { log_niveau, meld_browser_fouten, meld_browser_batches });
            onSaved();
          })}>{t("av1.set.save")}</Button> : undefined}>
          <div className="stack">
            <div>
              <div className="check-row"><span>{t("av1.set.notifyErrors")}</span><Switch label={t("av1.set.notifyErrors")} disabled={!editable || !browserOn} checked={browserOn && d.meld_browser_fouten} onChange={(v) => setD({ ...d, meld_browser_fouten: v })} /></div>
              <div className="check-row"><span>{t("av1.set.notifyBatches")}</span><Switch label={t("av1.set.notifyBatches")} disabled={!editable || !browserOn} checked={browserOn && d.meld_browser_batches} onChange={(v) => setD({ ...d, meld_browser_batches: v })} /></div>
            </div>
            <div><Button size="sm" onClick={() => void act("notify", notify)}>{t("av1.set.notifyAllow")}</Button></div>
            <Field label={t("av1.set.logLevel")} htmlFor="lim-log">
              <select id="lim-log" className="select" disabled={!editable} value={d.log_niveau} onChange={(e) => setD({ ...d, log_niveau: e.target.value as EngineSettings["log_niveau"] })}>
                <option value="debug">{t("av1.set.log.debug")}</option><option value="info">{t("av1.set.log.info")}</option><option value="warning">{t("av1.set.log.warning")}</option><option value="error">{t("av1.set.log.error")}</option>
              </select>
            </Field>
          </div>
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ profiles
// Wat MediaGuard per bestand meestuurt (Instellingen → AV1); de profielwaarde wordt dan niet gebruikt (N7).
const MANAGED_KEYS = new Set(["gpu_voor_sdr_1080", "gpu_voor_sdr_4k", "gpu_voor_hdr10", "dv_modus", "audio_talen", "ondertitel_talen", "commentaar_weg", "beste_per_taal", "film_grain"]);
const profileName = (name: string) => name.replace(/\(per-job policy\)/i, "(per bestand)");
function ProfilesSection({ dvEnabled }: { dvEnabled: boolean }) {
  const { t } = useI18n();
  const { can } = useSession();
  const profiles = useEncoder<Profile[]>("/profielen");
  const [local, setLocal] = useState<Profile[]>([]);
  const { pending, act } = useAct();
  useEffect(() => { if (profiles.data) setLocal(profiles.data); }, [profiles.data]);
  const editable = can("operator");
  const update = (id: number, key: string, raw: string | boolean) => setLocal((old) => old.map((p) => {
    if (p.id !== id) return p;
    const cur = p.instellingen[key];
    const v: unknown = typeof cur === "number" ? Number(raw) : Array.isArray(cur) ? String(raw).split(",").map((s) => s.trim()).filter(Boolean) : raw;
    return { ...p, instellingen: { ...p.instellingen, [key]: v } };
  }));
  const label = (key: string) => { const k = `av1.prof.${key}` as MessageKey; const v = t(k); return v === k ? key : v; };
  const help = (key: string) => { const k = `av1.prof.${key}.help` as MessageKey; const v = t(k); return v === k ? undefined : v; };
  return (
    <Card title={t("av1.set.profiles")} sub={t("av1.set.profilesSub")} icon={Layers} flush>
      {!profiles.data ? <div className="card-body"><StateView kind={profiles.error ? "offline" : "loading"} compact /></div> : local.map((p) => {
        const dirty = JSON.stringify(p) !== JSON.stringify(profiles.data?.find((x) => x.id === p.id));
        return (
          <details key={p.id} className="raw" open={local.length === 1}>
            <summary style={{ fontSize: 14, color: "var(--mg-text)" }}>{profileName(p.naam)}{dirty ? <StatusChip tone="warning" icon={null}>•</StatusChip> : null}</summary>
            <div className="card-body stack">
              <InlineState tone="info">{t("av1.set.managedHint")} <Link className="link-more" href="/instellingen?q=encoding">{t("av1.set.managedLink")}</Link></InlineState>
              <div className="profile-grid">
                {Object.entries(p.instellingen).filter(([key]) => MANAGED_KEYS.has(key)).map(([key]) => (
                  <Field key={key} label={label(key)} htmlFor={`p${p.id}-${key}`}>
                    <div className="t-2" id={`p${p.id}-${key}`} style={{ fontSize: 14 }} data-testid={`managed-${key}`}>{t("av1.set.managedValue")}</div>
                  </Field>
                ))}
              </div>
              <div className="profile-grid">
                {Object.entries(p.instellingen).filter(([key]) => !MANAGED_KEYS.has(key)).map(([key, v]) => (
                  <Field key={key} label={label(key)} htmlFor={`p${p.id}-${key}`} hint={help(key)}>
                    {typeof v === "boolean" ? <div><Switch id={`p${p.id}-${key}`} label={label(key)} disabled={!editable} checked={v} onChange={(x) => update(p.id, key, x)} /></div>
                      : key === "dv_modus" ? (
                        <select id={`p${p.id}-${key}`} className="select" disabled={!editable} value={String(v)} onChange={(e) => update(p.id, key, e.target.value)}>
                          <option value="skip">{t("av1.set.dvSkip")}</option><option value="av1" disabled={!dvEnabled}>{t("av1.set.dvAv1")}</option>
                        </select>
                      ) : <input id={`p${p.id}-${key}`} className="input" disabled={!editable} value={Array.isArray(v) ? v.join(", ") : String(v)} onChange={(e) => update(p.id, key, e.target.value)} />}
                  </Field>
                ))}
              </div>
              {editable ? <div><Button variant="primary" icon={Save} disabled={!dirty} pending={pending === `p${p.id}`} onClick={() => void act(`p${p.id}`, async () => {
                await mutation(`/profielen/${p.id}`, "PATCH", { naam: p.naam, instellingen: p.instellingen });
                await profiles.reload();
                return t("av1.set.profileSaved", { name: p.naam });
              })}>{t("av1.set.profileSave")}</Button></div> : null}
            </div>
          </details>
        );
      })}
    </Card>
  );
}

// ------------------------------------------------------------------ features
function FeaturesSection({ features, reload }: { features: Feature[]; reload: () => Promise<void> }) {
  const { t } = useI18n();
  const { can } = useSession();
  const { pending, act } = useAct();
  const [confirm, setConfirm] = useState<{ f: Feature; enable: boolean; word: string; tvTest?: boolean } | null>(null);
  const quality = useEncoder<{ vmaf_beschikbaar: boolean; uitleg?: string }>("/kwaliteit/status");
  const send = (f: Feature, enable: boolean, word: string | null, tvTest = false) => act(f.sleutel, async () => {
    await mutation(`/functies/${f.sleutel}`, "PATCH", { ingeschakeld: enable, configuratie: tvTest ? { ...f.configuratie, tv_test_geslaagd: true } : f.configuratie, bevestiging: word });
    setConfirm(null);
    await reload();
  });
  const toggle = (f: Feature) => {
    const enable = !f.ingeschakeld;
    const risky = RISKY[f.sleutel];
    if (risky && risky.enabled === enable) setConfirm({ f, enable, word: risky.word });
    else void send(f, enable, null);
  };
  return (
    <Card title={t("av1.set.features")} sub={t("av1.set.featuresSub")} icon={Puzzle}>
      <InlineState tone="info">{t("av1.set.dvElsewhere")} <Link className="link-more" href="/dolby-vision">{t("nav.dv")}</Link></InlineState>
      <div>
        {features.filter((f) => !HIDDEN_FEATURES.has(f.sleutel)).map((f) => (
          <div className="check-row" key={f.sleutel}>
            <div style={{ minWidth: 0 }}>
              <strong>{featureName(f.sleutel)}</strong>
              <p>{f.uitleg}</p>
              {f.laatste_fout ? <p className="t-danger">{f.laatste_fout}</p> : null}
              {f.sleutel === "vmaf" && quality.data && !quality.data.vmaf_beschikbaar ? <p className="t-warning" data-testid="vmaf-unavailable">{t("av1.set.vmafUnavailable")}{quality.data.uitleg ? ` ${quality.data.uitleg}` : ""}</p> : null}
            </div>
            <div className="row" style={{ gap: 8 }}>
              {f.sleutel === "dv_experimenteel" && f.ingeschakeld && f.configuratie.tv_test_geslaagd !== true && can("operator") ? (
                <Button size="sm" onClick={() => setConfirm({ f, enable: true, word: "DOLBY VISION TV-TEST GESLAAGD", tvTest: true })}>{t("av1.set.dvTvTest")}</Button>
              ) : null}
              <Switch label={featureName(f.sleutel)} checked={f.ingeschakeld} disabled={!can("operator") || pending === f.sleutel} onChange={() => toggle(f)} />
            </div>
          </div>
        ))}
      </div>
      <DangerDialog open={confirm !== null} onClose={() => setConfirm(null)} confirmWord={confirm?.word} pending={pending === confirm?.f.sleutel}
        title={confirm?.tvTest ? t("av1.set.dvTvTestTitle") : t("av1.set.featureTitle", { name: confirm ? featureName(confirm.f.sleutel) : "" })}
        description={confirm?.tvTest ? t("av1.set.dvTvTestBody") : confirm?.f.uitleg ?? t("av1.set.featureBody")}
        confirmLabel={confirm?.enable ? t("av1.set.on") : t("av1.set.off")}
        onConfirm={() => { if (confirm) void send(confirm.f, confirm.enable, confirm.word, confirm.tvTest); }} />
    </Card>
  );
}

// ------------------------------------------------------------------ maintenance
function MaintenanceSection() {
  const { t, bytes, dateTime } = useI18n();
  const { can } = useSession();
  const queue = useEncoder<QueueRecovery>("/systeem/wachtrijherstel");
  const recovery = useEncoder<Recovery[]>("/herstel");
  const backups = useEncoder<Backup[]>("/backups");
  const { pending, act } = useAct();
  const [run, setRun] = useState(false);
  const [drop, setDrop] = useState<Recovery | null>(null);
  const q = queue.data;
  const editable = can("operator");
  return (
    <Card title={t("av1.set.maintenance")} sub={t("av1.set.maintenanceSub")} icon={LifeBuoy}>
      <div className="stack">
        <div className="stack-sm">
          <div className="row-between wrap">
            <strong>{t("av1.set.queueRecovery")}</strong>
            {q ? <StatusChip tone={q.kan_herstellen ? "warning" : "success"}>{q.kan_herstellen ? t("av1.set.queueRecoveryRun") : t("av1.set.queueRecoveryNone")}</StatusChip> : null}
          </div>
          {q ? <p className="t-2" style={{ margin: 0, fontSize: 14 }}>{t("av1.set.queueRecoveryState", { active: q.actieve_jobs.length, orphans: q.weesjobs.length, temp: q.tijdelijke_jobs.length })} · {q.uitleg}</p> : null}
          {q?.onveilige_paden.length ? <InlineState tone="danger">{q.onveilige_paden[0]}</InlineState> : null}
          <div className="row wrap">
            <Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void queue.reload()}>{t("av1.set.recheck")}</Button>
            {editable ? <Button size="sm" variant="danger" icon={LifeBuoy} disabled={!q?.kan_herstellen || (q?.actieve_jobs.length ?? 0) > 0} onClick={() => setRun(true)}>{t("av1.set.queueRecoveryRun")}</Button> : null}
          </div>
        </div>
        <div className="stack-sm">
          <strong>{t("orphans.title")}</strong>
          <OrphansSummaryLine />
        </div>
        <div className="stack-sm">
          <strong>{t("av1.set.transactions")}</strong>
          {!recovery.data?.length ? <p className="hint" style={{ margin: 0 }}>{t("av1.set.transactionsEmpty")}</p> : (
            <div className="list">{recovery.data.map((r) => (
              <div className="list-row" key={r.id}>
                <div className="list-main"><div className="list-title">#{r.job_id} · {r.status}</div><div className="list-sub" title={r.bron_pad}>{r.toelichting ?? r.bron_pad}</div></div>
                {r.status === "conflict" && editable ? <Button size="sm" variant="danger" icon={Trash2} onClick={() => setDrop(r)}>{t("av1.set.transactionRemove")}</Button> : <StatusChip tone="warning">{r.status}</StatusChip>}
              </div>
            ))}</div>
          )}
        </div>
        <div className="stack-sm">
          <div className="row-between wrap"><strong>{t("av1.set.backups")}</strong>
            {editable ? <Button size="sm" icon={Archive} pending={pending === "backup"} onClick={() => void act("backup", async () => { await mutation("/backups/nu", "POST"); await backups.reload(); return t("av1.set.backupMade"); })}>{t("av1.set.backupNow")}</Button> : null}</div>
          {!backups.data?.length ? <p className="hint" style={{ margin: 0 }}>{t("av1.set.backupEmpty")}</p> : (
            <div className="list">{backups.data.slice(0, 8).map((b) => (
              <div className="list-row" key={b.id}>
                <span className="list-icon"><HardDriveDownload className="ico" aria-hidden="true" /></span>
                <div className="list-main"><div className="list-title">{dateTime(b.gemaakt_op)}</div><div className="list-sub mono">{bytes(b.grootte)} · {b.sha256.slice(0, 12)}…</div></div>
                <div className="list-aside">
                  <StatusChip tone={b.gecontroleerd_op ? "success" : "neutral"}>{b.gecontroleerd_op ? t("av1.set.backupTested") : t("av1.set.backupUntested")}</StatusChip>
                  {editable ? <Button size="sm" variant="ghost" pending={pending === `b${b.id}`} onClick={() => void act(`b${b.id}`, async () => { await mutation(`/backups/${b.id}/controleer-herstel`, "POST"); await backups.reload(); return t("av1.set.backupTested"); })}>{t("av1.set.backupVerify")}</Button> : null}
                </div>
              </div>
            ))}</div>
          )}
        </div>
      </div>
      <DangerDialog open={run} onClose={() => setRun(false)} icon={LifeBuoy} confirmWord="WACHTRIJ VEILIG HERSTELLEN" pending={pending === "recover"}
        title={t("av1.set.queueRecoveryTitle")} description={t("av1.set.queueRecoveryBody")} confirmLabel={t("av1.set.queueRecoveryRun")}
        onConfirm={() => void act("recover", async () => {
          const r = await mutation<{ herstelde_jobs: number[]; verwijderde_mappen: number }>("/systeem/wachtrijherstel", "POST", { bevestiging: "WACHTRIJ VEILIG HERSTELLEN" });
          setRun(false); await queue.reload();
          return t("av1.set.queueRecoveryDone", { jobs: r.herstelde_jobs.length, dirs: r.verwijderde_mappen });
        })} />
      <DangerDialog open={drop !== null} onClose={() => setDrop(null)} icon={Trash2} pending={pending === "drop"}
        title={t("av1.set.transactionRemoveTitle")} description={t("av1.set.transactionRemoveBody")} confirmLabel={t("av1.set.transactionRemove")}
        onConfirm={() => { const r = drop; if (r) void act("drop", async () => { await mutation(`/herstel/${r.id}/tijdelijk-resultaat`, "DELETE"); setDrop(null); await recovery.reload(); }); }} />
    </Card>
  );
}

export default function Av1SettingsPage() {
  const { t } = useI18n();
  const settings = useEncoder<EngineSettings>("/instellingen");
  const features = useEncoder<Feature[]>("/functies");
  const dvEnabled = features.data?.find((f) => f.sleutel === "dv_experimenteel")?.ingeschakeld === true;
  return (
    <div className="stack">
      <nav className="settings-nav" aria-label={t("av1.set.nav")}>
        {SECTIONS.map(([id, key]) => <a key={id} className="filter-chip" href={`#${id}`}>{t(key)}</a>)}
      </nav>
      <InlineState tone="info">{t("av1.set.managedByMg")}</InlineState>
      <div className="grid">
        <Section id="workers"><WorkersSection /></Section>
        <Section id="grenzen">{settings.data ? <LimitsSection settings={settings.data} onSaved={() => void settings.reload()} browserOn={features.data?.find((f) => f.sleutel === "browsermeldingen")?.ingeschakeld !== false} /> : <Card title={t("av1.set.limits")}><StateView kind={settings.error ? "offline" : "loading"} compact /></Card>}</Section>
        <Section id="profielen"><ProfilesSection dvEnabled={dvEnabled} /></Section>
        <Section id="onderdelen">{features.data ? <FeaturesSection features={features.data} reload={features.reload} /> : <Card title={t("av1.set.features")}><StateView kind={features.error ? "offline" : "loading"} compact /></Card>}</Section>
        <Section id="uitvoering"><TuningSection /></Section>
        <Section id="gpu-meting"><GpuBenchmarkSection /></Section>
        <Section id="onderhoud"><MaintenanceSection /></Section>
      </div>
    </div>
  );
}
