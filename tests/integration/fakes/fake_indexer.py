#!/usr/bin/env python3
"""Fake Newznab / Torznab indexer for MediaGuard fork integration tests (stdlib only).

Endpoints used by Sonarr/Radarr:
  GET /api?t=caps                         (newznab)   | /torznab/api?t=caps
  GET /api?t=search|tvsearch|movie&...    RSS (no query) and searches
  GET /getnzb/<id>.nzb                    minimal valid NZB
  GET /gettorrent/<id>.torrent            minimal valid .torrent (torznab)

Admin API (JSON):
  POST   /admin/releases   {"title", "categories":[5040], "size", "tvdbid", "season", "episode",
                            "absolute", "tmdbid", "imdbid", "seeders", "age_hours",
                            "search_only": false}  -> release (search_only: never in RSS)
  GET    /admin/releases
  DELETE /admin/releases                 remove all test releases (3 unrelated filler releases stay)
  GET    /admin/queries[?apikey=..]      every API hit (t, params, apikey, time, result count)
  DELETE /admin/queries
  GET    /admin/downloads                every NZB/torrent download (apikey, id)
  GET    /health

Every API key is accepted; the key identifies the calling instance in /admin/queries, so each
Sonarr/Radarr instance should get its own key (e.g. its instance id).
"""
import argparse
import hashlib
import json
import re
import threading
import time
import uuid
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape, quoteattr

LOCK = threading.Lock()
RELEASES = []
QUERIES = []
DOWNLOADS = []

CAPS = """<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server version="1.0" title="MediaGuard fake indexer" />
  <limits max="100" default="100" />
  <retention days="3000" />
  <registration available="no" open="no" />
  <searching>
    <search available="yes" supportedParams="q" />
    <tv-search available="yes" supportedParams="q,season,ep,tvdbid" />
    <movie-search available="yes" supportedParams="q,imdbid,tmdbid" />
  </searching>
  <categories>
    <category id="2000" name="Movies">
      <subcat id="2030" name="SD" /><subcat id="2040" name="HD" /><subcat id="2045" name="UHD" />
    </category>
    <category id="5000" name="TV">
      <subcat id="5030" name="SD" /><subcat id="5040" name="HD" /><subcat id="5045" name="UHD" /><subcat id="5070" name="Anime" />
    </category>
  </categories>
</caps>"""


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()


FILLER = [  # always present, never removed: real feeds always carry unrelated releases and the
            # Sonarr/Radarr indexer test refuses an empty feed
    {"title": "Unrelated.Show.S01E01.720p.HDTV.x264-FILLER", "categories": [5040]},
    {"title": "[Filler] Unrelated Anime - 01 [720p]", "categories": [5070]},
    {"title": "Unrelated.Movie.1999.720p.BluRay.x264-FILLER", "categories": [2040]},
]


def make_release(data):
    rid = data.get("id") or uuid.uuid4().hex[:16]
    return {
        "id": rid,
        "title": data["title"],
        "categories": [int(c) for c in data.get("categories", [5040])],
        "size": int(data.get("size", 1_500_000_000)),
        "tvdbid": data.get("tvdbid"),
        "season": data.get("season"),
        "episode": data.get("episode"),
        "absolute": data.get("absolute"),
        "tmdbid": data.get("tmdbid"),
        "imdbid": data.get("imdbid"),
        "seeders": int(data.get("seeders", 25)),
        "published": time.time() - float(data.get("age_hours", 0.5)) * 3600,
        "infohash": hashlib.sha1(rid.encode()).hexdigest(),
        "filler": bool(data.get("filler", False)),
        # only returned for searches (q/tvdbid/tmdbid/...), never in the RSS feed: simulates an
        # old release that RSS no longer carries (missing-episode backfill)
        "search_only": bool(data.get("search_only", False)),
    }


def reset_releases():
    RELEASES[:] = [make_release(dict(f, id=f"filler{i}", filler=True, age_hours=48)) for i, f in enumerate(FILLER)]


