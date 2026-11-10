"use client";

import { KeyRound, Plus, UserCheck, UserX } from "lucide-react";
import { useState } from "react";

import { Button, Card, Field, InlineState, Modal, StatusChip } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import type { Role, UserRow } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const ROLES: Role[] = ["admin", "operator", "viewer"];

export default function UsersPage() {
  const { t, dateTime } = useI18n();
  const { can, me } = useSession();
  const toast = useToast();
  const users = useResource<UserRow[]>(can("admin") ? "/auth/users" : null);
  const [adding, setAdding] = useState(false);
  const [pwdFor, setPwdFor] = useState<UserRow | null>(null);
  const [form, setForm] = useState({ username: "", password: "", role: "viewer" as Role });
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (!can("admin")) return <InlineState tone="warning">{t("users.adminOnly")}</InlineState>;

  const patch = async (user: UserRow, body: Record<string, unknown>) => {
    try {
      await api.patch(`/auth/users/${user.id}`, body);
      toast.push("success", t("users.updated"));
      await users.reload();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: e instanceof Error ? e.message : String(e) }));
    }
  };

  const create = async () => {
    setPending(true);
    setError(null);
    try {
      await api.post("/auth/users", form);
      toast.push("success", t("users.created.toast", { name: form.username }));
      setAdding(false);
      setForm({ username: "", password: "", role: "viewer" });
      await users.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  };

  const setPassword = async () => {
    if (!pwdFor) return;
    setPending(true);
    setError(null);
    try {
      await api.patch(`/auth/users/${pwdFor.id}`, { password: form.password });
      toast.push("success", t("users.updated"));
      setPwdFor(null);
      setForm((f) => ({ ...f, password: "" }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <Card title={t("users.title")} sub={t("users.sub")} flush actions={<Button variant="primary" size="sm" icon={Plus} onClick={() => { setError(null); setAdding(true); }}>{t("users.add")}</Button>}>
      <ResourceView resource={users} endpoint="GET /api/v1/auth/users">
        {(rows) => (
          <div className="table-wrap">
            <table className="table table--stack">
              <thead><tr><th>{t("users.username")}</th><th>{t("users.role")}</th><th>{t("users.locale")}</th><th>{t("users.lastLogin")}</th><th>{t("users.status")}</th><th /></tr></thead>
              <tbody>
                {rows.map((u) => (
                  <tr key={u.id}>
                    <td className="cell-primary"><div className="cell-title">{u.username}</div><div className="cell-sub">{t("users.created")} {dateTime(u.created_at)}</div></td>
                    <td data-label={t("users.role")}>
                      <select className="select input--sm" style={{ width: 150 }} aria-label={t("users.role")} value={u.role} disabled={u.username === me?.username} onChange={(e) => void patch(u, { role: e.target.value })}>
                        {ROLES.map((r) => <option key={r} value={r}>{t(`role.${r}`)}</option>)}
                      </select>
                    </td>
                    <td data-label={t("users.locale")}>{u.locale}</td>
                    <td data-label={t("users.lastLogin")}>{u.last_login_at ? dateTime(u.last_login_at) : t("users.never")}</td>
                    <td data-label={t("users.status")}>{u.disabled ? <StatusChip tone="danger">{t("users.disabled")}</StatusChip> : <StatusChip tone="success">{t("users.active")}</StatusChip>}</td>
                    <td>
                      <div className="row" style={{ justifyContent: "flex-end" }}>
                        <Button size="sm" icon={KeyRound} onClick={() => { setError(null); setPwdFor(u); }}>{t("users.resetPassword")}</Button>
                        {u.username !== me?.username ? (
                          u.disabled
                            ? <Button size="sm" icon={UserCheck} onClick={() => void patch(u, { disabled: false })}>{t("users.enable")}</Button>
                            : <Button size="sm" variant="danger" icon={UserX} onClick={() => void patch(u, { disabled: true })}>{t("users.disable")}</Button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ResourceView>

      <Modal open={adding} onClose={() => setAdding(false)} title={t("users.add")} footer={<><Button onClick={() => setAdding(false)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending} onClick={() => void create()}>{t("action.add")}</Button></>}>
        <div className="stack">
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          <Field label={t("users.username")} htmlFor="new-user"><input id="new-user" className="input" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} autoComplete="off" /></Field>
          <Field label={t("auth.password")} htmlFor="new-pass" hint={t("auth.passwordHint")}><input id="new-pass" type="password" className="input" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" /></Field>
          <Field label={t("users.role")} htmlFor="new-role">
            <select id="new-role" className="select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>{ROLES.map((r) => <option key={r} value={r}>{t(`role.${r}`)}</option>)}</select>
          </Field>
        </div>
      </Modal>

      <Modal open={Boolean(pwdFor)} onClose={() => setPwdFor(null)} title={`${t("users.resetPassword")} · ${pwdFor?.username ?? ""}`} footer={<><Button onClick={() => setPwdFor(null)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending} disabled={form.password.length < 12} onClick={() => void setPassword()}>{t("action.save")}</Button></>}>
        <div className="stack">
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          <Field label={t("users.newPassword")} htmlFor="reset-pass" hint={t("auth.passwordHint")}><input id="reset-pass" type="password" className="input" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" /></Field>
        </div>
      </Modal>
    </Card>
  );
}
