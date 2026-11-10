"use client";

import { Bot, Gauge, ListTree, Lock, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Card, Field, InlineState, PageHeader, Progress, Segmented, StatusChip, Switch } from "@/components/mg/primitives";
import { SettingsLink } from "@/components/mg/settings-link";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import { api } from "@/lib/api/client";
import type { AiCases, AiConsent, AiProviders, AiUsage } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type Mode = "disabled" | "shadow" | "advisory" | "bounded_auto";
const MODE_LABEL: Record<Mode, MessageKey> = { disabled: "ai.policy.disabled", shadow: "ai.policy.shadow", advisory: "ai.policy.advisory", bounded_auto: "ai.policy.bounded" };

export default function AiPage() {
  const { t, money, num, dateTime } = useI18n();
  const { can } = useSession();
  const settings = useResource<{ values: Record<string, unknown> }>("/ai/settings");
  const providers = useResource<AiProviders>("/ai/providers");
  const usage = useResource<AiUsage>("/ai/usage");
  const consent = useResource<AiConsent>("/ai/consent");
  const [status, setStatus] = useState("");
  const cases = useResource<AiCases>("/ai/cases", { status: status || undefined });
  useCoreEvents("ai", () => { void cases.reload(); void usage.reload(); });
  const { run, pending } = useAction();
  const values = settings.data?.values ?? {};
  const [provider, setProvider] = useState<string>("off");
  const [mode, setMode] = useState<Mode>("disabled");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!settings.data) return;
    const p = String(settings.data.values["ai.provider"] ?? "off");
    setProvider(p);
    setMode(String(settings.data.values["ai.mode"] ?? "disabled") as Mode);
    setModel(p !== "off" ? String(settings.data.values[`ai.${p}.model`] ?? "") : "");
  }, [settings.data]);
  const info = providers.data?.providers.find((p) => p.id === provider);
  const status0 = cases.data?.status;
  const dirty = settings.data && (provider !== String(values["ai.provider"] ?? "off") || mode !== String(values["ai.mode"] ?? "disabled") || (provider !== "off" && model !== String(values[`ai.${provider}.model`] ?? "")) || apiKey !== "");

  const save = async () => {
    setError(null);
    const body: { values: Record<string, unknown>; api_keys: Record<string, string>; reason: string } = { values: { "ai.provider": provider, "ai.mode": mode }, api_keys: {}, reason: "UI" };
    if (provider !== "off" && model) body.values[`ai.${provider}.model`] = model;
    if (provider !== "off" && apiKey) body.api_keys[provider] = apiKey;
    try {
      await api.patch("/ai/settings", body);
      setApiKey("");
      await Promise.all([settings.reload(), providers.reload(), cases.reload()]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <>
      <PageHeader title={t("nav.ai")} sub={t("ai.sub")} actions={status0 ? <StatusChip tone={status0.active ? "info" : "neutral"} size="lg">{t("ai.policyChip", { policy: t(MODE_LABEL[(status0.mode as Mode) ?? "disabled"] ?? "ai.policy.disabled") })}</StatusChip> : null} />
      {status0 && !status0.active ? <div style={{ marginBottom: 16 }}><InlineState>{status0.message_nl}{status0.blockers.length ? ` — ${status0.blockers.join(" ")}` : ""}</InlineState></div> : null}
      {status0 && (status0 as { idle_reason_nl?: string | null }).idle_reason_nl ? <div style={{ marginBottom: 16 }}><InlineState tone="info" role="status">{(status0 as { idle_reason_nl?: string | null }).idle_reason_nl}</InlineState></div> : null}
      <div className="grid">
        <Card className="span-4" title={t("ai.provider")} icon={Bot} bodyClass="stack">
          <ResourceView resource={providers} endpoint="GET /api/v1/ai/providers" compact>
            {(pv) => (
              <>
                <Segmented label={t("ai.provider")} value={provider} onChange={(v) => { setProvider(v); setModel(v !== "off" ? String(values[`ai.${v}.model`] ?? "") : ""); }} options={pv.options.map((o) => ({ value: o.id, label: o.label_nl }))} />
                {provider !== "off" && info ? (
                  <>
                    <Field label={t("ai.model")} htmlFor="ai-model" hint={info.models_error ?? t("ai.modelHint")}>
                      <select id="ai-model" className="select" value={model} onChange={(e) => setModel(e.target.value)}>
                        {[...new Set([model, ...info.models, ...info.suggested_models].filter(Boolean))].map((m) => <option key={m} value={m}>{m}</option>)}
                      </select>
                    </Field>
                    <Field label={t("ai.apiKey")} htmlFor="ai-key" hint={t("ai.keyHint")}>
                      <div className="stack-sm">
                        {info.api_key_set ? <StatusChip tone="success" icon={Lock}>{t("secrets.set")}</StatusChip> : <StatusChip tone="warning">{t("ai.keyMissing")}</StatusChip>}
                        <input id="ai-key" type="password" className="input" autoComplete="off" value={apiKey} placeholder={info.api_key_set ? t("secrets.replace") : ""} onChange={(e) => setApiKey(e.target.value)} disabled={!can("admin")} />
                      </div>
                    </Field>
                    {info.last_test.at ? <InlineState tone={info.last_test.ok ? "success" : "danger"}>{info.last_test.ok ? t("ai.testOk") : t("ai.testFailed")} · {dateTime(info.last_test.at)}{info.last_test.latency_ms ? ` · ${num(info.last_test.latency_ms)} ms` : ""}{info.last_test.detail ? ` · ${info.last_test.detail}` : ""}</InlineState> : null}
                    <Button disabled={!can("operator") || !info.api_key_set} pending={pending === "test"} onClick={() => void run("test", "post", `/ai/providers/${provider}/test`, { model }).then(() => providers.reload())}>{t("ai.testConnection")}</Button>
                    {info.blockers.length ? <InlineState tone="warning"><strong>{t("ai.blockers")}</strong> {info.blockers.join(" ")}</InlineState> : null}
                  </>
                ) : <InlineState>{t("ai.offNote")}</InlineState>}
                <div>
                  <span className="label">{t("ai.policy")}</span>
                  <Segmented label={t("ai.policy")} value={mode} onChange={setMode} options={(["disabled", "shadow", "advisory", "bounded_auto"] as Mode[]).map((m) => ({ value: m, label: t(MODE_LABEL[m]), disabled: m === "bounded_auto" && !status0?.bounded_auto.unlocked, title: m === "bounded_auto" && !status0?.bounded_auto.unlocked ? status0?.bounded_auto.reasons.join(" ") : undefined }))} />
                  {!status0?.bounded_auto.unlocked ? <p className="hint">{t("ai.boundedLocked")} {status0?.bounded_auto.reasons.join(" ")}</p> : null}
                </div>
                {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
                <div className="row"><Button variant="primary" disabled={!dirty || !can("admin")} pending={pending === "save"} onClick={() => void save()}>{t("action.save")}</Button>{dirty ? <Button variant="ghost" onClick={() => void settings.reload()}>{t("action.discard")}</Button> : null}</div>
              </>
            )}
          </ResourceView>
        </Card>

        <Card className="span-4" title={t("ai.budget")} icon={Gauge}>
          <ResourceView resource={usage} endpoint="GET /api/v1/ai/usage" compact>
            {(u) => (
              <div className="budget">
                <div className="budget-figure">{money(u.spent, u.currency)} <span className="t-3" style={{ fontSize: 14, fontWeight: 500 }}>{t("ai.of", { value: money(u.monthly_limit, u.currency) })}</span></div>
                <Progress value={u.monthly_limit ? ((u.spent + u.reserved) / u.monthly_limit) * 100 : 0} label={t("ai.budget")} tone={u.monthly_limit && u.spent / u.monthly_limit > 0.8 ? "warning" : undefined} />
                <dl className="kv" style={{ marginTop: 8 }}>
                  <dt>{t("ai.remaining")}</dt><dd>{money(u.remaining, u.currency)}</dd>
                  <dt>{t("ai.analysesToday")}</dt><dd>{num(u.calls_today)} / {num(u.max_calls_per_day)}</dd>
                  <dt>{t("ai.perAnalysis")}</dt><dd>{money(u.per_analysis_limit, u.currency)}</dd>
                  <dt>{t("ai.cacheHits")}</dt><dd>{num(u.cache_hits_month)}</dd>
                  <dt>{t("ai.month")}</dt><dd>{u.month} · {u.timezone}</dd>
                </dl>
                {u.price_note_nl ? <p className="hint">{u.price_note_nl}</p> : null}
              </div>
            )}
          </ResourceView>
        </Card>

        <Card className="span-4" title={t("ai.privacy")} icon={ShieldCheck} bodyClass="stack-sm">
          <ResourceView resource={consent} endpoint="GET /api/v1/ai/consent" compact>
            {(c) => (
              <>
                <p className="t-2" style={{ fontSize: 14 }}>{c.text_nl}</p>
                {c.providers.map((p) => (
                  <div key={p.provider} className="row-between">
                    <span style={{ fontSize: 14 }}>{p.provider === "anthropic" ? "Claude" : "OpenAI"}{p.granted && p.ts ? <span className="t-3" style={{ fontSize: 14 }}> · {t("ai.consentBy", { by: p.actor ?? "—", at: dateTime(p.ts) })}</span> : null}</span>
                    <Switch checked={p.granted} disabled={!can("admin")} label={t("ai.consentFor", { provider: p.provider })} onChange={(v) => void run(`c-${p.provider}`, "post", "/ai/consent", { provider: p.provider, granted: v }, t("toast.saved")).then(() => { void consent.reload(); void providers.reload(); })} />
                  </div>
                ))}
                <p className="t-3" style={{ fontSize: 14 }}>{t("ai.neverSent")}</p>
              </>
            )}
          </ResourceView>
        </Card>

        <Card className="span-12" title={t("ai.cases")} icon={ListTree} flush actions={<Segmented label={t("ai.cases")} value={status} onChange={setStatus} options={[{ value: "", label: t("media.all") }, { value: "open", label: t("ai.cases.open") }, { value: "accepted", label: t("ai.cases.done") }]} />}>
          <ResourceView resource={cases} endpoint="GET /api/v1/ai/cases" compact isEmpty={(r) => r.items.length === 0} empty={<StateView kind="empty" compact title={t("ai.noCases")} body={cases.data?.status.message_nl} />}>
            {(r) => (
              <div className="table-wrap">
                <p className="t-3" style={{ fontSize: 14, padding: "8px 16px 0" }}>{t("ai.zeroInteraction")}</p>
                <table className="table table--stack">
                  <thead><tr><th>{t("ai.case")}</th><th>{t("ai.whyAi")}</th><th>{t("ai.proposal")}</th><th>PolicyGuard</th><th>{t("ai.uncertainty")}</th><th className="num">{t("ai.cost")}</th><th /></tr></thead>
                  <tbody>{r.items.map((c) => (
                    <tr key={c.case_id}>
                      <td className="cell-primary"><div className="cell-title">{c.title ?? c.case_id}</div><div className="cell-sub">{c.task_type} · {String(c.status_label_nl ?? c.status ?? "")}</div></td>
                      <td data-label={t("ai.whyAi")}>{c.trigger_reason ?? "—"}</td>
                      <td data-label={t("ai.proposal")}>{c.proposal?.suggested_action ? <span className="tag">{c.proposal.suggested_action}</span> : "—"}<div className="cell-sub" style={{ maxWidth: 320 }}>{c.proposal?.reasoning_summary}</div></td>
                      <td data-label="PolicyGuard"><StatusChip tone={c.policy_result?.outcome === "allowed" ? "success" : c.policy_result?.outcome === "blocked" ? "danger" : "info"} icon={Lock}>{c.policy_result?.outcome ?? t("ai.outcome.advice_only")}</StatusChip></td>
                      <td data-label={t("ai.uncertainty")}>{c.proposal?.uncertainty ?? "—"}</td>
                      <td data-label={t("ai.cost")} className="num">{money(c.cost ?? null)}</td>
                      <td><div className="row" style={{ justifyContent: "flex-end" }}>
                        {/* zero-interactie: niets wacht op akkoord; alleen achteraf overrulen */}
                        {c.status === "auto_resolved" || c.status === "deferred" ? (
                          <Button size="sm" disabled={!can("operator")} pending={pending === `ok-${c.case_id}`} onClick={() => void run(`ok-${c.case_id}`, "post", `/ai/cases/${c.case_id}/approve`, {}, t("ai.accepted")).then(() => cases.reload())}>{t("ai.override")}</Button>
                        ) : null}
                        <Button size="sm" variant="ghost" disabled={!can("operator")} pending={pending === `fb-${c.case_id}`} onClick={() => void run(`fb-${c.case_id}`, "post", `/ai/cases/${c.case_id}/feedback`, { verdict: "incorrect" }, t("ai.feedbackSaved"))}>{t("ai.markWrong")}</Button>
                        <Button size="sm" variant="ghost" disabled={!can("operator")} pending={pending === `re-${c.case_id}`} onClick={() => void run(`re-${c.case_id}`, "post", `/ai/cases/${c.case_id}/analyze`, { force: true }, t("ai.reanalyze"))}>{t("ai.reanalyze")}</Button>
                      </div></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </ResourceView>
        </Card>
        <div className="span-12"><SettingsLink sections={["algemeen"]} /></div>
      </div>
    </>
  );
}
