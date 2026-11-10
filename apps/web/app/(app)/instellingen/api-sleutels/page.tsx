"use client";

import { Ban, Copy, Eye, KeySquare, Plus, UserCog } from "lucide-react";
import { useState } from "react";

import { AppPairing } from "@/components/mg/app-pairing";
import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Modal, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type KeyRole = "viewer" | "operator";
type KeyStatus = "actief" | "ingetrokken" | "verlopen";

export interface ApiKeyRow {
  id: number;
  name: string;
  prefix: string;
  role: KeyRole;
  created_by: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  status: KeyStatus;
  /** Wat de sleutel mag en de laatste 7 dagen deed (rapport 5, M13). */
  permissions_nl?: string;
  usage?: { actions_7d: number; by_action: Array<{ action: string; action_nl?: string; count: number; last?: string }> };
}

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 ._-]{1,63}$/;
const STATUS_TONE: Record<KeyStatus, "success" | "neutral" | "warning"> = { actief: "success", ingetrokken: "neutral", verlopen: "warning" };

/** Dagen tot een gekozen datum (einde van die dag, lokaal), minimaal 1. */
function daysUntil(date: string): number {
  const end = new Date(`${date}T23:59:59`);
  return Math.max(1, Math.ceil((end.valueOf() - Date.now()) / 86_400_000));
}

const today = () => new Date().toISOString().slice(0, 10);

