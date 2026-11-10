import { expect, test, type Page } from "@playwright/test";

/** Activiteit: alle secties zichtbaar (gemockte /api/v1/activity), geen horizontale scroll op 412 px. */
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

const now = new Date().toISOString();
const activity = {
  generated_at: now,
  summary: { downloads: 1, queued_downloads: 2, searches: 1, commands: 2, av1: 1, tasks: 1, problems: 1 },
  downloads: [
    { id: 1, instance_id: "sonarr-series", instance_name: "Sonarr Series", title: "The Expanse · S01E02 met een hele lange titel die niet mag overlopen op een telefoon", release: "The.Expanse.S01E02.1080p.WEB", protocol: "usenet", client: "SABnzbd", status: "downloading", state: "downloading", progress: 75, size: 1000, size_left: 250, eta: null, time_left: "00:05:00", quality: "WEBDL-1080p", warning: false, messages: [], link: "/bibliotheek/sonarr-series/7" },
    { id: 2, instance_id: "radarr-films", instance_name: "Radarr Films", title: "Wadi (2021)", release: "Wadi.2021", protocol: "torrent", client: "qBittorrent", status: "completed", state: "importpending", progress: 100, size: 10, size_left: 0, eta: null, time_left: null, quality: "Bluray-2160p", warning: true, messages: ["Geen bestanden gevonden die geïmporteerd kunnen worden"], link: null },
  ],
  commands: [
    { id: 9, instance_id: "sonarr-series", instance_name: "Sonarr Series", name: "SeriesSearch", label: "Serie zoeken", search: true, status: "started", queued_at: now, started_at: now, target: "The Expanse" },
    { id: 10, instance_id: "radarr-films", instance_name: "Radarr Films", name: "RssSync", label: "RSS-synchronisatie", search: true, status: "queued", queued_at: now, started_at: null, target: null },
  ],
  searches: [{ id: 3, instance_id: "sonarr-anime", ts: now, status: "grabbed", title: "Frieren", series_title: "Frieren", season_number: 1, episode_number: 5 }],
  encoding: { jobs: [{ id: 4, instance_id: "radarr-films", title: "Oppenheimer (2023).mkv", status: "running", label: "Encoderen", progress: 42, link: "/av1/wachtrij" }], autopilot: { enabled: true, blocked: null, queue_depth: 2 } },
  jobs: [{ id: 5, job_type: "library.sync", label: "Bibliotheek synchroniseren", status: "running", instance_id: "sonarr-anime", progress: 50, started_at: now, created_at: now, error: null, link: "/jobs" }],
  recent: [{ kind: "import", instance_id: "sonarr-series", at: now, title: "The Expanse · S01E01", detail: null, link: "/bibliotheek/sonarr-series/7" }],
  errors: [{ instance_id: "radarr-animefilms", name: "Radarr Animefilms", error: "niet bereikbaar" }],
};

test("Activiteit: alle secties, topbalk linkt hierheen, geen overloop op 412 px", async ({ page }) => {
  await page.setViewportSize({ width: 412, height: 900 });
  await page.route("**/api/v1/activity", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(activity) }));
  await login(page);
  // met iets dat aandacht vraagt gaat de pil naar Problemen, anders naar Activiteit
  await expect(page.locator(".top-pill").first()).toHaveAttribute("href", /^\/activiteit(\?tab=problemen)?$/);
  await page.goto("/activiteit");
  await expect(page.getByRole("heading", { name: "Activiteit", level: 1 })).toBeVisible();
  for (const name of ["Downloads", "Zoeken & taken in Sonarr/Radarr", "AV1", "MediaGuard-taken", "Recent (24 uur)"]) {
    await expect(page.getByRole("heading", { name: new RegExp(`^${name.replace(/[()&/]/g, "\\$&")}`), level: 2 })).toBeVisible();
  }
  await expect(page.getByTestId("activity-downloads").getByRole("progressbar").first()).toHaveAttribute("aria-valuenow", "75");
  await expect(page.getByText("Serie zoeken · The Expanse")).toBeVisible();
  await expect(page.getByText("Niet bereikbaar: Radarr Animefilms")).toBeVisible();
  await expect(page.getByText("Oppenheimer (2023)", { exact: true })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: "test-results/activity-mobile.png", fullPage: true });
});

test("Activiteit: lege toestand", async ({ page }) => {
  await page.route("**/api/v1/activity", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    ...activity, summary: { downloads: 0, queued_downloads: 0, searches: 0, commands: 0, av1: 0, tasks: 0, problems: 0 },
    downloads: [], commands: [], searches: [], jobs: [], recent: [], errors: [], encoding: { jobs: [], autopilot: { enabled: true, blocked: "bibliotheek nog vergrendeld (vóór de overstap)", queue_depth: 2 } } }) }));
  await login(page);
  await page.goto("/activiteit");
  await expect(page.getByText("Er wordt nu niets gedownload")).toBeVisible();
  await expect(page.getByText("Wacht: bibliotheek nog vergrendeld (vóór de overstap)")).toBeVisible();
});
