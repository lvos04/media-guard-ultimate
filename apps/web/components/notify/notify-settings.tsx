"use client";

import { BellRing, Plus, Send, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Modal, Segmented, StatusChip, Switch } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

import type { ChannelKind, Choice, NotifyChannel, NotifyOverview } from "./types";

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Instellingen → Meldingen: kanalen (met test), wat wanneer, overzichten. */
export function NotifySettings() {
  const { t, relative } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const data = useResource<NotifyOverview>(can("admin") ? "/notify/overview" : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ channel: NotifyChannel | null; kind: ChannelKind } | null>(null);
  const [removing, setRemoving] = useState<NotifyChannel | null>(null);

  if (!can("admin")) return <InlineState tone="warning">{t("apikeys.adminOnly")}</InlineState>;

  const run = async (key: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(key);
    try {
      await fn();
      if (ok) toast.push("success", ok);
      await data.reload();
    } catch (e) {
      toast.push("danger", msg(e));
    } finally {
      setBusy(null);
    }
  };

  const test = async (c: NotifyChannel) => {
    setBusy(`test-${c.id}`);
    try {
      const r = await api.post<{ ok: boolean; error?: string }>(`/notify/channels/${c.id}/test`);
      toast.push(r.ok ? "success" : "danger", r.ok ? t("notify.testOk", { name: c.name }) : t("notify.testFail", { error: r.error ?? "" }));
      await data.reload();
    } catch (e) {
      // core onbereikbaar of fout: niet stil laten falen (punt 39)
      toast.push("danger", t("notify.testFail", { error: msg(e) }));
    } finally {
      setBusy(null);
    }
  };

  const savePrefs = (body: Record<string, unknown>) => run("prefs", () => api.put("/notify/preferences", body), t("notify.saved"));

  return (
    <div className="stack" data-testid="notify-settings">
      <ResourceView resource={data} endpoint="GET /api/v1/notify/overview">
        {(ov) => (
          <>
            <Card title={t("notify.channels")} sub={t("notify.channelsSub")} icon={BellRing} flush
              actions={<Button size="sm" variant="primary" icon={Plus} onClick={() => setEditing({ channel: null, kind: "ntfy" })} data-testid="notify-add">{t("notify.add")}</Button>}>
              <div className="list">
                {!ov.channels.length ? <div className="list-row"><div className="list-main"><div className="list-sub">{t("notify.empty")}</div></div></div> : null}
                {ov.channels.map((c) => (
                  <div key={c.id} className="list-row" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 12 }} data-testid={`notify-channel-${c.kind}`}>
                    <div className="list-main" style={{ minWidth: 220 }}>
                      <div className="list-title">{c.name} <StatusChip tone={c.enabled ? "success" : "neutral"}>{c.enabled ? t("notify.enabled") : t("notify.disabled")}</StatusChip></div>
                      <div className="list-sub">{c.kind_nl}{c.last_ok_at ? ` · ${t("notify.lastOk", { when: relative(c.last_ok_at) })}` : ""}</div>
                      {c.last_error ? <div className="list-sub" style={{ color: "var(--danger)", whiteSpace: "normal" }}>{t("notify.lastError", { error: c.last_error })}</div> : null}
                      {c.kind === "ntfy" && c.subscribe_url ? (
                        <div className="row" style={{ gap: 16, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
                          {/* eslint-disable-next-line @next/next/no-img-element -- data-URI QR */}
                          {c.qr_svg ? <img src={c.qr_svg} alt={c.subscribe_url} width={148} height={148} style={{ borderRadius: 8, background: "#fff" }} data-testid="ntfy-qr" /> : null}
                          <div style={{ maxWidth: 360, minWidth: 0 }}>
                            <p className="hint" style={{ marginTop: 0 }}>{t("notify.ntfyIntro")}</p>
                            <div className="list-sub">{t("notify.ntfyOpen")}:</div>
                            <a className="link-more mono" href={c.subscribe_url} target="_blank" rel="noreferrer" style={{ overflowWrap: "anywhere" }}>{c.subscribe_url}</a>
                            <p className="hint">{t("notify.ntfyPrivacy")}</p>
                          </div>
                        </div>
                      ) : null}
                    </div>
                    <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                      <Button size="sm" variant={c.enabled ? "secondary" : "primary"} pending={busy === `en-${c.id}`}
                        onClick={() => void run(`en-${c.id}`, () => api.patch(`/notify/channels/${c.id}`, { enabled: !c.enabled }))}>{c.enabled ? t("notify.disable") : t("notify.enable")}</Button>
                      <Button size="sm" icon={Send} pending={busy === `test-${c.id}`} onClick={() => void test(c)} data-testid={`notify-test-${c.id}`}>{t("notify.test")}</Button>
                      <Button size="sm" onClick={() => setEditing({ channel: c, kind: c.kind })}>{t("action.edit")}</Button>
                      <Button size="sm" variant="danger" icon={Trash2} onClick={() => setRemoving(c)}>{t("notify.remove")}</Button>
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            <Card title={t("notify.categories")} sub={t("notify.categoriesSub")} flush>
              <div className="list">
                {ov.categories.map((cat) => (
                  <div key={cat.key} className="list-row" style={{ flexWrap: "wrap", gap: 8 }}>
                    <div className="list-main"><div className="list-title">{cat.label_nl}</div></div>
                    <Segmented<Choice> label={cat.label_nl} value={cat.value} onChange={(v) => void savePrefs({ categories: { [cat.key]: v } })}
                      options={ov.choices.map((ch) => ({ value: ch.value, label: ch.label_nl }))} />
                  </div>
                ))}
              </div>
            </Card>

            <DigestCard ov={ov} busy={busy} onSave={savePrefs} onRun={run} />
          </>
        )}
      </ResourceView>

      {editing && data.data ? (
        <ChannelDialog ov={data.data} channel={editing.channel} initialKind={editing.kind}
          onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); toast.push("success", t("notify.saved")); await data.reload(); }} />
      ) : null}

      <DangerDialog open={Boolean(removing)} onClose={() => setRemoving(null)} icon={Trash2}
        onConfirm={() => void run("rm", async () => { await api.del(`/notify/channels/${removing?.id}`); setRemoving(null); })}
        title={t("notify.removeTitle", { name: removing?.name ?? "" })} description={t("notify.removeBody")}
        confirmLabel={t("notify.remove")} pending={busy === "rm"} />
    </div>
  );
}

