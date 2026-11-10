"use client";

import { Gauge, RefreshCw } from "lucide-react";
import { useState } from "react";

import { errorText, useEncoder } from "@/components/av1/use-encoder";
import { Button, Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { mutation } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/** Engine: GET /gpu-plekken (laatste meting + CPU-plekkenproef), POST /gpu-plekken/meten. */
export interface GpuSlots {
  gpu: { gemeten_op?: string | null; resultaten?: Array<{ plekken: number; ok?: boolean; fps?: number | null; vram_mb?: number | null; fout?: string | null }>; beste?: number | null; uitleg_nl?: string | null } | null;
  bezig?: boolean;
  gpu_ingesteld?: number; gpu_effectief?: number;
  cpu?: { plekken?: number; uitleg_nl?: string | null } | null;
  cpu_ingesteld?: number;
}

/**
 * Rapport 7, §4.5: meet 1, 2 en 4 GPU-encodes tegelijk op dezelfde proefstukken en kies het
 * hoogste totaal aantal fps. Oudere engines zonder dit endpoint: de kaart blijft weg.
 */
export function GpuBenchmarkSection() {
  const { t, num, bytes, dateTime } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const res = useEncoder<GpuSlots>("/gpu-plekken", 15_000);
  const [pending, setPending] = useState(false);
  if (!res.data && res.error) return null;
  const d = res.data;
  const b = d?.gpu ?? null;
  const rows = b?.resultaten ?? [];
  const running = Boolean(d?.bezig);
  const start = async () => {
    setPending(true);
    try {
      await mutation("/gpu-plekken/meten", "POST");
      toast.push("success", t("gpu37.started"));
      await res.reload();
    } catch (e) {
      toast.push("danger", t("av1.failed", { message: errorText(e) }));
    } finally { setPending(false); }
  };
  return (
    <Card title={t("gpu37.title")} sub={t("gpu37.sub")} icon={Gauge}
      actions={can("operator") ? <Button size="sm" icon={RefreshCw} pending={pending || running} disabled={running} onClick={() => void start()} data-testid="gpu-benchmark-run">{t("gpu37.run")}</Button> : undefined}>
      <div className="stack-sm" data-testid="gpu-benchmark">
        {!d ? <StateView kind="loading" compact />
          : running ? <InlineState tone="info" role="status">{t("gpu37.running")}</InlineState>
            : !rows.length ? <p className="t-2" style={{ margin: 0, fontSize: 14 }}>{t("gpu37.none")}</p> : (
              <>
                <div className="table-wrap">
                  <table className="table table--compact">
                    <thead><tr><th>{t("gpu37.slots")}</th><th className="num">{t("gpu37.fps")}</th><th className="num">{t("gpu37.vram")}</th></tr></thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.plekken}>
                          <td className="cell-primary">{num(r.plekken)} {b?.beste === r.plekken ? <StatusChip tone="success">{t("gpu37.best")}</StatusChip> : null}{r.ok === false && r.fout ? <span className="t-2" style={{ fontSize: 14 }}> · {r.fout}</span> : null}</td>
                          <td className="num">{typeof r.fps === "number" ? num(r.fps, 1) : "—"}</td>
                          <td className="num">{r.vram_mb ? bytes(r.vram_mb * 1024 * 1024) : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="t-2" style={{ margin: 0, fontSize: 14 }}>
                  {b?.gemeten_op ? t("gpu37.measuredAt", { when: dateTime(b.gemeten_op) }) : null}
                  {b?.beste ? <> · {t("gpu37.advice", { n: num(b.beste) })}{d?.gpu_effectief === b.beste ? ` (${t("gpu37.applied")})` : ""}</> : null}
                </p>
              </>
            )}
        {b?.uitleg_nl ? <p className="t-2" style={{ margin: 0, fontSize: 14 }}>{b.uitleg_nl}</p> : null}
        {d?.cpu?.uitleg_nl ? <p className="t-2" style={{ margin: 0, fontSize: 14 }} data-testid="cpu-slot-proof">{d.cpu.uitleg_nl}</p> : null}
      </div>
    </Card>
  );
}
