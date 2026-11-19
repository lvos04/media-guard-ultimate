"""Identiteit van de actieve overdrachten in de connectorprojectie."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, NamedTuple

from mediaguard.context import AppContext
from mediaguard.downloads.live import ClientDownload
from mediaguard.provisioning import store
from mediaguard.provisioning.templates import DC_IMPLEMENTATIONS, PREFIX
from mediaguard.recovery.fsops import fsync_dir

_DIGEST = re.compile(r"[0-9a-f]{64}")
_KINDS = ("wachtrij", "instantie", "adres", "client")
_ALIAS = re.compile(rf"({'|'.join(_KINDS)}):[0-9a-f]{{64}}")


class _Source(NamedTuple):
    aliases: tuple[str | None, ...]
    initial_id: str
    initial_client: str | None


def content_hash(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()


def _read_index(path: Path, generation: str) -> tuple[str, dict[str, str]]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return "", {}
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Ongeldig bestand voor download-ID's")
        data = json.load(stream)
    if not (isinstance(data, dict) and isinstance(data.get("generation"), str)
            and _DIGEST.fullmatch(data["generation"]) and isinstance(data.get("measured"), str)
            and isinstance(data.get("aliases"), dict)
            and all(_ALIAS.fullmatch(k) and isinstance(v, str) and _DIGEST.fullmatch(v)
                    for k, v in data["aliases"].items())):
        raise ValueError("Onleesbare bronkoppelingen voor downloads")
    return (data["measured"], data["aliases"]) if generation == data["generation"] else ("", {})


def _write_index(path: Path, generation: str, measured: str, aliases: dict[str, str]) -> None:
    # Een geweigerde of onderbroken publicatie laat de vorige volledige meting staan.
    with NamedTemporaryFile(dir=path.parent, mode="w", delete_on_close=False) as output:
        json.dump({"generation": generation, "measured": measured, "aliases": aliases},
                  output, separators=(",", ":"))
        output.flush()
        os.fsync(output.fileno())
        os.replace(output.name, path)
        fsync_dir(path.parent)


def _sources(ctx: AppContext, rows: list[dict[str, Any]], origin: str) -> list[_Source]:
    """Herkenbare bronsleutels en het ID dat deze installatie bij eerste uitgifte gebruikte."""
    clients = store.load(ctx).download_clients
    configured = store.single_download_clients(clients)
    active = store.single_download_clients([c for c in clients if c.enabled])
    owners = store.download_id_owners(ctx, configured) if origin < "156" else None
    endpoints = {
        (DC_IMPLEMENTATIONS[c.implementation], c.name):
            (c.implementation, c.host.lower(), c.port, c.url_base, c.username)
        for c in clients if c.enabled
    }
    unique_endpoints = Counter(endpoints.values())
    instance_transfers = Counter((r.get("instance_id"), str(r.get("download_id") or "").upper())
                                 for r in rows)
    sources = []
    for row in rows:
        transfer = str(row.get("download_id") or "").upper()
        protocol = str(row.get("protocol") or "").lower()
        name = row.get("client") or ""
        if not isinstance(name, str):
            raise ValueError("Ongeldige downloadclient")
        queue = None
        if row.get("instance_id") and row.get("id") is not None:
            queue = "wachtrij:" + content_hash([row["instance_id"], row["id"], transfer])
        if not transfer and queue is None:
            raise ValueError("Downloadrij zonder identiteit")
        canonical = name.removeprefix(PREFIX) or active.get(protocol)
        client = "client:" + content_hash([protocol, transfer, canonical]) if (
            transfer and canonical) else None
        endpoint = endpoints.get((protocol, canonical)) if canonical else None
        address = "adres:" + content_hash([endpoint, transfer]) if (
            client and endpoint and unique_endpoints[endpoint] == 1) else None
        instance = row.get("instance_id")
        arr_transfer = "instantie:" + content_hash([instance, transfer]) if (
            instance and transfer and instance_transfers[instance, transfer] == 1) else None
        source = [protocol, transfer]
        initial_client = row.get("client") or (configured.get(protocol) if transfer else None)
        historical = initial_client or ""
        if not transfer:
            source = [row["instance_id"], row["id"]]
        elif owners is None:
            if historical:
                source.append(historical)
        else:
            historical = historical.removeprefix(PREFIX)
            if not historical or historical != owners.get(protocol):
                source.append(historical)
        sources.append(_Source((queue, arr_transfer, address, client),
                               content_hash(source), initial_client))
    return sources


def download_ids(ctx: AppContext, rows: list[dict[str, Any]], measured: datetime) -> list[str]:
    """Behoud het uitgegeven ID bij dezelfde Arr-rij of hetzelfde item van dezelfde client.

    Alleen de eerste uitgifte gebruikt de historische sleutelvorm van de installatie.
    De lijst van aliassen is gedeeld door lezers en kernprocessen en overleeft een herstart.
    """
    path = ctx.settings.download_index_path
    with ctx.db.startup_lock():
        origin = ctx.db.fetch_one("SELECT schema_version, hex(generation) AS generation "
                                  "FROM connector_evidence_origin CROSS JOIN outbox_generation")
        if not origin or not origin["generation"]:
            raise ValueError("Herkomst van de downloadmeting ontbreekt")
        generation = content_hash(origin["generation"])
        sources = _sources(ctx, rows, origin["schema_version"])
        last, previous = _read_index(path, generation)
        assigned: dict[int, str] = {}
        used: set[str] = set()
        columns = {kind: [s.aliases[pos] for s in sources] for pos, kind in enumerate(_KINDS)}
        arr_columns = (columns["wachtrij"], columns["instantie"])
        # Een bekende wachtrijrij is specifieker dan dezelfde torrent in de instantie.
        visible_arr_ids = {previous.get(queue) or previous.get(instance)
                           for queue, instance in zip(*arr_columns, strict=True)}
        # Alleen een meting van de downloadclient kan bij splitsen of samenvoegen
        # voorrang krijgen op de Arr-rij. Een door Arr gemelde naam is een aanwijzing.
        confirmed = []
        inferred = []
        for kind in ("adres", "client"):
            column = columns[kind]
            reported = [alias if isinstance(row, ClientDownload) else None
                        for alias, row in zip(column, rows, strict=True)]
            report_counts = Counter(reported)
            confirmed.append([
                key if key and report_counts[key] == 1 and (
                    not row.get("instance_id") or previous.get(key) not in visible_arr_ids
                ) else None for key, row in zip(reported, rows, strict=True)
            ])
            counts = Counter(column)
            inferred.append([key if counts[key] == 1 else None for key in column])
        # Eerst bevestigde clientitems (adres vóór naam), dan de eigen wachtrij
        # of instantie en pas daarna een eenduidige aanwijzing voor de client.
        for column in (*confirmed, *arr_columns, *inferred):
            for i, alias in enumerate(column):
                identity = previous.get(alias) if alias else None
                if identity and i not in assigned and identity not in used:
                    assigned[i] = identity
                    used.add(identity)
        previous_ids = set(previous.values())
        for i, source in enumerate(sources):
            if i in assigned:
                continue
            identity = source.initial_id
            if identity in used or identity in previous_ids:
                row = rows[i]
                identity = content_hash([identity, row.get("instance_id"), row.get("id"),
                                         source.initial_client])
            if identity in used:
                raise ValueError("Downloadrij zonder onderscheidende bron")
            assigned[i] = identity
            used.add(identity)

        # Bewaar hoogstens één actuele sleutel per soort per zichtbare download.
        # Ontbreekt een bron tijdelijk, dan blijft de laatst herkende koppeling gelden.
        old_keys = {(identity, alias.split(":", 1)[0]): alias
                    for alias, identity in previous.items()}
        remembered: dict[str, list[str]] = defaultdict(list)
        arr_names: dict[str, list[str]] = defaultdict(list)
        observed: dict[str, list[str]] = defaultdict(list)
        for i, source in enumerate(sources):
            identity = assigned[i]
            row = rows[i]
            from_client = isinstance(row, ClientDownload)
            for kind, alias in zip(_KINDS, source.aliases, strict=True):
                old = old_keys.get((identity, kind))
                if old and kind in ("adres", "client") and not row.get("client"):
                    alias = old  # Een naamloze Arr-rij bewijst geen wisseling van client.
                if key := alias or old:
                    claims = remembered
                    if alias and (kind in ("wachtrij", "instantie") or from_client):
                        claims = observed
                    elif alias and row.get("client"):
                        claims = arr_names
                    claims[key].append(identity)
        # Een actuele clientmeting gaat voor een Arr-naam, daarna volgen oude en
        # afgeleide koppelingen. Gelijkwaardige claims maken de alias meerduidig.
        current = {key: ids[0] for key, ids in (remembered | arr_names | observed).items()
                   if len(ids) == 1}
        if current != previous:
            stamp = measured.isoformat(timespec="microseconds")
            if stamp < last:
                raise ValueError("Ingehaalde downloadmeting")
            ctx.db.require_writable()
            _write_index(path, generation, stamp, current)
        return [assigned[i] for i in range(len(rows))]
