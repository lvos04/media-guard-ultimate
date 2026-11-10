"use client";

import { ChevronLeft, ChevronRight, Eye, EyeOff } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Button, PageHeader, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import type { CalendarResponse } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { MediaTabs } from "@/components/shell/media-tabs";
import { useI18n } from "@/lib/i18n";
import { EPISODE_STATE } from "@/lib/status";
import { cn } from "@/lib/utils";

function startOfWeek(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x;
}
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export default function CalendarPage() {
  const { t, locale, time } = useI18n();
  const [offset, setOffset] = useState(0);
  const [type, setType] = useState<string>("");
  const [unmonitored, setUnmonitored] = useState(false);
  const [now] = useState(() => new Date());
  const start = useMemo(() => { const s = startOfWeek(now); s.setDate(s.getDate() + offset * 7); return s; }, [now, offset]);
  const end = useMemo(() => { const e = new Date(start); e.setDate(e.getDate() + 7); return e; }, [start]);
  const cal = useResource<CalendarResponse>("/calendar", { start: iso(start), end: iso(end), include_unmonitored: unmonitored || undefined });
  const days = Array.from({ length: 7 }, (_, i) => { const d = new Date(start); d.setDate(d.getDate() + i); return d; });
  const today = iso(now);
  const range = `${start.toLocaleDateString(locale, { day: "numeric", month: "short" })} – ${new Date(end.valueOf() - 86400000).toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" })}`;

  return (
    <>
      <PageHeader title={t("nav.calendar")} sub={t("calendar.sub")} actions={<>
        <Segmented label={t("overview.mediaFilter")} value={type} onChange={setType} options={[{ value: "", label: t("media.all") }, { value: "tv", label: t("media.series") }, { value: "anime", label: t("media.anime") }, { value: "movie", label: t("media.movies") }, { value: "anime_movie", label: t("media.animeMovies") }]} />
        <button type="button" className="filter-chip" aria-pressed={unmonitored} onClick={() => setUnmonitored((v) => !v)}><EyeOff className="ico ico-sm" aria-hidden="true" />{t("calendar.includeUnmonitored")}</button>
        <div className="btn-group">
          <Button iconOnly icon={ChevronLeft} onClick={() => setOffset((o) => o - 1)}>{t("calendar.prev")}</Button>
          <Button onClick={() => setOffset(0)}>{t("calendar.thisWeek")}</Button>
          <Button iconOnly icon={ChevronRight} onClick={() => setOffset((o) => o + 1)}>{t("calendar.next")}</Button>
        </div>
      </>} />
      <MediaTabs />
      <p className="t-2" style={{ margin: "-8px 0 14px", fontSize: 14 }} aria-live="polite">{range}</p>
      <ResourceView resource={cal} endpoint="GET /api/v1/calendar">
        {(r) => {
          const entries = r.items.filter((e) => !type || e.media_type === type);
          return (
            <div className="week">
              {days.map((d) => {
                const key = iso(d);
                const items = entries.filter((e) => iso(new Date(e.date)) === key);
                return (
                  <section key={key} className={cn("day", key === today && "day--today")} aria-label={d.toLocaleDateString(locale, { weekday: "long", day: "numeric", month: "long" })}>
                    <div className="day-head"><span>{d.toLocaleDateString(locale, { weekday: "short" })}</span><strong>{d.getDate()}</strong></div>
                    {items.length === 0 ? <span className="t-3" style={{ fontSize: 14 }}>—</span> : items.map((e, i) => {
                      const view = EPISODE_STATE[e.state] ?? EPISODE_STATE.missing;
                      return (
                        <Link key={`${e.lib_item_id}-${i}`} href={`/bibliotheek/${e.instance_id}/${e.lib_item_id}`} className={cn("cal-ep", e.media_type === "anime" && "cal-ep--anime", (e.media_type === "movie" || e.media_type === "anime_movie") && "cal-ep--movie", e.has_file && "cal-ep--ok")}>
                          <strong>{e.title}</strong>
                          <span className="t-2">{e.type === "episode" ? `S${String(e.season_number).padStart(2, "0")}E${String(e.episode_number).padStart(2, "0")}${e.episode_title ? ` · ${e.episode_title}` : ""}` : e.release_label_nl} · {time(e.date)}</span>
                          <span className="row" style={{ gap: 4 }}>
                            <StatusChip tone={view.tone} icon={view.icon}>{t(view.label)}</StatusChip>
                            {e.monitored ? <Eye className="ico ico-sm t-3" aria-label={t("episode.monitored")} /> : <EyeOff className="ico ico-sm t-3" aria-label={t("status.unmonitored")} />}
                          </span>
                        </Link>
                      );
                    })}
                  </section>
                );
              })}
            </div>
          );
        }}
      </ResourceView>
    </>
  );
}
