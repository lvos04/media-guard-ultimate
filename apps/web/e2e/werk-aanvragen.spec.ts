import { expect, test, type Page } from "@playwright/test";

/** Aanvragen: het tabblad laadt tegen de echte core, zonder fout, op desktop en telefoon. */
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

for (const width of [1440, 390] as const) {
  test(`Aanvragen-tab toont de leveringsstatus (${width})`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await login(page);
    await page.goto("/activiteit?tab=aanvragen");
    await expect(page.getByTestId("delivery-root")).toBeVisible();
    await expect(page.getByText("Elke aanvraag (Seerr of nieuw in Sonarr/Radarr)")).toBeVisible();
    await expect(page.locator(".state--error")).toHaveCount(0);
    // geen vraag aan de gebruiker in deze stroom: hooguit een knop 'Nu controleren'
    const buttons = await page.getByTestId("delivery-root").getByRole("button").allTextContents();
    expect(buttons.filter((b) => /goedkeuren|kies|verplaats/i.test(b))).toEqual([]);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
}
