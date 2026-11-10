"""Peil-nulmeting: beheerder aanmaken, bibliotheek inventariseren uit de nep-arr, quarantainerijen zoals e2e/seed.ts.
Wachtwoord alleen uit de omgeving (MGP_PW); nergens weggeschreven."""
import datetime, os, sqlite3, sys, httpx

BASE = "http://127.0.0.1:18190"
PW = os.environ["MGP_PW"]
c = httpx.Client(base_url=BASE, timeout=120)
if c.get("/api/v1/auth/setup-status").json().get("needs_setup"):
    r = c.post("/api/v1/auth/setup", json={"username": "beheerder", "password": PW}); print("setup", r.status_code)
r = c.post("/api/v1/auth/login", json={"username": "beheerder", "password": PW}); print("login", r.status_code)
me = c.get("/api/v1/auth/me").json(); h = {"X-CSRF-Token": me.get("csrf_token") or ""}
inst = c.get("/api/v1/instances").json(); inst = inst if isinstance(inst, list) else inst.get("items", []); print("instanties", [(i["id"], i.get("url")) for i in inst])
import sys as _s
if "--alleen-gebruikers" not in _s.argv:
    r = c.post("/api/v1/library/sync", params={"mode": "inline"}, headers=h); print("sync", r.status_code, str(r.json())[:600])
if "--alleen-gebruikers" not in _s.argv:
  db = os.path.join(os.environ["MGP_DATA"], "mediaguard.db")
  now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
  con = sqlite3.connect(db); con.execute("PRAGMA foreign_keys=ON")
  for name, passed in (("Bergpas - S02E03.mkv", True), ("Stadshaven - S01E02.mkv", False)):
      cur = con.execute("INSERT INTO replacement_transactions(kind,instance_id,media_kind,provider_id,library_path,final_path,staged_path,source_snapshot_json,state,created_at,updated_at) VALUES ('replace','sonarr-series','episode_file','1',?,?,?,'{}','committed',?,?)", ("/media/tv/"+name, "/media/tv/"+name, "/work/staging/"+name, now, now))
      cur = con.execute("INSERT INTO quarantine_items(transaction_id,instance_id,media_kind,provider_id,original_path,quarantine_path,size,method,status,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (cur.lastrowid, "sonarr-series", "episode_file", "1", "/media/tv/"+name, "/work/quarantine/"+name, 4*1024**3, "link", "restorable", "Testdata: vervangen door AV1", now))
      if passed:
          con.execute("INSERT INTO quarantine_release_checks(item_id,status,checks_json,checked_at) VALUES (?,?,?,?)", (cur.lastrowid, "passed", '[{"name":"full_decode","passed":true,"detail":"0 fouten"}]', now))
  con.commit(); print("quarantaine: 2 rijen")
# extra gebruikers: kijker (alleen lezen) en een beheerder met en-US (zelfde wachtwoord, alleen in de omgeving)
for user, role in (("kijker", "viewer"), ("engels", "admin")):
    r = c.post("/api/v1/auth/users", json={"username": user, "password": PW, "role": role}, headers=h); print("gebruiker", user, r.status_code)
e = httpx.Client(base_url=BASE, timeout=60)
e.post("/api/v1/auth/login", json={"username": "engels", "password": PW})
eh = {"X-CSRF-Token": e.get("/api/v1/auth/me").json().get("csrf_token") or ""}
print("locale en-US", e.put("/api/v1/auth/me/locale", json={"locale": "en-US"}, headers=eh).status_code)
