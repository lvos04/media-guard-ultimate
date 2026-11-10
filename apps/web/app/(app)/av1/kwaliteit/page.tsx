"use client";

import { CircleCheck, CircleX, FlaskConical, Gauge, ScanEye, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { errorText, fileName, folderOf, useEncoder } from "@/components/av1/use-encoder";
import { KpiCard } from "@/components/mg/domain";
import { Button, Card, Field, InlineState, StatusChip, type Tone } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { mutation } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import type { DecisionItem, DolbyVisionItem, DolbyVisionOverview, Profile, QualityTrial, Verification } from "@/types";

interface QualityStatus { vmaf_beschikbaar: boolean; vmaf_ingeschakeld: boolean; uitleg: string }

const DV_TONE: Record<DolbyVisionItem["controle_status"], Tone> = {
  geverifieerd: "success", afgekeurd: "danger", wacht_op_resultaat: "accent", veilig_overgeslagen: "neutral", niet_ingepland: "neutral",
};

function Check({ ok, label }: { ok: boolean; label: string }) {
  return <StatusChip tone={ok ? "success" : "danger"} icon={ok ? CircleCheck : CircleX}>{label}</StatusChip>;
}

function Compare({ jobId }: { jobId: number }) {
  const { t } = useI18n();
  const [pos, setPos] = useState(50);
  const base = `/api/v1/encoder/kwaliteit/jobs/${jobId}/frame`;
  return (
    <div className="stack-sm">
      <div className="compare">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={`${base}/resultaat`} alt={t("av1.q.result")} loading="lazy" />
        <div className="compare-top" style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={`${base}/bron`} alt={t("av1.q.source")} loading="lazy" />
        </div>
        <span className="compare-line" style={{ left: `calc(${pos}% - 1px)` }} aria-hidden="true" />
        <span className="compare-tag" style={{ left: 8 }}>{t("av1.q.source")}</span>
        <span className="compare-tag" style={{ right: 8 }}>{t("av1.q.result")}</span>
      </div>
      <input className="range" type="range" min={0} max={100} value={pos} aria-label={t("av1.q.compareSlider")} onChange={(e) => setPos(Number(e.target.value))} />
    </div>
  );
}

export default function QualityPage() {
  const { t, bytes, num } = useI18n();
  const { can } = useSession();
  const status = useEncoder<QualityStatus>("/kwaliteit/status");
  const rows = useEncoder<Verification[]>("/kwaliteit/verificaties", 60_000);
  const dv = useEncoder<DolbyVisionOverview>("/kwaliteit/dolby-vision", 60_000);
  const profiles = useEncoder<Profile[]>("/profielen");
  const files = useEncoder<{ items: DecisionItem[] }>("/besluiten/items?per_pagina=500");
  const [profileId, setProfileId] = useState(0);
  const [fileId, setFileId] = useState(0);
  const pid = profileId || profiles.data?.[0]?.id || 0;
  const fid = fileId || files.data?.items[0]?.bestand_id || 0;
  const trials = useEncoder<QualityTrial[]>(pid ? `/profielen/${pid}/proefdraaien` : null, 15_000);
  const [msg, setMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [open, setOpen] = useState<number | null>(null);

  const list = rows.data ?? [];
  const passed = list.filter((r) => r.geslaagd).length;
  const vmafScores = list.map((r) => r.vmaf_score).filter((v): v is number => typeof v === "number");
  const avgVmaf = vmafScores.length ? vmafScores.reduce((a, b) => a + b, 0) / vmafScores.length : null;

  const startTrial = async () => {
    setPending(true);
    try {
      await mutation(`/profielen/${pid}/proefdraai`, "POST", { bestand_id: fid, crf_waarden: [27, 29, 31] });
      setMsg({ tone: "success", text: t("av1.q.trialStarted") });
      window.setTimeout(() => void trials.reload(), 4000);
    } catch (e) {
      setMsg({ tone: "danger", text: t("av1.failed", { message: errorText(e) }) });
    } finally {
      setPending(false);
    }
  };

  const dvText = (it: DolbyVisionItem) => it.controle_status === "geverifieerd" ? t("av1.q.dvVerified", { from: it.dv_bron ?? "—", to: it.dv_resultaat ?? "—" })
    : it.controle_status === "afgekeurd" ? it.verificatie_toelichting ?? t("av1.q.dvRejected")
      : it.controle_status === "wacht_op_resultaat" ? t("av1.q.dvWaiting")
        : it.controle_status === "veilig_overgeslagen" ? t("av1.q.dvSkipped") : t("av1.q.dvNotPlanned");

  return (
    <div className="stack">
      <section className="kpi-grid">
        <KpiCard icon={ShieldCheck} label={t("av1.q.verified")} value={num(passed)} unavailable={!rows.data} tone="success" meta={t("av1.q.verifiedOf", { passed: num(passed), total: num(list.length) })} />
        <KpiCard icon={CircleX} label={t("av1.q.rejected")} value={num(list.length - passed)} unavailable={!rows.data} tone={list.length - passed ? "danger" : undefined} meta={t("av1.kpi.errorsMeta")} />
        <KpiCard icon={Gauge} label={t("av1.q.vmaf")} value={avgVmaf === null ? t("chart.noReading") : num(avgVmaf, 1)} unavailable={!status.data} meta={status.data?.vmaf_beschikbaar ? (status.data.vmaf_ingeschakeld ? t("av1.q.vmafOn") : t("av1.q.vmafDisabled")) : (status.data?.uitleg || t("av1.q.vmafOff"))} />
        <KpiCard icon={ScanEye} label={t("av1.q.dvSources")} value={num(dv.data?.totaal ?? 0)} unavailable={!dv.data} meta={dv.data ? `P5 ${dv.data.profielen["5"]} · P7 ${dv.data.profielen["7"]} · P8 ${dv.data.profielen["8"]}` : undefined} />
      </section>

      <div className="grid">
        <Card className="span-12" title={t("av1.q.results")} icon={ShieldCheck} flush>
          {!rows.data && rows.error ? <div className="card-body"><StateView kind="offline" compact title={t("av1.engineDownTitle")} body={t("av1.engineDownBody")} /></div>
            : !rows.data ? <div className="card-body"><StateView kind="loading" /></div>
              : !list.length ? <div className="card-body"><StateView kind="empty" compact title={t("av1.q.resultsEmpty")} /></div>
                : (
                  <div className="list">
                    {list.slice(0, 50).map((r) => (
                      <div key={r.job_id}>
                        <div className="list-row list-row--hover" style={{ cursor: "pointer", flexWrap: "wrap" }} onClick={() => setOpen(open === r.job_id ? null : r.job_id)}>
                          <span className={`list-icon ${r.geslaagd ? "list-icon--success" : "list-icon--danger"}`}>{r.geslaagd ? <CircleCheck className="ico" aria-hidden="true" /> : <CircleX className="ico" aria-hidden="true" />}</span>
                          <div className="list-main"><div className="list-title" title={r.pad}>{fileName(r.pad)}</div><div className="list-sub">#{r.job_id} · {folderOf(r.pad)}</div></div>
                          <div className="list-aside">
                            {r.vmaf_score !== null ? <StatusChip tone={r.vmaf_score >= 95 ? "success" : r.vmaf_score >= 90 ? "warning" : "danger"} icon={null}>{t("av1.q.vmafScore", { score: num(r.vmaf_score, 1) })}</StatusChip> : null}
                            <StatusChip tone={r.geslaagd ? "success" : "danger"}>{r.geslaagd ? t("av1.q.verified") : t("av1.q.rejected")}</StatusChip>
                          </div>
                        </div>
                        {open === r.job_id ? (
                          <div className="card-body stack-sm" style={{ borderTop: "1px solid var(--mg-border-soft)", background: "var(--mg-sunken)" }}>
                            <div className="checks">
                              <Check ok={r.hdr_bron === r.hdr_resultaat} label={r.hdr_bron ? t("av1.q.check.hdr") : t("av1.q.check.sdr")} />
                              <Check ok={Boolean(r.md_gelijk)} label={t("av1.q.check.md")} />
                              <Check ok={r.dv_bron === r.dv_resultaat || r.dv_resultaat === 10} label={r.dv_bron ? t("av1.q.check.dv", { from: r.dv_bron, to: r.dv_resultaat || "—" }) : t("av1.q.check.noDv")} />
                              <Check ok={r.audio_resultaat > 0 && r.audio_resultaat <= r.audio_bron} label={t("av1.q.check.audio", { from: r.audio_bron, to: r.audio_resultaat })} />
                              <Check ok={r.attachments_resultaat >= r.attachments_bron} label={t("av1.q.check.attachments", { n: r.attachments_resultaat })} />
                            </div>
                            {r.toelichting ? <p className="t-2" style={{ margin: 0, fontSize: 14 }}>{r.toelichting}</p> : null}
                            {r.bron_frame && r.resultaat_frame ? <><span className="label" style={{ margin: 0 }}>{t("av1.q.compare")}</span><Compare jobId={r.job_id} /></> : <p className="hint" style={{ margin: 0 }}>{t("av1.q.noFrames")}</p>}
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                )}
        </Card>

        {files.data?.items.length ? <Card className="span-6" title={t("av1.q.trialTitle")} sub={t("av1.q.trialSub")} icon={FlaskConical}>
          <div className="stack-sm">
            <div className="form-grid">
              <Field label={t("av1.q.trialProfile")} htmlFor="trial-profile">
                <select id="trial-profile" className="select" value={pid} onChange={(e) => setProfileId(Number(e.target.value))}>
                  {(profiles.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.naam}</option>)}
                </select>
              </Field>
              <Field label={t("av1.q.trialFile")} htmlFor="trial-file">
                <select id="trial-file" className="select" value={fid} onChange={(e) => setFileId(Number(e.target.value))}>
                  {(files.data?.items ?? []).map((f) => <option key={f.id} value={f.bestand_id}>{fileName(f.pad)}</option>)}
                </select>
              </Field>
            </div>
            {can("operator") ? <div><Button variant="primary" icon={FlaskConical} disabled={!pid || !fid} pending={pending} onClick={() => void startTrial()}>{t("av1.q.trialStart")}</Button></div> : null}
            {msg ? <InlineState tone={msg.tone} role="status">{msg.text}</InlineState> : null}
            {trials.data?.length ? (
              <div className="table-wrap">
                <table className="table table--compact">
                  <thead><tr><th>{t("av1.q.trialCrf")}</th><th className="num">{t("av1.q.trialSize")}</th><th className="num">{t("av1.q.trialTime")}</th><th className="num">VMAF</th><th>{t("av1.q.trialStatus")}</th></tr></thead>
                  <tbody>{trials.data.map((tr) => (
                    <tr key={`${tr.taak_id}-${tr.crf}`}><td className="num">{tr.crf}</td><td className="num">{bytes(tr.grootte)}</td><td className="num">{tr.duur_encode_seconden ? `${num(tr.duur_encode_seconden)} s` : "—"}</td><td className="num">{num(tr.vmaf_score, 1)}</td>
                      <td><StatusChip tone={tr.status === "klaar" ? "success" : "danger"}>{tr.status}</StatusChip></td></tr>
                  ))}</tbody>
                </table>
              </div>
            ) : null}
          </div>
        </Card> : null}

        <Card className={files.data?.items.length ? "span-6" : "span-12"} title={t("av1.q.dvTitle")} sub={t("av1.q.dvSub")} icon={ScanEye} flush>
          {!dv.data ? <div className="card-body"><StateView kind="loading" compact /></div>
            : !dv.data.items.length ? <div className="card-body"><StateView kind="empty" compact title={t("av1.q.dvNone")} /></div>
              : (
                <div className="list" style={{ maxHeight: 420, overflow: "auto" }}>
                  {dv.data.items.map((it) => (
                    <div className="list-row" key={it.bestand_id}>
                      <div className="list-main"><div className="list-title" title={it.pad}>{fileName(it.pad)}</div>
                        <div className="list-sub">{it.dv_profile === 5 ? t("av1.q.dvP5") : it.dv_profile === 7 ? t("av1.q.dvP7") : it.dv_profile === 8 ? t("av1.q.dvP8") : t("av1.q.profile", { n: it.dv_profile })}</div></div>
                      <div className="list-aside"><StatusChip tone={DV_TONE[it.controle_status]}>{dvText(it)}</StatusChip></div>
                    </div>
                  ))}
                </div>
              )}
        </Card>
      </div>
    </div>
  );
}
