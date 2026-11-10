import { expect, test, type Page } from "@playwright/test";

/** Rapport 5: uitlegpagina en vaktermen, Downloads, opslagprognose, bel, telefoonbreedte. */
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

test("Hoe werkt MediaGuard: schema, woordenlijst en link vanuit de kopbalk (M10)", async ({ page }) => {
  await login(page);
  await page.getByTestId("help-link").click();
  await expect(page.getByRole("heading", { name: "Hoe werkt MediaGuard", level: 1 })).toBeVisible();
  await expect(page.getByTestId("glossary")).toContainText("Vrijgavecontrole");
  await expect(page.getByTestId("glossary")).toContainText("Prullenbak");
  await expect(page.locator("#herstelbundel")).toHaveCount(1);
});

test("vakterm toont uitleg en linkt naar de woordenlijst (M10)", async ({ page }) => {
  await login(page);
  await page.goto("/quarantaine");
  await page.getByTestId("term-vrijgavecontrole").click();
  const tip = page.getByRole("tooltip");
  await expect(tip).toContainText("vóór de overdracht");
  await tip.getByRole("link").click();
  await expect(page).toHaveURL(/\/hoe-werkt-het#vrijgavecontrole$/);
});

test("Activiteit → Downloads: bronnen, oorzaken en clientfouten (M7)", async ({ page }) => {
  await login(page);
  await page.route("**/api/v1/downloads/health", (r) => r.fulfill({ json: {
    sources_7d: { indexer: [{ name: "NZBgeek", grabs: 10, imported: 7, failed: 3, success_rate: 0.7, avoided: false }],
      group: [{ name: "GRPB", grabs: 4, imported: 0, failed: 4, success_rate: 0, avoided: true }] },
    sources_30d: { indexer: [], group: [] },
    client_errors: [{ client: "SABnzbd", name: "Dr.Steen.S01E21", reason_nl: "Ontbrekende artikelen", at: "2026-11-05 10:00:00" }],
    causes: [{ reason_nl: "Download niet meer in de client", count: 8 }], paused_items: [],
  } }));
  await page.goto("/activiteit?tab=downloads");
  await expect(page.getByTestId("download-health")).toBeVisible();
  await expect(page.getByTestId("dl-group")).toContainText("Wordt vermeden");
  await expect(page.getByTestId("dl-client-errors")).toContainText("Ontbrekende artikelen");
});

test("Systeem: vrije ruimte over tijd met prognose (M6)", async ({ page }) => {
  await login(page);
  await page.route("**/api/v1/storage/forecast", (r) => r.fulfill({ json: {
    disks: [{ path: "/data/Media", free: 21_700_000_000_000, total: 40_000_000_000_000, trend_per_day: -300_000_000_000, days_left: 72, days_left_nl: "Vol over ± 72 dagen",
      history: [{ day: "2026-11-03", free: 22_300_000_000_000 }, { day: "2026-11-04", free: 22_000_000_000_000 }, { day: "2026-11-05", free: 21_700_000_000_000 }], enough_data: true }],
    soonest_days: 72, soonest_nl: "Schijf vol over ± 72 dagen", basis_nl: "Op basis van 3 dagen", truenas: null,
  } }));
  await page.goto("/systeem");
  await expect(page.getByTestId("storage-trend")).toContainText("Schijf vol over ± 72 dagen");
});

test("bel telt wat op jou wacht als er niets ongelezen is (T12)", async ({ page }) => {
  await page.route("**/api/v1/notifications/count", (r) => r.fulfill({ json: { unread: 0, attention: 3, badge: 3 } }));
  await login(page);
  await expect(page.getByTestId("bell-badge")).toHaveText("3");
});

test.describe("telefoon", () => {
  test.use({ viewport: { width: 360, height: 800 } });
  test("nieuwe pagina's scrollen niet zijwaarts, ook met lange paden", async ({ page }) => {
    const long = "/mnt/een-heel-lange-poolnaam-voor-media/met/diepe/mappen/en-nog-een-lange-mapnaam-zonder-spaties/Films";
    await page.route("**/api/v1/storage/forecast", (r) => r.fulfill({ json: {
      disks: [{ path: long, free: 21e12, total: 40e12, trend_per_day: -1e11, days_left: 200, days_left_nl: "Vol over ± 200 dagen", history: [], enough_data: false }],
      soonest_days: 200, soonest_nl: "Schijf vol over ± 200 dagen", truenas: null } }));
    await page.route("**/api/v1/storage/twins", (r) => r.fulfill({ json: [{ instance_id: "radarr-films", instance_name: "Films", root_folder_id: 1, path: `${long}/films`, twin_of: [`${long}/Films`], offline: false, items: 0, empty_on_disk: true, removable: true }] }));
    await login(page);
    for (const path of ["/hoe-werkt-het", "/activiteit?tab=downloads", "/systeem", "/ontbrekend", "/av1/instellingen"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(over, path).toBeLessThanOrEqual(1);
    }
  });
});
