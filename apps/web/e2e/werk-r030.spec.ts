import { expect, test, type Page } from "@playwright/test";

/** Verbeterronde na het Cowork-testrapport (4 nov): crash, foutgrenzen, 404, gedeelde SSE, menu's. */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

test("Release Lab: afwijsredenen {text, code} crashen niet; zoeken alleen op verzoek (B3)", async ({ page }) => {
  let searches = 0;
  await page.route("**/api/v1/instances", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: "sonarr-series", name: "Sonarr Series", app: "sonarr", media_type: "tv" }]) }));
  await page.route("**/api/v1/instances/sonarr-series/releases**", (route) => {
    searches += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([
      { guid: "a", title: "Lichtval.S01E03.2160p.WEB-DL", size: 4e9, rejected: true, rejections: [{ text: "Taal komt niet overeen", code: "language" }, "Al in bezit"], quality: { quality: { name: "WEBDL-2160p" } } },
      { guid: "b", title: "Lichtval.S01E03.1080p.WEB-DL", size: 2e9, rejected: false, rejections: [], quality: { quality: { name: "WEBDL-1080p" } } },
    ]) });
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  await page.goto("/release-lab?instance=sonarr-series&episode=452&title=Lichtval%20S01E03");
  await expect(page.getByRole("heading", { name: "Waarom deze release?", level: 1 })).toBeVisible();
  await page.waitForTimeout(800);
  expect(searches, "geen automatische indexerzoekopdracht bij openen").toBe(0);
  await page.getByRole("button", { name: "Releases zoeken" }).click();
  await expect(page.getByText("Taal komt niet overeen", { exact: true })).toBeVisible();
  await expect(page.getByText("Al in bezit")).toBeVisible();
  await expect(page.getByText("This page couldn't load")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("foutgrens per paneel: één kapot onderdeel geeft een Nederlandse melding met 'Opnieuw proberen'", async ({ page }) => {
  // releases als object i.p.v. lijst: de tabel kan dat niet tonen → alleen dat paneel faalt
  await page.route("**/api/v1/instances", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: "sonarr-series", name: "Sonarr Series", app: "sonarr", media_type: "tv" }]) }));
  await page.route("**/api/v1/instances/sonarr-series/releases**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ guid: "x", title: { kapot: true }, rejected: false }]) }));
  await login(page);
  await page.goto("/release-lab?instance=sonarr-series&episode=1&title=Test");
  await page.getByRole("button", { name: "Releases zoeken" }).click();
  await expect(page.getByTestId("error-boundary").first()).toContainText("Dit onderdeel kon niet worden geladen");
  await expect(page.getByTestId("error-boundary").first().getByRole("button", { name: "Opnieuw proberen" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Waarom deze release?", level: 1 })).toBeVisible();
});

test("Nederlandse 404 met knop naar Overzicht", async ({ page }) => {
  await login(page);
  await page.goto("/bestaat-echt-niet");
  await expect(page.getByText("Deze pagina bestaat niet")).toBeVisible();
  await page.getByRole("link", { name: "Naar Overzicht" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
});

test("één SSE-verbinding voor alle tabbladen samen (H5)", async ({ page, context }) => {
  await login(page);
  let streams = 0;
  const seen: string[] = [];
  // alleen verzoeken van de nieuwe tabbladen tellen (de leider mag zelf opnieuw verbinden)
  context.on("request", (r) => { if (r.url().includes("/api/v1/events/stream") && r.frame().page() !== page) { streams += 1; seen.push(`${r.frame().page().url()} ${r.url()}`); } });
  const second = await context.newPage();
  await second.goto("/activiteit");
  await expect(second.getByRole("heading", { name: "Activiteit", level: 1 })).toBeVisible();
  const third = await context.newPage();
  await third.goto("/bibliotheek");
  await expect(third.locator("h1").first()).toBeVisible();
  await page.waitForTimeout(1500);
  expect(streams, `extra tabbladen openen geen eigen stream: ${seen.join(" | ")}`).toBe(0);
  // de leider sluit: een ander tabblad neemt het over
  await page.close();
  await expect.poll(() => streams, { timeout: 10_000 }).toBeGreaterThanOrEqual(1);
  await second.close();
  await third.close();
});

test("profielmenu sluit op Escape, buiten-klik en bij navigeren (M14)", async ({ page }) => {
  await login(page);
  const button = page.getByTestId("profile-button");
  await button.click();
  await expect(page.getByTestId("profile-menu")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("profile-menu")).toHaveCount(0);
  await button.click();
  await page.locator("main").click({ position: { x: 20, y: 300 } });
  await expect(page.getByTestId("profile-menu")).toHaveCount(0);
  await button.click();
  await page.goto("/bibliotheek");
  await expect(page.getByTestId("profile-menu")).toHaveCount(0);
});

test("gezondheid: kopbalk, zijbalk en Overzicht lezen hetzelfde model (B2/H8)", async ({ page }) => {
  await page.route("**/api/v1/health/summary", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    active: { total: 3, downloads: 1, searches: 0, encodes: 1, tasks: 1 }, errors: { total: 8, av1_not_activated: 8 }, attention: { count: 0, errors: 0, warnings: 0 } }) }));
  await page.route("**/api/v1/attention", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], count: 0, errors: 0 }) }));
  await page.route("**/api/v1/health/errors", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ total: 8, groups: [
    { key: "av1_not_activated", label_nl: "AV1-bestanden niet geplaatst", count: 8, href: "/av1/wachtrij?status=mislukt", items: [{ id: 1, title: "Film A", reason_nl: "Schrijven mislukt", href: "/av1/wachtrij?job=1" }] }] }) }));
  await login(page);
  // aandacht gaat vóór 'bezig': kopbalk telt hetzelfde als 'Wacht op jou' (rapport 2, N13)
  // Peil: totaalstatus Beperkt (gemeten fouten) en apart wat op jou wacht
  await expect(page.getByTestId("status-hero")).toContainText(/wacht(en)? op jou/);
  await expect(page.getByTestId("status-hero")).toContainText("Beperkt");
  await expect(page.getByTestId("todo-card")).toContainText("8 AV1-bestanden niet geplaatst");
  // statusregel en 'Wacht op jou' tellen dezelfde lijst
  // alle bronnen laden los; zodra het stabiel is tonen statusregel, 'Wacht op jou' en kopbalk hetzelfde getal
  const numOf = async (id: string) => /(\d+)/.exec((await page.getByTestId(id).innerText()) ?? "")?.[1] ?? "";
  await expect.poll(async () => {
    const chip = (await page.getByTestId("todo-card").locator(".card-title .chip").textContent())?.trim() ?? "";
    return [chip, await numOf("status-hero"), await numOf("status-pill")].every((v) => v === chip) ? chip : `${chip}≠`;
  }, { timeout: 10_000 }).toMatch(/^\d+$/);
  await page.goto("/activiteit?tab=problemen");
  // 'Fouten' is herleidbaar: per onderdeel de items zelf (rapport 2, N13)
  await expect(page.getByTestId("problems-errors")).toContainText("AV1-bestanden niet geplaatst");
  await page.getByTestId("problems-errors").getByText("AV1-bestanden niet geplaatst").click();
  await expect(page.getByTestId("problems-errors")).toContainText("Film A");
});