def same_number(value, param):
    digits = re.sub(r"[^0-9]", "", str(param))
    return value is not None and digits != "" and int(value) == int(digits)


def matches(release, params, base_t):
    t = params.get("t", "search")
    cats = [c for c in params.get("cat", "").split(",") if c]
    if cats:
        rcats = {str(c) for c in release["categories"]}
        # parent category matching (5000 matches 5040)
        expanded = set(rcats) | {c[:1] + "000" for c in rcats}
        if not expanded.intersection(cats):
            return False
    if t == "tvsearch":
        if release.get("tvdbid") is None and release.get("tmdbid") is not None:
            return False
        if params.get("tvdbid") and str(release.get("tvdbid")) != params["tvdbid"]:
            return False
        if params.get("season") and not same_number(release.get("season"), params["season"]):
            return False
        if params.get("ep") and not same_number(release.get("episode"), params["ep"]):
            return False
    if t == "movie":
        if params.get("tmdbid") and str(release.get("tmdbid")) != params["tmdbid"]:
            return False
        if params.get("imdbid"):
            want = params["imdbid"].lower().lstrip("t")
            have = (release.get("imdbid") or "").lower().lstrip("t")
            if want != have:
                return False
    q = params.get("q")
    if q:
        title_tokens = set(norm(release["title"]))
        for token in norm(q):
            if token not in title_tokens and token.lstrip("0") not in {x.lstrip("0") for x in title_tokens}:
                return False
    return True


def item_xml(release, host, apikey, torznab):
    rid = release["id"]
    pub = formatdate(release["published"], usegmt=True)
    if torznab:
        link = f"http://{host}/gettorrent/{rid}.torrent?apikey={apikey}"
        enclosure_type = "application/x-bittorrent"
        ns = "torznab"
    else:
        link = f"http://{host}/getnzb/{rid}.nzb?apikey={apikey}"
        enclosure_type = "application/x-nzb"
        ns = "newznab"
    attrs = [("size", release["size"])]
    attrs += [("category", c) for c in release["categories"]]
    for key in ("tvdbid", "season", "episode", "tmdbid"):
        if release.get(key) is not None:
            attrs.append((key, release[key]))
    if release.get("imdbid"):
        attrs.append(("imdb", release["imdbid"].lower().lstrip("t")))
    if torznab:
        attrs += [("seeders", release.get("seeders", 10)), ("peers", release.get("seeders", 10) + 2),
                  ("infohash", release["infohash"])]
    attr_xml = "".join(f'<{ns}:attr name="{k}" value={quoteattr(str(v))} />' for k, v in attrs)
    return (f"<item><title>{escape(release['title'])}</title>"
            f"<guid isPermaLink=\"true\">http://{host}/details/{rid}</guid>"
            f"<link>{escape(link)}</link><comments>http://{host}/details/{rid}</comments>"
            f"<pubDate>{pub}</pubDate><category>{release['categories'][0]}</category>"
            f"<description>{escape(release['title'])}</description>"
            f"<enclosure url={quoteattr(link)} length=\"{release['size']}\" type=\"{enclosure_type}\" />"
            f"{attr_xml}</item>")


def nzb_for(release):
    title = escape(release["title"])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head><meta type="title">{title}</meta><meta type="mediaguard-release-id">{release['id']}</meta></head>
  <file poster="mediaguard@test.invalid" date="{int(release['published'])}" subject="{title} - &quot;{title}.mkv&quot; yEnc (1/1)">
    <groups><group>alt.binaries.mediaguard.test</group></groups>
    <segments><segment bytes="{release['size']}" number="1">{release['id']}@mediaguard.test</segment></segments>
  </file>
