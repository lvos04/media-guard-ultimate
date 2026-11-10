import { expect, test, type Page } from "@playwright/test";

/**
 * Dekking: elke instelling uit het register (GET /settings/schema) staat ergens op Instellingen.
 * Globale instellingen op de pagina zelf (na 'Alles uitklappen'); instellingen die alleen per
 * serie/instantie/bestand gelden onder 'Per serie of instantie' (?view=all). Een nieuwe instelling
 * zonder plek laat deze test falen.
 */
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

async function rendered(page: Page): Promise<Set<string>> {
  return new Set(await page.locator("[data-setting]").evaluateAll((els) => els.map((e) => e.getAttribute("data-setting") ?? "")));
}

test("elke instelling uit het register staat op Instellingen", async ({ page }) => {
  test.setTimeout(180_000);
  await login(page);
  const schema = await (await page.request.get("/api/v1/settings/schema")).json() as { settings: Array<{ key: string; hidden?: boolean; scopes: string[] }> };
  const wanted = schema.settings.filter((d) => !d.hidden);
  expect(wanted.length).toBeGreaterThan(150);

  await page.goto("/instellingen");
  await page.getByTestId("settings-expand-all").click();
  await expect(page.locator("[data-setting]").first()).toBeVisible();
  const seen = await rendered(page);

  // instellingen die alleen per serie/instantie/bestand bestaan: 'Per serie of instantie' (?view=all)
  const scopedOnly = wanted.filter((d) => !seen.has(d.key));
  expect(scopedOnly.filter((d) => d.scopes.includes("global")).map((d) => d.key), "globale instellingen horen op de pagina zelf").toEqual([]);
  for (const d of scopedOnly) {
    const scope = d.scopes.find((s) => s !== "instance") ?? d.scopes[0];
    const id = scope === "media_type" ? "tv" : "1";
    await page.goto(`/instellingen?view=all&scope=${scope}&id=${id}&q=${encodeURIComponent(d.key)}`);
    await expect(page.locator(`[data-setting="${d.key}"]`), `${d.key} onder 'Per serie of instantie'`).toBeVisible();
    seen.add(d.key);
  }
  const missing = wanted.map((d) => d.key).filter((k) => !seen.has(k));
  expect(missing, `instellingen zonder plek in de UI: ${missing.join(", ")}`).toEqual([]);
});

test("zoeken op Instellingen vindt een geavanceerde instelling en een gereedschap", async ({ page }) => {
  await login(page);
  await page.goto("/instellingen");
  await page.getByTestId("settings-search").fill("ondertitel");
  await expect(page.locator('[data-setting="encoding.keep_all_subtitles"]')).toBeVisible();
  await page.getByTestId("settings-search").fill("release lab");
  await expect(page.getByRole("link", { name: /Release Lab/ })).toBeVisible();
});
