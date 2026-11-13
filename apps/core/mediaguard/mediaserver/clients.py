"""Kleine HTTP-clients voor Emby (hoofddoel), Jellyfin, Plex, Bazarr en OpenSubtitles.

Emby en Jellyfin delen dezelfde API (Jellyfin is van Emby afgeleid); Emby zet er ``/emby``
voor. Eén client met een prefix dekt dus beide.

Authenticatie **per server** (Δ-MG-AUTH, besluit BG-01, contract 1.3 §2.3 regel 2): Emby houdt
``X-Emby-Token``; Jellyfin (Jelly Leon 12.x en stock-Jellyfin 12.x, waar
``EnableLegacyAuthorization`` uit staat) krijgt alleen ``Authorization: MediaBrowser …
Token="…"``. De sleutel staat nooit in een URL (geen ``api_key=``) en nooit in een log.

Alle clients gebruiken ``ctx.services["mediaserver_transport"]`` als die er is (tests geven
daar een ``httpx.MockTransport``). Fouten worden ``MediaServerError`` met een Nederlandse tekst.
"""

from __future__ import annotations

import html
import json as jsonlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, quote_plus

import httpx

from mediaguard import __version__
from mediaguard.secrets_store import redact

TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MediaServerError(RuntimeError):
    """Fout van een mediaserver, met Nederlandse tekst; ``status`` is de HTTP-status (0 = geen
    antwoord of geen HTTP-fout)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


#: tekens die in een sleutel nooit voorkomen en de kop ``Authorization: MediaBrowser …`` zouden
#: breken (aanhalingsteken, komma, backslash, spaties en stuurtekens)
_KEY_OK = re.compile(r"[\x21-\x7e]+")
_KEY_BAD = set('"\\,')


def valid_key(key: str) -> bool:
    """Een API-sleutel van Emby/Jellyfin/Plex: zichtbare ASCII zonder ``"``, ``,`` of ``\\``."""
    return bool(_KEY_OK.fullmatch(key)) and not (_KEY_BAD & set(key)) and len(key) <= 300


#: wat 1.2.2 als ``X-Emby-Token`` kon versturen: zichtbare ASCII, met spaties of tabs alleen
#: binnenin. Nooit CR, LF, NUL of een ander stuurteken (kopinjectie) en geen niet-ASCII (dat kon
#: 1.2.2 ook niet versturen).
_KEY_HEADER_SAFE = re.compile(r"[\x21-\x7e](?:[\x20-\x7e\t]*[\x21-\x7e])?")


def header_safe(key: str) -> bool:
    return bool(_KEY_HEADER_SAFE.fullmatch(key))


def auth_headers(kind: str, key: str) -> dict[str, str]:
    """Aanmeldkop per soort server (Δ-MG-AUTH).

    - ``emby``: ``X-Emby-Token`` (ongewijzigd sinds 1.2.2; de Emby-koppeling onderbreekt nooit).
      Een sleutel die 1.2.2 opsloeg (zonder tekencontrole) blijft werken; alleen tekens die een
      kop kunnen toevoegen of breken worden geweigerd (``header_safe``);
    - ``jellyfin``: ``Authorization: MediaBrowser Client=…, Device=…, DeviceId=…, Version=…,
      Token="…"``. Jellyfin 12.x weigert ``X-Emby-Token``, ``api_key=`` en
      ``X-Emby-Authorization`` zodra ``EnableLegacyAuthorization`` uit staat (12.x-standaard);
      deze kop werkt ook op 10.x. ``"``, ``,`` en ``\\`` zouden die kop breken (``valid_key``).
    """
    if not key:
        return {}
    if not (valid_key(key) if kind == "jellyfin" else header_safe(key)):
        raise MediaServerError("ongeldige API-sleutel (onverwachte tekens)")
    if kind == "jellyfin":
        return {"Authorization": (
            'MediaBrowser Client="MediaGuard", Device="MediaGuard", '
            f'DeviceId="mediaguard-core", Version="{__version__}", Token="{key}"')}
    return {"X-Emby-Token": key}


def _transport(services: dict[str, Any]) -> httpx.AsyncBaseTransport | None:
    return services.get("mediaserver_transport")


#: koppen die een sleutel dragen: ze gaan nooit naar een ander adres en hun waarde komt nooit in
#: een foutmelding
_SECRET_HEADERS = ("x-emby-token", "x-plex-token", "authorization", "x-api-key", "api-key")
_TOKEN_IN_HEADER = re.compile(r'(?i)token="([^"]+)"')


