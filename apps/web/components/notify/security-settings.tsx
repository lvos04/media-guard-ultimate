"use client";

import { Copy, KeyRound, LogOut, ShieldCheck, ShieldOff } from "lucide-react";
import { useState } from "react";

import { Button, Card, Field, InlineState, Modal, StatusChip, Switch } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

interface SecMe { totp_enabled: boolean; recovery_codes_left: number; enabled_at: string | null; required: boolean; require_2fa_admins: boolean; notify_new_login: boolean; nudge?: boolean }
interface SessionRow { id: string; username: string; created_at: string; last_seen_at: string; ip: string | null; device_nl: string; current: boolean }
interface LoginRow { id: number; username: string; ts: string; ip: string | null; device_nl: string; success: boolean; method_nl: string; reason: string | null }

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Instellingen → Beveiliging: 2FA, beleid, ingelogde apparaten en aanmeldgeschiedenis. */
export function SecuritySettings() {
  const { t, dateTime, relative } = useI18n();
  const { can, refresh } = useSession();
  const toast = useToast();
  const isAdmin = can("admin");
  const [allUsers, setAllUsers] = useState(false);
  const me = useResource<SecMe>("/security/me");
  const sessions = useResource<{ items: SessionRow[] }>("/security/sessions", allUsers ? { all_users: true } : undefined);
  const logins = useResource<{ items: LoginRow[] }>("/security/logins", allUsers ? { all_users: true, limit: 50 } : { limit: 50 });
  const [setup, setSetup] = useState<{ secret: string; qr_svg: string | null } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [disabling, setDisabling] = useState(false);
  const [newCodes, setNewCodes] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pw, setPw] = useState({ current: "", next: "", again: "" });
  const [pwError, setPwError] = useState<string | null>(null);

  const act = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    try { await fn(); } catch (e) { setError(msg(e)); } finally { setBusy(null); }
  };

  const start = () => act("setup", async () => { setCode(""); setSetup(await api.post("/security/totp/setup")); });
  const enable = () => act("enable", async () => {
    const r = await api.post<{ recovery_codes: string[] }>("/security/totp/enable", { code });
    setSetup(null); setCodes(r.recovery_codes); await me.reload(); await refresh();
  });
  const disable = () => act("disable", async () => {
    await api.post("/security/totp/disable", { password }); setDisabling(false); setPassword(""); await me.reload(); await refresh();
  });
  const regen = () => act("regen", async () => {
    const r = await api.post<{ recovery_codes: string[] }>("/security/totp/recovery-codes", { code });
    setNewCodes(false); setCode(""); setCodes(r.recovery_codes); await me.reload();
  });
  const policy = (body: Record<string, boolean>) => act("policy", async () => { await api.put("/security/policy", body); await me.reload(); toast.push("success", t("notify.saved")); });
  const revoke = (id: string) => act(`rv-${id}`, async () => { await api.del(`/security/sessions/${id}`); toast.push("success", t("sec.loggedOut")); await sessions.reload(); });
  const revokeOthers = () => act("rv-others", async () => { await api.post("/security/sessions/revoke-others"); toast.push("success", t("sec.loggedOut")); await sessions.reload(); });
  const changePassword = async () => {
    if (pw.next !== pw.again) { setPwError(t("pw.mismatch")); return; }
    setBusy("pw");
    setPwError(null);
    try {
      const r = await api.post<{ message_nl?: string }>("/auth/me/password", { current_password: pw.current, new_password: pw.next });
      toast.push("success", r.message_nl ?? t("pw.done"));
      setPw({ current: "", next: "", again: "" });
      await sessions.reload();
    } catch (e) { setPwError(msg(e)); } finally { setBusy(null); }
  };
  const copyCodes = async () => {
    try { await navigator.clipboard.writeText((codes ?? []).join("\n")); toast.push("success", t("apikeys.created.copied")); } catch { toast.push("info", t("apikeys.created.copyFailed")); }
  };

  return (
    <div className="stack" data-testid="security-settings">
      {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      <ResourceView resource={me} endpoint="GET /api/v1/security/me">
        {(m) => (
          <>
            {m.required && !m.totp_enabled ? <InlineState tone="warning" role="alert">{t("sec.required")}</InlineState> : null}
            {m.nudge && !m.totp_enabled && !m.required ? <InlineState tone="info" icon={ShieldCheck}><strong>{t("sec.nudgeTitle")}</strong> {t("sec.nudgeBody")}</InlineState> : null}
            <Card title={t("sec.totp")} icon={ShieldCheck}
              actions={m.totp_enabled ? <StatusChip tone="success">{t("notify.enabled")}</StatusChip> : <StatusChip tone="neutral">{t("notify.disabled")}</StatusChip>}>
              <div className="stack">
                <p className="page-sub" style={{ margin: 0 }}>{m.totp_enabled ? t("sec.totpOn", { when: dateTime(m.enabled_at), count: m.recovery_codes_left }) : t("sec.totpOff")}</p>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  {!m.totp_enabled ? <Button variant="primary" icon={ShieldCheck} pending={busy === "setup"} onClick={() => void start()} data-testid="totp-start">{t("sec.totpStart")}</Button> : (
                    <>
                      <Button onClick={() => { setCode(""); setNewCodes(true); }}>{t("sec.newCodes")}</Button>
                      <Button variant="danger" icon={ShieldOff} onClick={() => { setPassword(""); setDisabling(true); }}>{t("sec.disable")}</Button>
                    </>
                  )}
                </div>
              </div>
            </Card>
            {isAdmin ? (
              <Card title={t("sec.policy")}>
                <div className="stack">
                  <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
                    <Switch checked={m.require_2fa_admins} label={t("sec.requireAdmins")} onChange={(v) => void policy({ require_2fa_admins: v })} />
                    <div><div className="list-title">{t("sec.requireAdmins")}</div><p className="hint" style={{ margin: 0 }}>{t("sec.requireAdminsHint")}</p></div>
                  </div>
                  <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
                    <Switch checked={m.notify_new_login} label={t("sec.notifyNew")} onChange={(v) => void policy({ notify_new_login: v })} />
                    <div><div className="list-title">{t("sec.notifyNew")}</div><p className="hint" style={{ margin: 0 }}>{t("sec.notifyNewHint")}</p></div>
                  </div>
                </div>
              </Card>
            ) : null}
          </>
        )}
      </ResourceView>

      <Card title={t("pw.title")} sub={t("pw.sub")} icon={KeyRound}>
        <form className="stack" onSubmit={(e) => { e.preventDefault(); void changePassword(); }} data-testid="own-password">
          {pwError ? <InlineState tone="danger" role="alert">{pwError}</InlineState> : null}
          <Field label={t("pw.current")} htmlFor="pw-current"><input id="pw-current" type="password" className="input" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
          <Field label={t("pw.new")} htmlFor="pw-new" hint={t("auth.passwordHint")}><input id="pw-new" type="password" className="input" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></Field>
          <Field label={t("pw.repeat")} htmlFor="pw-again"><input id="pw-again" type="password" className="input" autoComplete="new-password" value={pw.again} onChange={(e) => setPw({ ...pw, again: e.target.value })} /></Field>
          <div className="row"><Button type="submit" variant="primary" pending={busy === "pw"} disabled={!pw.current || pw.next.length < 12 || !pw.again}>{t("pw.save")}</Button></div>
        </form>
      </Card>

      <Card title={t("sec.sessions")} sub={t("sec.sessionsSub")} flush
        actions={<div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          {isAdmin ? <label className="row" style={{ gap: 6 }}><Switch checked={allUsers} label={t("sec.allUsers")} onChange={setAllUsers} /><span>{t("sec.allUsers")}</span></label> : null}
          <Button size="sm" icon={LogOut} pending={busy === "rv-others"} onClick={() => void revokeOthers()}>{t("sec.logoutOthers")}</Button>
        </div>}>
        <ResourceView resource={sessions} endpoint="GET /api/v1/security/sessions">
          {(d) => (
            <div className="list" data-testid="session-list">
              {d.items.map((s) => (
                <div key={s.id} className="list-row" style={{ flexWrap: "wrap", gap: 8 }}>
                  <div className="list-main">
                    <div className="list-title">{s.device_nl}{allUsers ? ` · ${s.username}` : ""} {s.current ? <StatusChip tone="accent">{t("sec.thisDevice")}</StatusChip> : null}</div>
                    <div className="list-sub">{[s.ip, t("sec.lastSeen", { when: relative(s.last_seen_at) })].filter(Boolean).join(" · ")}</div>
                  </div>
                  {!s.current ? <Button size="sm" variant="danger" icon={LogOut} pending={busy === `rv-${s.id}`} onClick={() => void revoke(s.id)}>{t("sec.logout")}</Button> : null}
                </div>
              ))}
            </div>
          )}
        </ResourceView>
      </Card>

      <Card title={t("sec.logins")} flush>
        <ResourceView resource={logins} endpoint="GET /api/v1/security/logins" isEmpty={(d) => !d.items.length} empty={<div className="list-row"><div className="list-sub">{t("sec.noLogins")}</div></div>}>
          {(d) => (
            <div className="list">
              {d.items.map((l) => (
                <div key={l.id} className="list-row" style={{ flexWrap: "wrap", gap: 8 }}>
                  <div className="list-main">
                    <div className="list-title">{dateTime(l.ts)} · {l.username}</div>
                    <div className="list-sub">{[l.method_nl, l.device_nl, l.ip, l.reason].filter(Boolean).join(" · ")}</div>
                  </div>
                  <StatusChip tone={l.success ? "success" : "danger"}>{l.success ? t("sec.loginOk") : t("sec.loginFail")}</StatusChip>
                </div>
              ))}
            </div>
          )}
        </ResourceView>
      </Card>

      <Modal open={Boolean(setup)} onClose={() => setSetup(null)} title={t("sec.totpStart")} icon={ShieldCheck}
        footer={<><Button onClick={() => setSetup(null)}>{t("action.cancel")}</Button><Button variant="primary" pending={busy === "enable"} disabled={code.replace(/\D/g, "").length !== 6} onClick={() => void enable()} data-testid="totp-confirm">{t("sec.confirm")}</Button></>}>
        <div className="stack">
          <p className="page-sub" style={{ margin: 0 }}>{t("sec.totpScan")}</p>
          {/* eslint-disable-next-line @next/next/no-img-element -- QR als data-URI */}
          {setup?.qr_svg ? <img src={setup.qr_svg} alt="" width={180} height={180} style={{ background: "#fff", borderRadius: 8 }} data-testid="totp-qr" /> : null}
          <div className="list-sub">{t("sec.totpManual")}: <code className="mono" style={{ overflowWrap: "anywhere" }} data-testid="totp-secret">{setup?.secret}</code></div>
          <Field label={t("sec.code")} htmlFor="totp-code">
            <input id="totp-code" className="input" inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
        </div>
      </Modal>

      <Modal open={Boolean(codes)} onClose={() => setCodes(null)} title={t("sec.codesTitle")} icon={ShieldCheck}
        footer={<><Button icon={Copy} onClick={() => void copyCodes()}>{t("sec.copyCodes")}</Button><Button variant="primary" onClick={() => setCodes(null)} data-testid="codes-done">{t("sec.codesDone")}</Button></>}>
        <div className="stack">
          <InlineState tone="warning">{t("sec.codesBody")}</InlineState>
          <pre className="mono" style={{ columns: 2, margin: 0 }} data-testid="recovery-codes">{(codes ?? []).join("\n")}</pre>
        </div>
      </Modal>

      <Modal open={newCodes} onClose={() => setNewCodes(false)} title={t("sec.newCodes")} icon={ShieldCheck}
        footer={<><Button onClick={() => setNewCodes(false)}>{t("action.cancel")}</Button><Button variant="primary" pending={busy === "regen"} onClick={() => void regen()}>{t("sec.confirm")}</Button></>}>
        <Field label={t("sec.code")} htmlFor="regen-code"><input id="regen-code" className="input" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></Field>
      </Modal>

      <Modal open={disabling} onClose={() => setDisabling(false)} title={t("sec.disableTitle")} icon={ShieldOff} danger
        footer={<><Button onClick={() => setDisabling(false)}>{t("action.cancel")}</Button><Button variant="danger" pending={busy === "disable"} disabled={!password} onClick={() => void disable()}>{t("sec.disable")}</Button></>}>
        <div className="stack">
          <p className="page-sub" style={{ margin: 0 }}>{t("sec.disableBody")}</p>
          <Field label={t("sec.password")} htmlFor="disable-pw"><input id="disable-pw" type="password" className="input" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        </div>
      </Modal>
    </div>
  );
}
