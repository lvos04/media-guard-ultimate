import { expect, test, type Page } from "@playwright/test";

/** Vereenvoudigde navigatie (0.2.8): klein menu, oude adressen blijven werken, 'Nog te doen'. */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

test("klein menu, 'Nog te doen' en oude adressen sturen door", async ({ page }) => {
  await login(page);
  // Migratie staat er alleen bij zolang er een migratie loopt (migration.spec start er een)
  await expect(page.locator(".sidebar .nav-item").first()).toBeVisible();
  const labels = (await page.locator(".sidebar .nav-item .nav-label").allTextContents()).filter((l) => l !== "Migratie");
  expect(labels).toEqual(["Overzicht", "Activiteit", "Bibliotheek", "Ruimte", "Aanvragen", "Instellingen"]);
  // verse installatie: er zijn nog instellingen nodig → kaart met één knop per punt
  await expect(page.getByTestId("todo-card")).toBeVisible();
  await expect(page.getByTestId("todo-card").getByRole("link", { name: "Regelen" })).toBeVisible();

  await page.goto("/jobs?status=failed");
  await expect(page).toHaveURL(/\/activiteit\?.*tab=problemen/);
  // Activiteit laadt eerst zijn gegevens; op een drukke CI-runner duurt dat soms > 5 s
  await expect(page.getByRole("tab", { name: "Problemen" })).toHaveAttribute("aria-selected", "true", {
    timeout: 20_000,
  });
  await page.goto("/jobs");
  await expect(page).toHaveURL(/\/activiteit\?.*tab=geschiedenis/);

  // kwaliteit (profielen + optimaliseren) is nu een eigen tab onder Instellingen
  await page.goto("/instellingen");
  await expect(page.getByRole("link", { name: /Kwaliteit & profielen/ }).first()).toBeVisible();
  await page.goto("/kalender");
  await expect(page.locator(".sidebar .nav-item[aria-current=page]")).toHaveText("Bibliotheek");
  await page.goto("/ontbrekend");
  await expect(page.locator(".sidebar .nav-item[aria-current=page]")).toHaveText("Bibliotheek");
  await page.goto("/quarantaine");
  await expect(page.locator(".sidebar .nav-item[aria-current=page]")).toHaveText("Ruimte");
  await page.goto("/audio");
  await expect(page).toHaveURL(/\/instellingen\?view=all&q=audio/);
});
