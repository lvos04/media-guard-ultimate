import { expect, test, type Page } from "@playwright/test";

/** API-sleutels: aanmaken → één keer tonen → werkt tegen de echte core → intrekken → 401. */
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

async function setTheme(page: Page, theme: "dark" | "light" | null) {
  const me = await (await page.request.get("/api/v1/auth/me")).json() as { csrf_token: string };
  const r = await page.request.put("/api/v1/auth/me/preferences", { data: { theme }, headers: { "X-CSRF-Token": me.csrf_token } });
  expect(r.ok()).toBeTruthy();
  // de keuze die deze browser al bewaarde (mg.theme) gaat voor op de serverkeuze; leeg hem, zodat de net gezette
  // voorkeur geldt (anders hangt de test af van het thema dat een eerdere test achterliet)
  await page.evaluate(() => { try { window.localStorage.removeItem("mg.theme"); } catch { /* ignore */ } });
}

for (const theme of ["light", "dark"] as const) {
  for (const width of [1440, 390] as const) {
    test.describe(`${theme} ${width}`, () => {
      test.use({ viewport: { width, height: width === 390 ? 844 : 900 } });

      test(`API-sleutel aanmaken, één keer tonen en intrekken (${theme}, ${width})`, async ({ page, request }) => {
        const name = `mso-${theme}-${width}`;
        await login(page);
        await setTheme(page, theme);
        await page.goto("/instellingen/api-sleutels");
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await expect(page.getByRole("heading", { name: "API-sleutels" }).first()).toBeVisible();
        await expect(page.locator(".state--error")).toHaveCount(0);

        // aanmaken: operator is vooraf gekozen, met uitleg voor MSO
        await page.getByTestId("apikey-new").click();
        const dialog = page.getByRole("dialog", { name: "Nieuwe sleutel" });
        await expect(dialog.getByRole("button", { name: "Operator" })).toHaveAttribute("aria-pressed", "true");
        await expect(dialog).toContainText("Media Source Optimizer");
        await expect(page.getByTestId("apikey-create")).toBeDisabled();
        await dialog.getByLabel("Naam").fill(name);
        await page.getByTestId("apikey-create").click();

        // eenmalig tonen, met kopieerknop en waarschuwing
        const shown = page.getByRole("dialog", { name: `Sleutel '${name}' aangemaakt` });
        await expect(shown).toContainText("Deze sleutel wordt niet opnieuw getoond");
        const key = await page.getByTestId("apikey-value").inputValue();
        expect(key).toMatch(/^mgk_[A-Za-z0-9_-]{40,}$/);
        await expect(page.getByTestId("apikey-copy")).toBeEnabled();
        await page.getByTestId("apikey-done").click();
        await expect(shown).toHaveCount(0);

        // de lijst toont alleen de prefix, nooit de sleutel
        const row = page.getByTestId(`apikey-row-${name}`);
        await expect(row).toContainText(key.slice(0, 8));
        await expect(row).toContainText("Actief");
        await expect(page.locator("body")).not.toContainText(key);

        // machine-toegang zoals MSO: geen cookie, geen CSRF
        const me = await request.get("/api/v1/auth/me", { headers: { "X-Api-Key": key, Cookie: "" } });
        expect(me.status()).toBe(200);
        expect(await me.json()).toMatchObject({ username: `apikey:${name}`, role: "operator", csrf_token: null });

        // intrekken via de DangerDialog
        await row.getByRole("button", { name: "Intrekken" }).click();
        const danger = page.getByRole("alertdialog");
        await expect(danger).toContainText(name);
        await danger.getByRole("button", { name: "Intrekken" }).click();
        await expect(danger).toHaveCount(0);
        await expect(row).toContainText("Ingetrokken");
        await expect(row.getByRole("button", { name: "Intrekken" })).toHaveCount(0);

        const after = await request.get("/api/v1/auth/me", { headers: { "X-Api-Key": key, Cookie: "" } });
        expect(after.status()).toBe(401);
        expect(await after.json()).toEqual({ detail: "Ongeldige of ingetrokken API-sleutel" });

        await page.screenshot({ path: `e2e/.results/apikeys-${theme}-${width}.png`, fullPage: true });
        await setTheme(page, null);
      });
    });
  }
}
