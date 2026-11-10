import { execFileSync } from "node:child_process";
import path from "node:path";

/**
 * Seeds quarantine rows straight into the running core's SQLite DB (test-only; the core has no
 * API to create quarantine items because only verified replacements produce them).
 * Returns the created item ids: one with a passed release check, one unchecked.
 */
export function seedQuarantine(): number[] {
  const db = path.join(process.env.MG_E2E_DATA_DIR as string, "mediaguard.db");
  const script = `
import sqlite3, sys, datetime
now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
c = sqlite3.connect(sys.argv[1]); c.execute("PRAGMA foreign_keys=ON")
ids = []
for name, passed in (("Severance - S02E07.mkv", True), ("Dark - S01E01.mkv", False)):
    cur = c.execute("INSERT INTO replacement_transactions(kind,instance_id,media_kind,provider_id,library_path,final_path,staged_path,source_snapshot_json,state,created_at,updated_at) VALUES ('replace','sonarr-series','episode_file','1',?,?,?,'{}','committed',?,?)", ("/media/tv/"+name, "/media/tv/"+name, "/work/staging/"+name, now, now))
    tx = cur.lastrowid
    cur = c.execute("INSERT INTO quarantine_items(transaction_id,instance_id,media_kind,provider_id,original_path,quarantine_path,size,method,status,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (tx, "sonarr-series", "episode_file", "1", "/media/tv/"+name, "/work/quarantine/"+name, 4*1024**3, "link", "restorable", "E2E-seed: vervangen door AV1", now))
    ids.append(cur.lastrowid)
    if passed:
        c.execute("INSERT INTO quarantine_release_checks(item_id,status,checks_json,checked_at) VALUES (?,?,?,?)", (cur.lastrowid, "passed", '[{"name":"full_decode","passed":true,"detail":"0 fouten"}]', now))
c.commit(); print(",".join(map(str, ids)))
`;
  const out = execFileSync(process.env.MG_E2E_PYTHON as string, ["-c", script, db], { encoding: "utf8" });
  return out.trim().split(",").map(Number);
}

/**
 * Builds Sonarr-like backup zips for the migration wizard (test-only), mirroring the schema of
 * apps/core/tests/test_migration_fakes.py: a real SQLite `sonarr.db` + `config.xml`.
 * Returns { valid, invalid } file paths. `invalid` is a well-formed zip without a database.
 */
