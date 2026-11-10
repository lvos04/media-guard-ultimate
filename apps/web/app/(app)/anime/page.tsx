"use client";

import { ListTree, RefreshCw, ScanSearch } from "lucide-react";
import { useState } from "react";

import { Button, Card, Field, InlineState, PageHeader } from "@/components/mg/primitives";
import { AnimeRoutingSection } from "@/components/mg/anime-routing";
import { SettingsLink } from "@/components/mg/settings-link";
import { useAction } from "@/lib/api/actions";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export default function AnimePage() {
  const { t } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const [m, setM] = useState({ tvdb_id: "", absolute: "", season: "", episode: "" });
  const [xem, setXem] = useState("");
  const [parse, setParse] = useState("");
  const [parsed, setParsed] = useState<unknown>(null);
  const n = (v: string) => (v.trim() === "" ? null : Number(v));
  return (
    <>
      <PageHeader title={t("nav.anime")} sub={t("anime.sub")} />
      <div className="grid">
        <div className="span-12"><AnimeRoutingSection /></div>
        <Card className="span-6" title={t("anime.manual")} icon={ListTree}>
          <p className="t-2" style={{ fontSize: 14, marginBottom: 12 }}>{t("anime.manualBody")}</p>
          <div className="grid" style={{ gap: 10 }}>
            {(["tvdb_id", "absolute", "season", "episode"] as const).map((k) => (
              <div key={k} className="span-3 md-span-6"><Field label={t(`anime.field.${k}`)} htmlFor={`am-${k}`}><input id={`am-${k}`} className="input num" inputMode="numeric" value={m[k]} onChange={(e) => setM({ ...m, [k]: e.target.value })} /></Field></div>
            ))}
          </div>
          <div className="row" style={{ marginTop: 12 }}><Button variant="primary" disabled={!can("operator") || !m.tvdb_id || !m.absolute} pending={pending === "manual"} onClick={() => void run("manual", "post", "/identity/anime-mappings/manual", { tvdb_id: n(m.tvdb_id), absolute: n(m.absolute), season: n(m.season), episode: n(m.episode) }, t("anime.applied"))}>{t("action.apply")}</Button></div>
        </Card>
        <Card className="span-6" title={t("anime.xem")} icon={RefreshCw}>
          <p className="t-2" style={{ fontSize: 14, marginBottom: 12 }}>{t("anime.xemBody")}</p>
          <div className="row wrap" style={{ alignItems: "flex-end" }}>
            <Field label={t("anime.field.tvdb_id")} htmlFor="xem-id"><input id="xem-id" className="input num" inputMode="numeric" value={xem} onChange={(e) => setXem(e.target.value)} /></Field>
            <Button disabled={!can("operator") || !xem} pending={pending === "xem"} onClick={() => void run("xem", "post", `/identity/anime-mappings/thexem/${encodeURIComponent(xem)}/refresh`, {}, t("anime.xemDone"))}>{t("anime.xemRefresh")}</Button>
          </div>
        </Card>
        <Card className="span-12" title={t("anime.parse")} icon={ScanSearch}>
          <form className="row wrap" onSubmit={async (e) => { e.preventDefault(); const r = await run("parse", "post", "/identity/parse", { name: parse }); if (r !== undefined) setParsed(r); }}>
            <input className="input mono" style={{ flex: "1 1 360px" }} aria-label={t("anime.parse")} placeholder="[SubsPlease] Golfbreker - 1089 (1080p) [ABCD1234].mkv" value={parse} onChange={(e) => setParse(e.target.value)} />
            <Button type="submit" pending={pending === "parse"} disabled={!parse.trim()}>{t("anime.parseRun")}</Button>
          </form>
          {parsed ? <pre className="payload-preview" style={{ marginTop: 12 }}>{JSON.stringify(parsed, null, 2)}</pre> : <div style={{ marginTop: 12 }}><InlineState>{t("anime.parseHint")}</InlineState></div>}
        </Card>
        <div className="span-12"><SettingsLink sections={["taal", "downloads"]} /></div>
      </div>
    </>
  );
}
