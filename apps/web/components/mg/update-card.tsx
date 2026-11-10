"use client";

import { CircleArrowUp, ExternalLink } from "lucide-react";

import { Button, Card, StatusChip, Switch } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/** GET /api/v1/system/update (docs/APP_API.md, core mediaguard/updates.py). */
interface SystemUpdate {
  enabled: boolean;
  current: string;
  latest: string | null;
  update_available: boolean;
  url: string | null;
  image: string | null;
  checked_at: string | null;
  error_nl: string | null;
}

const SETTING = "updates.check_enabled";

/** Updates: dagelijkse controle op een nieuwe versie (aan/uit), status en 'Nu controleren'. */
export function UpdateCard() {
  const { t, relative } = useI18n();
  const { can } = useSession();
  const { run, pending } = useAction();
  const upd = useResource<SystemUpdate>("/system/update");
  const toggle = async (value: boolean) => {
    const ok = await run("upd-toggle", "patch", "/settings/scoped",
      { changes: [{ scope_type: "global", scope_id: "", key: SETTING, value }], reason: "UI: updates" }, t("upd.saved"));
    if (ok !== undefined) void upd.reload();
  };
  const checkNow = async () => {
    const r = await run<SystemUpdate>("upd-check", "post", "/system/update/check", {}, t("upd.checkedToast"));
    if (r !== undefined) void upd.reload();
  };
  return (
    <Card className="span-6" id="updates" title={t("upd.title")} sub={t("upd.sub")} icon={CircleArrowUp} flush>
      <ResourceView resource={upd} endpoint="GET /api/v1/system/update" compact>
        {(u) => (
          <div className="list">
            <div className="list-row">
              <div className="list-main"><label className="list-title" htmlFor="upd-toggle">{t("upd.toggle")}</label></div>
              <Switch id="upd-toggle" checked={u.enabled} label={t("upd.toggle")} disabled={!can("operator") || pending === "upd-toggle"} onChange={(v) => void toggle(v)} />
            </div>
            <div className="list-row list-row--wrap" data-testid="update-status">
              <div className="list-main">
                <div className="list-title">
                  {u.update_available && u.latest ? t("upd.available", { version: u.latest }) : !u.enabled ? t("upd.off") : u.checked_at ? t("upd.upToDate") : t("upd.never")}
                </div>
                <div className="list-sub">
                  {[t("upd.current", { version: u.current }), u.enabled && u.checked_at ? t("upd.checked", { when: relative(u.checked_at) }) : null].filter(Boolean).join(" · ")}
                </div>
                {u.update_available && u.image ? <div className="list-sub" style={{ whiteSpace: "normal" }}>{t("upd.how", { tag: u.image.split(":").pop() ?? "" })}</div> : null}
                {u.enabled && u.error_nl ? <div className="list-sub" role="status">{u.error_nl}</div> : null}
              </div>
              {u.update_available ? <StatusChip tone="warning">{u.latest}</StatusChip> : null}
            </div>
            {u.enabled ? (
              <div className="list-row" style={{ gap: 8, flexWrap: "wrap" }}>
                <Button size="sm" disabled={!can("operator")} pending={pending === "upd-check"} onClick={() => void checkNow()}>{t("upd.checkNow")}</Button>
                {u.update_available && u.url ? <a className="btn btn--sm" href={u.url} target="_blank" rel="noreferrer"><ExternalLink className="ico" aria-hidden="true" />{t("upd.notes")}</a> : null}
              </div>
            ) : null}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