def _secrets(headers: dict[str, str]) -> set[str]:
    found: set[str] = set()
    for name, value in headers.items():
        if name.lower() not in _SECRET_HEADERS or not value:
            continue
        found.add(value)
        found.update(_TOKEN_IN_HEADER.findall(value))
        scheme, _, rest = value.partition(" ")
        if scheme.lower() == "bearer" and rest.strip():
            found.add(rest.strip())
    return {s for s in found if s}


def _clean(text: str, headers: dict[str, str], body_secrets: Iterable[str] = ()) -> str:
    """Tekst uit een foutpagina zonder de meegestuurde sleutels, ook als de server ze
    terugkaatst (letterlijk, JSON-, URL- of HTML-gecodeerd); daarna de vaste maskerregels.
    ``body_secrets``: geheimen die in de body meegingen (zoals het wachtwoord van
    OpenSubtitles); die gaan er op dezelfde manier uit."""
    secrets = _secrets(headers) | {s for s in body_secrets if s}
    for secret in sorted(secrets, key=len, reverse=True):
        for form in {secret, jsonlib.dumps(secret)[1:-1], quote(secret, safe=""),
                     quote_plus(secret), html.escape(secret), html.escape(secret, quote=False)}:
            if form:
                text = text.replace(form, "***")
    return redact(text)


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    return url.scheme, url.host.lower(), url.port or {"http": 80, "https": 443}.get(url.scheme)


def _same_server(start: httpx.URL, target: httpx.URL) -> bool:
    """Zelfde server: zelfde schema, host en poort; of http -> https op dezelfde host met de
    standaardpoorten (80 -> 443). Besluit van de gebruiker 2026-11-12 ("alleen zelfde server")."""
    a, b = _origin(start), _origin(target)
    return a == b or (a[1] == b[1] and a[0] == "http" and a[2] == 80
                      and b[0] == "https" and b[2] == 443)


def _where(url: httpx.URL) -> str:
    """Het adres van een omleiding zonder pad, query of gebruikersdeel (geen sleutel)."""
    host = f"[{url.host}]" if ":" in url.host else url.host
    return f"{url.scheme}://{host}" + (f":{url.port}" if url.port else "")


async def _call(services: dict[str, Any], method: str, url: str, *,
                headers: dict[str, str], params: Any = None, json: Any = None,
                timeout: httpx.Timeout | float = TIMEOUT, raw: bool = False,
                any_host: bool = False, body_secrets: Iterable[str] = ()) -> Any:
    """Eén verzoek. Een omleiding wordt alleen gevolgd op dezelfde server (``_same_server``);
    wijst ze naar een ander adres, dan een ``MediaServerError`` en gaat er geen verzoek (en dus
    geen sleutel) daarheen. ``any_host`` is alleen voor een verzoek zonder sleutel (de
    downloadlink van OpenSubtitles, zoals in 1.2.2); met een sleutelkop telt het niet.
    ``body_secrets``: geheimen in de body; ze komen net als de sleutels uit de koppen nooit in
    een foutmelding."""
    foreign_ok = any_host and not _secrets(headers)
    try:
        async with httpx.AsyncClient(transport=_transport(services), timeout=timeout,
                                     follow_redirects=False) as client:
            resp = await client.request(method, url, headers=headers, params=params, json=json)
            start, hops = resp.request.url, 0
            while resp.next_request is not None:
                target = resp.next_request
                if not foreign_ok and not _same_server(start, target.url):
                    raise MediaServerError(
                        f"server stuurt door naar een ander adres: {_where(target.url)}",
                        resp.status_code)
                hops += 1
                if hops > client.max_redirects:
                    raise httpx.TooManyRedirects("te veel omleidingen", request=target)
                resp = await client.send(target)
    except httpx.HTTPError as exc:
        raise MediaServerError(f"niet bereikbaar ({type(exc).__name__})") from exc
    if resp.status_code in (401, 403):
        raise MediaServerError("sleutel geweigerd (controleer de API-sleutel)", resp.status_code)
    if resp.status_code == 503:
        # Jellyfin 12.x antwoordt zo op elke route zolang hij opstart of migreert (gemeten)
        raise MediaServerError("start nog op of is even niet beschikbaar (fout 503)", 503)
    if resp.status_code >= 400:
        # eerst de sleutels eruit, dan pas inkorten (anders blijft een halve sleutel staan)
        clean = _clean(resp.text, headers, body_secrets)
        raise MediaServerError(f"fout {resp.status_code}: {clean[:200]}", resp.status_code)
    if raw:
        return resp.content
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


