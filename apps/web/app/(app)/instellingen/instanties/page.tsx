"use client";

import { Inbox, Pause, Play, Route, Wrench } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { InstanceCard, InstanceLogo, InstanceStatusChip } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, LinkButton, Modal, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { Instance, LibraryKpis, OwnershipConflicts, WantedKpis } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useCoreEvents } from "@/lib/events";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export default function InstancesPage() {
  const { t } = useI18n();
  const { can } = useSession();
  const instances = useResource<Instance[]>("/instances");
  const kpis = useResource<LibraryKpis>("/library/kpis");
  const wanted = useResource<WantedKpis>("/wanted/kpis");
  const conflicts = useResource<OwnershipConflicts>("/ownership/conflicts");
  const { run, pending } = useAction();
  const [managing, setManaging] = useState<Instance | null>(null);
  const [form, setForm] = useState({ name: "", url: "", public_url: "", api_key: "" });
  useCoreEvents("instance", () => void instances.reload());

  const test = async (id: string) => { await run(`test-${id}`, "post", `/instances/${id}/test`, {}, t("instancesPage.testOk")); await instances.reload(); };
  const toggle = async (inst: Instance) => {
    await run(`toggle-${inst.id}`, "post", `/instances/${inst.id}/${inst.suspended ? "resume" : "suspend"}`, {}, inst.suspended ? t("instancesPage.resumed") : t("instancesPage.suspended"));
    await instances.reload();
  };
  const saveManage = async () => {
    if (!managing) return;
    const ok = await run("manage", "patch", `/instances/${managing.id}`, { name: form.name, url: form.url, public_url: form.public_url || null, ...(form.api_key ? { api_key: form.api_key } : {}) }, t("toast.saved"));
    if (ok !== undefined) { setManaging(null); await instances.reload(); }
  };

  return (
    <div className="stack">
      <div className="row-between wrap">
        <p className="t-2" style={{ fontSize: 14, maxWidth: 720 }}>{t("instancesPage.sub")}</p>
        <LinkButton href="/inrichten?tab=seerr" icon={Inbox}>{t("instancesPage.routeTest")}</LinkButton>
      </div>

      <ResourceView resource={instances} endpoint="GET /api/v1/instances" isEmpty={(r) => r.length === 0} empty={<StateView kind="empty" title={t("instancesPage.empty")} />}>
        {(rows) => (
          <div className="instance-grid">
            {rows.map((inst) => (
              <InstanceCard key={inst.id} instance={inst}
                stats={{ items: kpis.data?.instances.find((i) => i.id === inst.id)?.kpis.total, missing: wanted.data?.per_instance[inst.id]?.missing }}
                actions={<>
                  <Button size="sm" disabled={!can("operator")} pending={pending === `test-${inst.id}`} onClick={() => void test(inst.id)}>{t("action.test")}</Button>
                  <Button size="sm" icon={inst.suspended ? Play : Pause} disabled={!can("operator")} pending={pending === `toggle-${inst.id}`} onClick={() => void toggle(inst)}>{inst.suspended ? t("action.resume") : t("action.pause")}</Button>
                  <Button size="sm" variant="ghost" icon={Wrench} onClick={() => { setForm({ name: inst.name, url: inst.url, public_url: inst.public_url ?? "", api_key: "" }); setManaging(inst); }}>{t("action.manage")}</Button>
                </>} />
            ))}
          </div>
        )}
      </ResourceView>

      <div className="grid">
        <Card className="span-6" title={t("instancesPage.mapping")} icon={Route} actions={<Link className="link-more" href="/migratie">{t("nav.migration")}</Link>}>
          <ResourceView resource={conflicts} endpoint="GET /api/v1/ownership/conflicts" compact isEmpty={(r) => r.conflicts.length === 0} empty={<InlineState tone="success">{t("instancesPage.noConflicts")}</InlineState>}>
            {(r) => (
              <div className="list" style={{ margin: "0 -16px -16px" }}>
                {r.conflicts.map((c, i) => (
                  <div className="list-row" key={i}>
                    <div className="list-main"><div className="list-title">{String(c.title ?? c.canonical_id ?? `#${i + 1}`)}</div><div className="list-sub" style={{ whiteSpace: "normal" }}>{String(c.message_nl ?? "")}</div>
                      <details className="tech-details"><summary>{t("state.techDetails")}</summary><span className="mono" style={{ overflowWrap: "anywhere" }}>{JSON.stringify(c)}</span></details></div>
                    <StatusChip tone="warning">{t("instancesPage.conflict")}</StatusChip>
                  </div>
                ))}
              </div>
            )}
          </ResourceView>
        </Card>
        <Card className="span-6" title={t("instancesPage.seerrRouting")} icon={Inbox}>
          <div className="stack-sm">
            <InlineState>{t("live.seerrSub")}</InlineState>
            <LinkButton href="/inrichten?tab=seerr" size="sm">{t("live.seerr")}</LinkButton>
          </div>
        </Card>
      </div>

      <Modal open={Boolean(managing)} onClose={() => setManaging(null)} title={managing?.name ?? ""} icon={Wrench}
        footer={<><Button onClick={() => setManaging(null)}>{t("action.cancel")}</Button><Button variant="primary" pending={pending === "manage"} disabled={!can("operator")} onClick={() => void saveManage()}>{t("action.save")}</Button></>}>
        {managing ? (
          <div className="stack">
            <div className="row"><InstanceLogo instance={managing} /><span className="mono">{managing.id}</span><InstanceStatusChip instance={managing} /></div>
            <Field label={t("instancesPage.name")} htmlFor="inst-name"><input id="inst-name" className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label={t("instancesPage.url")} htmlFor="inst-url"><input id="inst-url" className="input mono" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} /></Field>
            <Field label={t("instances.apiKey")} htmlFor="inst-key" hint={t("instancesPage.apiKeyHint")}><input id="inst-key" type="password" className="input" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} /></Field>
            <Field label={t("instancesPage.publicUrl")} htmlFor="inst-public" hint={t("instancesPage.publicUrlHint")}><input id="inst-public" className="input mono" value={form.public_url} onChange={(e) => setForm({ ...form, public_url: e.target.value })} /></Field>
            <dl className="kv">
              <dt>{t("instancesPage.shadow")}</dt><dd>{managing.shadow_mode ? t("settings.yes") : t("settings.no")}</dd>
              <dt>{t("instancesPage.hookPolicy")}</dt><dd className="mono">{managing.hook_fail_policy ?? "—"}</dd>
            </dl>
            <InlineState tone="info">{t("instancesPage.deleteNote")}</InlineState>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
