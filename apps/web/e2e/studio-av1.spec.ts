import { expect, test, type Page } from "@playwright/test";

/**
 * AV1 Studio against a mocked engine (HTTP via page.route, live channel via routeWebSocket):
 * every worker of every lane gets its own progress bar, bars never move backwards within a
 * phase, a phase without a percentage shows a "working" bar, and the job drawer opens.
 */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

test.beforeAll(async ({ request }) => {
  const status = await (await request.get("/api/v1/auth/setup-status")).json() as { needs_setup: boolean };
  if (status.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
});

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

const system = {
  cpu_percent: 41, cpu_temp: null, geheugen: { totaal_bytes: 16e9, gebruikt_bytes: 6e9, beschikbaar_bytes: 10e9, percent: 37 },
  gpu: { video_percent: 55, render_percent: 20, freq_mhz: null, power_w: null, package_power_w: null, platform_power_w: null, platform_power_beschikbaar: false, beschikbaar: true },
  schijf_vrij_gb: 900, schijf_vrij_bytes: 900e9, schijf_gereserveerd_bytes: 0, schijf_beschikbaar_na_reservering: 900e9,
  cache_status: "voldoende", cache_waarschuwing_gb: 250, cache_pauze_gb: 150, effectieve_cache_waarschuwing_gb: 250, effectieve_cache_pauze_gb: 150,
  opslagfout: null, opslagmodus: "aparte_schijf", systeemschijf_reserve_gb: 150,
};

function tick(gpuPct: number | null, cpuPhase: string, cpuPct: number | null) {
  const started = new Date(Date.now() - 600_000).toISOString();
  return JSON.stringify({
    type: "tick", tijd: "2026-11-03", systeem: system, wachtrij: { wachtend: 5, bezig: 2, klaar_vandaag: 1, fouten: 0 },
    workers: [
      { baan: "gpu", slot: 1, aantal: 2, job_id: 7, status: "bezig", fase: "encode", fase_gestart_op: started, gestart_op: started, bestand: "/data/media/movies/Wadi (2021)/Wadi.mkv", encoder: "av1_qsv", percent: gpuPct, fps: 96, speed: 4, eta_seconden: 900, bitrate_kbps: 9000, grootte_bron: 40e9, grootte_nu: 8e9, gepauzeerd: false },
      { baan: "gpu", slot: 2, aantal: 2, job_id: 8, status: "bezig", fase: "encode", fase_gestart_op: started, gestart_op: started, bestand: "/data/media/movies/Tenet (2020)/Tenet.mkv", encoder: "av1_qsv", percent: 12, fps: 90, speed: 3.6, eta_seconden: 2400, grootte_bron: 30e9, grootte_nu: 2e9, gepauzeerd: false },
      { baan: "cpu", slot: 1, aantal: 1, job_id: 9, status: cpuPhase === "verifieren" ? "verifieren" : "bezig", fase: cpuPhase, fase_gestart_op: started, gestart_op: started, bestand: "/data/media/tv/Show/S01E01.mkv", encoder: "libsvtav1", percent: cpuPct, fps: 30, speed: 1.2, eta_seconden: 3000, grootte_bron: 3e9, grootte_nu: 0, gepauzeerd: false },
    ],
  });
}

test("AV1 Studio: eigen voortgangsbalk per worker, nooit terug, fase zonder % = lopende balk", async ({ page }) => {
  await page.route("**/api/v1/encoder/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1/encoder", "");
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/statistieken") return json({ totaal: { bespaard_bytes: 2.5e12, gemiddeld: 41.5, jobs: 120 }, per_dag: [{ dag: "2026-11-02", bespaard_bytes: 9e10, jobs: 4 }], doorvoer_bytes_per_dag_7d: 1.1e11, resterend_bytes: 4e12, prognose_klaar: "2026-12-20" });
    if (path === "/systeem/diagnose") return json({ dependencies: { tools: { dovi_tool: true }, ffmpeg_version: "8.0.1", libsvtav1: true, av1_qsv: true, dolbyvision: true, hdr10plus: true, render_node: { pad: "/dev/dri/renderD128", aanwezig: false, leesbaar: false, schrijfbaar: false }, qsv_gereed: false } });
    if (path === "/systeem/metingen") return json([]);
    if (path === "/instellingen") return json({ meld_browser_fouten: false, meld_browser_batches: false });
    if (path === "/jobs") return json({ items: [{ id: 7, status: "bezig", baan: "gpu", encoder: "av1_qsv", pad: "/data/media/movies/Wadi (2021)/Wadi.mkv", bron_grootte: 40e9, percent: 40, reden: "bitrate boven drempel", gestart_op: new Date().toISOString(), fase: "encode", commando: "[\"ffmpeg\",\"-i\",\"Wadi.mkv\"]" }], totaal: 1, pagina: 1, per_pagina: 250 });
    if (path === "/jobs/7") return json({ id: 7, status: "bezig", baan: "gpu", encoder: "av1_qsv", pad: "/data/media/movies/Wadi (2021)/Wadi.mkv", bron_grootte: 40e9, percent: 40, reden: "bitrate boven drempel", gestart_op: new Date().toISOString(), fase: "encode", commando: "[\"ffmpeg\",\"-i\",\"Wadi.mkv\"]" });
    if (path === "/jobs/7/log") return json({ regels: ["ffmpeg version 8.0.1", "Stream #0:0: Video: hevc"], volgende: 2 });
    return json({});
  });
  let send: ((t: string) => void) | null = null;
  await page.routeWebSocket("**/api/v1/encoder/ws", (ws) => {
    send = (t) => ws.send(t);
    ws.send(tick(40, "analyse", null));
  });

  await login(page);
  await page.goto("/av1");
  for (const id of ["worker-gpu-1", "worker-gpu-2", "worker-cpu-1"]) await expect(page.getByTestId(id)).toBeVisible();
  const gpuBar = page.getByTestId("worker-gpu-1").getByRole("progressbar");
  await expect(gpuBar).toHaveAttribute("aria-valuenow", "40");
  // analysis has no percentage: a "working" bar without aria-valuenow, not 0 %
  const cpuBar = page.getByTestId("worker-cpu-1").getByRole("progressbar");
  await expect(cpuBar).not.toHaveAttribute("aria-valuenow", /.*/);
  await expect(cpuBar).toHaveAttribute("aria-valuetext", /Analyse/);

  send!(tick(55, "encode", 20));
  await expect(gpuBar).toHaveAttribute("aria-valuenow", "55");
  await expect(cpuBar).toHaveAttribute("aria-valuenow", "20");
  // a jittering lower reading in the same phase must not move the bar backwards
  send!(tick(53, "encode", 21));
  await expect(cpuBar).toHaveAttribute("aria-valuenow", "21");
  await expect(gpuBar).toHaveAttribute("aria-valuenow", "55");
  // new phase starts fresh
  send!(tick(60, "verifieren", null));
  await expect(cpuBar).toHaveAttribute("aria-valuetext", /Verificatie/);
  await expect(cpuBar).not.toHaveAttribute("aria-valuenow", /.*/);

  // hardware card explains how to pass the GPU through in TrueNAS
  await expect(page.getByTestId("av1-hardware")).toBeVisible();
  await expect(page.getByText(/Resources → GPU/)).toBeVisible();

  // job drawer with log
  await page.getByTestId("worker-gpu-1").getByRole("button", { name: "Details" }).click();
  await expect(page.getByTestId("job-drawer")).toBeVisible();
  await expect(page.getByTestId("job-drawer").getByText("ffmpeg version 8.0.1")).toBeVisible();
  await page.screenshot({ path: "e2e/.results/av1-studio-workers.png", fullPage: true });
});

test("AV1 Studio: oude routes sturen door naar de nieuwe plek", async ({ page }) => {
  await login(page);
  await page.goto("/av1/workers");
  await expect(page).toHaveURL(/\/av1\/instellingen/);
  await page.goto("/av1/fouten");
  await expect(page).toHaveURL(/\/av1\/wachtrij\?status=mislukt/);
  await page.goto("/av1/logs?job=12");
  await expect(page).toHaveURL(/\/av1\/wachtrij\?job=12/);
  await page.goto("/av1/beslissingen");
  await expect(page).toHaveURL(/\/av1\/wachtrij$/);
  // de AV1-engine-instellingen horen nu bij Instellingen (eigen kop, weg terug)
  for (const [path, h1] of [["/av1/wachtrij", "Ruimte besparen"], ["/av1/kwaliteit", "Ruimte besparen"], ["/av1/instellingen", "AV1-engine"]]) {
    await page.goto(path);
    await expect(page.locator("h1").first()).toHaveText(h1);
    await expect(page.locator(".state--error")).toHaveCount(0);
  }
});
