"""API-gebaseerde import (fallback van ADR-008) en delta-synchronisatie bron -> doel.

Leest de bron uitsluitend met GET (via ``SourceClient``) en schrijft alleen naar de
DOEL-fork. Idempotent en herstartbaar: elke aangemaakte resource wordt per run in
``instance_migration_checkpoints`` vastgelegd (bron-ID -> doel-ID) en bestaande
resources worden op naam/extern ID herkend, zodat een herhaalde run niets dubbel maakt.

Indexers/downloadclients worden UITGESCHAKELD aangemaakt (schaduwmodus). Geheime velden
die de bron-API gemaskeerd teruggeeft (``********``/leeg) kunnen niet worden
overgenomen: zo'n resource krijgt de vlag 'credentials nodig' (opnieuw invullen in
Downloads & Indexers, of laten synchroniseren door Prowlarr).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mediaguard.arr.client import ArrClient, ArrError, ArrNotFound, ArrValidationError
from mediaguard.instances.deps import MASK, _is_secret_field
from mediaguard.migration import compat, store
from mediaguard.migration.backupfile import remap

if TYPE_CHECKING:
    from mediaguard.context import AppContext

Log = Callable[[str], None]
SKIPPED: Any = object()  # niet over te zetten naar deze (oudere) versie
SECTION_CATEGORY = {"indexer": "indexers", "downloadclient": "download_clients",
                    "importlist": "import_lists"}
IMPORT_LIST_FLAGS = ("enableAutomaticAdd", "enabled", "enableAuto", "searchForMissingEpisodes",
                     "searchOnAdd")


def _clean(body: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in body.items() if k != "id"}
    out.pop("presets", None)
    return out


def _needs_credentials(resource: dict[str, Any]) -> list[str]:
    missing = []
    for field in resource.get("fields") or []:
        if _is_secret_field(field) and field.get("value") in (MASK, None, ""):
            privacy = str(field.get("privacy", "")).lower()
            if field.get("value") == MASK or privacy in ("apikey", "password"):
                missing.append(str(field.get("name")))
    return missing


def _map_ids(ids: list[Any] | None, mapping: dict[str, str]) -> list[int]:
    return [int(mapping[str(i)]) for i in ids or [] if str(i) in mapping]


async def _optional(coro: Any, default: Any) -> Any:
    try:
        return await coro
    except ArrNotFound:
        return default


def new_items_override(series: dict[str, Any], options: dict[str, Any]) -> str | None:
    """D1-optie 'Nieuwe seizoenen automatisch volgen'. None = exact de bron volgen."""
    follow = options.get("follow_new_seasons")
    if follow and series.get("monitored"):
        return "all"
    return None


class Importer:
    def __init__(self, ctx: AppContext, run_id: int, target: str, src: ArrClient,
                 tgt: ArrClient, rules: list[tuple[str, str]], log: Log,
                 options: dict[str, Any] | None = None, phase: str = "import") -> None:
        self.ctx, self.run_id, self.target = ctx, run_id, target
        self.src, self.tgt, self.rules, self.log = src, tgt, rules, log
        self.options = options or {}
        self.phase = phase
        self.state: dict[str, Any] = {"indexers": {}, "download_clients": {},
                                      "import_lists": {}, "config": {}}
        self.credentials_needed: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}
        self._name_maps: dict[str, dict[str, str]] = {}
        self._tgt_name: str | None = None
        self._schemas: dict[str, list[dict[str, Any]] | None] = {}

    # -- helpers ------------------------------------------------------------------------
    def _cp(self, kind: str) -> dict[str, str]:
        mapping = store.checkpoint_map(self.ctx, self.run_id, self.target, "import", kind)
        return {**self._name_maps.get(kind, {}), **mapping}

    async def _build_name_maps(self) -> None:
        """Na een native restore bestaan er geen import-checkpoints: koppel dan op naam/label
        (tags, kwaliteits- en taalprofielen)."""
        tgt_tags = {str(t["label"]).lower(): str(t["id"]) for t in await self.tgt.tags()}
        self._name_maps["tag"] = {str(t["id"]): tgt_tags[str(t["label"]).lower()]
                                  for t in await self.src.tags()
                                  if str(t["label"]).lower() in tgt_tags}
        tgt_qp = {q["name"]: str(q["id"]) for q in await self.tgt.quality_profiles()}
        self._name_maps["quality_profile"] = {str(q["id"]): tgt_qp[q["name"]]
                                              for q in await self.src.quality_profiles()
                                              if q["name"] in tgt_qp}
        if self.src.app == "sonarr":
            tgt_lp = {p["name"]: str(p["id"]) for p in await self.tgt.language_profiles()}
            self._name_maps["language_profile"] = {
                str(p["id"]): tgt_lp[p["name"]] for p in await self.src.language_profiles()
                if p["name"] in tgt_lp}

    def _save(self, kind: str, source_key: Any, target_ref: Any,
              data: dict[str, Any] | None = None) -> None:
        store.checkpoint_set(self.ctx, self.run_id, self.target, "import", kind,
                             str(source_key), target_ref, data)

    def _count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    # -- versieverschil (nieuwere bron → oudere fork) -----------------------------------
    async def _target_name(self) -> str:
        if self._tgt_name is None:
            try:
                version = (await self.tgt.system_status()).get("version")
            except (ArrError, AttributeError):
                version = None
            self._tgt_name = compat.tgt_label(self.tgt.app, version)
        return self._tgt_name

    async def _schema(self, path: str) -> list[dict[str, Any]] | None:
        if path not in self._schemas:
            try:
                data = await self.tgt.get(path)
            except ArrError:
                data = None
            self._schemas[path] = data if isinstance(data, list) and data else None
        return self._schemas[path]

    async def _unsupported(self, category: str, key: Any, what: str = "") -> None:
        reason = f"{what} bestaat niet in {await self._target_name()}".strip()
        reason = reason[0].upper() + reason[1:]
        prev = store.checkpoint_get(self.ctx, self.run_id, self.target, "import",
                                    compat.UNSUPPORTED_KIND, f"{category}|{key}")
        old = str((prev or {}).get("data", {}).get("reason_nl") or "")
        if old and reason not in old:
            reason = f"{old}; {reason[0].lower() + reason[1:]}"
        elif old:
            reason = old
        self._save(compat.UNSUPPORTED_KIND, f"{category}|{key}", None,
                   {"category": category, "key": str(key), "reason_nl": reason})
        self._count("unsupported")
        self.log(f"Overgeslagen ({category} '{key}'): {reason}")

    async def _exists(self, path: str, item_id: int) -> bool:
        try:
            await self.tgt.get(f"{path}/{int(item_id)}")
            return True
        except ArrError:
            return False

    async def _write(self, category: str, key: Any, call: Any, body: dict[str, Any]) -> Any:
        """Schrijf naar het doel; eigenschappen die het doel niet kent worden weggelaten en
        er wordt één keer opnieuw geprobeerd. Lukt het dan nog niet, of bestaat het
        endpoint niet, dan is het een automatisch geaccepteerd verschil (SKIPPED)."""
        try:
            return await call(body)
        except ArrNotFound:
            await self._unsupported(category, key, "Dit type instelling")
            return SKIPPED
        except ArrValidationError as exc:
            props = compat.failing_properties(exc.details) or \
                compat.failing_properties({"message": exc.message})
            stripped = compat.strip_properties(body, props) if props else body
            if stripped == body:
                raise
            try:
                result = await call(stripped)
            except (ArrValidationError, ArrNotFound):
                await self._unsupported(category, key)
                return SKIPPED
            await self._unsupported(category, key, "Onderdeel " + ", ".join(props))
            return result

    # -- configuratie -------------------------------------------------------------------
    async def run_config(self) -> dict[str, Any]:
        await self._tags()
        await self._custom_formats()
        await self._quality_definitions()
        await self._quality_profiles()
        await self._root_folders()
        await self._remote_path_mappings()
        await self._delay_profiles()
        await self._providers("downloadclient", "download_clients")
        await self._providers("indexer", "indexers")
        await self._release_profiles()
        await self._import_lists()
        await self._configs()
        return {"counts": self.counts, "credentials_needed": self.credentials_needed,
                "state": self.state}

    async def _tags(self) -> None:
        existing = {t["label"].lower(): int(t["id"]) for t in await self.tgt.tags()}
        for tag in await self.src.tags():
            tid = existing.get(str(tag["label"]).lower())
            if tid is None:
                tid = int((await self.tgt.tag_create(tag["label"]))["id"])
                self._count("tags_created")
            self._save("tag", tag["id"], tid)

    async def _custom_formats(self) -> None:
        src = await _optional(self.src.custom_formats(), [])
        if not src:
            return
        existing = {c["name"]: int(c["id"]) for c in await _optional(self.tgt.custom_formats(),
                                                                       [])}
        spec_schema = await self._schema("customformat/schema")
        langs = await self._schema("language")
        known_langs = {int(x["id"]) for x in langs or [] if str(x.get("id")).lstrip("-")
                       .isdigit()} or None
        for cf in src:
            cid = existing.get(cf["name"])
            if cid is None:
                body, dropped = compat.fit_custom_format(_clean(cf), spec_schema, known_langs)
                if body is None:
                    await self._unsupported("custom_formats", cf["name"],
                                            "Voorwaarde " + ", ".join(dropped))
                    continue
                created = await self._write("custom_formats", cf["name"],
                                            lambda b: self.tgt.post("customformat", b), body)
                if created is SKIPPED:
                    continue
                if dropped:
                    await self._unsupported("custom_formats", cf["name"],
                                            "Voorwaarde " + ", ".join(dropped))
                cid = int(created["id"])
                self._count("custom_formats_created")
            self._save("custom_format", cf["id"], cid)

    async def _quality_definitions(self) -> None:
        src = await _optional(self.src.get("qualitydefinition"), [])
        tgt = await _optional(self.tgt.get("qualitydefinition"), [])
        if not src or not tgt:
            return
        by_q = {int(d["quality"]["id"]): d for d in src}
        changed = []
        for d in tgt:
            s = by_q.get(int(d["quality"]["id"]))
            if s is None:
                continue
            new = {**d, **{k: s.get(k) for k in ("title", "minSize", "maxSize", "preferredSize")
                           if k in s}}
            if new != d:
                changed.append(new)
        if changed:
            await self.tgt.put("qualitydefinition/update", changed)
            self._count("quality_definitions_updated", len(changed))

    async def _quality_profiles(self) -> None:
        cf_map = self._cp("custom_format")
        existing = {q["name"]: q for q in await self.tgt.quality_profiles()}
        tgt_cf_names = {int(c["id"]) for c in await _optional(self.tgt.custom_formats(), [])}
        known_q = {int(d["quality"]["id"]) for d in await self._schema("qualitydefinition")
                   or [] if isinstance(d.get("quality"), dict)}
        langs = {int(x["id"]) for x in await self._schema("language") or []
                 if str(x.get("id")).lstrip("-").isdigit()}
        template = next(iter(existing.values()), None)
        for qp in await self.src.quality_profiles():
            body, _ = compat.fit_to_template(_clean(qp), template)
            dropped_q: list[int] = []
            if known_q and isinstance(body.get("items"), list):
                body["items"], dropped_q = compat.fit_quality_items(body["items"], known_q)
                group_ids = {int(i["id"]) for i in body["items"] if i.get("items")
                             and str(i.get("id")).isdigit()}
                allowed = compat.allowed_ids(body["items"])
                if body.get("cutoff") not in set(allowed) | group_ids and allowed:
                    body["cutoff"] = allowed[-1]
            lang = body.get("language")
            if langs and isinstance(lang, dict) and lang.get("id") not in langs:
                fallback = (template or {}).get("language")
                if fallback:
                    body["language"] = fallback
                else:
                    body.pop("language", None)
                dropped_q.append(-1)
            items = []
            for fi in qp.get("formatItems") or []:
                mapped = cf_map.get(str(fi.get("format")))
                if mapped is not None and int(mapped) in tgt_cf_names:
                    items.append({**fi, "format": int(mapped)})
            if "formatItems" in qp:
                body["formatItems"] = items
            current = existing.get(qp["name"])
            if current is None:
                created = await self._write("quality_profiles", qp["name"],
                                            lambda b: self.tgt.post("qualityprofile", b), body)
                if created is SKIPPED:
                    continue
                qid = int(created["id"])
                self._count("quality_profiles_created")
            else:
                qid = int(current["id"])
                done = await self._write(
                    "quality_profiles", qp["name"],
                    lambda b, q=qid: self.tgt.put(f"qualityprofile/{q}", b), {**body, "id": qid})
                if done is SKIPPED and not await self._exists("qualityprofile", qid):
                    continue
                self._count("quality_profiles_updated")
            if dropped_q:
                what = [f"kwaliteit {q}" for q in dropped_q if q >= 0] + \
                    (["taal"] if -1 in dropped_q else [])
                await self._unsupported("quality_profiles", qp["name"],
                                        "Onderdeel " + ", ".join(what))
            self._save("quality_profile", qp["id"], qid, {"name": qp["name"]})
        if self.src.app == "sonarr":
            src_lp = await self.src.language_profiles()
            tgt_lp = {p["name"]: int(p["id"]) for p in await self.tgt.language_profiles()}
            for lp in src_lp:
                if lp["name"] in tgt_lp:
                    self._save("language_profile", lp["id"], tgt_lp[lp["name"]])

    async def _root_folders(self) -> None:
        existing = {str(r["path"]).rstrip("/") for r in await self.tgt.root_folders()}
        for root in await self.src.root_folders():
            path = str(remap(str(root["path"]), self.rules))
            if path.rstrip("/") not in existing:
                await self.tgt.root_folder_add(path)
                existing.add(path.rstrip("/"))
                self._count("root_folders_created")

    async def _remote_path_mappings(self) -> None:
        existing = {(m["host"], m["remotePath"], str(m["localPath"]).rstrip("/"))
                    for m in await self.tgt.remote_path_mappings()}
        for m in await self.src.remote_path_mappings():
            local = str(remap(str(m["localPath"]), self.rules))
            key = (m["host"], m["remotePath"], local.rstrip("/"))
            if key not in existing:
                await self.tgt.remote_path_mapping_create(m["host"], m["remotePath"], local)
                existing.add(key)
                self._count("remote_path_mappings_created")

    async def _delay_profiles(self) -> None:
        src = await _optional(self.src.get("delayprofile"), [])
        if not src:
            return
        tags = self._cp("tag")
        tgt = await _optional(self.tgt.get("delayprofile"), [])
        by_tags = {tuple(sorted(p.get("tags") or [])): p for p in tgt}
        for p in src:
            mapped = sorted(_map_ids(p.get("tags"), tags))
            body, _ = compat.fit_to_template({**_clean(p), "tags": mapped},
                                             next(iter(tgt), None))
            current = by_tags.get(tuple(mapped))
            if current is not None:
                if {k: current.get(k) for k in body} != body:
                    await self._write(
                        "delay_profiles", ",".join(map(str, mapped)) or "standaard",
                        lambda b: self.tgt.put(f"delayprofile/{int(b['id'])}", b),
                        {**body, "id": int(current["id"])})
                    self._count("delay_profiles_updated")
            elif await self._write("delay_profiles", ",".join(map(str, mapped)) or "standaard",
                                   lambda b: self.tgt.post("delayprofile", b),
                                   body) is not SKIPPED:
                self._count("delay_profiles_created")

    async def _release_profiles(self) -> None:
        src = await _optional(self.src.get("releaseprofile"), [])
        if not src:
            return
        tags, idx = self._cp("tag"), self._cp("indexer")
        tgt = await _optional(self.tgt.get("releaseprofile"), [])
        seen = {(str(p.get("required")), str(p.get("ignored"))) for p in tgt}
        for p in src:
            key = (str(p.get("required")), str(p.get("ignored")))
            if key in seen:
                continue
            body, _ = compat.fit_to_template(
                {**_clean(p), "tags": _map_ids(p.get("tags"), tags)}, next(iter(tgt), None))
            if p.get("indexerId"):
                body["indexerId"] = int(idx.get(str(p["indexerId"]), 0) or 0)
            seen.add(key)
            if await self._write("release_profiles", p.get("name") or key[0],
                                 lambda b: self.tgt.post("releaseprofile", b),
                                 body) is not SKIPPED:
                self._count("release_profiles_created")

    async def _providers(self, kind: str, section: str) -> None:
        tags = self._cp("tag")
        dc_map = self._cp("downloadclient")
        existing = {p["name"]: p for p in await self.tgt.provider_list(kind)}
        flags = ("enableRss", "enableAutomaticSearch", "enableInteractiveSearch") \
            if kind == "indexer" else ("enable",)
        for res in await self.src.provider_list(kind):
            self.state[section].setdefault(res["name"], {f: bool(res.get(f)) for f in flags})
            missing = _needs_credentials(res)
            current = existing.get(res["name"])
            if current is None:
                body, dropped = compat.fit_provider(_clean(res),
                                                    await self._schema(f"{kind}/schema"))
                if body is None:
                    await self._unsupported(SECTION_CATEGORY[kind], res["name"],
                                            f"Type '{res.get('implementation')}'")
                    self.state[section].pop(res["name"], None)
                    continue
                body["tags"] = _map_ids(res.get("tags"), tags)
                if kind == "indexer":
                    body["enableRss"] = False
                    body["enableAutomaticSearch"] = False
                    body["downloadClientId"] = int(dc_map.get(
                        str(res.get("downloadClientId") or 0), 0) or 0)
                else:
                    body["enable"] = False
                if missing:
                    body["fields"] = [{**f, "value": None} if f.get("name") in missing else f
                                      for f in body.get("fields") or []]
                current = await self._write(
                    SECTION_CATEGORY[kind], res["name"],
                    lambda b, k=kind: self.tgt.provider_create(k, b, force_save=True), body)
                if current is SKIPPED:
                    self.state[section].pop(res["name"], None)
                    continue
                self._count(f"{kind}_created")
            else:
                if any(current.get(f) for f in (("enableRss", "enableAutomaticSearch")
                                                if kind == "indexer" else ("enable",))):
                    body = dict(current)
                    for f in (("enableRss", "enableAutomaticSearch") if kind == "indexer"
                              else ("enable",)):
                        body[f] = False
                    await self.tgt.provider_update(kind, body, force_save=True)
            prev = store.checkpoint_get(self.ctx, self.run_id, self.target, "import", kind,
                                        str(res["id"]))
            needs = missing if prev is None else prev["data"].get("credentials_needed", missing)
            self._save(kind, res["id"], current["id"], {"name": res["name"],
                                                         "credentials_needed": needs})
            if needs:
                self.credentials_needed.append({"kind": kind, "name": res["name"],
                                                "target_id": current["id"], "fields": needs})

    async def _import_lists(self) -> None:
        src = await _optional(self.src.provider_list("importlist"), [])
        if not src:
            return
        tags, qps = self._cp("tag"), self._cp("quality_profile")
        existing = {p["name"]: p for p in await self.tgt.provider_list("importlist")}
        for res in src:
            flags = {f: res[f] for f in IMPORT_LIST_FLAGS if f in res}
            self.state["import_lists"].setdefault(res["name"], flags)
            if res["name"] in existing:
                self._save("importlist", res["id"], existing[res["name"]]["id"])
                continue
            body, _ = compat.fit_provider(_clean(res), await self._schema("importlist/schema"))
            if body is None:
                await self._unsupported("import_lists", res["name"],
                                        f"Type '{res.get('implementation')}'")
                self.state["import_lists"].pop(res["name"], None)
                continue
            body.update({f: False for f in flags})
            body["tags"] = _map_ids(res.get("tags"), tags)
            if res.get("qualityProfileId") is not None:
                body["qualityProfileId"] = int(qps.get(str(res["qualityProfileId"]), 0) or 0)
            if res.get("rootFolderPath"):
                body["rootFolderPath"] = remap(res["rootFolderPath"], self.rules)
            created = await self._write(
                "import_lists", res["name"],
                lambda b: self.tgt.provider_create("importlist", b, force_save=True), body)
            if created is SKIPPED:
                self.state["import_lists"].pop(res["name"], None)
                continue
            self._save("importlist", res["id"], created["id"], {"name": res["name"]})
            self._count("import_lists_created")

    async def _configs(self) -> None:
        for name in ("naming", "mediamanagement", "indexer"):
            src = await _optional(self.src.get(f"config/{name}"), None)
            tgt = await _optional(self.tgt.get(f"config/{name}"), None)
            if not isinstance(src, dict) or not isinstance(tgt, dict):
                continue
            # alleen velden die het doel kent; nieuwere velden van de bron vallen weg
            body = {**{k: v for k, v in src.items() if k in tgt},
                    "id": tgt.get("id", src.get("id"))}
            if name == "indexer":
                self.state["config"].setdefault("rsssyncinterval", src.get("rssSyncInterval"))
                body["rssSyncInterval"] = 0
            if name == "mediamanagement" and body.get("recycleBin"):
                body["recycleBin"] = remap(body["recycleBin"], self.rules)
            if await self._write("config", name,
                                 lambda b, n=name: self.tgt.put(f"config/{n}", b),
                                 body) is not SKIPPED:
                self._count("config_sections")

    # -- items ---------------------------------------------------------------------------
    async def run_items(self) -> dict[str, Any]:
        await self._build_name_maps()
        if self.src.app == "sonarr":
            return await self._series()
        return await self._movies()

    async def _series(self) -> dict[str, Any]:  # noqa: C901 - stapsgewijs, idempotent
        tags, qps, lps = self._cp("tag"), self._cp("quality_profile"), \
            self._cp("language_profile")
        tgt_by_tvdb = {int(s["tvdbId"]): s for s in await self.tgt.series_list()}
        default_lp = next(iter(lps.values()), None)
        pending: list[str] = []
        stats = {"created": 0, "updated": 0, "unchanged": 0, "episode_changes": 0}
        for s in await self.src.series_list():
            self.ctx.services.get("migration_cancel_check", lambda: None)()
            desired_new = compat.known_enum(
                new_items_override(s, self.options) or s.get("monitorNewItems"),
                compat.MONITOR_NEW_ITEMS, "all")
            seasons = [{"seasonNumber": x["seasonNumber"], "monitored": bool(x["monitored"])}
                       for x in s.get("seasons") or []]
            want = {"monitored": bool(s.get("monitored")),
                    "seriesType": compat.known_enum(s.get("seriesType"), compat.SERIES_TYPES,
                                                    "standard"),
                    "seasonFolder": s.get("seasonFolder", True),
                    "qualityProfileId": int(qps.get(str(s.get("qualityProfileId")), 0) or 0),
                    "tags": sorted(_map_ids(s.get("tags"), tags))}
            if desired_new is not None:
                want["monitorNewItems"] = desired_new
            current = tgt_by_tvdb.get(int(s["tvdbId"]))
            if current is None:
                body = {"title": s["title"], "tvdbId": s["tvdbId"], **want,
                        "rootFolderPath": remap(s.get("rootFolderPath"), self.rules),
                        "path": remap(s.get("path"), self.rules), "seasons": seasons,
                        "addOptions": {"monitor": "skip", "searchForMissingEpisodes": False,
                                       "searchForCutoffUnmetEpisodes": False}}
                if s.get("languageProfileId") is not None and default_lp is not None:
                    body["languageProfileId"] = int(lps.get(str(s["languageProfileId"]),
                                                            default_lp))
                current = await self.tgt.series_add(body)
                stats["created"] += 1
            else:
                diff = {k: v for k, v in want.items()
                        if (sorted(current.get(k) or []) if k == "tags" else current.get(k)) != v}
                cur_seasons = {x["seasonNumber"]: bool(x["monitored"])
                               for x in current.get("seasons") or []}
                if diff or any(cur_seasons.get(x["seasonNumber"]) != x["monitored"]
                               for x in seasons):
                    merged = {**current, **want}
                    by_n = {x["seasonNumber"]: x["monitored"] for x in seasons}
                    merged["seasons"] = [{**x, "monitored": by_n.get(x["seasonNumber"],
                                                                     x["monitored"])}
                                         for x in current.get("seasons") or []]
                    await self.tgt.series_update(merged)
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1
            self._save("series", s["id"], current["id"], {"tvdbId": s["tvdbId"],
                                                          "title": s["title"]})
            changed = await self._episodes(int(s["id"]), int(current["id"]))
            if changed is None:
                pending.append(str(s["tvdbId"]))
            else:
                stats["episode_changes"] += changed
        stats["episodes_pending"] = len(pending)
        return {"series": stats, "pending_tvdb": pending}

    async def _episodes(self, src_id: int, tgt_id: int) -> int | None:
        """Episode-level monitorstatus exact overnemen. None = doel heeft de afleveringen
        nog niet (metadata-refresh loopt nog); de stap moet later opnieuw draaien."""
        src_eps = await self.src.episodes(src_id)
        wait = int(self.ctx.config.value("migration.episode_wait_seconds"))
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            tgt_eps = await self.tgt.episodes(tgt_id)
            by_key = {(int(e["seasonNumber"]), int(e["episodeNumber"])): e for e in tgt_eps}
            if all((int(e["seasonNumber"]), int(e["episodeNumber"])) in by_key for e in src_eps):
                break
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(min(5.0, max(0.1, wait / 10)))
        on, off = [], []
        for e in src_eps:
            t = by_key[(int(e["seasonNumber"]), int(e["episodeNumber"]))]
            if bool(t.get("monitored")) != bool(e.get("monitored")):
                (on if e.get("monitored") else off).append(int(t["id"]))
        if on:
            await self.tgt.episodes_monitor(on, True)
        if off:
            await self.tgt.episodes_monitor(off, False)
        return len(on) + len(off)

    async def _movies(self) -> dict[str, Any]:
        tags, qps = self._cp("tag"), self._cp("quality_profile")
        tgt_by_tmdb = {int(m["tmdbId"]): m for m in await self.tgt.movie_list()}
        stats = {"created": 0, "updated": 0, "unchanged": 0}
        for m in await self.src.movie_list():
            want = {"monitored": bool(m.get("monitored")),
                    "minimumAvailability": compat.known_enum(
                        m.get("minimumAvailability"), compat.MIN_AVAILABILITY, "released"),
                    "qualityProfileId": int(qps.get(str(m.get("qualityProfileId")), 0) or 0),
                    "tags": sorted(_map_ids(m.get("tags"), tags))}
            current = tgt_by_tmdb.get(int(m["tmdbId"]))
            if current is None:
                root = m.get("rootFolderPath") or str(m.get("path", "")).rsplit("/", 1)[0]
                body = {"title": m["title"], "tmdbId": m["tmdbId"], "year": m.get("year"), **want,
                        "rootFolderPath": remap(root, self.rules),
                        "path": remap(m.get("path"), self.rules),
                        "addOptions": {"searchForMovie": False, "monitor": "movieOnly"}}
                current = await self.tgt.movie_add(body)
                stats["created"] += 1
            elif any((sorted(current.get(k) or []) if k == "tags" else current.get(k)) != v
                     for k, v in want.items()):
                await self.tgt.movie_update({**current, **want})
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
            self._save("movie", m["id"], current["id"], {"tmdbId": m["tmdbId"]})
        return {"movies": stats, "pending_tvdb": []}


async def credentials_resolved(tgt: ArrClient, item: dict[str, Any]) -> bool:
    """Test een 'credentials nodig'-resource opnieuw (gebruiker kan intussen de sleutel
    hebben ingevuld of Prowlarr heeft gesynchroniseerd)."""
    kind = item["kind"]
    try:
        res = await tgt.provider_get(kind, int(item["target_id"]))
        if _needs_credentials(res) and any(
                f.get("value") in (None, "") for f in res.get("fields") or []
                if f.get("name") in item["fields"]):
            return False
        await tgt.provider_test(kind, res)
        return True
    except ArrError:
        return False
