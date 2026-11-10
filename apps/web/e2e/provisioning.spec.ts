import { expect, test, type Page } from "@playwright/test";

/**
 * "Inrichten" against the real core. The e2e core has no Sonarr/Radarr forks and no API keys
 * for them, so the status grid must honestly show "Invoer nodig" and the needs list.
 */
test.describe.configure({ mode: "serial" });

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

test("inrichten: statusgrid met 'invoer nodig' en ontbrekende invoer", async ({ page }) => {
  await login(page);
  // Inrichten zit nu onder Instellingen → Koppelingen (kleiner menu)
  await page.locator(".sidebar").getByRole("link", { name: "Instellingen" }).click();
  await page.getByRole("link", { name: /Koppelingen inrichten/ }).click();
  await expect(page).toHaveURL(/\/inrichten/);
  await expect(page.getByRole("heading", { name: "Koppelingen inrichten", level: 1 })).toBeVisible();
  const needs = page.getByTestId("prov-needs");
  await expect(needs).toContainText("API-sleutel van Sonarr Series ontbreekt");
  await expect(needs).toContainText("Kies Prowlarr of voer indexers in");
  const grid = page.getByTestId("prov-grid");
  await expect(grid.locator('[data-instance="sonarr-anime"]')).toBeVisible();
  await page.getByRole("button", { name: "Controleren", exact: true }).click();
  await expect(grid.locator('[data-instance="sonarr-series"] [aria-label="Invoer nodig"]').first()).toBeVisible();
});

test("inrichten: downloadclient opslaan blijft bewaard, wachtwoord komt nooit terug", async ({ page }) => {
  await login(page);
  await page.goto("/inrichten?tab=downloads");
  await page.getByRole("button", { name: "Downloadclient toevoegen" }).click();
  const dc = page.getByTestId("dc-0");
  await dc.getByLabel("Naam", { exact: true }).fill("SAB");
  await dc.getByLabel("Host").fill("sabnzbd");
  await dc.getByLabel("API-sleutel").fill("e2e-geheime-sleutel");
  await dc.getByLabel("Wachtwoord").fill("e2e-geheim-wachtwoord");
  await page.getByTestId("prov-save").click();
  await expect(page.getByText("Opgeslagen").first()).toBeVisible();

  await page.reload();
  const saved = page.getByTestId("dc-0");
  await expect(saved.getByLabel("Naam", { exact: true })).toHaveValue("SAB");
  await expect(saved.getByLabel("Host")).toHaveValue("sabnzbd");
  await expect(saved.getByText("ingesteld")).toHaveCount(2);
  await expect(saved.getByLabel("Wachtwoord")).toHaveValue("");
  await expect(saved.getByLabel("API-sleutel")).toHaveValue("");
  const html = await page.content();
  expect(html).not.toContain("e2e-geheim-wachtwoord");
  expect(html).not.toContain("e2e-geheime-sleutel");
  const cfg = await page.evaluate(async () => (await fetch("/api/v1/provisioning/config")).text());
  expect(cfg).toContain("\"password_set\":true");
  expect(cfg).not.toContain("e2e-geheim-wachtwoord");

  // the needs list no longer asks for a download client
  await page.goto("/inrichten");
  await expect(page.getByTestId("prov-needs")).not.toContainText("Nog geen downloadclient ingevoerd");
});