# -- Emby / Jellyfin ------------------------------------------------------------------------------
# Een gangbare tv-client: speelt H.264/HEVC/AV1 in MKV/MP4 direct af, met AAC/AC3/EAC3.
TV_PROFILE: dict[str, Any] = {
    "Name": "MediaGuard afspeeltest",
    "MaxStreamingBitrate": 120_000_000,
    "DirectPlayProfiles": [
        {"Container": "mkv,mp4", "Type": "Video", "VideoCodec": "h264,hevc,av1",
         "AudioCodec": "aac,ac3,eac3,mp3,opus,flac,truehd,dts"},
    ],
    "TranscodingProfiles": [
        {"Container": "ts", "Type": "Video", "VideoCodec": "h264", "AudioCodec": "aac",
         "Protocol": "hls", "Context": "Streaming"},
    ],
    "SubtitleProfiles": [{"Format": "srt", "Method": "External"},
                         {"Format": "ass", "Method": "Embed"},
                         {"Format": "pgssub", "Method": "Embed"}],
}


@dataclass
class Session:
    user: str
    title: str
    path: str | None
    paused: bool


@dataclass
class PlaybackResult:
    result: str               # ok|transcode|failed|not_found|skipped
    detail_nl: str
    item_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class EmbyClient:
    """Emby (``kind='emby'``, paden onder ``/emby``) of Jellyfin (``kind='jellyfin'``)."""

    def __init__(self, services: dict[str, Any], url: str, api_key: str | None,
                 kind: str = "emby", label: str | None = None) -> None:
        self.services, self.key, self.kind = services, api_key or "", kind
        self.base = url.rstrip("/")
        if self.base.endswith("/emby"):
            self.base = self.base[:-5]
        self.url = self.base + ("/emby" if kind == "emby" else "")
        self._label = label

    @property
    def label(self) -> str:
        return self._label or ("Emby" if self.kind == "emby" else "Jellyfin")

    @property
    def headers(self) -> dict[str, str]:
        return {"Accept": "application/json", **auth_headers(self.kind, self.key)}

    async def _get(self, path: str, **params: Any) -> Any:
        return await _call(self.services, "GET", self.url + path, headers=self.headers,
                           params=params or None)

    async def _public_info(self) -> Any:
        """Openbare info, zonder sleutel. Jellyfin 12.x kent het voorvoegsel ``/emby`` niet meer
        (404, gemeten op 12.4); dan nog één keer zonder, zodat zoeken en "automatisch" ook een
        Jellyfin-server vinden en de test kan zeggen wat er op het adres draait."""
        accept = {"Accept": "application/json"}
        try:
            return await _call(self.services, "GET", self.url + "/System/Info/Public",
                               headers=accept, timeout=5.0)
        except MediaServerError as exc:
            if exc.status != 404 or self.url == self.base:
                raise
        return await _call(self.services, "GET", self.base + "/System/Info/Public",
                           headers=accept, timeout=5.0)

    async def info(self) -> dict[str, Any]:
        data = await self._public_info()
        # hoofdletterongevoelig: Jellyfin 12.x antwoordt tijdens het opstarten in camelCase
        low = {str(k).lower(): v for k, v in data.items()} if isinstance(data, dict) else {}
        if not (low.get("id") or low.get("servername")):
            raise MediaServerError(f"geen {self.label}-server op dit adres")
        # Alleen wie zich als Jellyfin meldt, is Jellyfin. Emby 4.10.2.0 geeft hier geen
        # ``ProductName`` (gemeten): zonder die naam blijft het Emby, zoals in 1.2.2.
        detected = "jellyfin" if "jellyfin" in str(low.get("productname") or "").lower() \
            else "emby"
        return {"name": low.get("servername"), "version": low.get("version"),
                "id": low.get("id"), "kind": detected}

    async def check_key(self) -> None:
        await self._get("/System/Info")

    async def locations(self) -> list[str]:
        folders = await self._get("/Library/VirtualFolders")
        return [loc for f in folders or [] for loc in (f.get("Locations") or [])]

    async def refresh_paths(self, paths: list[str]) -> None:
        await _call(self.services, "POST", self.url + "/Library/Media/Updated",
                    headers=self.headers,
                    json={"Updates": [{"Path": p, "UpdateType": "Modified"} for p in paths]})

    async def refresh_all(self) -> None:
        await _call(self.services, "POST", self.url + "/Library/Refresh", headers=self.headers)

    async def sessions(self) -> list[Session]:
        rows = await self._get("/Sessions", ActiveWithinSeconds=120)
        out: list[Session] = []
        for s in rows or []:
            item = s.get("NowPlayingItem") or None
            if not item:
                continue
            title = item.get("Name") or ""
            if item.get("SeriesName"):
                title = f"{item['SeriesName']} — {title}"
            out.append(Session(user=s.get("UserName") or "", title=title,
                               path=item.get("Path"),
                               paused=bool((s.get("PlayState") or {}).get("IsPaused"))))
        return out

    async def _admin_user(self) -> str | None:
        users = await self._get("/Users")
        for u in users or []:
            if (u.get("Policy") or {}).get("IsAdministrator"):
                return str(u.get("Id"))
        return str(users[0]["Id"]) if users else None

    async def find_item(self, server_path: str, search: str, user_id: str | None
                        ) -> str | None:
        """Zoek het item met precies dit pad (op titel, daarna afleveringen van de serie)."""
        params: dict[str, Any] = {"Recursive": "true", "Fields": "Path",
                                  "IncludeItemTypes": "Movie,Series,Episode",
                                  "SearchTerm": search, "Limit": 50}
        if user_id:
            params["UserId"] = user_id
        found = await self._get("/Items", **params)
        items = (found or {}).get("Items") or []
        for it in items:
            if it.get("Path") == server_path:
                return str(it["Id"])
        for it in items:
            if it.get("Type") != "Series":
                continue
            eps = await self._get("/Items", ParentId=it["Id"], Recursive="true",
                                  IncludeItemTypes="Episode", Fields="Path",
                                  **({"UserId": user_id} if user_id else {}))
            for ep in (eps or {}).get("Items") or []:
                if ep.get("Path") == server_path:
                    return str(ep["Id"])
        return None

    async def playback_test(self, server_path: str, search: str) -> PlaybackResult:
        user_id = await self._admin_user()
        item_id = await self.find_item(server_path, search, user_id)
        if item_id is None:
            return PlaybackResult("not_found", f"{self.label} kent dit bestand (nog) niet")
        params = {"UserId": user_id} if user_id else None
        info = await _call(self.services, "POST", f"{self.url}/Items/{item_id}/PlaybackInfo",
                           headers=self.headers, params=params,
                           json={"DeviceProfile": TV_PROFILE, "EnableDirectPlay": True,
                                 "EnableDirectStream": True, "EnableTranscoding": True,
                                 "AutoOpenLiveStream": False,
                                 "MaxStreamingBitrate": 120_000_000})
        return judge_playback(info, item_id, self.label)


