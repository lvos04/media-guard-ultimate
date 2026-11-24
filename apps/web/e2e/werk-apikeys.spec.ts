import { execFileSync } from "node:child_process";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { ADMIN } from "./fixtures/admin";

/** API-sleutels: aanmaken → één keer tonen → werkt tegen de echte core → intrekken → 401. */

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

// -- rechten (scopes), S-P3-600: meetpunten uit docs/ontwerp/API_SLEUTEL_SCOPES_ONTWERP.md §8 --------------------------
const SCOPE = "emergency_stop:on";
const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const ROL_EN_RECHTEN = "Rechten (scopes) kunnen alleen op een sleutel met rol kijker. Kies rol kijker of laat de rechten leeg.";

async function csrf(page: Page): Promise<Record<string, string>> {
  const me = await (await page.request.get("/api/v1/auth/me")).json() as { csrf_token: string };
  return { "X-CSRF-Token": me.csrf_token };
}

async function newKeyDialog(page: Page) {
  await page.getByTestId("apikey-new").click();
  return page.getByRole("dialog", { name: "Nieuwe sleutel" });
}

/** Maakt de sleutel aan via de dialoog en geeft de request-body en de eenmalig getoonde sleutel terug. */
async function createVia(page: Page, name: string): Promise<{ body: Record<string, unknown>; key: string }> {
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/v1/auth/api-keys") && r.method() === "POST");
  await page.getByTestId("apikey-create").click();
  const body = (await sent).postDataJSON() as Record<string, unknown>;
  await expect(page.getByRole("dialog", { name: `Sleutel '${name}' aangemaakt` })).toBeVisible();
  const key = await page.getByTestId("apikey-value").inputValue();
  await page.getByTestId("apikey-done").click();
  return { body, key };
}

async function stopUit(request: APIRequestContext, page: Page) {
  const r = await request.post("/api/v1/system/emergency-stop", { data: { active: false, reason: "e2e opruimen" }, headers: await csrf(page) });
  expect(r.ok()).toBeTruthy();
}

