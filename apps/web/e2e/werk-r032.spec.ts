import { expect, test, type Page } from "@playwright/test";

/** Testrapport 2: langlopende verbindingen, tellers, Release Lab-kolommen, meervouden. */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

// telt open EventSource-/WebSocket-verbindingen per tabblad
const COUNTER = () => {
  const w = window as unknown as { __mgOpen: { es: number; ws: number } };
  w.__mgOpen = { es: 0, ws: 0 };
  const ES = window.EventSource;
  if (ES) {
    window.EventSource = class extends ES {
      constructor(url: string | URL, init?: EventSourceInit) {
        super(url, init);
        w.__mgOpen.es += 1;
        const close = this.close.bind(this);
        let open = true;
        this.close = () => { if (open) { open = false; w.__mgOpen.es -= 1; } close(); };
      }
    } as typeof EventSource;
  }
  const WS = window.WebSocket;
  window.WebSocket = class extends WS {
    constructor(url: string | URL, protocols?: string | string[]) {
      super(url, protocols);
      w.__mgOpen.ws += 1;
      this.addEventListener("close", () => { w.__mgOpen.ws -= 1; });
    }
  } as typeof WebSocket;
};
const openOf = (p: Page) => p.evaluate(() => (window as unknown as { __mgOpen?: { es: number; ws: number } }).__mgOpen ?? { es: 0, ws: 0 });

test("hooguit één langlopende verbinding van elke soort over twee tabbladen (H5)", async ({ page, context }) => {
  await context.addInitScript(COUNTER);
  await login(page);
  await page.goto("/av1");
  const second = await context.newPage();
  await second.goto("/av1");
  await expect(second.locator("h1").first()).toBeVisible();
  await page.waitForTimeout(2500);
  const [a, b] = [await openOf(page), await openOf(second)];
  expect(a.es + b.es, `EventSource: ${JSON.stringify([a, b])}`).toBeLessThanOrEqual(1);
  expect(a.ws + b.ws, `WebSocket: ${JSON.stringify([a, b])}`).toBeLessThanOrEqual(1);
  await second.close();
});

test("kopbalk, zijbalk en menubadge tonen hetzelfde aantal als 'Wacht op jou' (N13)", async ({ page }) => {
  // Altijd minstens één punt dat aandacht vraagt (ADR-0020 punt 4): de test sloeg zichzelf eerder stil over als de
  // testomgeving toevallig niets open had. Eén nagebootst 'Wacht op jou'-punt van core; de rest blijft echt.
  await page.route(/\/api\/v1\/attention(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    items: [{ key: "e2e.n13", severity: "warning", title_nl: "Testpunt N13", detail_nl: "Nagebootst aandachtspunt voor de tellers",
      href: "/activiteit?tab=problemen", count: 1, actions: [], rows: [] }], count: 1, errors: 0, dismissed: 0,
  }) }));
  await login(page);
  const hero = page.getByTestId("status-hero");
  await expect(hero).toBeVisible();
  await expect(hero).toContainText(/\d+ ding/);
  const heroText = (await hero.innerText()).trim();
  const numOf = async (id: string) => /(\d+)/.exec(await page.getByTestId(id).innerText())?.[1] ?? "";
  await expect.poll(async () => {
    const [a, b, c] = [await numOf("status-hero"), await numOf("status-pill"), await numOf("sidebar-health")];
    return a === b && b === c ? a : `${a}/${b}/${c}`;
  }, { timeout: 10_000 }).toMatch(/^\d+$/);
  // geen haakjes-meervoud meer
  expect(heroText).not.toMatch(/\(en\)|\(s\)/);
});

test("Release Lab vult kwaliteit, taal en score uit de samenvatting van core en groepeert afwijzingen (N20)", async ({ page }) => {
  await page.route("**/api/v1/instances", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: "sonarr-series", name: "Sonarr Series", app: "sonarr", media_type: "tv" }]) }));
  await page.route("**/api/v1/instances/sonarr-series/releases**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([
    { guid: "a", title: "Lichtval.S01E03.2160p.Remux", size: 4e10, quality: "Bluray-2160p Remux", languages: ["English"], custom_format_score: 150, rejected: true,
      rejections: [{ text: "Bluray-2160p Remux is not wanted in profile", code: "quality" }], raw: { quality: { quality: { name: "Bluray-2160p Remux", resolution: 2160 } } } },
    { guid: "b", title: "Lichtval.S01E03.2160p.WEB-DL", size: 6e9, quality: "WEBDL-2160p", languages: ["English", "Dutch"], custom_format_score: 500, rejected: false, rejections: [], raw: {} },
  ]) }));
  await login(page);
  await page.goto("/release-lab?instance=sonarr-series&episode=452&title=Lichtval%20S01E03");
  await page.getByRole("button", { name: /Releases zoeken|Zoek releases|zoeken/i }).first().click();
  await expect(page.getByText("WEBDL-2160p").first()).toBeVisible();
  await expect(page.getByText("Engels, Nederlands")).toBeVisible();
  await expect(page.getByText("staat niet in het kwaliteitsprofiel").first()).toBeVisible();
  await expect(page.getByText(/Toegestaan \(1\)/)).toBeVisible();
  // vanuit een aflevering: menu markeert Bibliotheek
  await expect(page.locator('nav a[aria-current="page"]').first()).toContainText(/Bibliotheek/);
});