#: taalcodes waaraan Emby/Jellyfin een Nederlandse ondertitel herkennen (ISO 639-1/-2 en namen)
NL_LANGUAGES = {"nl", "nld", "dut", "dutch", "nederlands", "flemish", "vlaams"}


def nl_subtitle_visible(streams: list[dict[str, Any]]) -> bool:
    """Ziet de server een Nederlandse ondertitel (ingebed of extern ``.nl.srt``)?"""
    for st in streams:
        if st.get("Type") != "Subtitle":
            continue
        lang = str(st.get("Language") or "").strip().lower()
        title = str(st.get("DisplayTitle") or st.get("Title") or "").lower()
        if lang in NL_LANGUAGES or lang.split("-")[0] in NL_LANGUAGES or any(
                w in title for w in ("dutch", "nederlands")):
            return True
    return False


def judge_playback(info: dict[str, Any], item_id: str | None = None,
                   label: str = "Emby") -> PlaybackResult:
    if not isinstance(info, dict):
        return PlaybackResult("failed", f"onverwacht antwoord van {label}", item_id)
    if info.get("ErrorCode"):
        return PlaybackResult("failed", f"{label} kan dit niet afspelen ({info['ErrorCode']})",
                              item_id)
    sources = info.get("MediaSources") or []
    if not sources:
        return PlaybackResult("failed", f"{label} vindt geen afspeelbare bron", item_id)
    src = sources[0]
    streams = src.get("MediaStreams") or []
    video: dict[str, Any] = next((s for s in streams if s.get("Type") == "Video"), {})
    extra = {"codec": video.get("Codec"), "range": video.get("VideoRangeType")
             or video.get("VideoRange"), "nl_subtitles": nl_subtitle_visible(streams)}
    if src.get("SupportsDirectPlay") or src.get("SupportsDirectStream"):
        return PlaybackResult("ok", "Speelt direct af op een gangbare tv", item_id, extra)
    if src.get("SupportsTranscoding") and src.get("TranscodingUrl"):
        return PlaybackResult("transcode", f"Speelt af via omzetten door {label}", item_id,
                              extra)
    return PlaybackResult("failed", "Niet direct af te spelen en omzetten lukt niet", item_id,
                          extra)


