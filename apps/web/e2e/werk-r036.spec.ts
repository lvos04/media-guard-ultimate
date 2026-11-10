import { expect, test, type Page } from "@playwright/test";

/** Rapport 6: noodstop-dialoog met typen, vangnet, uitgestelde activatie, weesbestanden, telefoon. */
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

test("noodstop: reden letter voor letter typen sluit de dialoog niet (N7)", async ({ page }) => {
  await login(page);
  await page.getByTestId("profile-button").click();
  await page.getByTestId("estop-button").click();
  const dialog = page.getByRole("alertdialog", { name: "Noodstop activeren?" });
  await expect(dialog).toBeVisible();
  const field = page.getByLabel("Reden (komt in de auditlog)");
  await field.click();
  // tekens die elders sneltoetsen zijn (g, /, ?, j, k) en spaties
  const reason = "Op verzoek: g o / ? j k alles pauzeren";
  await field.pressSequentially(reason, { delay: 15 });
  await expect(dialog).toBeVisible();
  await expect(field).toHaveValue(reason);
  await expect(field).toBeFocused();
  // annuleren sluit zonder iets te doen
  await dialog.getByRole("button", { name: "Annuleren" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId("estop-banner")).toHaveCount(0);
  // opnieuw: volledig typen en activeren
  await page.getByTestId("profile-button").click();
  await page.getByTestId("estop-button").click();
  await page.getByLabel("Reden (komt in de auditlog)").pressSequentially("E2E volledige reden", { delay: 10 });
  await page.getByTestId("estop-confirm").click();
  await expect(page.getByTestId("estop-banner")).toContainText("E2E volledige reden");
  await page.getByTestId("estop-banner").getByRole("button", { name: "Noodstop opheffen" }).click();
  await expect(page.getByTestId("estop-banner")).toHaveCount(0);
  await expect(page.getByText(/Noodstop opgeheven/)).toBeVisible();
});

test("Terugzetten: vangnet bij Direct verwijderen is zichtbaar met volgende opruiming (N8)", async ({ page }) => {
  await login(page);
  await page.route("**/api/v1/quarantine/usage", (r) => r.fulfill({ json: {
    used_bytes: 438e9, quota_bytes: 500e9, percent: 87.6, quota_exceeded: false, encode_queue_paused: false,
    release_checks: { passed: 30, failed: 0, pending: 0 }, retention_days: 7, auto_purge: true,
    next_purge: { count: 0, bytes: 0 }, mode: "immediate", restorable: true,
    safety_net: { hours: 48, bytes: 500e9, max_bytes: 500e9, items: 30, used_bytes: 438e9, first_expiry_at: "2026-11-07T19:00:00Z" },
  } }));
  await page.goto("/quarantaine");
  await expect(page.getByTestId("safety-net")).toContainText("48 uur");
  await expect(page.getByTestId("safety-net")).toContainText("30 item");
  await expect(page.getByTestId("quarantine-mode")).not.toContainText("terugzetten is niet mogelijk");
  await expect(page.getByTestId("next-purge")).toContainText("7 nov");
});

test("AV1: uitgestelde activatie toont blokkade en automatische herkansing (N4)", async ({ page }) => {
  await login(page);
  await page.route(/\/api\/v1\/encoding\/jobs\?.*awaiting_activation/, (r) => r.fulfill({ json: [{
    id: 91, source_path: "/data/Media/Films/Webloper/Webloper.mkv", status: "awaiting_activation",
    error: "Activatie uitgesteld: er loopt nog een download/import", source_snapshot: { size: 40e9 }, staged_output: { size: 7e9 },
    deferred: { reason_nl: "Er loopt nog een import voor deze film", retry_at: "2026-11-06T08:30:00Z",
      blocked_by: { title: "Webloper Achter het Scherm 2160p", client: "SABnzbd", since: "2026-11-06T07:10:00Z", href: "/activiteit?tab=downloads" } },
  }] }));
  await page.goto("/av1");
  await expect(page.getByTestId("deferred-91")).toContainText("Wacht op: Webloper");
  await expect(page.getByTestId("deferred-91")).toContainText("Wordt automatisch opnieuw geprobeerd");
  await expect(page.getByTestId("awaiting-activation").getByRole("button", { name: "Nu proberen" })).toBeVisible();
});

test("Systeem: weesbestanden in quarantaine — automatisch opgeruimd en wat overblijft (N9)", async ({ page }) => {
  await login(page);
  await page.route("**/api/v1/quarantine/orphans", (r) => r.fulfill({ json: {
    checked_at: "2026-11-06T06:00:00Z", removed: 5, freed_bytes: 239e9, left: 1, left_bytes: 13e9,
    items: [{ path: "/data/.mediaguard-quarantine/txn-6", size: 13e9, reason_nl: "Origineel niet op zijn plek gevonden", removed: false },
      { path: "/data/.mediaguard-quarantine/txn-1", size: 63.4e9, reason_nl: "Geannuleerde vervanging", removed: true }] } }));
  await page.goto("/systeem");
  const card = page.getByTestId("quarantine-orphans");
  await expect(card).toContainText("5 weesbestanden automatisch opgeruimd");
  await expect(card).toContainText("1 weesbestand");
  await expect(card).toContainText("txn-6");
  await expect(card).not.toContainText("txn-1");
});

test.describe("telefoon", () => {
  test.use({ viewport: { width: 360, height: 800 } });
  test("noodstop-dialoog en terugzetten scrollen niet zijwaarts", async ({ page }) => {
    await login(page);
    for (const path of ["/quarantaine", "/systeem", "/av1"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(over, path).toBeLessThanOrEqual(1);
    }
  });
});