</nzb>
""".encode()


def bencode(value):
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(k) + bencode(value[k]) for k in sorted(value)) + b"e"
    raise TypeError(type(value))


def torrent_for(release):
    info = {"name": release["title"] + ".mkv", "length": release["size"], "piece length": 262144,
            "pieces": hashlib.sha1(release["id"].encode()).digest()}
    return bencode({"announce": "http://tracker.invalid/announce", "info": info})


class Handler(BaseHTTPRequestHandler):
    server_version = "MediaGuardFakeIndexer/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, indent=1).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        params = {k: v[-1] for k, v in parse_qs(url.query).items()}
        path = url.path.rstrip("/") or "/"
        host = self.headers.get("Host") or self.server.public_host
        if path == "/health":
            return self._send(200, {"status": "ok", "releases": len(RELEASES)})
        if path in ("/api", "/torznab/api"):
            return self._api(params, host, torznab=path.startswith("/torznab"))
        m = re.match(r"^/get(nzb|torrent)/([^/.]+)\.(nzb|torrent)$", path)
        if m:
            with LOCK:
                rel = next((r for r in RELEASES if r["id"] == m.group(2)), None)
                DOWNLOADS.append({"time": time.time(), "id": m.group(2), "apikey": params.get("apikey"),
                                  "title": rel["title"] if rel else None})
            if not rel:
                return self._send(404, {"error": "unknown release"})
            if m.group(1) == "nzb":
                return self._send(200, nzb_for(rel), "application/x-nzb")
            return self._send(200, torrent_for(rel), "application/x-bittorrent")
        if path == "/admin/releases":
            with LOCK:
                return self._send(200, RELEASES)
        if path == "/admin/queries":
            with LOCK:
                rows = [q for q in QUERIES if not params.get("apikey") or q["apikey"] == params["apikey"]]
                return self._send(200, rows)
        if path == "/admin/downloads":
            with LOCK:
                return self._send(200, DOWNLOADS)
        return self._send(404, {"error": "not found"})

    def _api(self, params, host, torznab):
        t = params.get("t", "search")
        apikey = params.get("apikey", "")
        if t == "caps":
            with LOCK:
                QUERIES.append({"time": time.time(), "apikey": apikey, "t": t, "params": params, "results": 0})
            return self._send(200, CAPS, "application/xml")
        if t not in ("search", "tvsearch", "movie"):
            return self._send(200, '<error code="202" description="No such function" />', "application/xml")
        offset = int(params.get("offset") or 0)
        limit = int(params.get("limit") or 100)
        with LOCK:
            is_rss = not any(params.get(k) for k in ("q", "tvdbid", "tmdbid", "imdbid", "season", "ep"))
            hits = [r for r in sorted(RELEASES, key=lambda r: -r["published"])
                    if matches(r, params, t) and not (is_rss and r.get("search_only"))]
            page = hits[offset:offset + limit]
            QUERIES.append({"time": time.time(), "apikey": apikey, "t": t, "params": params,
                            "rss": is_rss, "results": len(page), "titles": [r["title"] for r in page]})
        ns = ('xmlns:torznab="http://torznab.com/schemas/2015/feed"' if torznab
              else 'xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"')
        items = "".join(item_xml(r, host, apikey, torznab) for r in page)
        body = (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" {ns}>'
                f"<channel><title>MediaGuard fake indexer</title><description>fake</description>"
                f'<{"torznab" if torznab else "newznab"}:response offset="{offset}" total="{len(hits)}" />'
                f"{items}</channel></rss>")
        return self._send(200, body, "application/rss+xml")

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/admin/releases":
            data = self._json_body()
            if "title" not in data:
                return self._send(400, {"error": "title required"})
            release = make_release(data)
            with LOCK:
                RELEASES.append(release)
            return self._send(201, release)
        return self._send(404, {"error": "not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path.rstrip("/")
        with LOCK:
            if path == "/admin/releases":
                reset_releases()
            elif path == "/admin/queries":
                QUERIES.clear()
                DOWNLOADS.clear()
            else:
                return self._send(404, {"error": "not found"})
        return self._send(200, {"ok": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9117)
    ap.add_argument("--public-host", default="fakeindexer:9117")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    reset_releases()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    srv.public_host = args.public_host
    srv.verbose = args.verbose
    print(f"fake indexer listening on :{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
