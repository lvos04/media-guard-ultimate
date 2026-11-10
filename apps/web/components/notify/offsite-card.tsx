"use client";

import { CloudUpload } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Card, Field, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type Kind = "off" | "path" | "webdav" | "s3" | "email";
interface Offsite {
  config: { kind: Kind; path: string; webdav_url: string; webdav_user: string; s3_endpoint: string; s3_region: string; s3_bucket: string; s3_prefix: string; s3_access_key: string; email_channel: number; keep: number; secrets_set: Record<string, boolean> };
  target: string;
  history: Array<{ id: number; bundle_name: string; target_kind: string; status: "ok" | "failed"; detail: string | null; created_at: string }>;
  last_ok: { bundle_name: string; created_at: string } | null;
  latest_bundle: string | null;
  pending: boolean;
  kinds: Array<{ value: Kind; label_nl: string }>;
  email_channels: Array<{ id: number; name: string }>;
}

/** Systeem → Back-ups: herstelbundel automatisch buiten de NAS (rapport 5, M11). */
export function OffsiteCard() {
  const { t, dateTime } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const res = useResource<Offsite>(can("admin") ? "/notify/offsite" : null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const c = res.data?.config;
    if (!c) return;
    setForm({ kind: c.kind, path: c.path, webdav_url: c.webdav_url, webdav_user: c.webdav_user, s3_endpoint: c.s3_endpoint, s3_region: c.s3_region,
      s3_bucket: c.s3_bucket, s3_prefix: c.s3_prefix, s3_access_key: c.s3_access_key, email_channel: String(c.email_channel || ""), keep: String(c.keep), webdav_password: "", s3_secret_key: "" });
  }, [res.data]);

  if (!can("admin")) return null;
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });

  const save = async () => {
    setBusy("save");
    setError(null);
    const body: Record<string, unknown> = { ...form, email_channel: Number(form.email_channel || 0), keep: Number(form.keep || 7) };
    if (!form.webdav_password) delete body.webdav_password;
    if (!form.s3_secret_key) delete body.s3_secret_key;
    try { await api.put("/notify/offsite", body); toast.push("success", t("notify.saved")); await res.reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  };
  const run = async () => {
    setBusy("run");
    setError(null);
    try {
      const r = await api.post<{ ok: boolean; error?: string }>("/notify/offsite/run");
      if (r.ok) toast.push("success", t("offsite.ok")); else setError(t("offsite.fail", { error: r.error ?? "" }));
      await res.reload();
    } catch (e) {
      // niet stil laten falen (punt 39)
      const text = t("offsite.fail", { error: e instanceof Error ? e.message : String(e) });
      setError(text);
      toast.push("danger", text);
    } finally { setBusy(null); }
  };

  const input = (key: string, label: string, opts: { hint?: string; type?: string } = {}) => (
    <Field label={label} htmlFor={`offsite-${key}`} hint={opts.hint}>
      <input id={`offsite-${key}`} className="input" type={opts.type ?? "text"} autoComplete="off" value={form[key] ?? ""} onChange={set(key)} />
    </Field>
  );
  const kind = (form.kind ?? "off") as Kind;

  return (
    <Card className="span-6" title={t("offsite.title")} sub={t("offsite.sub")} icon={CloudUpload} id="offsite">
      <ResourceView resource={res} endpoint="GET /api/v1/notify/offsite">
        {(d) => (
          <div className="stack" data-testid="offsite-card">
            {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
            <div className="list-sub">
              {d.last_ok ? <StatusChip tone="success">{t("offsite.last", { name: d.last_ok.bundle_name, when: dateTime(d.last_ok.created_at) })}</StatusChip> : t("offsite.none")}
            </div>
            {d.pending ? <InlineState tone="info">{t("offsite.pending")}</InlineState> : null}
            <Field label={t("offsite.kind")} htmlFor="offsite-kind">
              <select id="offsite-kind" className="input" value={kind} onChange={set("kind")}>
                {d.kinds.map((k) => <option key={k.value} value={k.value}>{k.label_nl}</option>)}
              </select>
            </Field>
            {kind === "path" ? input("path", t("offsite.path"), { hint: t("offsite.pathHint") }) : null}
            {kind === "webdav" ? (<>
              {input("webdav_url", t("offsite.webdavUrl"), { hint: t("offsite.webdavHint") })}
              {input("webdav_user", t("offsite.webdavUser"))}
              {input("webdav_password", t("offsite.webdavPassword"), { type: "password", hint: d.config.secrets_set.webdav_password ? t("notify.secretSet") : undefined })}
            </>) : null}
            {kind === "s3" ? (<>
              {input("s3_endpoint", t("offsite.s3Endpoint"), { hint: t("offsite.s3Hint") })}
              {input("s3_region", t("offsite.s3Region"))}
              {input("s3_bucket", t("offsite.s3Bucket"))}
              {input("s3_prefix", t("offsite.s3Prefix"))}
              {input("s3_access_key", t("offsite.s3Access"))}
              {input("s3_secret_key", t("offsite.s3Secret"), { type: "password", hint: d.config.secrets_set.s3_secret_key ? t("notify.secretSet") : undefined })}
            </>) : null}
            {kind === "email" ? (
              d.email_channels.length ? (
                <Field label={t("offsite.emailChannel")} htmlFor="offsite-email" hint={t("offsite.emailHint")}>
                  <select id="offsite-email" className="input" value={form.email_channel ?? ""} onChange={set("email_channel")}>
                    <option value="">—</option>
                    {d.email_channels.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </Field>
              ) : <InlineState tone="warning">{t("offsite.noEmail")} {t("offsite.emailHint")}</InlineState>
            ) : null}
            {kind === "path" ? input("keep", t("offsite.keep"), { type: "number" }) : null}
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <Button variant="primary" pending={busy === "save"} onClick={() => void save()} data-testid="offsite-save">{t("offsite.save")}</Button>
              <Button pending={busy === "run"} disabled={d.config.kind === "off"} onClick={() => void run()} data-testid="offsite-run">{t("offsite.run")}</Button>
            </div>
            {d.history.length ? (
              <details className="tech-details">
                <summary>{t("offsite.history")}</summary>
                <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                  {d.history.map((h) => <li key={h.id} className="list-sub" style={{ whiteSpace: "normal" }}>{dateTime(h.created_at)} · {h.bundle_name} · {h.status === "ok" ? t("offsite.ok") : t("offsite.fail", { error: h.detail ?? "" })}</li>)}
                </ul>
              </details>
            ) : null}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
