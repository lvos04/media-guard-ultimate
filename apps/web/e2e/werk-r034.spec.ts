import { expect, test, type Page } from "@playwright/test";

/** Rapport 4: eigen meting na omzetting, filmtabs, geen zijwaarts scrollen op telefoon. */
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

const MOVIE = {
  id: 9901, kind: "movie", media_type: "movie", instance_id: "radarr-films", instance_name: "Films", provider_id: 9901,
  title: "Testfilm", year: 2020, monitored: true, status: "released", size_on_disk: 54_500_000_000, poster_url: null,
  stats: { total: 1, available: 1, missing: 0, cutoff_unmet: 0, upcoming: 0, unmonitored: 0 },
  file: { quality: "Remux-2160p", size: 54_500_000_000, path: "/data/Media/Films/Testfilm/Testfilm.mkv" }, wanted: [],
};
// precies de vorm die core (mediainfo_summary) levert: video/audio/subtitles
const MEASURED = {
  video: { codec: "av1", width: 3840, height: 2160, hdr10: true, dv_profile: 10, kbps: 9000 },
  audio: [{ language: "eng", codec: "truehd", channels: 8 }, { language: "nld", codec: "ac3", channels: 6 }],
  subtitles: [{ language: "nld", codec: "subrip" }, { language: "eng", codec: "subrip" }],
  size: 9_800_000_000, source: "mediaguard",
};
const DETAILS = {
  movie: MOVIE, media: { resolution: "3840x2160", video_codec: "x265", dynamic_range: "HDR10", hdr: true },
  history: [], av1: [{ id: 1, status: "committed", status_nl: "Geplaatst", created_at: "2026-11-05 09:25:00", source_size: 54_500_000_000, output_size: 9_800_000_000, output_mediainfo: MEASURED }],
  media_mediaguard: MEASURED, identity: null,
};

test("film: Video en audio toont MediaGuards meting (DV, sporen) en de AV1-grootte (S5/S18)", async ({ page }) => {
  await login(page);
  await page.route("**/api/v1/library/9901/details", (r) => r.fulfill({ json: DETAILS }));
  await page.route("**/api/v1/library/9901", (r) => r.fulfill({ json: MOVIE }));
  await page.route("**/api/v1/identity/**", (r) => r.fulfill({ json: { status: "pending" } }));
  await page.goto("/bibliotheek/radarr-films/9901");
  await expect(page.getByRole("heading", { name: "Testfilm", level: 1 })).toBeVisible();
  await expect(page.getByText("→ AV1 (MediaGuard)")).toBeVisible();
  await page.getByRole("tab", { name: "Video en audio" }).click();
  await expect(page.getByText("Dolby Vision 10")).toBeVisible();
  await expect(page.getByText(/Engels · truehd · 8 kan\./)).toBeVisible();
  await expect(page.getByText("SDR", { exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "Conversies" }).click();
  await expect(page.getByText(/50,8 GB → 9,1 GB/)).toBeVisible();
});

test("onbekende meting toont 'niet gemeten', nooit standaard SDR (S18)", async ({ page }) => {
  await login(page);
  const empty = { ...DETAILS, media: null, media_mediaguard: { video: { codec: null }, audio: [], subtitles: [] } };
  await page.route("**/api/v1/library/9901/details", (r) => r.fulfill({ json: empty }));
  await page.route("**/api/v1/library/9901", (r) => r.fulfill({ json: MOVIE }));
  await page.route("**/api/v1/identity/**", (r) => r.fulfill({ json: { status: "pending" } }));
  await page.goto("/bibliotheek/radarr-films/9901");
  await page.getByRole("tab", { name: "Video en audio" }).click();
  await expect(page.getByText("niet gemeten").first()).toBeVisible();
  await expect(page.getByText("SDR", { exact: true })).toHaveCount(0);
});

const LONG = "Volledige Opruiming van een Andere Wereld onder een Godin zonder Gelovigen — een heel lange titel zonder spaties-ditiseenwoordzonderafbreekpuntendatnooitophoudt";

for (const width of [390, 420] as const) {
  test(`geen zijwaarts scrollen op telefoon (${width}), ook met lange aanvraagtitels (S9)`, async ({ page }) => {
    await page.setViewportSize({ width, height: 860 });
    await login(page);
    await page.route("**/api/v1/delivery/requests**", (r) => r.fulfill({ json: {
      items: [{ id: 1, title: LONG, media_type: "series", source: "seerr", instance_id: "sonarr-anime", stage: "searching", stage_nl: "Wordt gezocht", state: "active",
        detail_nl: "26 aflevering(en) ontbreken", next_step_nl: "Volgende zoekpoging 5 nov 11:38", progress: { total: 26, have: 0 }, requested_at: null, delivered_at: null,
        last_failure_nl: "Uitpakken mislukt (SABnzbd)" }],
      counts: { total: 9, active: 1, waiting_release: 0, not_found: 0, delivered: 8 } } }));
    for (const path of ["/", "/activiteit", "/activiteit?tab=aanvragen", "/bibliotheek", "/ontbrekend", "/av1", "/av1/wachtrij", "/systeem", "/instellingen", "/quarantaine", "/inrichten"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle").catch(() => undefined);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `${path} scrolt zijwaarts`).toBeLessThanOrEqual(1);
    }
    await page.goto("/activiteit?tab=aanvragen");
    await expect(page.getByText("Laatste downloadfout: Uitpakken mislukt (SABnzbd)")).toBeVisible();
  });
}
