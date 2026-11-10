import { expect, test, type Page } from "@playwright/test";

const GB = 1024 ** 3; // de app rekent in GiB (1 GB = 1024³)

/** Rapport 7 (v1.0.0): één quarantainegetal, weescontrole zichtbaar, downloads uit de clients, DV-routes. */
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

test("Terugzetten: één getal incl. nog te controleren, opruimdoel en weescontrole (A1/N9)", async ({ page }) => {
  await login(page);
  await page.route("**/api/v1/quarantine/usage", (r) => r.fulfill({ json: {
    used_bytes: 531 * GB, total_bytes: 531 * GB, pending_bytes: 41.8 * GB, quota_bytes: 500 * GB, percent: 106, quota_exceeded: true,
    encode_queue_paused: true, release_checks: { passed: 30, failed: 0, pending: 1 }, retention_days: 7, auto_purge: true,
    next_purge: { count: 6, bytes: 106 * GB }, mode: "immediate", restorable: true, purge_target_bytes: 425 * GB, purge_target_percent: 85,
    safety_net: { hours: 48, bytes: 500 * GB, max_bytes: 500 * GB, items: 31, used_bytes: 489 * GB, first_expiry_at: "2026-11-07T19:00:00Z" },
  } }));
  await page.route("**/api/v1/quarantine/orphans", (r) => r.fulfill({ json: { checked_at: "2026-11-06T09:00:00Z", items: [], removed: 6, left: 0, left_bytes: 0, freed_bytes: 252 * GB } }));
  await page.goto("/quarantaine");
  await expect(page.getByTestId("quarantine-total")).toContainText("531");
  await expect(page.getByTestId("safety-net")).toContainText("531");
  await expect(page.getByTestId("purge-target")).toContainText("85%");
  await expect(page.getByTestId("queue-paused")).toContainText("Je hoeft niets te doen");
  await expect(page.getByTestId("orphans-line")).toContainText("0 over");
  await expect(page.getByTestId("orphans-line").getByRole("link", { name: "Details" })).toHaveAttribute("href", "/systeem#quarantaine-wezen");
});

test("Activiteit → Downloads: wat er nu in de clients staat, en een onbereikbare client (A2)", async ({ page }) => {
  await login(page);
  await page.route("**/api/v1/activity", (r) => r.fulfill({ json: {
    generated_at: "2026-11-06T09:00:00Z",
    summary: { downloads: 1, queued_downloads: 1, searches: 0, commands: 0, av1: 0, tasks: 0, problems: 0 },
    downloads: [{ id: 1, instance_id: "sonarr-anime", instance_name: "Anime", title: "Spierkracht.S02E01.1080p.WEB-DL", release: null, protocol: "usenet",
      client: "SABnzbd", status: "downloading", state: "downloading", progress: 42, size: 1.5 * GB, size_left: 0.87 * GB, eta: null, time_left: "00:04:00",
      quality: "WEBDL-1080p", warning: false, messages: [], link: null }],
    commands: [], jobs: [], searches: [], recent: [], encoding: { jobs: [], autopilot: {} }, errors: [],
    download_clients: [{ name: "qBittorrent", ok: false, error_nl: "verbinding geweigerd" }, { name: "SABnzbd", ok: true }],
  } }));
  await page.goto("/activiteit?tab=downloads");
  await expect(page.getByTestId("live-downloads")).toContainText("Spierkracht");
  await expect(page.getByTestId("live-downloads")).toContainText("van 1,5 GB over");
  await expect(page.getByTestId("download-clients-down")).toContainText("qBittorrent");
});

test("Dolby Vision-pagina toont de routes voor HDR10+ en DV 8.1 (§4.3)", async ({ page }) => {
  await login(page);
  await page.goto("/dolby-vision");
  await expect(page.getByTestId("dv-routes")).toContainText("Dolby Vision 8.1");
  await expect(page.getByTestId("dv-routes")).toContainText("HDR10+");
});
