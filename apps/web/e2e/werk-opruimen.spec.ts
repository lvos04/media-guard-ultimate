import { expect, test } from "@playwright/test";

/** Downloads opruimen: kaart onder Systeem → Opslag tegen de echte core (nog niets gedraaid). */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

test.beforeAll(async ({ request }) => {
  const status = await (await request.get("/api/v1/auth/setup-status")).json() as { needs_setup: boolean };
  if (status.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
});

for (const width of [1440, 390] as const) {
  test(`opruimkaart zichtbaar en zonder fouten (${width})`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/login");
    await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
    await page.getByLabel("Wachtwoord").fill(ADMIN.password);
    await page.getByRole("button", { name: "Inloggen" }).click();
    await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
    await page.goto("/systeem#opslag");
    const card = page.getByTestId("download-cleanup");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Oude downloads opruimen");
    await expect(card).toContainText("prullenbak");
    await expect(card.getByRole("button", { name: "Nu opruimen" })).toBeVisible();
    const api = await page.request.get("/api/v1/downloads/cleanup");
    expect(api.ok()).toBeTruthy();
    expect(((await api.json()) as { enabled: boolean }).enabled).toBe(true);
  });
}
