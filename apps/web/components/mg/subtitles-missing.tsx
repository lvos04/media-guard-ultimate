"use client";

import { Captions } from "lucide-react";
import { useState } from "react";

import { Button, Card } from "@/components/mg/primitives";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { instanceLabel } from "@/lib/labels";

interface MissingSub { key: string; instance_id?: string | null; media_kind?: string | null; provider_id?: string | number | null; title?: string | null; source?: string | null; searched_at?: string | null }

/**
 * Titels waarvoor Nederlandse ondertitels gewenst zijn maar nog ontbreken (rapport 5, M8).
 * MediaGuard zoekt ze zelf via Bazarr/OpenSubtitles; deze kaart is alleen ter informatie.
 */
export function SubtitlesMissing() {
  const { t, num, relative, locale } = useI18n();
  const [all, setAll] = useState(false);
  const res = useResource<{ total: number; items: MissingSub[] }>("/mediaserver/subtitles/missing", { limit: 100 }, 600_000);
  const d = res.data;
  if (!d || !d.total) return null; // geen route (oudere core) of niets ontbreekt
  const items = all ? d.items : d.items.slice(0, 5);
  return (
    <Card title={t("subs.title", { count: num(d.total) })} icon={Captions} sub={t("subs.sub")} flush id="nl-ondertitels">
      <div className="list" data-testid="subs-missing">
        {items.map((it) => (
          <div className="list-row" key={it.key}>
            <div className="list-main">
              <div className="list-title" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{it.title ?? it.key} <span className="chip">{t("subs.badge")}</span></div>
              <div className="list-sub">{[it.instance_id ? instanceLabel(it.instance_id, null, locale) : null, it.searched_at ? t("subs.searched", { when: relative(it.searched_at) }) : t("subs.notSearched")].filter(Boolean).join(" · ")}</div>
            </div>
          </div>
        ))}
        {d.items.length > 5 ? <div className="list-row"><Button size="sm" variant="ghost" onClick={() => setAll((v) => !v)}>{all ? t("subs.less") : t("subs.more", { count: d.items.length - 5 })}</Button></div> : null}
      </div>
    </Card>
  );
}
