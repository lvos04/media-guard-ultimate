import { expect, test, type Page } from "@playwright/test";

import { makeBackupZips } from "./seed";

/**
 * Migration wizard against the real core (/api/v1/migration/*). Runs after mediaguard.spec.ts
 * (alphabetical, 1 worker) when run together; standalone it creates the admin itself.
 * The e2e core has no Sonarr/Radarr and no job runner (MG_START_BACKGROUND=0): uploads, the
 * mapping preview and the path check are synchronous; long steps stay "In wachtrij".
 */
test.describe.configure({ mode: "serial" });

const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

// standalone runs: create the admin if mediaguard.spec.ts did not run first
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

test("migratie: ongeldige back-up geweigerd, geldige herkend, mapping, padcontrole en herladen", async ({ page }) => {
  test.setTimeout(90_000);
  const zips = makeBackupZips();
  await login(page);
  await page.goto("/migratie");
  await expect(page.getByRole("heading", { name: "Back-ups uploaden" })).toBeVisible();
  const input = page.getByTestId("backup-upload").locator('input[type="file"]');

  // 1. a zip without database is rejected by the core with a Dutch reason
  await input.setInputFiles(zips.invalid);
  const rejected = page.locator('[data-upload-state="error"]');
  await expect(rejected).toContainText("Geen bruikbare Sonarr/Radarr-back-up");
  await expect(rejected).toContainText("Zip bevat geen sonarr.db of radarr.db");

  // 2. a real Sonarr-like backup is accepted and auto-detected (app, version, anime → sonarr-anime)
  await input.setInputFiles(zips.valid);
  const verdict = page.getByTestId("upload-verdict");
  await expect(verdict).toContainText("Herkend: Sonarr 4.0.14.2939");
  await expect(verdict).toContainText("2 series");
  await expect(verdict).toContainText("sonarr-anime");
  await expect(page.getByTestId("source-table")).toContainText("Sonarr-Anime");

  // 3. start a run → wizard
  await page.getByRole("button", { name: "Run starten" }).click();
  await expect(page).toHaveURL(/\/migratie\?run=\d+/);
  await expect(page.getByRole("heading", { name: /^Run #\d+/ })).toBeVisible();

  // 4. mapping preview: matrix renders with the proposal, wrong-app targets disabled
  await page.getByRole("button", { name: "Mapping bron → doel" }).click();
  await expect(page).toHaveURL(/step=mapping/);
  await page.getByRole("button", { name: "Voorstel maken" }).click();
  const matrix = page.getByTestId("mapping-matrix");
  await expect(matrix).toContainText("Voorgesteld");
  await expect(matrix.getByRole("radio", { name: /naar sonarr-anime/ })).toBeChecked();
  await expect(matrix.getByRole("radio", { name: /naar radarr-films/ })).toBeDisabled();
  // 5. path remap preview (rules are saved with the mapping) with existence check (/anime does not exist on the e2e host)
  const paths = page.locator('[data-testid^="paths-"]').first();
  await paths.getByRole("button", { name: "Regel toevoegen" }).click();
  await paths.getByLabel("Bronpad").fill("/anime");
  await paths.getByLabel("Pad in MediaGuard").fill("/media/anime");
  await paths.getByRole("button", { name: "Paden controleren" }).click();
  const report = page.locator('[data-testid^="path-report-"]').first();
  await expect(report).toContainText("NO-GO");
  await expect(report).toContainText("/media/anime");
  await expect(report).toContainText("niet gevonden");
  // saving path rules re-opens the mapping (backend: edit ⇒ proposed); confirm afterwards
  await page.getByRole("button", { name: "Mapping bevestigen" }).click();
  await expect(matrix).toContainText("Bevestigd");

  // 6. preflight runs as a job (no runner in e2e → stays queued, honestly shown)
  await page.getByRole("button", { name: "Controle vooraf", exact: true }).click();
  await page.getByRole("button", { name: "Controle vooraf uitvoeren" }).click();
  await expect(page.locator('[data-testid^="job-"]').first()).toContainText("In wachtrij");

  // 7. state survives a reload (server-side run state, step in the URL)
  await page.goto(page.url().replace(/step=[a-z_]+/, "step=mapping"));
  await expect(page.getByTestId("mapping-matrix").getByRole("radio", { name: /naar sonarr-anime/ })).toBeChecked();
  await expect(page.getByTestId("mapping-matrix")).toContainText("Bevestigd");
  await expect(page.locator('[data-testid^="path-report-"]').first()).toContainText("NO-GO");
  await page.reload();
  await expect(page.getByTestId("mapping-matrix")).toContainText("Bevestigd");

  // 8. the run is listed and resumable from the overview
  await page.getByRole("button", { name: "Alle runs" }).click();
  await expect(page.getByRole("button", { name: "Hervatten" }).first()).toBeVisible();
});

test("migratie: upload direct na het openen wacht op de sessie (geen CSRF-fout)", async ({ page }) => {
  const zips = makeBackupZips();
  await login(page);
  // /auth/me bewust traag: de pagina staat er al, het CSRF-token nog niet (de race die op CI
  // 'CSRF-controle mislukt' gaf). De upload moet op het token wachten.
  await page.route("**/api/v1/auth/me", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    await route.continue();
  });
  await page.goto("/migratie");
  await expect(page.getByRole("heading", { name: "Back-ups uploaden" })).toBeVisible();
  await page.getByTestId("backup-upload").locator('input[type="file"]').setInputFiles(zips.invalid);
  const rejected = page.locator('[data-upload-state="error"]');
  await expect(rejected).toContainText("Geen bruikbare Sonarr/Radarr-back-up");
  await expect(rejected).not.toContainText("CSRF");
  await page.unroute("**/api/v1/auth/me");
});

test("anime-routering: sectie rendert tegen de echte core", async ({ page }) => {
  await login(page);
  await page.goto("/anime");
  const section = page.getByTestId("anime-routing");
  await expect(section.getByRole("heading", { name: /Anime-routering/ }).first()).toBeVisible();
  await expect(section.getByText("Nog niets gerouteerd")).toBeVisible();
  // instellingen staan centraal; de sectie linkt ernaartoe
  await section.getByTestId("settings-link").getByRole("link", { name: /Taal & anime/ }).click();
  await expect(page).toHaveURL(/\/instellingen\?open=taal/);
  await expect(page.locator('[data-setting="routing.anime.mode"]')).toBeVisible();
});
