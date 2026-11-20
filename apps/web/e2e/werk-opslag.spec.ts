import { expect, test } from "@playwright/test";

/** Activiteit → Opslag & downloads: wat kwam erbij en waar ging de ruimte heen. */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };
const GB = 1024 ** 3;

const LEDGER = {
  period: "24h", period_nl: "Laatste 24 uur",
  summary_nl: "Laatste 24 uur: +412 GB — 37 afleveringen, 4 films; AV1 bespaarde 96 GB; netto +316 GB",
  totals: { added: 380 * GB, upgrades_net: 32 * GB, av1_saved: 96 * GB, library_net: 316 * GB },
  buckets: [
    { key: "added", label_nl: "Erbij", bytes: 380 * GB, bytes_nl: "+380 GB", hint_nl: "41 nieuwe bestanden geïmporteerd" },
    { key: "upgrades", label_nl: "Upgrades", bytes: 32 * GB, bytes_nl: "+32,0 GB", hint_nl: "3 betere versies (alleen het verschil)" },
    { key: "av1", label_nl: "AV1-besparing", bytes: -96 * GB, bytes_nl: "−96,0 GB", hint_nl: "12 bestanden kleiner gemaakt" },
    { key: "quarantine", label_nl: "Quarantaine & vangnet", bytes: 20 * GB, bytes_nl: "+20,0 GB", hint_nl: "20,0 GB bewaard, 0,0 GB opgeruimd" },
    { key: "downloads", label_nl: "Downloadmappen", bytes: 4 * GB, bytes_nl: "+4,0 GB", hint_nl: "verschil in de downloadmappen" },
    { key: "unexplained", label_nl: "Onverklaard", bytes: 2 * GB, bytes_nl: "+2,0 GB", hint_nl: "niet aan MediaGuard toe te wijzen" },
  ],
  reconciliation: { used_delta: 342 * GB, explained: 340 * GB, unexplained: 2 * GB,
    text_nl: "Gebruikte ruimte +342 GB: verklaard +340 GB, onverklaard +2,0 GB." },
  by_category: [{ key: "anime", label_nl: "Anime", count: 37, bytes: 120 * GB, bytes_nl: "+120 GB" }],
  by_source: [{ key: "seerr", label_nl: "Seerr-aanvraag", count: 4, bytes: 200 * GB, bytes_nl: "+200 GB" }],
  items: [
    { at: new Date().toISOString(), instance_id: "sonarr-anime", instance_name: "Anime", category_nl: "Anime", media_kind: "episode",
      title: "Een hele lange animetitel die op een telefoon moet afbreken zonder zijwaarts scrollen", episode: "S01E21",
      kind: "new", kind_nl: "Nieuw", size: 2 * GB, net_bytes: 2 * GB, quality: "WEBDL-1080p", release_group: "GRPB", indexer: "NZBgeek",
      source: "arr", source_nl: "Sonarr/Radarr (RSS of handmatig)", href: "/bibliotheek/sonarr-anime/1" },
    { at: new Date(Date.now() - 3600_000).toISOString(), instance_id: "radarr-films", instance_name: "Films", media_kind: "movie",
      title: "Stormvogel", kind: "upgrade", kind_nl: "Upgrade", size: 60 * GB, old_size: 50 * GB, net_bytes: 10 * GB,
      source: "seerr", source_nl: "Seerr-aanvraag", href: null },
  ],
  dirs: [{ path: "/data/downloads/usenet/complete/tv-anime/een/heel/lang/pad/dat/moet/afbreken", group: "downloads",
    label_nl: "Downloads (klaar) · tv-anime", bytes: 12 * GB, own_bytes: 9 * GB }],
  dirs_measured_at: new Date().toISOString(),
};