# -- Plex -----------------------------------------------------------------------------------
class PlexClient:
    kind = "plex"

    def __init__(self, services: dict[str, Any], url: str, api_key: str | None,
                 label: str | None = None) -> None:
        self.services, self.url, self.key = services, url.rstrip("/"), api_key or ""
        self.label = label or "Plex"

    @property
    def headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.key:
            h["X-Plex-Token"] = self.key
        return h

    async def info(self) -> dict[str, Any]:
        data = await _call(self.services, "GET", self.url + "/identity",
                           headers={"Accept": "application/json"}, timeout=5.0)
        mc = (data or {}).get("MediaContainer") or {}
        if not mc.get("machineIdentifier"):
            raise MediaServerError("geen Plex-server op dit adres")
        return {"name": "Plex", "version": mc.get("version"), "id": mc["machineIdentifier"]}

    async def check_key(self) -> None:
        await _call(self.services, "GET", self.url + "/library/sections", headers=self.headers)

    async def _sections(self) -> list[dict[str, Any]]:
        data = await _call(self.services, "GET", self.url + "/library/sections",
                           headers=self.headers)
        return ((data or {}).get("MediaContainer") or {}).get("Directory") or []

    async def locations(self) -> list[str]:
        return [loc["path"] for s in await self._sections() for loc in s.get("Location") or []
                if loc.get("path")]

    async def refresh_paths(self, paths: list[str]) -> None:
        sections = await self._sections()
        for p in paths:
            for s in sections:
                if any(p.startswith(loc.get("path", "").rstrip("/") + "/")
                       for loc in s.get("Location") or []):
                    parent = p.rsplit("/", 1)[0]
                    await _call(self.services, "GET",
                                f"{self.url}/library/sections/{s['key']}/refresh",
                                headers=self.headers, params={"path": parent})

    async def refresh_all(self) -> None:
        await _call(self.services, "GET", self.url + "/library/sections/all/refresh",
                    headers=self.headers)

    async def sessions(self) -> list[Session]:
        data = await _call(self.services, "GET", self.url + "/status/sessions",
                           headers=self.headers)
        out: list[Session] = []
        for m in ((data or {}).get("MediaContainer") or {}).get("Metadata") or []:
            parts = [p for media in m.get("Media") or [] for p in media.get("Part") or []]
            title = m.get("title") or ""
            if m.get("grandparentTitle"):
                title = f"{m['grandparentTitle']} — {title}"
            out.append(Session(user=(m.get("User") or {}).get("title") or "", title=title,
                               path=parts[0].get("file") if parts else None,
                               paused=(m.get("Player") or {}).get("state") == "paused"))
        return out

    async def playback_test(self, server_path: str, search: str) -> PlaybackResult:
        return PlaybackResult("skipped", "Afspeeltest is alleen beschikbaar voor Emby/Jellyfin")


Server = EmbyClient | PlexClient


def make_server(kind: str, services: dict[str, Any], url: str, key: str | None,
                label: str | None = None) -> Server:
    if kind == "plex":
        return PlexClient(services, url, key, label)
    return EmbyClient(services, url, key, "jellyfin" if kind == "jellyfin" else "emby", label)


# -- Bazarr ---------------------------------------------------------------------------------
@dataclass
class WantedSub:
    kind: str                 # episode|movie
    bazarr_id: int            # sonarrSeriesId of radarrId (zoals Bazarr ze kent)
    episode_id: int | None
    title: str
    season: int | None
    episode: int | None
    languages: list[str]