test("back-up: downloadlink met echte bestandsnaam (H10) en Systeem & prestaties onder Systeem (M15)", async ({ page }) => {
  await page.route("**/api/v1/backups", (route) => route.request().method() === "GET"
    ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ file: "mediaguard-20261104-120000.db", kind: "auto", created_at: "2026-11-04T12:00:00Z" }]) })
    : route.continue());
  await login(page);
  await page.goto("/systeem");
  await expect(page.getByTestId("backup-download").first()).toHaveAttribute("href", /\/backups\/mediaguard-20261104-120000\.db\/download$/);
  await expect(page.getByTestId("section-prestaties")).toBeVisible();
  await page.goto("/instellingen?open=prestaties#prestaties");
  await expect(page).toHaveURL(/\/systeem\?open=prestaties/);
});

test.describe("telefoon", () => {
  test.use({ viewport: { width: 412, height: 900 }, hasTouch: true });
  test("zoekknop opent volledig scherm; knoppen minstens 44 px hoog (L1/L8)", async ({ page }) => {
    await login(page);
    await page.getByTestId("mobile-search").click();
    await expect(page.locator(".search-sheet [data-global-search]")).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.locator(".search-sheet")).toHaveCount(0);
    const small = await page.evaluate(() => [...document.querySelectorAll("main .btn")].filter((b) => {
      const r = b.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && r.height < 43.5;
    }).length);
    expect(small).toBe(0);
  });
});
