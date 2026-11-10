"""Typfout in een mediamap herstellen: alleen na bevestiging, Sonarr verplaatst zelf."""

from __future__ import annotations

import os
from pathlib import Path

from test_arr_fakes import FakeArr, b1ctx, make_series, wire  # noqa: F401

from mediaguard import jobs
from mediaguard.library.sync import sync_instance


def _world(ctx, tmp_path: Path) -> tuple[FakeArr, Path, Path]:
    old, new = tmp_path / "Anime Fims", tmp_path / "Anime Films"
    fake = wire(ctx, "sonarr-anime", FakeArr("sonarr", api_key="anime-key-222"))
    fake.root_folders = [{"id": 1, "path": f"{old}/", "accessible": True,
                          "freeSpace": 10**12, "unmappedFolders": []}]
    for sid, title in ((1, "Frieren"), (2, "Mosgeest"), (3, "Monster")):
        (old / title).mkdir(parents=True)
        (old / title / "ep1.mkv").write_bytes(b"x")
        fake.add_series(make_series(sid, title, 1000 + sid, root=f"{old}/"), [])

    def editor(body):
        for sid in body["seriesIds"]:
            s = fake.series[sid]
            dest = Path(body["rootFolderPath"]) / s["title"]
            os.rename(s["path"], dest)  # wat Sonarr zelf doet bij moveFiles
            s["path"], s["rootFolderPath"] = str(dest), body["rootFolderPath"]
        return 202, {}
    fake.intercept[("PUT", "/api/v3/series/editor")] = editor
    return fake, old, new


async def test_typo_detect_and_fix(client, b1ctx, tmp_path):  # noqa: F811
    fake, old, new = _world(b1ctx, tmp_path)
    await sync_instance(b1ctx, "sonarr-anime")
    found = client.get("/api/v1/storage/typos").json()
    t = next(x for x in found if x["instance_id"] == "sonarr-anime")
    assert t["path"] == str(old) and t["suggested_path"] == str(new)
    assert t["items"] == 3 and t["free_space_ok"] is True and t["root_folder_id"] == 1
    # zichtbaar in 'Wacht op jou', met keuze voor de gebruiker
    att = client.get("/api/v1/attention").json()
    item = next(i for i in att["items"] if i["key"] == "storage.typo")
    assert item["needs_user"] is True and item["href"] == "/instellingen?tab=opslag"
    # zonder bevestiging gebeurt er niets
    url = "/api/v1/storage/typos/sonarr-anime/1/fix"
    assert client.post(url, json={}).status_code == 400
    assert not [c for c in fake.calls if c[0] == "PUT"]
    r = client.post(url, json={"confirm": True})
    assert r.status_code == 200, r.text
    await jobs.JobRunner(b1ctx).run_until_idle()
    job = b1ctx.db.fetch_one("SELECT status, result, error FROM jobs WHERE id=?",
                             (r.json()["job_id"],))
    assert job["status"] == "succeeded", dict(job)
    assert {s["path"] for s in fake.series.values()} == {str(new / n) for n in
                                                          ("Frieren", "Mosgeest", "Monster")}
    assert (new / "Frieren" / "ep1.mkv").exists()   # media nooit verwijderd
    assert old.exists()                               # oude map blijft op schijf
    assert [r["path"] for r in fake.root_folders] == [f"{new}/"]  # lege oude hoofdmap weg


async def test_typo_fix_refused_during_shadow_and_for_unknown(client, b1ctx, tmp_path):  # noqa: F811
    from mediaguard.instances.registry import update_instance

    _world(b1ctx, tmp_path)
    update_instance(b1ctx, "sonarr-anime", {"shadow_mode": True})
    r = client.post("/api/v1/storage/typos/sonarr-anime/1/fix", json={"confirm": True})
    assert r.status_code == 409 and "schaduwfase" in r.json()["detail"]
    r = client.post("/api/v1/storage/typos/old-sonarr/1/fix", json={"confirm": True})
    assert r.status_code == 404


async def test_typo_fix_aborts_cleanly_when_move_fails(client, b1ctx, tmp_path):  # noqa: F811
    fake, old, _new = _world(b1ctx, tmp_path)
    fake.intercept[("PUT", "/api/v3/series/editor")] = lambda _b: (500, {"message": "boom"})
    r = client.post("/api/v1/storage/typos/sonarr-anime/1/fix", json={"confirm": True})
    await jobs.JobRunner(b1ctx).run_until_idle()
    job = b1ctx.db.fetch_one("SELECT status, error FROM jobs WHERE id=?", (r.json()["job_id"],))
    assert job["status"] == "failed" and "Gestopt na 0 van 3" in (job["error"] or "")
    assert (old / "Frieren" / "ep1.mkv").exists()
    assert any(r["path"] == f"{old}/" for r in fake.root_folders)  # oude hoofdmap blijft
