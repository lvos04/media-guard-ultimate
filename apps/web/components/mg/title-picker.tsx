"use client";

import { Search } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { api } from "@/lib/api/client";
import type { LibraryDetail, LibraryItem, LibraryPage } from "@/lib/api/contracts";
import { useI18n } from "@/lib/i18n";

export interface PickedTitle {
  instance: string;
  item: LibraryItem;
  /** Sonarr: gekozen aflevering (optioneel als alleen een serie nodig is). */
  episodeId?: number;
  label: string;
}

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * Zoek op titel in plaats van een intern ID te typen (Release Lab, Hernoemen).
 * `needEpisode`: voor series ook seizoen + aflevering laten kiezen.
 */
export function TitlePicker({ onPick, needEpisode = false, instanceId, mediaKind, autoFocus }: {
  onPick: (p: PickedTitle) => void; needEpisode?: boolean; instanceId?: string; mediaKind?: "series" | "movie"; autoFocus?: boolean;
}) {
  const { t } = useI18n();
  const id = useId();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<LibraryItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [series, setSeries] = useState<LibraryDetail | null>(null);
  const [season, setSeason] = useState<number | null>(null);
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    let live = true;
    setBusy(true);
    const timer = window.setTimeout(() => {
      api.get<LibraryPage>("/library", { q: q.trim(), page_size: 8, instance_id: instanceId || undefined, kind: mediaKind })
        .then((r) => { if (live) setHits(r.items ?? []); })
        .catch(() => { if (live) setHits([]); })
        .finally(() => { if (live) setBusy(false); });
    }, 200);
    return () => { live = false; window.clearTimeout(timer); };
  }, [q, instanceId, mediaKind]);

  const choose = async (item: LibraryItem) => {
    const label = item.year ? `${item.title} (${item.year})` : item.title;
    if (item.kind === "series" && needEpisode) {
      const d = await api.get<LibraryDetail>(`/library/${item.id}`).catch(() => null);
      setSeries(d);
      setSeason(d?.seasons?.find((s) => s.season_number > 0)?.season_number ?? d?.seasons?.[0]?.season_number ?? null);
      setHits([]);
      setQ(label);
      return;
    }
    setHits([]);
    setQ(label);
    onPick({ instance: item.instance_id, item, label });
  };

  const seasonData = series?.seasons?.find((s) => s.season_number === season);
  return (
    <div className="stack-sm" style={{ position: "relative", width: "100%", maxWidth: 520 }}>
      <label className="label" htmlFor={`${id}-q`}>{t("picker.label")}</label>
      <div className="topbar-search" style={{ height: 48 }}>
        <Search className="ico" aria-hidden="true" />
        <input id={`${id}-q`} value={q} autoFocus={autoFocus} placeholder={t("picker.placeholder")} autoComplete="off"
          onChange={(e) => { setQ(e.target.value); setSeries(null); }} // aria-controls alleen als de lijst bestaat (axe aria-valid-attr-value)
          aria-autocomplete="list" aria-controls={hits.length ? `${id}-list` : undefined} />
      </div>
      {hits.length ? (
        <div className="list card" id={`${id}-list`} role="listbox" style={{ maxHeight: 320, overflow: "auto" }}>
          {hits.map((h) => (
            <button key={h.id} type="button" role="option" aria-selected={false} className="list-row list-row--hover" style={{ border: 0, background: "none", width: "100%", textAlign: "left", minHeight: 44 }} onClick={() => void choose(h)}>
              <div className="list-main">
                <div className="list-title">{h.title}{h.year ? ` (${h.year})` : ""}</div>
                <div className="list-sub">{h.instance_name}</div>
              </div>
            </button>
          ))}
        </div>
      ) : q.trim().length >= 2 && !busy && !series ? <p className="hint">{t("picker.none")}</p> : null}
      {series && needEpisode ? (
        <div className="row wrap" style={{ alignItems: "flex-end" }}>
          <label className="stack-sm">
            <span className="label">{t("picker.season")}</span>
            <select className="select" value={season ?? ""} onChange={(e) => setSeason(Number(e.target.value))}>
              {(series.seasons ?? []).map((s) => <option key={s.season_number} value={s.season_number}>{s.season_number === 0 ? t("picker.specials") : t("picker.seasonN", { n: s.season_number })}</option>)}
            </select>
          </label>
          <label className="stack-sm" style={{ flex: 1, minWidth: 200 }}>
            <span className="label">{t("picker.episode")}</span>
            <select className="select" defaultValue="" onChange={(e) => {
              const ep = seasonData?.episodes.find((x) => x.episode_id === Number(e.target.value));
              if (ep) onPick({ instance: series.instance_id, item: series, episodeId: ep.episode_id, label: `${series.title} S${pad(season ?? 0)}E${pad(ep.episode_number)}` });
            }}>
              <option value="" disabled>{t("picker.chooseEpisode")}</option>
              {(seasonData?.episodes ?? []).map((ep) => <option key={ep.episode_id} value={ep.episode_id}>{`E${pad(ep.episode_number)}${ep.title ? ` · ${ep.title}` : ""}`}</option>)}
            </select>
          </label>
        </div>
      ) : null}
    </div>
  );
}