for (const theme of ["light", "dark"] as const) {
  for (const width of [1440, 390] as const) {
    test.describe(`rechten ${theme} ${width}`, () => {
      test.use({ viewport: { width, height: width === 390 ? 844 : 900 } });

      test(`rechten (scopes): alleen bij Kijker, terug naar Operator wist ze, aanmaken, tonen en intrekken (${theme}, ${width})`, async ({ page }) => {
        const hub = `hub-${theme}-${width}`;
        const op = `mso-r-${theme}-${width}`;
        const lezer = `lezer-${theme}-${width}`;
        await login(page);
        await setTheme(page, theme);
        await page.goto("/instellingen/api-sleutels");
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await expect(page.locator(".state--error")).toHaveCount(0);

        // 1. Operator (standaard): geen veld in de DOM
        let dialog = await newKeyDialog(page);
        await expect(dialog.getByRole("button", { name: "Operator" })).toHaveAttribute("aria-pressed", "true");
        await expect(page.locator("#apikey-scope-estop")).toHaveCount(0);
        await expect(dialog.getByRole("group", { name: "Rechten (scopes)" })).toHaveCount(0);

        // 2. Kijker: de schakelaar staat uit, met naam en uitleg ("nooit opheffen")
        await dialog.getByRole("button", { name: "Kijker" }).click();
        const sw = dialog.getByRole("switch", { name: "Noodstop activeren" });
        await expect(sw).toBeVisible();
        await expect(sw).toHaveAttribute("aria-checked", "false");
        await expect(sw).toHaveAccessibleDescription(/nooit opheffen/);
        await expect(sw).toHaveAccessibleDescription(/emergency_stop:on/);
        await expect(dialog.getByRole("group", { name: "Rechten (scopes)" })).toHaveAccessibleDescription(/Alles uit = een gewone kijker-sleutel/);
        await expect(dialog).toContainText("Alleen lezen; kan niets laten zoeken of wijzigen.");

        // 3. aanzetten (ook via het label), terug naar Operator, weer Kijker: uit; als Operator aanmaken stuurt geen scopes
        await dialog.locator('label[for="apikey-scope-estop"]').click();
        await expect(sw).toHaveAttribute("aria-checked", "true");
        await expect(dialog).toContainText("Alleen lezen, plus precies de rechten hieronder.");
        await dialog.getByRole("button", { name: "Operator" }).click();
        await expect(page.locator("#apikey-scope-estop")).toHaveCount(0);
        await dialog.getByRole("button", { name: "Kijker" }).click();
        await expect(sw).toHaveAttribute("aria-checked", "false");
        await sw.click();
        await dialog.getByRole("button", { name: "Operator" }).click();
        await dialog.getByLabel("Naam").fill(op);
        const asOperator = await createVia(page, op);
        expect(asOperator.body).toEqual({ name: op, role: "operator" });
        await expect(page.getByTestId(`apikey-row-${op}`)).toContainText("Operator");
        await expect(page.getByTestId(`apikey-scopes-${op}`)).toHaveCount(0);

        // 5. Kijker zonder recht: de rij ziet eruit als altijd
        dialog = await newKeyDialog(page);
        await expect(dialog.getByRole("button", { name: "Operator" })).toHaveAttribute("aria-pressed", "true");
        await dialog.getByRole("button", { name: "Kijker" }).click();
        await expect(dialog.getByRole("switch", { name: "Noodstop activeren" })).toHaveAttribute("aria-checked", "false");
        await dialog.getByLabel("Naam").fill(lezer);
        expect((await createVia(page, lezer)).body).toEqual({ name: lezer, role: "viewer" });
        await expect(page.getByTestId(`apikey-row-${lezer}`)).toContainText("Kijker");
        await expect(page.getByTestId(`apikey-row-${lezer}`)).toContainText("Mag: Alleen lezen");
        await expect(page.getByTestId(`apikey-scopes-${lezer}`)).toHaveCount(0);

        // 4. Kijker plus recht: "Kijker" en "Noodstop activeren" bij de naam, en "Mag:" zegt "nooit opheffen"
        dialog = await newKeyDialog(page);
        await expect(page.locator("#apikey-scope-estop")).toHaveCount(0); // opnieuw open: Operator, rechten leeg
        await dialog.getByRole("button", { name: "Kijker" }).click();
        await dialog.getByRole("switch", { name: "Noodstop activeren" }).click();
        await dialog.getByLabel("Naam").fill(hub);
        const made = await createVia(page, hub);
        expect(made.body).toEqual({ name: hub, role: "viewer", scopes: [SCOPE] });
        const row = page.getByTestId(`apikey-row-${hub}`);
        await expect(row).toContainText("Kijker");
        await expect(page.getByTestId(`apikey-scopes-${hub}`)).toContainText("Noodstop activeren");
        await expect(row).toContainText("Mag: Alleen lezen, plus de noodstop activeren (nooit opheffen); verder niets");
        await expect(page.locator("body")).not.toContainText(made.key);

        // de sleutel tegen de echte core: lezen en de noodstop aanzetten mag, opheffen nooit
        const h = { "X-Api-Key": made.key, Cookie: "" };
        const me = await page.request.get("/api/v1/auth/me", { headers: h });
        expect(await me.json()).toMatchObject({ username: `apikey:${hub}`, role: "viewer", scopes: [SCOPE] });
        try {
          const on = await page.request.post("/api/v1/system/emergency-stop", { headers: h, data: { active: true, reason: "e2e" } });
          expect(on.status()).toBe(200);
          const off = await page.request.post("/api/v1/system/emergency-stop", { headers: h, data: { active: false } });
          expect(off.status()).toBe(403);
          expect(await off.json()).toEqual({ detail: "Deze API-sleutel mag de noodstop alleen aanzetten, nooit opheffen" });
          const other = await page.request.post("/api/v1/notifications/read-all", { headers: h, data: {} });
          expect(other.status()).toBe(403);
        } finally {
          await stopUit(page.request, page);
        }

        // 7. intrekdialoog: feit "Rechten (scopes)" met het label; na intrekken blijven de labels staan
        await row.getByRole("button", { name: "Intrekken" }).click();
        const danger = page.getByRole("alertdialog");
        await expect(danger).toContainText("Rechten (scopes)");
        await expect(danger.getByTestId("apikey-revoke-scopes")).toContainText("Noodstop activeren");
        await page.screenshot({ path: `e2e/.results/apikeys-scopes-intrekken-${theme}-${width}.png`, fullPage: true });
        await danger.getByRole("button", { name: "Intrekken" }).click();
        await expect(danger).toHaveCount(0);
        await expect(row).toContainText("Ingetrokken");
        await expect(page.getByTestId(`apikey-scopes-${hub}`)).toContainText("Noodstop activeren");
        const after = await page.request.post("/api/v1/system/emergency-stop", { headers: h, data: { active: true } });
        expect(after.status()).toBe(401);

        // de dialoog met het recht aan, voor de ontwerpreview
        dialog = await newKeyDialog(page);
        await dialog.getByRole("button", { name: "Kijker" }).click();
        await dialog.getByRole("switch", { name: "Noodstop activeren" }).click();
        await page.screenshot({ path: `e2e/.results/apikeys-scopes-dialoog-${theme}-${width}.png`, fullPage: true });
        await page.keyboard.press("Escape");
        await setTheme(page, null);
      });
    });
  }
}

