"use client";

import { KeyRound, Lock, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Modal, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import type { SecretRow } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export default function SecretsPage() {
  const { t, dateTime } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const secrets = useResource<SecretRow[]>(can("admin") ? "/settings/secrets" : null);
  const [editing, setEditing] = useState<{ name: string; fixed: boolean } | null>(null);
  const [value, setValue] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  if (!can("admin")) return <InlineState tone="warning">{t("users.adminOnly")}</InlineState>;

  const save = async () => {
    const target = editing?.fixed ? editing.name : name.trim();
    setPending(true);
    setError(null);
    try {
      await api.put(`/settings/secrets/${encodeURIComponent(target)}`, { value });
      toast.push("success", t("secrets.saved", { name: target }));
      setEditing(null);
      setValue("");
      setName("");
      await secrets.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  };

  const remove = async () => {
    if (!deleting) return;
    setPending(true);
    try {
      await api.del(`/settings/secrets/${encodeURIComponent(deleting)}`);
      toast.push("success", t("secrets.deleted", { name: deleting }));
      setDeleting(null);
      await secrets.reload();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: e instanceof Error ? e.message : String(e) }));
    } finally {
      setPending(false);
    }
  };

  return (
    <Card title={t("secrets.title")} sub={t("secrets.sub")} icon={Lock} flush actions={<Button variant="primary" size="sm" icon={Plus} onClick={() => { setError(null); setEditing({ name: "", fixed: false }); }}>{t("secrets.add")}</Button>}>
      <ResourceView resource={secrets} endpoint="GET /api/v1/settings/secrets" isEmpty={(rows) => rows.length === 0} empty={<StateView kind="empty" compact title={t("secrets.empty")} body={t("secrets.nameHint")} />}>
        {(rows) => (
          <>
          {rows.some((s) => s.readable === false) ? (
            <div className="card-body" style={{ paddingBottom: 0 }} data-testid="secrets-mismatch">
              <InlineState tone="danger" role="alert">
                <strong>{t("secrets.mismatchTitle")}.</strong> {t("secrets.mismatchBody", { count: rows.filter((s) => s.readable === false).length })}
              </InlineState>
            </div>
          ) : null}
          <div className="table-wrap">
            <table className="table table--stack">
              <thead><tr><th>{t("secrets.name")}</th><th>{t("secrets.updated")}</th><th>{t("secrets.by")}</th><th>{t("users.status")}</th><th /></tr></thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.name}>
                    <td className="cell-primary"><span className="mono">{s.name}</span></td>
                    <td data-label={t("secrets.updated")}>{dateTime(s.updated_at)}</td>
                    <td data-label={t("secrets.by")}>{s.updated_by}</td>
                    <td data-label={t("users.status")}>{s.readable === false
                      ? <StatusChip tone="danger">{t("secrets.unreadable")}</StatusChip>
                      : <StatusChip tone="success" icon={Lock}>{t("secrets.set")}</StatusChip>}</td>
                    <td><div className="row" style={{ justifyContent: "flex-end" }}>
                      <Button size="sm" icon={KeyRound} onClick={() => { setError(null); setEditing({ name: s.name, fixed: true }); }}>{t("secrets.replace")}</Button>
                      <Button size="sm" variant="danger" icon={Trash2} onClick={() => setDeleting(s.name)}>{t("action.delete")}</Button>
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}
      </ResourceView>

      <Modal open={Boolean(editing)} onClose={() => setEditing(null)} title={editing?.fixed ? `${t("secrets.replace")} · ${editing.name}` : t("secrets.add")}
        footer={<><Button onClick={() => setEditing(null)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending} disabled={!value || (!editing?.fixed && !name.trim())} onClick={() => void save()}>{t("action.save")}</Button></>}>
        <div className="stack">
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          {!editing?.fixed ? <Field label={t("secrets.name")} htmlFor="secret-name" hint={t("secrets.nameHint")}><input id="secret-name" className="input mono" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" placeholder="seerr:api_key" /></Field> : null}
          <Field label={t("secrets.value")} htmlFor="secret-value"><input id="secret-value" type="password" className="input" value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" /></Field>
        </div>
      </Modal>

      <DangerDialog open={Boolean(deleting)} onClose={() => setDeleting(null)} onConfirm={() => void remove()} pending={pending} icon={Trash2}
        title={t("secrets.deleteTitle", { name: deleting ?? "" })} description={t("secrets.deleteBody")} confirmLabel={t("action.delete")} />
    </Card>
  );
}
