import { expect, test } from "@playwright/test";

/** Mediaserver (Emby) en ondertitels: alles in te stellen in de app (rapport 5, M3/M8). */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

test.beforeAll(async ({ request }) => {
  const status = await (await request.get("/api/v1/auth/setup-status")).json() as { needs_setup: boolean };
  if (status.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
});

for (const width of [1440, 390] as const) {
  test(`mediaserver-tab: Emby-sleutel en schakelaars in de app (${width})`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/login");
    await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
    await page.getByLabel("Wachtwoord").fill(ADMIN.password);
    await page.getByRole("button", { name: "Inloggen" }).click();
    await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
    await page.goto("/inrichten?tab=mediaserver");
    const tab = page.getByTestId("mediaserver-tab");
    await expect(tab).toBeVisible();
    await expect(tab).toContainText("Mediaserver");
    // waar de sleutel te maken is, staat erbij
    await expect(tab).toContainText("Geavanceerd → API-sleutels");
    await expect(tab.getByLabel("API-sleutel", { exact: true })).toBeVisible();
    await expect(tab.getByRole("button", { name: "Verbinding testen" })).toBeVisible();
    for (const name of ["Verversen na overdracht", "Afspeeltest", "Recent bekeken niet omzetten"]) {
      await expect(tab.getByRole("switch", { name })).toBeVisible();
    }
    await expect(page.getByTestId("subtitles-card")).toContainText("Bazarr");
    // schakelaar werkt en blijft bewaard
    const sw = tab.getByRole("switch", { name: "Afspeeltest" });
    const before = await sw.getAttribute("aria-checked");
    await sw.click();
    await expect(sw).not.toHaveAttribute("aria-checked", String(before));
    const st = await (await page.request.get("/api/v1/mediaserver/status")).json() as { features: { playback_test: boolean } };
    expect(String(st.features.playback_test)).not.toBe(before);
    await sw.click();
    await expect(sw).toHaveAttribute("aria-checked", String(before));
    // geen zijwaarts scrollen
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    // statustekst blijft binnen de pil, met elk lettertype (390 px: de ondertitelchip liep er 35 px uit)
    const chipOverflow = await tab.locator(".chip").evaluateAll((els) => els.map((el) => el.scrollWidth - el.clientWidth));
    expect(chipOverflow.length).toBeGreaterThan(0);
    for (const extra of chipOverflow) expect(extra).toBeLessThanOrEqual(1);
    // Peil O-01: systeemfont (geen meegeleverd lettertype, geen fontverzoeken)
    const font = await page.evaluate(() => ({
      inter: getComputedStyle(document.documentElement).getPropertyValue("--font-inter").trim(),
      body: getComputedStyle(document.body).fontFamily,
    }));
    expect(font.inter).toBe("");
    expect(font.body.split(",")[0].replace(/["']/g, "").trim()).toBe("system-ui");
  });
}