export default function ApiKeysPage() {
  const { t, dateTime } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const keys = useResource<{ items: ApiKeyRow[] }>(can("admin") ? "/auth/api-keys" : null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState<{ name: string; role: KeyRole; expires: string }>({ name: "", role: "operator", expires: "" });
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [created, setCreated] = useState<{ name: string; key: string } | null>(null);
  const [revoking, setRevoking] = useState<ApiKeyRow | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  if (!can("admin")) return <InlineState tone="warning">{t("apikeys.adminOnly")}</InlineState>;

  const nameOk = NAME_RE.test(form.name.trim());
  const dateOk = !form.expires || form.expires > today();

  const open = () => { setError(null); setForm({ name: "", role: "operator", expires: "" }); setAdding(true); };

  const create = async () => {
    if (!nameOk || !dateOk) return;
    setPending(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { name: form.name.trim(), role: form.role };
      if (form.expires) body.expires_in_days = daysUntil(form.expires);
      const res = await api.post<ApiKeyRow & { key: string }>("/auth/api-keys", body);
      setAdding(false);
      setCreated({ name: res.name, key: res.key });
      await keys.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  };

  const revoke = async () => {
    if (!revoking) return;
    setPending(true);
    setRevokeError(null);
    try {
      await api.post(`/auth/api-keys/${revoking.id}/revoke`, {});
      toast.push("success", t("apikeys.revoked", { name: revoking.name }));
      setRevoking(null);
      await keys.reload();
    } catch (e) {
      setRevokeError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  };

  const copy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.key);
      toast.push("success", t("apikeys.created.copied"));
    } catch {
      toast.push("info", t("apikeys.created.copyFailed"));
    }
  };

  return (
    <div className="stack">
    <AppPairing />
    <Card title={t("apikeys.title")} sub={t("apikeys.sub")} icon={KeySquare} flush
      actions={<Button variant="primary" size="sm" icon={Plus} onClick={open} data-testid="apikey-new">{t("apikeys.new")}</Button>}>
      <ResourceView resource={keys} endpoint="GET /api/v1/auth/api-keys" isEmpty={(d) => !d.items.length} empty={<StateView kind="empty" body={t("apikeys.empty")} action={<Button variant="primary" size="sm" icon={Plus} onClick={open}>{t("apikeys.new")}</Button>} />}>
        {(data) => (
          <div className="table-wrap">
            <table className="table table--stack" data-testid="apikey-table">
              <thead><tr>
                <th>{t("apikeys.col.name")}</th><th>{t("apikeys.col.role")}</th><th>{t("apikeys.col.prefix")}</th>
                <th>{t("apikeys.col.created")}</th><th>{t("apikeys.col.lastUsed")}</th><th>{t("apikeys.col.expires")}</th>
                <th>{t("apikeys.col.status")}</th><th />
              </tr></thead>
              <tbody>
                {data.items.map((k) => (
                  <tr key={k.id} data-testid={`apikey-row-${k.name}`}>
                    <td className="cell-primary"><div className="cell-title">{k.name}</div><div className="cell-sub">{t("apikeys.by", { name: k.created_by.replace(/^user:/, "") })}</div>
                      {k.permissions_nl ? <div className="cell-sub" style={{ whiteSpace: "normal" }}>{t("apikeys.may", { what: k.permissions_nl })}</div> : null}
                      {k.usage ? <div className="cell-sub" style={{ whiteSpace: "normal" }} data-testid={`apikey-usage-${k.name}`}>{k.usage.actions_7d
                        ? t("apikeys.usage", { count: k.usage.actions_7d, what: k.usage.by_action.slice(0, 3).map((a) => `${a.action_nl ?? a.action} ×${a.count}`).join(", ") })
                        : t("apikeys.usageNone")}</div> : null}</td>
                    <td data-label={t("apikeys.col.role")}><StatusChip tone={k.role === "operator" ? "accent" : "neutral"} icon={k.role === "operator" ? UserCog : Eye}>{t(`role.${k.role}`)}</StatusChip></td>
                    <td data-label={t("apikeys.col.prefix")}><code className="mono">{k.prefix}…</code></td>
                    <td data-label={t("apikeys.col.created")}>{dateTime(k.created_at)}</td>
                    <td data-label={t("apikeys.col.lastUsed")}>{k.last_used_at ? dateTime(k.last_used_at) : t("apikeys.never")}</td>
                    <td data-label={t("apikeys.col.expires")}>{k.expires_at ? dateTime(k.expires_at) : t("apikeys.noExpiry")}</td>
                    <td data-label={t("apikeys.col.status")}><StatusChip tone={STATUS_TONE[k.status]} icon={k.status === "ingetrokken" ? Ban : undefined}>{t(`apikeys.status.${k.status}`)}</StatusChip></td>
                    <td>
                      {k.status !== "ingetrokken" ? (
                        <div className="row" style={{ justifyContent: "flex-end" }}>
                          <Button size="sm" variant="danger" icon={Ban} onClick={() => { setRevokeError(null); setRevoking(k); }}>{t("apikeys.revoke")}</Button>
                        </div>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ResourceView>

      <Modal open={adding} onClose={() => setAdding(false)} title={t("apikeys.new")} icon={KeySquare}
        footer={<><Button onClick={() => setAdding(false)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending} disabled={!nameOk || !dateOk} onClick={() => void create()} data-testid="apikey-create">{t("apikeys.form.create")}</Button></>}>
        <div className="stack">
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          <Field label={t("apikeys.form.name")} htmlFor="apikey-name" hint={t("apikeys.form.nameHint")} error={form.name && !nameOk ? t("apikeys.form.nameInvalid") : undefined}>
            <input id="apikey-name" className="input" value={form.name} maxLength={64} autoComplete="off" onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label={t("apikeys.form.role")} htmlFor="apikey-role" hint={form.role === "operator" ? t("apikeys.form.hintOperator") : t("apikeys.form.hintViewer")}>
            <div id="apikey-role">
              <Segmented label={t("apikeys.form.role")} value={form.role} onChange={(role) => setForm({ ...form, role })}
                options={[{ value: "operator", label: t("apikeys.form.roleOperator") }, { value: "viewer", label: t("apikeys.form.roleViewer") }]} />
            </div>
          </Field>
          <Field label={t("apikeys.form.expires")} htmlFor="apikey-expires" hint={t("apikeys.form.expiresHint")} error={!dateOk ? t("apikeys.form.datePast") : undefined}>
            <input id="apikey-expires" type="date" className="input" min={today()} value={form.expires} onChange={(e) => setForm({ ...form, expires: e.target.value })} />
          </Field>
        </div>
      </Modal>

      <Modal open={Boolean(created)} onClose={() => setCreated(null)} title={t("apikeys.created.title", { name: created?.name ?? "" })} icon={KeySquare}
        footer={<Button variant="primary" onClick={() => setCreated(null)} data-testid="apikey-done">{t("apikeys.created.done")}</Button>}>
        <div className="stack">
          <InlineState tone="warning" role="alert"><strong>{t("apikeys.created.warning")}</strong></InlineState>
          <Field label={t("apikeys.created.label")} htmlFor="apikey-value">
            <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
              <input id="apikey-value" className="input mono" readOnly value={created?.key ?? ""} onFocus={(e) => e.currentTarget.select()} data-testid="apikey-value" style={{ minWidth: 0, flex: 1 }} />
              <Button icon={Copy} onClick={() => void copy()} data-testid="apikey-copy">{t("apikeys.created.copy")}</Button>
            </div>
          </Field>
        </div>
      </Modal>

      <DangerDialog open={Boolean(revoking)} onClose={() => setRevoking(null)} onConfirm={() => void revoke()} icon={Ban}
        title={t("apikeys.revokeTitle", { name: revoking?.name ?? "" })} description={t("apikeys.revokeBody")}
        facts={revoking ? [{ label: t("apikeys.col.role"), value: t(`role.${revoking.role}`) }, { label: t("apikeys.col.prefix"), value: <code className="mono">{revoking.prefix}…</code> }] : undefined}
        confirmLabel={t("apikeys.revoke")} pending={pending} error={revokeError} />
    </Card>
    </div>
  );
}
