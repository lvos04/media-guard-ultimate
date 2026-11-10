import fs from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

/**
 * Nieuwe opzet (november 2026): elke route uit de functie-inventaris opent zonder 404 of
 * foutstatus, elke oude deep link komt op de juiste plek uit, en het hoofdmenu heeft precies
 * de vijf onderdelen + Instellingen. Bron: docs/design/ia-map.json (scripts/gen-feature-inventory.py).
 */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };
const MAP = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../../docs/design/ia-map.json"), "utf8")) as {
  routes: Record<string, { area: string; where: string; behaviour: string }>;
  deep_links: Array<{ from: string; to: string }>;
};

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

async function notBroken(page: Page, href: string) {
  const resp = await page.goto(href, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => undefined);
  expect(resp?.status() ?? 200, `${href}: HTTP-status`).toBeLessThan(400);
  expect(await page.getByTestId("not-found").count(), `${href}: 404-pagina`).toBe(0);
  expect(await page.locator("body").innerText(), `${href}: Next-404`).not.toMatch(/This page could not be found/);
  // een pagina-kop moet er zijn (geen lege of gecrashte pagina)
  await expect(page.locator("h1").first(), `${href}: paginakop`).toBeVisible();
}

const norm = (u: string) => {
  const url = new URL(u, "http://x");
  const qs = [...url.searchParams.entries()].sort().map(([k, v]) => `${k}=${v}`).join("&");
  return `${url.pathname}${qs ? `?${qs}` : ""}${url.hash}`;
};

test("elke route uit de inventaris opent op een echte pagina", async ({ page }) => {
  test.setTimeout(240_000);
  await login(page);
  const routes = Object.keys(MAP.routes).filter((r) => !r.includes("[") && r !== "/login" && r !== "/setup");
  expect(routes.length).toBeGreaterThan(30);
  for (const r of routes) await notBroken(page, r);
});

test("oude adressen en deep links komen op de juiste plek uit", async ({ page }) => {
  test.setTimeout(240_000);
  await login(page);
  for (const { from, to } of MAP.deep_links) {
    await page.goto(from, { waitUntil: "domcontentloaded" });
    await expect.poll(() => norm(page.url()), { message: `${from} → ${to}`, timeout: 10_000 }).toBe(norm(to));
    expect(await page.getByTestId("not-found").count(), `${from}: 404`).toBe(0);
  }
});

test("hoofdmenu: vijf onderdelen + Instellingen; maximaal één tabrij per pagina", async ({ page }) => {
  await login(page);
  const nav = page.locator("aside.sidebar");
  for (const name of ["Overzicht", "Activiteit", "Bibliotheek", "Ruimte", "Aanvragen", "Instellingen"]) {
    await expect(nav.getByRole("link", { name, exact: false }).first()).toBeVisible();
  }
  for (const href of ["/instellingen", "/inrichten", "/systeem", "/kwaliteit", "/av1", "/av1/wachtrij", "/quarantaine", "/activiteit", "/aanvragen", "/instellingen/meldingen", "/dolby-vision"]) {
    await page.goto(href);
    await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => undefined);
    expect(await page.locator("main [role='tablist']").count(), `${href}: aantal tabrijen`).toBeLessThanOrEqual(1);
  }
});

test("telefoon: onderste balk met de vijf onderdelen, Instellingen via het profielmenu", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  const bottom = page.locator("nav.bottomnav");
  for (const name of ["Overzicht", "Activiteit", "Bibliotheek", "Ruimte", "Aanvragen"]) await expect(bottom.getByText(name, { exact: true })).toBeVisible();
  await page.getByTestId("profile-button").click();
  await page.getByRole("menuitem", { name: "Instellingen" }).click();
  await expect(page).toHaveURL(/\/instellingen/);
});

test("geen horizontale scroll op 360 en 390 px, donker en licht", async ({ page }) => {
  test.setTimeout(180_000);
  await login(page);
  for (const theme of ["dark", "light"]) {
    await page.evaluate((th) => window.localStorage.setItem("mg.theme", th), theme);
    for (const width of [360, 390]) {
      await page.setViewportSize({ width, height: 800 });
      for (const href of ["/", "/activiteit", "/bibliotheek", "/av1", "/aanvragen", "/instellingen", "/instellingen/api-sleutels", "/systeem"]) {
        await page.goto(href);
        await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => undefined);
        expect(await page.evaluate(() => document.documentElement.dataset.theme ?? ""), `${href}: thema`).toBe(theme);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, `${href} @ ${width}px (${theme}): horizontale scroll`).toBeLessThanOrEqual(1);
      }
    }
  }
});
