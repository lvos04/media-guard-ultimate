import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

/**
 * Verbeterlijst 24, 25, 26 en 39 tegen de echte core:
 *  24 — telefoon: Opslaan-balk boven de navigatie, vraag bij weggaan met niet-opgeslagen
 *       wijzigingen, meldingenpaneel binnen beeld;
 *  25 — 'Instellingen voor deze titel' op de detailpagina (zonder ID's te typen);
 *  26 — Aanvragen: leverstatus bovenaan met looptijd, pogingen, titel-link en 'Nu zoeken';
 *  39 — noodstop: Overzicht zegt 'gepauzeerd' in plaats van 'bezig'.
 */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };
const PHONE = { width: 390, height: 844 };

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

async function csrf(page: Page): Promise<string> {
  return (await (await page.request.get("/api/v1/auth/me")).json() as { csrf_token: string }).csrf_token;
}

/** Eén serie in de bibliotheekcache en een lopende aanvraag ervoor (test-only, rechtstreeks in SQLite). */
function seedTitle(): { libId: number; requestId: number } {
  const db = path.join(process.env.MG_E2E_DATA_DIR as string, "mediaguard.db");
  const script = `
import sqlite3, sys, datetime
now = datetime.datetime.now(datetime.timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
c = sqlite3.connect(sys.argv[1])
row = c.execute("SELECT id FROM lib_items WHERE instance_id='sonarr-series' AND provider_id=4242").fetchone()
lib = row[0] if row else c.execute("INSERT INTO lib_items(instance_id,kind,provider_id,title,year,tvdb_id,monitored,synced_at) VALUES ('sonarr-series','series',4242,'E2E Titelserie',2024,424242,1,?)", (iso(now),)).lastrowid
row = c.execute("SELECT id FROM delivery_items WHERE item_key='series:tvdb:424242'").fetchone()
req = row[0] if row else c.execute("INSERT INTO delivery_items(item_key,source,media_type,tvdb_id,title,instance_id,provider_id,stage,state,detail_nl,next_step_nl,attempts,requested_at,created_at,updated_at) VALUES ('series:tvdb:424242','seerr','series',424242,'E2E Titelserie','sonarr-series',4242,'searching','active','2 aflevering(en) ontbreken','Volgende zoekpoging morgen',3,?,?,?)", (iso(now - datetime.timedelta(days=3, hours=2)), iso(now), iso(now))).lastrowid
c.commit(); print(lib, req)
`;
  const out = execFileSync(process.env.MG_E2E_PYTHON as string, ["-I", "-c", script, db], { encoding: "utf8" }).trim().split(" ").map(Number);
  return { libId: out[0], requestId: out[1] };
}

test("telefoon: Opslaan-balk boven de navigatie en eerst vragen bij weggaan (24)", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await login(page);
  await page.goto("/instellingen");
  await page.getByTestId("settings-search").fill("jobs.max_concurrency");
  const field = page.locator('[data-setting="jobs.max_concurrency"]').getByRole("textbox");
  await field.fill("6");
  const bar = page.locator(".savebar");
  await expect(bar).toBeVisible();
  const barBox = (await bar.boundingBox())!;
  const navBox = (await page.locator(".bottomnav").boundingBox())!;
  // de balk staat boven de onderste navigatie, niet erachter
  expect(barBox.y + barBox.height).toBeLessThanOrEqual(navBox.y + 1);

  // naar een andere pagina: eerst de vraag; 'Blijven' houdt de wijziging
  await page.locator(".bottomnav").getByRole("link", { name: "Overzicht" }).click();
  await expect(page.getByRole("alertdialog", { name: "Niet opgeslagen" })).toBeVisible();
  await page.getByTestId("unsaved-stay").click();
  await expect(page).toHaveURL(/\/instellingen/);
  await expect(field).toHaveValue("6");
  // 'Niet opslaan en verder' gaat wel
  await page.locator(".bottomnav").getByRole("link", { name: "Overzicht" }).click();
  await page.getByTestId("unsaved-leave").click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
  // en is niet opgeslagen
  const eff = await (await page.request.get("/api/v1/settings/effective?keys=jobs.max_concurrency")).json() as Array<{ value: number }>;
  expect(eff[0].value).toBe(4);
  // zonder wijzigingen geen vraag
  await page.locator(".bottomnav").getByRole("link", { name: "Activiteit" }).click();
  await expect(page).toHaveURL(/\/activiteit/);
});