export function makeBackupZips(): { valid: string; invalid: string } {
  const dir = path.join(process.env.MG_E2E_DATA_DIR as string, "fixtures");
  const script = `
import os, sqlite3, sys, tempfile, zipfile, json
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
tmp = tempfile.mkdtemp(); db = os.path.join(tmp, "sonarr.db")
c = sqlite3.connect(db)
c.executescript("""
CREATE TABLE VersionInfo (Version INTEGER, AppliedOn TEXT, Description TEXT);
CREATE TABLE Tags (Id INTEGER PRIMARY KEY, Label TEXT);
CREATE TABLE RootFolders (Id INTEGER PRIMARY KEY, Path TEXT);
CREATE TABLE QualityProfiles (Id INTEGER PRIMARY KEY, Name TEXT, Cutoff INTEGER, Items TEXT, UpgradeAllowed INTEGER);
CREATE TABLE CustomFormats (Id INTEGER PRIMARY KEY, Name TEXT, Specifications TEXT);
CREATE TABLE Indexers (Id INTEGER PRIMARY KEY, Name TEXT, Implementation TEXT, Settings TEXT, ConfigContract TEXT, EnableRss INTEGER, EnableAutomaticSearch INTEGER, EnableInteractiveSearch INTEGER, Priority INTEGER, Tags TEXT, DownloadClientId INTEGER);
CREATE TABLE DownloadClients (Id INTEGER PRIMARY KEY, Enable INTEGER, Name TEXT, Implementation TEXT, Settings TEXT, ConfigContract TEXT, Priority INTEGER, Tags TEXT);
CREATE TABLE RemotePathMappings (Id INTEGER PRIMARY KEY, Host TEXT, RemotePath TEXT, LocalPath TEXT);
CREATE TABLE Config (Id INTEGER PRIMARY KEY, Key TEXT, Value TEXT);
CREATE TABLE History (Id INTEGER PRIMARY KEY);
CREATE TABLE Series (Id INTEGER PRIMARY KEY, TvdbId INTEGER, Title TEXT, Monitored INTEGER, MonitorNewItems INTEGER, SeriesType INTEGER, QualityProfileId INTEGER, Path TEXT, Tags TEXT, Seasons TEXT, SeasonFolder INTEGER, Status INTEGER);
CREATE TABLE Episodes (Id INTEGER PRIMARY KEY, SeriesId INTEGER, SeasonNumber INTEGER, EpisodeNumber INTEGER, Monitored INTEGER, AirDateUtc TEXT, EpisodeFileId INTEGER);
CREATE TABLE EpisodeFiles (Id INTEGER PRIMARY KEY, SeriesId INTEGER, SeasonNumber INTEGER, RelativePath TEXT, Size INTEGER);
CREATE TABLE ImportLists (Id INTEGER PRIMARY KEY, Name TEXT, Implementation TEXT, Settings TEXT, ConfigContract TEXT, EnableAutomaticAdd INTEGER, SearchForMissingEpisodes INTEGER, RootFolderPath TEXT, QualityProfileId INTEGER, Tags TEXT);
""")
c.execute("INSERT INTO VersionInfo VALUES (210, '2026-01-01', 'x')")
c.execute("INSERT INTO Tags VALUES (1, 'anime')")
c.execute("INSERT INTO RootFolders VALUES (1, '/anime')")
c.execute("INSERT INTO QualityProfiles VALUES (1, 'Anime 1080p', 3, '[]', 1)")
c.execute("INSERT INTO Indexers VALUES (1, 'Nyaa', 'Torznab', '{}', 'TorznabSettings', 1, 1, 1, 25, '[]', 0)")
c.execute("INSERT INTO DownloadClients VALUES (1, 1, 'qBittorrent', 'QBittorrent', '{\\"category\\": \\"tv-anime\\"}', 'QBittorrentSettings', 1, '[]')")
c.execute("INSERT INTO Config(Key, Value) VALUES ('rsssyncinterval', '15')")
for sid, tvdb, title in ((1, 900817, 'Golfbreker'), (2, 267440, 'Attack on Titan')):
    c.execute("INSERT INTO Series VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (sid, tvdb, title, 1, 0, 2, 1, '/anime/' + title, '[1]', json.dumps([{"seasonNumber": 1, "monitored": True}]), 1, 0))
    for ep in (1, 2):
        eid = sid * 100 + ep
        c.execute("INSERT INTO Episodes VALUES (?,?,?,?,?,?,?)", (eid, sid, 1, ep, 1, '2020-01-0%d 10:00:00' % ep, eid + 5000))
        c.execute("INSERT INTO EpisodeFiles VALUES (?,?,?,?,?)", (eid + 5000, sid, 1, 'S01E0%d.mkv' % ep, 1))
c.commit(); c.close()
valid = os.path.join(out, "sonarr_backup_v4.0.14.2939_2026.10.01_10.00.00.zip")
with zipfile.ZipFile(valid, "w") as z:
    z.write(db, "sonarr.db")
    z.writestr("config.xml", "<Config>\\n  <Port>8989</Port>\\n  <ApiKey>OLDKEY</ApiKey>\\n  <UrlBase></UrlBase>\\n  <InstanceName>Sonarr-Anime</InstanceName>\\n</Config>\\n")
invalid = os.path.join(out, "geen-backup.zip")
with zipfile.ZipFile(invalid, "w") as z:
    z.writestr("leesmij.txt", "dit is geen Sonarr-back-up")
print(valid); print(invalid)
`;
  const out = execFileSync(process.env.MG_E2E_PYTHON as string, ["-I", "-c", script, dir], { encoding: "utf8" }).trim().split("\n");
  return { valid: out[0], invalid: out[1] };
}

/**
 * Seeds a small, linked world for the link crawler: one series with an episode and file, one
 * movie, an identity doubt, an AV1 job awaiting activation, a grab + import (Activiteit → Recent),
 * notifications with links and an optimisation plan with a (now automatic) review item.
 * Returns the titles the crawler expects on detail pages.
 */
