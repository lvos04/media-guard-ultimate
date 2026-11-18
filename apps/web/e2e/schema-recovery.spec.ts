import { expect, test } from "@playwright/test";

test("de herstelweergave vervangt aanmelden en het dashboard op 320 px", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  const title = "MediaGuard staat in herstelweergave";
  const message = "database is van een nieuwere MediaGuard-versie; zet de bijpassende versie of de set van vóór de update terug";
  for (const path of ["/", "/login", "/setup", "/activiteit"]) {
    const response = await page.goto(path);
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    await expect(page.getByText(message, { exact: true })).toBeVisible();
    await expect(page.getByText("Aanmelden en het dashboard zijn tijdelijk niet beschikbaar.")).toBeVisible();
    await expect(page.getByText(/formulier van de TrueNAS Custom App/)).toBeVisible();
    await expect(page.getByText(/snapshot van vóór de update terug/)).toBeVisible();
    await expect(page.locator("form, nav")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  }

  const health = await page.request.get("/api/v1/health");
  expect(health.status()).toBe(503);
  expect(await health.json()).toMatchObject({ status: "down", reason_code: "schema_nieuwer" });
});
