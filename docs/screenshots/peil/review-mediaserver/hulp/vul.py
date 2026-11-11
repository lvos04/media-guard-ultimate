"""Peil-review mediaservers: testgegevens via de echte API van de core (zoals een beheerder het in de app doet).

Gebruikers beheerder / operator (testen en zoeken, niets wijzigen) / kijker (alleen lezen) / engels (beheerder,
en-US); bibliotheek uit de nep-arr; en zes mediaservers, elk in een andere stand:

  1 Emby (de bestaande koppeling)  → nep-Emby, sleutel klopt            → Gekoppeld · Gezond (echt gemeten tegen de nep)
  2 Jelly Leon (Jellyfin)          → nep-Jellyfin, sleutel klopt        → Gekoppeld · Gezond
  3 Jellyfin woonkamer             → adres waar niets luistert          → Gekoppeld · Storing (laatste goede meting: zie onder)
  4 Emby zolder                    → nep-Emby, daarna ontkoppeld        → Ontkoppeld
  5 Plex                           → adres, geen sleutel                → Niet gekoppeld (sleutel ontbreekt)
  6 Jellyfin gastenkamer           → nep-Jellyfin, sleutel klopt        → Gekoppeld · Verouderd (meting te oud; zie onder)

Twee dingen kan de API niet maken en staan daarom rechtstreeks in de testdatabase (zoals ../../voor/hulp/vul.py
dat voor de quarantainerijen doet): de meting van server 6 is drie uur teruggezet (te oud), en server 3 heeft een
"laatste goede meting" van gisteren gekregen. Dat is de wegwerp-testdatabase van een verse core in $MGP_DATA (buiten de
repo, door start.sh aangemaakt), nooit de database van een echte installatie. Eén server, twee servers en een
ontkoppelde server 1 bootst meet.mjs na in de browser. Wachtwoord alleen uit de omgeving (MGP_PW); nergens
weggeschreven.
"""
import datetime
import json
import os
import sqlite3

import httpx

BASE = "http://127.0.0.1:18390"
PW = os.environ["MGP_PW"]
MS_KEY = "nep-sleutel-peil-mediaserver"
c = httpx.Client(base_url=BASE, timeout=120)
if c.get("/api/v1/auth/setup-status").json().get("needs_setup"):
    print("setup", c.post("/api/v1/auth/setup", json={"username": "beheerder", "password": PW}).status_code)
print("login", c.post("/api/v1/auth/login", json={"username": "beheerder", "password": PW}).status_code)
h = {"X-CSRF-Token": c.get("/api/v1/auth/me").json().get("csrf_token") or ""}
print("sync", c.post("/api/v1/library/sync", params={"mode": "inline"}, headers=h).status_code)
for user, role in (("operator", "operator"), ("kijker", "viewer"), ("engels", "admin")):
    print("gebruiker", user, c.post("/api/v1/auth/users", json={"username": user, "password": PW, "role": role}, headers=h).status_code)
e = httpx.Client(base_url=BASE, timeout=60)
e.post("/api/v1/auth/login", json={"username": "engels", "password": PW})
eh = {"X-CSRF-Token": e.get("/api/v1/auth/me").json().get("csrf_token") or ""}
print("locale en-US", e.put("/api/v1/auth/me/locale", json={"locale": "en-US"}, headers=eh).status_code)


def config(**body):
    r = c.post("/api/v1/mediaserver/config", json=body, headers=h)
    data = r.json()
    print("config", {k: v for k, v in body.items() if k != "api_key"}, r.status_code, (data.get("test") or {}).get("message_nl"))
    return data


EMBY, JELLY, NIETS = "http://127.0.0.1:18931", "http://127.0.0.1:18932", "http://127.0.0.1:9"
config(kind="emby", url=EMBY, api_key=MS_KEY)                                                   # 1
config(add=True, kind="jellyfin", url=JELLY, label="Jelly Leon", api_key=MS_KEY)                # 2
config(add=True, kind="jellyfin", url=NIETS, label="Jellyfin woonkamer", api_key=MS_KEY)        # 3
vier = config(add=True, kind="emby", url=EMBY, label="Emby zolder", api_key=MS_KEY)["server_id"]  # 4
config(server_id=vier, enabled=False)
config(add=True, kind="plex", url="http://127.0.0.1:18933", label="Plex")                       # 5
zes = config(add=True, kind="jellyfin", url=JELLY, label="Jellyfin gastenkamer", api_key=MS_KEY)["server_id"]  # 6

if os.environ.get("MGP_DATA"):
    con = sqlite3.connect(os.path.join(os.environ["MGP_DATA"], "mediaguard.db"))
    now = datetime.datetime.now(datetime.UTC)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

    def contact(sid, fn):
        key = f"mediaserver:{sid}:contact"
        row = con.execute("SELECT value_json FROM system_state WHERE key=?", (key,)).fetchone()
        value = fn(json.loads(row[0]) if row else {})
        con.execute("INSERT INTO system_state(key,value_json,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                    "value_json=excluded.value_json, updated_at=excluded.updated_at", (key, json.dumps(value), iso(now)))

    oud = iso(now - datetime.timedelta(hours=3))
    contact(zes, lambda v: {**v, "at": oud, "ok": True, "last_ok_at": oud})
    contact(3, lambda v: {**v, "last_ok_at": iso(now - datetime.timedelta(days=1))})
    con.commit()
    print("testdatabase: meting server", zes, "drie uur oud; server 3 laatste goede meting gisteren")
st = c.get("/api/v1/mediaserver/status").json()
for s in st["servers"]:
    print(s["server_id"], s["label"], "| enabled", s["enabled"], "| key", s["key_set"], "| connected", s["connected"], "|", s["contact"])