export function seedLinkWorld(quarantineId: number): { series: string; movie: string } {
  const db = path.join(process.env.MG_E2E_DATA_DIR as string, "mediaguard.db");
  const script = `
import sqlite3, sys, json, datetime
now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
c = sqlite3.connect(sys.argv[1]); qid = int(sys.argv[2])
c.execute("INSERT OR IGNORE INTO lib_items(instance_id,kind,provider_id,title,year,monitored,has_file,episode_count,episode_file_count,path,synced_at) VALUES ('sonarr-series','series',501,'Crawler Serie',2020,1,1,1,1,'/data/media/tv/Crawler Serie',?)", (now,))
c.execute("INSERT OR IGNORE INTO lib_items(instance_id,kind,provider_id,title,year,monitored,has_file,file_id,path,synced_at) VALUES ('radarr-films','movie',601,'Crawler Film',2021,1,1,9001,'/data/media/movies/Crawler Film (2021)',?)", (now,))
c.execute("INSERT OR IGNORE INTO lib_episodes(instance_id,provider_id,series_provider_id,season_number,episode_number,title,air_date_utc,monitored,has_file,episode_file_id,synced_at) VALUES ('sonarr-series',7001,501,1,1,'Piloot','2020-01-01T00:00:00Z',1,1,8001,?)", (now,))
c.execute("INSERT OR IGNORE INTO lib_files(instance_id,kind,provider_id,owner_provider_id,path,size,synced_at) VALUES ('sonarr-series','episode',8001,501,'/data/media/tv/Crawler Serie/S01E01.mkv',1000,?)", (now,))
c.execute("INSERT OR IGNORE INTO lib_files(instance_id,kind,provider_id,owner_provider_id,path,size,synced_at) VALUES ('radarr-films','movie',9001,601,'/data/media/movies/Crawler Film (2021)/film.mkv',1000,?)", (now,))
c.execute("INSERT INTO identity_results(instance_id,kind,provider_id,file_path,status,confidence,actionable,expected_json,actual_json,evidence_json,reason_nl,source,created_at) VALUES ('sonarr-series','episode_file','8001','/data/media/tv/Crawler Serie/S01E01.mkv','mismatch',90,1,'[]','[]','[]','Sterk bewijs: andere aflevering','import',?)", (now,))
c.execute("INSERT INTO encoding_jobs(instance_id,media_kind,provider_id,source_path,status,policy_json,source_snapshot_json,staged_output_json,created_at,updated_at) VALUES ('radarr-films','movie_file','9001','/data/media/movies/Crawler Film (2021)/film.mkv','awaiting_activation','{}','{\\"size\\": 1000}','{\\"size\\": 400, \\"path\\": \\"/work/staging/1/out.mkv\\"}',?,?)", (now, now))
c.execute("INSERT OR IGNORE INTO release_provenance(instance_id,event_type,download_id,release_title,series_id,idempotency_key,received_at) VALUES ('sonarr-series','Grab','dl1','Crawler.Serie.S01E01.1080p',501,'crawl-grab',?)", (now,))
c.execute("INSERT OR IGNORE INTO import_events(instance_id,event_type,download_id,series_id,file_path,idempotency_key,payload_json,received_at) VALUES ('sonarr-series','Download','dl1',501,'/data/media/tv/Crawler Serie/S01E01.mkv','crawl-imp','{}',?)", (now,))
for sev, title, link in (("warning","Radarr Films: Offline","/instellingen/instanties"),("warning","Anime wacht","/anime"),("warning","Vrijgavecontrole mislukt","/quarantaine?item=%d" % qid),("warning","Inrichting wijkt af","/inrichten")):
    c.execute("INSERT INTO notifications(ts,severity,title,body,link,read) VALUES (?,?,?,?,?,0)", (now, sev, title, "e2e", link))
cur = c.execute("INSERT INTO optimization_plans(status,filters_json,summary_json,forecast_json,item_count,created_at,updated_at) VALUES ('completed','{}','{}','{}',1,?,?)", (now, now))
c.execute("INSERT INTO optimization_items(plan_id,priority,instance_id,media_kind,provider_id,title,action,bucket,reason_nl,status) VALUES (?,2,'sonarr-series','episode_file','8001','Crawler Serie','verify','onzeker','Identiteit uncertain','auto_identity')", (cur.lastrowid,))
c.commit()
`;
  execFileSync(process.env.MG_E2E_PYTHON as string, ["-c", script, db, String(quarantineId)], { encoding: "utf8" });
  return { series: "Crawler Serie", movie: "Crawler Film" };
}
