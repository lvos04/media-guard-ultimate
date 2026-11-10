import { expect, test, type Page } from "@playwright/test";

/** 0.2.9: Seerr/Downloads onder Koppelingen, instellingen op één plek, Release Lab vooraf ingevuld. */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

test("oude Seerr/Downloads-adressen, instellingenlinks en Release Lab vanuit een item", async ({ page }) => {
  await login(page);
  await page.goto("/seerr");
  await expect(page).toHaveURL(/\/inrichten\?.*tab=seerr/);
  await expect(page.locator("#live-seerr")).toBeVisible();
  await page.goto("/downloads");
  await expect(page).toHaveURL(/\/inrichten\?.*tab=downloads/);
  await expect(page.locator("#live-download-clients")).toBeVisible();
  await page.goto("/inrichten?tab=instellingen");
  await expect(page).toHaveURL(/\/instellingen\?open=algemeen/);

  // functiepagina: geen eigen instellingenblok meer, wel een link naar de juiste sectie
  await page.goto("/quarantaine");
  const link = page.getByTestId("settings-link").getByRole("link", { name: /Originelen & herstellen/ });
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/instellingen\?open=originelen/);
  await expect(page.getByTestId("advanced-originelen")).toHaveAttribute("aria-expanded", "true");

  // Release Lab met ingevulde gegevens: geen ID-velden, wel de titel van het item
  await page.goto("/release-lab?instance=sonarr-series&episode=12&title=Shameless%20S05E01");
  await expect(page.getByRole("heading", { name: "Waarom deze release?", level: 1 })).toBeVisible();
  await expect(page.getByText("Releases voor Shameless S05E01")).toBeVisible();
  // geen automatische zoekopdracht bij elk bezoek, en kiezen gaat op titel (geen ID-velden)
  await expect(page.getByText("Nog geen releases geladen")).toBeVisible();
  await page.getByRole("button", { name: "Ander item kiezen" }).click();
  await expect(page.getByRole("textbox", { name: "Titel" })).toBeVisible();
  await expect(page.locator("#rl-ep")).toHaveCount(0);

  // geen horizontaal scrollen op de telefoon
  await page.setViewportSize({ width: 412, height: 900 });
  for (const url of ["/inrichten?tab=seerr", "/inrichten?tab=downloads", "/quarantaine"]) {
    await page.goto(url);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, url).toBeLessThanOrEqual(0);
  }
});