test("rechten (scopes) via de API: operator met rechten en een onbekend recht geven 422 met een Nederlandse tekst", async ({ page }) => {
  // 6. rechtstreeks via de API, met de tekst letterlijk in het antwoord
  await login(page);
  const headers = await csrf(page);
  const bad = async (data: Record<string, unknown>) => {
    const r = await page.request.post("/api/v1/auth/api-keys", { data, headers });
    expect(r.status()).toBe(422);
    return (await r.json()) as { detail: unknown };
  };
  expect(await bad({ name: "api-op", role: "operator", scopes: [SCOPE] })).toEqual({ detail: ROL_EN_RECHTEN });
  expect(await bad({ name: "api-onbekend", role: "viewer", scopes: ["vliegen"] })).toEqual({ detail: `Onbekend recht (scope) 'vliegen'. Toegestaan: ${SCOPE}.` });
  expect(await bad({ name: "api-zoeken", role: "viewer", scopes: ["wanted:search"] })).toEqual({ detail: `Onbekend recht (scope) 'wanted:search'. Toegestaan: ${SCOPE}.` });
  const list = await (await page.request.get("/api/v1/auth/api-keys")).json() as { items: Array<{ name: string }> };
  expect(list.items.map((i) => i.name)).not.toContain("api-op");
});

test("rechten (scopes): een serverfout staat bovenaan de dialoog, de invoer blijft staan", async ({ page }) => {
  // §3.3: het scherm kan de fout zelf niet veroorzaken; hier antwoordt de server (nagebootst) met de 422-tekst
  await login(page);
  await page.goto("/instellingen/api-sleutels");
  const dialog = await newKeyDialog(page);
  await dialog.getByRole("button", { name: "Kijker" }).click();
  await dialog.getByRole("switch", { name: "Noodstop activeren" }).click();
  await dialog.getByLabel("Naam").fill("hub-fout");
  const detail = `Onbekend recht (scope) 'wanted:search'. Toegestaan: ${SCOPE}.`;
  await page.route("**/api/v1/auth/api-keys", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail }) })
    : route.continue());
  await page.getByTestId("apikey-create").click();
  await expect(dialog.getByRole("alert")).toHaveText(detail);
  await expect(dialog.getByLabel("Naam")).toHaveValue("hub-fout");
  await expect(dialog.getByRole("switch", { name: "Noodstop activeren" })).toHaveAttribute("aria-checked", "true");
  await page.unroute("**/api/v1/auth/api-keys");
});