test.beforeAll(async ({ request }) => {
  const status = await (await request.get("/api/v1/auth/setup-status")).json() as { needs_setup: boolean };
  if (status.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
});

async function login(page: import("@playwright/test").Page, width: number) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

for (const width of [1440, 390] as const) {
  test(`opslagoverzicht tegen de echte core (${width})`, async ({ page }) => {
    await login(page, width);
    await page.goto("/activiteit?tab=opslag");
    const view = page.getByTestId("storage-ledger");
    await expect(view).toBeVisible();
    await expect(page.getByTestId("ledger-summary")).toContainText("Laatste 24 uur");
    await page.getByRole("button", { name: "7 dagen" }).click();
    await expect(page.getByTestId("ledger-summary")).toContainText("Laatste 7 dagen");
    const api = await page.request.get("/api/v1/storage/ledger?period=30d");
    expect(api.ok()).toBeTruthy();
  });

  test(`opslagoverzicht met gegevens, sorteren en geen zijwaarts scrollen (${width})`, async ({ page }) => {
    await page.route("**/api/v1/storage/ledger**", (route) => route.fulfill({ json: LEDGER }));
    await login(page, width);
    await page.goto("/activiteit?tab=opslag&periode=24h");
    await expect(page.getByTestId("ledger-summary")).toContainText("+412 GB");
    await expect(page.getByTestId("ledger-buckets")).toContainText("AV1-besparing");
    const table = page.getByTestId("ledger-table");
    await expect(table).toContainText("Stormvogel");
    await expect(table).toContainText("S01E21");
    if (width === 1440) {
      await page.getByRole("button", { name: /^Ruimte/ }).click();
      await expect(table.locator("tbody tr").first()).toContainText("Stormvogel");
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
}

test("link vanuit de opslagtrend naar het overzicht", async ({ page }) => {
  await login(page, 1440);
  await page.goto("/systeem#opslag");
  await page.getByTestId("ledger-link").click();
  // opslag is een sectie op Ruimte geworden
  await expect(page).toHaveURL(/\/av1#opslag$/);
  await expect(page.getByTestId("storage-ledger")).toBeVisible();
});

test("768 px: opslagposten houden dezelfde Peil-kleur en de kerntekst volgt de taal", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 900 });
  await page.route("**/api/v1/storage/ledger**", (route) => route.fulfill({ json: LEDGER }));
  expect((await page.request.post("/api/v1/auth/login", { data: ADMIN })).ok()).toBeTruthy();
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: { ...me, locale: "en-US" } }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "System status", exact: true })).toBeVisible();
  await expect(page.getByTestId("storage-ledger-line")).toContainText("Last 24 hours: +412 GB");
  await page.getByRole("link", { name: "View storage", exact: true }).click();
  const ledger = page.getByTestId("storage-ledger");
  await expect(ledger.getByRole("heading", { name: "What was added and where the space went" })).toBeVisible();
  await expect(page.getByTestId("ledger-summary")).toContainText("AV1 saved 96 GB");
  for (const [key, color] of [["added", "chart-1"], ["upgrades", "chart-2"], ["av1", "chart-3"],
    ["quarantine", "chart-4"], ["downloads", "chart-5"], ["unexplained", "chart-other"]]) {
    const row = page.getByTestId("ledger-buckets").locator(`[data-bucket="${key}"]`);
    const detail = row.locator("div[aria-hidden] > div");
    const swatch = row.locator("span[aria-hidden]");
    expect(await detail.evaluate((el) => (el as HTMLElement).style.backgroundColor)).toBe(`var(--${color})`);
    await expect(detail).toHaveCSS("background-color", await swatch.evaluate((el) => getComputedStyle(el).backgroundColor));
    if (key !== "av1") {
      const segment = ledger.locator(`.ledger-stack [data-bucket="${key}"]`);
      await expect(segment).toHaveCSS("background-color", await detail.evaluate((el) => getComputedStyle(el).backgroundColor));
    } else {
      await expect(row).toContainText("−96 GB");
    }
  }
  const overloop = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  expect(overloop).toBeLessThanOrEqual(1);
  if (process.env.MG_PEIL_680_SHOTS) {
    await ledger.getByRole("region", { name: "What was added and where the space went" })
      .screenshot({ path: `${process.env.MG_PEIL_680_SHOTS}/opslag-768-en.png`, animations: "disabled" });
  }
});
