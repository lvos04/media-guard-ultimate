"use client";

import { KeyRound, Lock, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, Modal, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api, ApiError } from "@/lib/api/client";
import type { SecretRow } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const POOL_SECRET = "hub:poolstatus";
type PoolBinding = { origin: string; revision: string };

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
  const [binding, setBinding] = useState<PoolBinding | null>();
  const target = editing?.fixed ? editing.name : name.trim();
  const isPool = editing !== null && target === POOL_SECRET;

  useEffect(() => {
    if (!isPool) return;
    const controller = new AbortController();
    // Houd de getoonde keuze vast zolang de beheerder deze waarde invoert.
    api.get<PoolBinding | null>("/settings/pool-hub-binding", undefined, controller.signal)
      .then((chosen) => { if (!controller.signal.aborted) setBinding(chosen); })
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      });
    return () => controller.abort();
  }, [isPool]);

  if (!can("admin")) return <InlineState tone="warning">{t("users.adminOnly")}</InlineState>;

  const closeEditor = () => {
    setEditing(null);
    setName("");
    setValue("");
    setBinding(undefined);
    setError(null);
  };

  const rename = (next: string) => {
    if ((next.trim() === POOL_SECRET) !== isPool) {
      setValue("");
      setBinding(undefined);
      setError(null);
    }
    setName(next);
  };

  const save = async () => {
    if (isPool && !binding) return;
    setPending(true);
    setError(null);
    try {
      const body = isPool && binding ? { value, pool_revision: binding.revision } : { value };
      await api.put(`/settings/secrets/${encodeURIComponent(target)}`, body);
      toast.push("success", t("secrets.saved", { name: target }));
      closeEditor();
      await secrets.reload();
    } catch (e) {
      if (isPool && e instanceof ApiError && e.status === 409) {
        closeEditor();
        toast.push("danger", t("secrets.poolChanged"));
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
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

      <Modal open={Boolean(editing)} onClose={() => { if (!pending) closeEditor(); }} title={editing?.fixed ? `${t("secrets.replace")} · ${editing.name}` : t("secrets.add")}
        footer={<><Button disabled={pending} onClick={closeEditor}>{t("action.cancel")}</Button><Button variant="primary" pending={pending} disabled={!value || !target || (isPool && !binding)} onClick={() => void save()}>{t("action.save")}</Button></>}>
        <div className="stack">
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          {!editing?.fixed ? <Field label={t("secrets.name")} htmlFor="secret-name" hint={t("secrets.nameHint")}><input id="secret-name" className="input mono" value={name} onChange={(e) => rename(e.target.value)} autoComplete="off" placeholder="seerr:api_key" /></Field> : null}
          {isPool && !error ? <InlineState tone={binding === null ? "warning" : "info"}>
            {binding ? t("secrets.poolTarget", { origin: binding.origin }) : binding === null ? t("secrets.poolMissing") : t("state.loading")}
          </InlineState> : null}
          <Field label={t("secrets.value")} htmlFor="secret-value"><input id="secret-value" type="password" className="input" disabled={isPool && !binding} value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" /></Field>
        </div>
      </Modal>

      <DangerDialog open={Boolean(deleting)} onClose={() => setDeleting(null)} onConfirm={() => void remove()} pending={pending} icon={Trash2}
        title={t("secrets.deleteTitle", { name: deleting ?? "" })} description={t("secrets.deleteBody")} confirmLabel={t("action.delete")} />
    </Card>
  );
}