test("rechten (scopes): een recht dat deze versie niet kent heet Onbekend, met reden, ook in geforceerde kleuren", async ({ page }) => {
  await login(page);
  const r = await page.request.post("/api/v1/auth/api-keys", { data: { name: "hub-later", role: "viewer" }, headers: await csrf(page) });
  expect(r.ok()).toBeTruthy();
  const { id } = await r.json() as { id: number };
  // zoals na terugzetten naar een versie die het recht niet kent: alleen in de testdatabase
  const db = path.join(process.env.MG_E2E_DATA_DIR as string, "mediaguard.db");
  execFileSync(process.env.MG_E2E_PYTHON as string, ["-I", "-c",
    "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('UPDATE api_keys SET scopes=? WHERE id=?', ('[\"wanted:search\"]', int(sys.argv[2]))); c.commit()",
    db, String(id)]);
  await page.goto("/instellingen/api-sleutels");
  const chips = page.getByTestId("apikey-scopes-hub-later");
  await expect(chips).toContainText("Onbekend recht: wanted:search");
  await expect(chips.locator('[data-status="unknown"]')).toHaveCount(1);
  await expect(page.getByTestId("apikey-row-hub-later")).toContainText("Deze versie van MediaGuard kent dit recht niet en staat er niets mee toe.");
  await expect(page.getByTestId("apikey-row-hub-later")).toContainText("Mag: Alleen lezen; verder niets");
  await page.emulateMedia({ forcedColors: "active" });
  const style = await chips.locator('[data-status="unknown"]').evaluate((el) => getComputedStyle(el).borderTopStyle);
  expect(style).toBe("dashed");
  await page.emulateMedia({ forcedColors: "none" });
});

for (const width of [320, 1440] as const) {
  test(`rechten (scopes): dialoog op Kijker zonder axe-bevinding, doelen ≥ 44 px, geen overloop en de Tab-volgorde (${width})`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 320 ? 700 : 900 });
    await login(page);
    await page.goto("/instellingen/api-sleutels");
    const dialog = await newKeyDialog(page);
    const kijker = dialog.getByRole("button", { name: "Kijker" });
    await kijker.click();
    const sw = dialog.getByRole("switch", { name: "Noodstop activeren" });
    await expect(sw).toBeVisible();
    // Tab: Kijker → Noodstop activeren → Vervaldatum; terug naar Operator verliest de focus niet
    await kijker.focus();
    await page.keyboard.press("Tab");
    await expect(sw).toBeFocused();
    await page.keyboard.press("Space");
    await expect(sw).toHaveAttribute("aria-checked", "true");
    await page.keyboard.press("Tab");
    await expect(dialog.locator("#apikey-expires")).toBeFocused();
    const box = await sw.boundingBox();
    expect(box && box.width >= 44 && box.height >= 44, `schakelaar ${JSON.stringify(box)}`).toBeTruthy();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const small = await dialog.locator("#apikey-scopes").evaluate((group) => [...group.querySelectorAll("*")]
      .filter((el) => [...el.childNodes].some((n) => n.nodeType === 3 && (n.textContent ?? "").trim()))
      .map((el) => parseFloat(getComputedStyle(el).fontSize)).filter((px) => px < 13.95));
    expect(small).toEqual([]);
    const res = await new AxeBuilder({ page: page as unknown as ConstructorParameters<typeof AxeBuilder>[0]["page"] }).include('[role="dialog"]').withTags(AXE_TAGS).analyze();
    expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    await dialog.getByRole("button", { name: "Operator" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#apikey-scope-estop")).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Operator" })).toBeFocused();
  });
}
