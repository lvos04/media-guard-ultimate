"use client";

import { useEffect, useState } from "react";

import { Progress, StatusChip } from "@/components/mg/primitives";
import { api } from "@/lib/api/client";
import type { AdminHealth, Instance } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { instanceLabel } from "@/lib/labels";

export interface MediaFolder { path: string; name: string; free: number | null; total: number | null; ok: boolean }
interface RootFolder { path: string; accessible?: boolean; freeSpace?: number; totalSpace?: number }

/** Mediamappen uit Sonarr/Radarr (die worden echt vol), met gewone namen (M5). */
export function useMediaFolders(): { folders: MediaFolder[]; loaded: boolean } {
  const { locale } = useI18n();
  const instances = useResource<Instance[]>("/instances");
  const [folders, setFolders] = useState<MediaFolder[]>([]);
  const [loaded, setLoaded] = useState(false);
  const ids = (instances.data ?? []).filter((i) => !(i as { suspended?: boolean }).suspended).map((i) => `${i.id}|${i.name}`).join(",");
  useEffect(() => {
    if (!ids) return;
    let live = true;
    const list = ids.split(",").map((x) => x.split("|"));
    Promise.allSettled(list.map(([id]) => api.get<RootFolder[]>(`/instances/${id}/root-folders`))).then((res) => {
      if (!live) return;
      const seen = new Map<string, MediaFolder>();
      res.forEach((r, i) => {
        if (r.status !== "fulfilled") return;
        for (const f of r.value ?? []) {
          if (seen.has(f.path)) continue;
          seen.set(f.path, { path: f.path, name: `${instanceLabel(list[i][0], list[i][1], locale)} — ${f.path.replace(/\/+$/, "").split("/").pop() || f.path}`, free: f.freeSpace ?? null, total: f.totalSpace ?? null, ok: f.accessible !== false });
        }
      });
      setFolders([...seen.values()]);
      setLoaded(true);
    });
    return () => { live = false; };
  }, [ids, locale]);
  return { folders, loaded };
}

const WORK_NAMES: Record<string, "storage.work.config" | "storage.work.staging" | "storage.work.quarantine" | "storage.work.media"> = {
  "/config/core": "storage.work.config", "/config": "storage.work.config", "/work/encode/staging": "storage.work.staging", "/work/quarantine": "storage.work.quarantine",
};

/** Opslag: eerst de mediamappen, daarna de werkruimte van MediaGuard (één regel als alles op dezelfde schijf staat). */
export function StorageOverview() {
  const { t, bytes, pct } = useI18n();
  const health = useResource<AdminHealth>("/admin/health", undefined, 60_000);
  const { folders, loaded } = useMediaFolders();
  type Vol = NonNullable<AdminHealth["storage"]>[number] & { kind?: string; write_probe?: { ok?: boolean; step_nl?: string | null; step?: string | null; error?: string | null } };
  const all = (health.data?.storage ?? []) as Vol[];
  const work = all.filter((w) => w.kind !== "library");
  // schrijftest per mediamap (aanmaken → hernoemen → verwijderen); bij een fout de stap die misging
  const probes = new Map(all.filter((w) => w.kind === "library" && w.write_probe).map((w) => [w.path, w.write_probe!]));
  const sameDisk = work.length > 1 && work.every((w) => w.ok && w.total === work[0].total && w.free === work[0].free);
  const row = (key: string, name: string, path: string, free: number | null | undefined, total: number | null | undefined, ok: boolean, error?: string) => {
    const used = total && free !== null && free !== undefined ? ((total - free) / total) * 100 : null;
    return (
      <div className="meter-row" key={key}>
        <span><strong>{name}</strong><details className="tech-details"><summary>{t("state.techDetails")}</summary><span className="mono">{path}</span></details></span>
        <span className="num t-2">{ok ? `${bytes(free)} ${t("system.free")}${used !== null ? ` · ${t("storage.used", { pct: pct(used) })}` : ""}` : <StatusChip tone="danger">{error ?? t("status.offline")}</StatusChip>}</span>
        {used !== null ? <Progress value={used} tone={used > 90 ? "danger" : used > 75 ? "warning" : undefined} label={name} /> : null}
      </div>
    );
  };
  return (
    <div className="stack-sm" data-testid="storage-overview">
      <div className="eyebrow">{t("storage.media")}</div>
      <div className="meter-list">
        {folders.length ? folders.map((f) => {
          const p = probes.get(f.path);
          // zelfde map, alleen andere hoofdletters (/data/media/tv naast /data/Media/TV): N11
          const twin = folders.find((o) => o.path !== f.path && o.path.toLowerCase() === f.path.toLowerCase());
          return (
            <div key={f.path} className="stack-sm">
              {row(f.path, f.name, f.path, f.free, f.total, f.ok)}
              {twin ? <span className="t-warning" style={{ fontSize: 14 }} data-testid="storage-twin">{t("storage.twin", { other: twin.path })}</span> : null}
              {p ? (p.ok === false
                ? <StatusChip tone="danger">{t("storage.writeFailed", { step: p.step_nl ?? p.step ?? "?" })}</StatusChip>
                : <span className="t-3" style={{ fontSize: 14 }}>{t("storage.writeOk")}</span>) : null}
              {p?.ok === false && p.error ? <span className="t-2" style={{ fontSize: 14 }}>{p.error}</span> : null}
            </div>
          );
        }) : <span className="t-3">{loaded ? t("storage.noMedia") : t("state.loading")}</span>}
      </div>
      <div className="eyebrow" style={{ marginTop: 8 }}>{t("storage.work")}</div>
      <div className="meter-list">
        {sameDisk
          ? row("work", t("storage.work.all"), work.map((w) => w.path).join(" · "), work[0].free, work[0].total, true)
          : work.map((w) => row(w.path, WORK_NAMES[w.path] ? t(WORK_NAMES[w.path]) : w.path, w.path, w.free, w.total, w.ok, w.error))}
      </div>
    </div>
  );
}
