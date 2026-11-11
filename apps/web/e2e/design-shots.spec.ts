import { expect, test, type Page } from "@playwright/test";

/**
 * Alleen voor ontwerpwerk: schermafbeeldingen van de hoofdpagina's (1440 en 390, donker en licht).
 * Draait alleen met MG_SHOTS_DIR=<map>; in de gewone E2E-run wordt hij overgeslagen.
 */
const DIR = process.env.MG_SHOTS_DIR;
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };
const PAGES: Array<[string, string]> = [
  ["overzicht", "/"], ["activiteit", "/activiteit"], ["bibliotheek", "/bibliotheek"], ["ontbrekend", "/ontbrekend"],
  ["ruimte", "/av1"], ["wachtrij", "/av1/wachtrij"], ["terugzetten", "/quarantaine"], ["av1-instellingen", "/av1/instellingen"],
  ["instellingen", "/instellingen"], ["koppelingen", "/inrichten"], ["mediaservers", "/inrichten?tab=mediaserver"], ["kwaliteit", "/kwaliteit"], ["systeem", "/systeem"],
  ["aanvragen", "/aanvragen"], ["hoe-werkt-het", "/hoe-werkt-het"],
];

test.skip(!DIR, "alleen voor ontwerp-schermafbeeldingen");

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

for (const [w, h] of [[1440, 900], [390, 844]] as const) {
  for (const theme of ["dark", "light"] as const) {
    test(`shots ${w} ${theme}`, async ({ page }) => {
      test.setTimeout(240_000);
      await page.setViewportSize({ width: w, height: h });
      await page.addInitScript((t) => { try { window.localStorage.setItem("mg.theme", t); } catch { /* */ } }, theme);
      await login(page);
      for (const [name, href] of PAGES) {
        await page.goto(href);
        await page.waitForTimeout(1200);
        await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage: true });
      }
    });
  }
}