function DigestCard({ ov, busy, onSave, onRun }: {
  ov: NotifyOverview; busy: string | null; onSave: (b: Record<string, unknown>) => Promise<void>;
  onRun: (key: string, fn: () => Promise<unknown>, ok?: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const toast = useToast();
  const [time, setTime] = useState(ov.digest.time);
  const [baseUrl, setBaseUrl] = useState(ov.base_url);
  const [rate, setRate] = useState(String(ov.rate_per_hour));
  const [growth, setGrowth] = useState(String(ov.storage?.growth_alert_gb ?? 200));
  const sendNow = () => onRun("digest-now", async () => {
    const r = await api.post<{ queued: number }>("/notify/digests/send-now");
    toast.push("success", t("notify.digestQueued", { count: r.queued }));
  });
  return (
    <Card title={t("notify.digest")} sub={t("notify.digestSub")}
      actions={<Button size="sm" icon={Send} pending={busy === "digest-now"} onClick={() => void sendNow()}>{t("notify.digestNow")}</Button>}>
      <div className="stack">
        <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <Switch checked={ov.digest.daily} label={t("notify.digestDaily")} onChange={(v) => void onSave({ digest_daily: v })} />
          <span>{t("notify.digestDaily")}</span>
          <label className="label" htmlFor="digest-time" style={{ margin: 0 }}>{t("notify.digestTime")}</label>
          <input id="digest-time" type="time" className="input" style={{ width: 120 }} value={time}
            onChange={(e) => setTime(e.target.value)} onBlur={() => { if (time && time !== ov.digest.time) void onSave({ digest_time: time }); }} />
        </div>
        <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <Switch checked={ov.digest.weekly} label={t("notify.digestWeekly")} onChange={(v) => void onSave({ digest_weekly: v })} />
          <span>{t("notify.digestWeekly")}</span>
          <label className="label" htmlFor="digest-weekday" style={{ margin: 0 }}>{t("notify.digestWeekday")}</label>
          <select id="digest-weekday" className="input" style={{ width: 160 }} value={ov.digest.weekday}
            onChange={(e) => void onSave({ digest_weekday: Number(e.target.value) })}>
            {[1, 2, 3, 4, 5, 6, 7].map((d) => <option key={d} value={d}>{t(`notify.weekday.${d}` as MessageKey)}</option>)}
          </select>
        </div>
        {ov.storage ? (
          <div className="stack" data-testid="notify-storage" style={{ gap: 8 }}>
            <strong style={{ fontSize: 14 }}>{t("notify.storageTitle")}</strong>
            <p className="hint" style={{ margin: 0 }}>{t("notify.storageSub")} <Link href="/av1#opslag">{t("ledger.look")}</Link></p>
            <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
              <Switch checked={ov.storage.daily} label={t("notify.storageDaily")} onChange={(v) => void onSave({ storage_daily: v })} />
              <span>{t("notify.storageDaily")}</span>
            </div>
            <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
              <Switch checked={ov.storage.weekly} label={t("notify.storageWeekly")} onChange={(v) => void onSave({ storage_weekly: v })} />
              <span>{t("notify.storageWeekly")}</span>
            </div>
            <Field label={t("notify.storageGrowth")} htmlFor="notify-growth" hint={t("notify.storageGrowthHint")}>
              <input id="notify-growth" type="number" min={0} max={10000} className="input" style={{ width: 120 }} value={growth} onChange={(e) => setGrowth(e.target.value)}
                onBlur={() => { const n = Number(growth); if (n >= 0 && n <= 10000 && n !== ov.storage?.growth_alert_gb) void onSave({ storage_growth_alert_gb: n }); }} />
            </Field>
          </div>
        ) : null}
        <details className="tech-details">
          <summary>{t("notify.advanced")}</summary>
          <div className="stack" style={{ marginTop: 12 }}>
            <Field label={t("notify.baseUrl")} htmlFor="notify-base" hint={t("notify.baseUrlHint")}>
              <input id="notify-base" className="input" placeholder="https://" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
                onBlur={() => { if (baseUrl !== ov.base_url) void onSave({ base_url: baseUrl }); }} />
            </Field>
            <Field label={t("notify.rate")} htmlFor="notify-rate" hint={t("notify.rateHint")}>
              <input id="notify-rate" type="number" min={1} max={200} className="input" style={{ width: 120 }} value={rate} onChange={(e) => setRate(e.target.value)}
                onBlur={() => { const n = Number(rate); if (n >= 1 && n <= 200 && n !== ov.rate_per_hour) void onSave({ rate_per_hour: n }); }} />
            </Field>
          </div>
        </details>
      </div>
    </Card>
  );
}

function ChannelDialog({ ov, channel, initialKind, onClose, onSaved }: {
  ov: NotifyOverview; channel: NotifyChannel | null; initialKind: ChannelKind; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [kind, setKind] = useState<ChannelKind>(channel?.kind ?? initialKind);
  const [name, setName] = useState(channel?.name ?? "");
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries(channel?.config ?? {}).map(([k, v]) => [k, String(v)])));
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const spec = ov.kinds.find((k) => k.kind === kind);
  const save = async () => {
    if (!spec) return;
    setPending(true);
    setError(null);
    const config: Record<string, string> = {};
    const secrets: Record<string, string> = {};
    for (const f of spec.fields) {
      const v = (values[f.name] ?? "").trim();
      if (f.secret) { if (v) secrets[f.name] = v; } else config[f.name] = v;
    }
    try {
      if (channel) await api.patch(`/notify/channels/${channel.id}`, { name, config, secrets });
      else await api.post("/notify/channels", { kind, name, config, secrets, enabled: true });
      await onSaved();
    } catch (e) {
      setError(msg(e));
    } finally {
      setPending(false);
    }
  };
  return (
    <Modal open onClose={onClose} title={channel ? channel.name : t("notify.add")} icon={BellRing}
      footer={<><Button onClick={onClose}>{t("action.cancel")}</Button><Button variant="primary" pending={pending} onClick={() => void save()} data-testid="notify-save">{t("notify.save")}</Button></>}>
      <div className="stack">
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
        {!channel ? (
          <Field label={t("notify.kind")} htmlFor="notify-kind">
            <select id="notify-kind" className="input" value={kind} onChange={(e) => { setKind(e.target.value as ChannelKind); setValues(e.target.value === "ntfy" ? { server: "https://ntfy.sh" } : {}); }}>
              {ov.kinds.map((k) => <option key={k.kind} value={k.kind}>{k.label_nl}</option>)}
            </select>
          </Field>
        ) : null}
        <Field label={t("notify.name")} htmlFor="notify-name">
          <input id="notify-name" className="input" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
        </Field>
        {spec?.fields.map((f) => {
          const id = `notify-f-${f.name}`;
          const isSet = channel?.secrets_set?.[f.name];
          return (
            <Field key={f.name} label={t(`notify.field.${f.name}` as MessageKey)} htmlFor={id} hint={f.secret && isSet ? t("notify.secretSet") : undefined}>
              <input id={id} className="input" type={f.secret ? "password" : f.name === "port" ? "number" : "text"} autoComplete="off"
                value={values[f.name] ?? ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
            </Field>
          );
        })}
      </div>
    </Modal>
  );
}
