"use client";

import { Download, QrCode, Smartphone } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button, Card, InlineState, Modal } from "@/components/mg/primitives";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";

interface Pairing {
  server_url?: string | null;
  server_url_source?: "setting" | "request";
  existing_key?: { id: number; prefix: string; created_at: string } | null;
}
interface PairingKey { key_id: number; key_prefix: string; qr_data_uri?: string; qr_text: string; qr_payload?: { url?: string }; message_nl?: string }

const APK_URL = "https://github.com/lvos04/media-guard-ultimate/releases/latest";

/** Android-app koppelen: nieuwe app-sleutel + QR-code (POST /app/pairing/key, docs/APP_API.md §4). */
export function AppPairing() {
  const { t } = useI18n();
  const info = useResource<Pairing>("/app/pairing");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PairingKey | null>(null);

  const create = async () => {
    setConfirm(false);
    setBusy(true);
    setError(null);
    try {
      setResult(await api.post<PairingKey>("/app/pairing/key", {}));
      void info.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const url = result?.qr_payload?.url ?? info.data?.server_url ?? null;

  return (
    <div data-testid="app-pairing">
      <Card title={t("app.pair")} sub={t("app.pairSub")} icon={Smartphone}
        actions={<a className="btn btn--sm btn--ghost" href={APK_URL} target="_blank" rel="noreferrer"><Download className="ico" aria-hidden="true" />{t("app.download")}</a>}>
        <div className="stack-sm">
          {info.data && info.data.server_url_source !== "setting" ? (
            <InlineState tone="info">{t("app.pairNoUrl")} <Link href="/instellingen#koppelingen">{t("set.sec.koppelingen")}</Link></InlineState>
          ) : null}
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          <div className="row wrap" style={{ gap: 8 }}>
            <Button variant="primary" icon={QrCode} pending={busy} onClick={() => (info.data?.existing_key ? setConfirm(true) : void create())} data-testid="app-pair-start">{t("app.pairStart")}</Button>
          </div>
        </div>
      </Card>
      <Modal open={confirm} onClose={() => setConfirm(false)} title={t("app.pair")} description={t("app.pairReplace")}
        footer={<><Button onClick={() => setConfirm(false)} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" onClick={() => void create()}>{t("app.pairStart")}</Button></>} />
      <Modal open={Boolean(result)} onClose={() => setResult(null)} title={t("app.pairTitle")} description={result?.message_nl ?? t("app.pairOnce")}
        footer={<Button variant="primary" onClick={() => setResult(null)}>{t("action.close")}</Button>}>
        {result ? (
          <div className="stack-sm" style={{ alignItems: "center", textAlign: "center" }} data-testid="app-pair-qr">
            {result.qr_data_uri
              // eslint-disable-next-line @next/next/no-img-element -- data-URI van de server, geen optimalisatie nodig
              ? <img src={result.qr_data_uri} alt={t("app.pairTitle")} width={240} height={240} style={{ width: 240, maxWidth: "100%", height: "auto", background: "#fff", padding: 8, borderRadius: 8 }} />
              : <pre className="mono" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 14 }}>{result.qr_text}</pre>}
            {url ? <p className="hint" style={{ margin: 0, overflowWrap: "anywhere" }}>{t("app.pairUrl", { url })}</p> : null}
            <p className="hint" style={{ margin: 0 }}>{t("app.pairOnce")}</p>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