test("telefoon: meldingenpaneel valt niet buiten beeld (24)", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await login(page);
  await page.getByTestId("bell").click();
  const panel = page.getByTestId("notif-panel");
  await expect(panel).toBeVisible();
  const box = (await panel.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(PHONE.width);
});

test("titelpagina: 'Niet meer zoeken' en een uitzondering zonder ID te typen (25)", async ({ page }) => {
  const { libId } = seedTitle();
  await page.setViewportSize(PHONE);
  await login(page);
  await page.goto(`/bibliotheek/sonarr-series/${libId}`);
  const card = page.getByTestId("title-settings");
  await expect(card).toBeVisible();
  await expect(page.getByRole("heading", { name: "Instellingen voor deze titel" })).toBeVisible();

  // Niet meer zoeken: één tik, opgeslagen als uitzondering op deze serie
  await card.getByRole("switch", { name: "Niet meer zoeken" }).click();
  await expect(page.getByText("MediaGuard zoekt deze titel niet meer")).toBeVisible();
  const own = async () => await (await page.request.get("/api/v1/settings/overrides?scope_type=series&scope_id=sonarr-series:4242")).json() as Array<{ key: string; value: unknown }>;
  await expect.poll(async () => (await own()).find((o) => o.key === "backfill.excluded")?.value).toBe(true);

  // uitzondering toevoegen via de onderwerpenlijst
  await page.getByTestId("title-category").selectOption("Ontbrekende media");
  const priority = card.locator('[data-setting="backfill.priority"] select');
  await priority.selectOption("high");
  await page.getByTestId("title-save").click();
  await expect(page.getByTestId("title-exceptions")).toContainText("Uitzonderingen (1)");
  await expect.poll(async () => (await own()).find((o) => o.key === "backfill.priority")?.value).toBe("high");
  // en weer weghalen
  await page.getByTestId("title-remove-backfill.priority").click();
  await expect(page.getByTestId("title-exceptions")).toHaveCount(0);
  await card.getByRole("switch", { name: "Niet meer zoeken" }).click();
  await expect.poll(async () => (await own()).some((o) => o.key === "backfill.excluded")).toBe(false);

  // 'Alle instellingen' opent de uitzonderingen van precies deze titel, met de naam erbij
  await page.getByTestId("title-settings-all").click();
  await expect(page.getByTestId("settings-scope-title")).toContainText("E2E Titelserie");
  // op de telefoon een keuzelijst met onderwerpen in plaats van de lijst links
  await expect(page.getByTestId("settings-category")).toBeVisible();
});

test("Aanvragen: leverstatus bovenaan met looptijd, pogingen, link en 'Nu zoeken' (26, 39)", async ({ page }) => {
  const { libId } = seedTitle();
  await page.setViewportSize(PHONE);
  await login(page);
  await page.goto("/aanvragen");
  const root = page.getByTestId("delivery-root");
  await expect(root).toBeVisible();
  // bovenaan: vóór het zoeken in Seerr
  const rootBox = (await root.boundingBox())!;
  const header = (await page.getByRole("heading", { name: "Aanvragen", level: 1 }).boundingBox())!;
  expect(rootBox.y).toBeGreaterThan(header.y);
  const row = root.getByTestId("delivery-row").filter({ hasText: "E2E Titelserie" });
  await expect(row.getByTestId("delivery-age")).toHaveText("Loopt 3 dagen · 3 zoekpogingen");
  await expect(row.getByTestId("delivery-title-link")).toHaveAttribute("href", `/bibliotheek/sonarr-series/${libId}`);
  await expect(row.getByTestId("delivery-search-now")).toBeEnabled();

  // noodstop: 'Nu zoeken' kan niet, Overzicht zegt 'noodstop' in plaats van 'bezig'
  const token = await csrf(page);
  expect((await page.request.post("/api/v1/system/emergency-stop", { data: { active: true, reason: "e2e" }, headers: { "X-CSRF-Token": token } })).ok()).toBeTruthy();
  try {
    await page.reload();
    await expect(page.getByTestId("delivery-row").filter({ hasText: "E2E Titelserie" }).getByTestId("delivery-search-now")).toBeDisabled();
    await page.goto("/");
    await expect(page.getByTestId("hero-sub")).toContainText("Noodstop staat aan");
  } finally {
    await page.request.post("/api/v1/system/emergency-stop", { data: { active: false, reason: "" }, headers: { "X-CSRF-Token": token } });
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