class BazarrClient:
    def __init__(self, services: dict[str, Any], url: str, api_key: str | None) -> None:
        self.services, self.url, self.key = services, url.rstrip("/"), api_key or ""

    @property
    def headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "X-API-KEY": self.key}

    async def info(self) -> dict[str, Any]:
        data = await _call(self.services, "GET", self.url + "/api/system/status",
                           headers=self.headers, timeout=5.0)
        d = (data or {}).get("data") or {}
        return {"name": "Bazarr", "version": d.get("bazarr_version")}

    async def wanted(self) -> list[WantedSub]:
        out: list[WantedSub] = []
        eps = await _call(self.services, "GET", self.url + "/api/episodes/wanted",
                          headers=self.headers, params={"start": 0, "length": -1})
        for e in (eps or {}).get("data") or []:
            season = episode = None
            num = str(e.get("episode_number") or "")
            if "x" in num:
                a, _, b = num.partition("x")
                season, episode = (int(a) if a.isdigit() else None,
                                   int(b) if b.isdigit() else None)
            out.append(WantedSub(
                "episode", int(e.get("sonarrSeriesId") or 0), int(e.get("sonarrEpisodeId") or 0),
                str(e.get("seriesTitle") or ""), season, episode,
                [str(s.get("code2") or "") for s in e.get("missing_subtitles") or []]))
        movies = await _call(self.services, "GET", self.url + "/api/movies/wanted",
                             headers=self.headers, params={"start": 0, "length": -1})
        for m in (movies or {}).get("data") or []:
            out.append(WantedSub(
                "movie", int(m.get("radarrId") or 0), None, str(m.get("title") or ""), None,
                None, [str(s.get("code2") or "") for s in m.get("missing_subtitles") or []]))
        return out

    async def search_missing(self, w: WantedSub) -> None:
        if w.kind == "movie":
            await _call(self.services, "PATCH", self.url + "/api/movies", headers=self.headers,
                        params={"radarrid": w.bazarr_id, "action": "search-missing"})
        else:
            await _call(self.services, "PATCH", self.url + "/api/series", headers=self.headers,
                        params={"seriesid": w.bazarr_id, "action": "search-missing"})


# -- OpenSubtitles ---------------------------------------------------------------------------
OS_API = "https://api.opensubtitles.com/api/v1"


class OpenSubtitlesClient:
    def __init__(self, services: dict[str, Any], api_key: str, username: str,
                 password: str) -> None:
        self.services, self.key, self.user, self.pw = services, api_key, username, password
        self.token: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        h = {"Accept": "application/json", "Api-Key": self.key,
             "User-Agent": f"MediaGuard v{__version__}"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def login(self) -> None:
        # gebruikersnaam en wachtwoord staan als geheim opgeslagen: ook die nooit in een melding
        data = await _call(self.services, "POST", OS_API + "/login", headers=self.headers,
                           json={"username": self.user, "password": self.pw},
                           body_secrets=(self.pw, self.user))
        self.token = (data or {}).get("token")
        if not self.token:
            raise MediaServerError("OpenSubtitles: inloggen mislukt")

    async def search(self, *, tmdb_id: int | None, imdb_id: str | None, season: int | None,
                     episode: int | None) -> str | None:
        """Beste Nederlandse ondertitel (meest gedownload) → file_id, of None."""
        params: dict[str, Any] = {"languages": "nl"}
        if season is not None and episode is not None:
            params.update(season_number=season, episode_number=episode)
            if tmdb_id:
                params["parent_tmdb_id"] = tmdb_id
            elif imdb_id:
                params["parent_imdb_id"] = imdb_id.lstrip("t")
        elif tmdb_id:
            params["tmdb_id"] = tmdb_id
        elif imdb_id:
            params["imdb_id"] = imdb_id.lstrip("t")
        else:
            return None
        data = await _call(self.services, "GET", OS_API + "/subtitles", headers=self.headers,
                           params=params)
        best: tuple[int, str] | None = None
        for row in (data or {}).get("data") or []:
            attrs = row.get("attributes") or {}
            if attrs.get("language") not in (None, "nl"):
                continue
            files = attrs.get("files") or []
            if not files:
                continue
            score = int(attrs.get("download_count") or 0)
            if best is None or score > best[0]:
                best = (score, str(files[0]["file_id"]))
        return best[1] if best else None

    async def download(self, file_id: str) -> bytes:
        if not self.token:
            await self.login()
        data = await _call(self.services, "POST", OS_API + "/download", headers=self.headers,
                           json={"file_id": int(file_id) if file_id.isdigit() else file_id})
        link = (data or {}).get("link")
        if not link:
            raise MediaServerError("OpenSubtitles gaf geen downloadlink (dagquotum op?)")
        # de downloadlink draagt geen sleutel; een omleiding naar een ander adres (zoals 1.2.2
        # die volgde) blijft toegestaan
        content = await _call(self.services, "GET", link, headers={}, raw=True, any_host=True)
        return bytes(content)
