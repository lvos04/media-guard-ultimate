"use client";

import { FolderPen } from "lucide-react";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { JobProgress } from "@/components/mg/job-progress";
import { Button, InlineState } from "@/components/mg/primitives";
import { useToast } from "@/components/mg/toast";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export interface FolderTypo {
  instance_id: string;
  instance_name: string;
  root_folder_id: number;
  path: string;
  suggested_path: string;
  items: number;
  free_space_ok: boolean;
  /** waarom hernoemen nu niet kan (proefmodus, gepauzeerd); null = mag */
  blocked_reason_nl?: string | null;
}

const base = (p: string) => p.replace(/\/+$/, "").split("/").pop() ?? p;

/**
 * Hoofdmappen met een waarschijnlijke typfout ("Anime Fims"). Hernoemen gebeurt alleen na jouw
 * bevestiging; Sonarr/Radarr verplaatsen dan zelf de series/films, er wordt niets verwijderd.
 */
export function FolderTypos() {
  const { t, num } = useI18n();
  const { can } = useSession();
  const typos = useResource<FolderTypo[]>("/storage/typos", undefined, 300_000);
  const toast = useToast();
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<FolderTypo | null>(null);
  const [jobs, setJobs] = useState<Record<string, number>>({});
  // oudere core zonder dit endpoint: niets tonen
  if (!typos.data?.length) return null;
  const key = (f: FolderTypo) => `${f.instance_id}:${f.root_folder_id}`;
  const fix = async (f: FolderTypo) => {
    setPending(`fix-${key(f)}`);
    setError(null);
    try {
      // 400 geen bevestiging · 404 onbekend/geen typfout · 409 proefmodus/gepauzeerd/te weinig ruimte · 422 ongeldig doelpad
      const r = await api.post<{ job_id: number }>(`/storage/typos/${encodeURIComponent(f.instance_id)}/${f.root_folder_id}/fix`, { confirm: true, target_path: f.suggested_path });
      toast.push("success", t("typo.started"));
      if (r?.job_id) setJobs((j) => ({ ...j, [key(f)]: r.job_id }));
      setConfirm(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
    }
  };
  return (
    <div className="stack-sm" data-testid="folder-typos" style={{ marginTop: 12 }}>
      <div className="eyebrow">{t("typo.title")}</div>
      {typos.data.map((f) => (
        <div key={key(f)} className="stack-sm">
          <InlineState tone="warning" icon={FolderPen}>
            <span>{t("typo.row", { name: base(f.path), suggestion: base(f.suggested_path), instance: f.instance_name, count: num(f.items) })}</span>
            {!f.free_space_ok ? <span> {t("typo.noSpace")}</span> : null}
            {f.blocked_reason_nl ? <span> {f.blocked_reason_nl}</span> : null}
          </InlineState>
          {jobs[key(f)] ? <JobProgress jobId={jobs[key(f)]} compact onFinished={() => void typos.reload()} />
            : can("admin") ? <div><Button size="sm" variant="primary" disabled={Boolean(f.blocked_reason_nl) || !f.free_space_ok} onClick={() => { setError(null); setConfirm(f); }}>{t("typo.fix", { name: base(f.suggested_path) })}</Button></div> : null}
        </div>
      ))}
      <DangerDialog open={confirm !== null} onClose={() => setConfirm(null)} icon={FolderPen} pending={confirm ? pending === `fix-${key(confirm)}` : false} error={error}
        title={t("typo.confirmTitle", { name: confirm ? base(confirm.suggested_path) : "" })}
        description={t("typo.confirmBody", { instance: confirm?.instance_name ?? "", count: num(confirm?.items ?? 0) })}
        impact={confirm ? [{ label: t("typo.from"), value: confirm.path }, { label: t("typo.to"), value: confirm.suggested_path }, { label: t("dialog.impactCount"), value: num(confirm.items) }] : []}
        facts={[{ label: t("dialog.rollback"), value: t("typo.rollback") }]}
        confirmLabel={t("typo.fix", { name: confirm ? base(confirm.suggested_path) : "" })}
        onConfirm={() => { if (confirm) void fix(confirm); }} />
    </div>
  );
}

interface FolderTwin {
  instance_id: string; instance_name: string; root_folder_id: number; path: string; twin_of: string[] | null;
  offline: boolean; items: number; empty_on_disk: boolean | null; removable: boolean; label_nl?: string;
}

/**
 * Tweelingmappen (alleen hoofdletters verschillen, bv. /data/media/tv naast /data/Media/TV) en
 * hoofdmappen die offline zijn (N11). Een lege tweeling mag na bevestiging weg; er gaat geen media verloren.
 */
export function FolderTwins() {
  const { t, num } = useI18n();
  const { can } = useSession();
  const twins = useResource<FolderTwin[]>("/storage/twins", undefined, 300_000);
  const toast = useToast();
  const [confirm, setConfirm] = useState<FolderTwin | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (twins.error?.kind === "unavailable" || !twins.data?.length) return null;
  const remove = async () => {
    if (!confirm) return;
    setPending(true);
    setError(null);
    try {
      await api.post(`/storage/twins/${encodeURIComponent(confirm.instance_id)}/${confirm.root_folder_id}/remove`, { confirm: true });
      toast.push("success", t("twins.removed", { path: confirm.path }));
      setConfirm(null);
      void twins.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  };
  // per tweelingfamilie (zelfde pad op hoofdletters na): welke houden? De map met de meeste titels;
  // bij gelijke aantallen de map met hoofdletters (de echte map, niet het kleine-letter-sjabloon) (R18)
  const families = new Map<string, FolderTwin[]>();
  for (const f of twins.data) if (!f.offline) families.set(f.path.toLowerCase(), [...(families.get(f.path.toLowerCase()) ?? []), f]);
  const advice = [...families.values()].filter((fam) => fam.length > 1).map((fam) => {
    const keep = [...fam].sort((a, b) => (b.items - a.items) || (Number(/[A-Z]/.test(b.path)) - Number(/[A-Z]/.test(a.path))))[0];
    return { keep, fam };
  });
  return (
    <div className="stack-sm" data-testid="folder-twins">
      {advice.map(({ keep, fam }) => (
        <InlineState key={`keep-${keep.path}`} tone="info" data-testid="twin-keep">
          <strong>{t("storage.twinKeep", { path: keep.path })}</strong> — {t("storage.twinKeepWhy")}.{" "}
          <span className="t-3">{fam.map((f) => t("storage.twinCounts", { path: f.path, count: num(f.items) })).join(" · ")}</span>
        </InlineState>
      ))}
      {twins.data.map((f) => (
        <InlineState key={`${f.instance_id}-${f.root_folder_id}`} tone={f.offline ? "danger" : "warning"}>
          <strong>{f.offline ? t("twins.offline", { path: f.path, name: f.instance_name }) : t("twins.twin", { path: f.path, other: (f.twin_of ?? []).join(", "), name: f.instance_name })}</strong>{" "}
          {f.items ? t("twins.items", { count: num(f.items) }) : t("twins.noItems")}{" "}
          {f.removable && can("operator") ? <Button size="sm" onClick={() => setConfirm(f)}>{t("twins.remove")}</Button> : null}
        </InlineState>
      ))}
      <DangerDialog open={confirm !== null} onClose={() => setConfirm(null)} pending={pending} error={error}
        title={t("twins.removeTitle", { path: confirm?.path ?? "" })} description={t("twins.removeBody")} confirmLabel={t("twins.remove")}
        onConfirm={() => void remove()} />
    </div>
  );
}
