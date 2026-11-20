import { expect, test, type Page } from "@playwright/test";
import { ADMIN } from "./fixtures/admin";

const oudeTijd = "2025-01-01T08:00:00Z";
const taak = { id: 680, source_path: "Test Show.mkv", status: "running", encoder_status: "running", progress: 0.5, updated_at: oudeTijd };

test.beforeEach(async ({ page, baseURL }) => {
  expect(new URL(baseURL!).hostname).toBe("localhost");
  const data = ADMIN;
  if ((await (await page.request.get("/api/v1/auth/setup-status")).json()).needs_setup) {
    expect((await page.request.post("/api/v1/auth/setup", { data })).ok()).toBeTruthy();
  }
  expect((await page.request.post("/api/v1/auth/login", { data })).ok()).toBeTruthy();
  await page.setViewportSize({ width: 768, height: 900 });
  await page.clock.install();
  const status = await page.request.get("/api/v1/status");
  expect(status.ok()).toBeTruthy();
  const kern = await status.json();
  // De kern uit deze proef blijft dezelfde. De meettijd volgt de klok van het afzonderlijke leesverzoek.
  await page.route("**/api/v1/status", async route => {
    const time = await page.evaluate(() => new Date().toISOString());
    return route.fulfill({ json: { ...kern, time } });
  });
});

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

async function jsonBron(page: Page, route: string, waarde: unknown) {
  await page.route("**/api/v1/" + route, (verzoek) => verzoek.fulfill({ json: waarde }));
}

async function legeActielijst(page: Page) {
  await jsonBron(page, "attention", { items: [], errors: 0, count: 0 });
  await jsonBron(page, "provisioning/status", { needs: [] });
  await jsonBron(page, "migration/runs", []);
  await jsonBron(page, "seerr/settings", { api_key_set: true });
  await jsonBron(page, "wanted/kpis", { missing: 0, missing_aired_7d: 0, resolved_7d: 0, needs_user: { reasons: [] } });
  await jsonBron(page, "jobs?**", []);
}

function samenvatting(tijd: string, actief: number) {
  return { generated_at: tijd, complete: true, errors: { total: 0 },
    active: { total: actief, downloads: actief }, attention: { count: 0 } };
}

test("R-HARDE-EIS taakvoortgang en bevestiging door de encoder", async ({ page }) => {
  const kern = await (await page.request.get("/api/v1/status")).json();
  let stop = false;
  let herstel = false;
  let hersteltijd = "";
  await page.route("**/api/v1/status", async (r) => r.fulfill({ json: { ...kern, time: await page.evaluate(() => new Date().toISOString()), emergency_stop: { active: stop } } }));
  await page.route("**/api/v1/encoder/health", (r) => herstel ? r.fulfill({ json: { status: "ok" } })
    : r.fulfill({ status: 503, json: { detail: "Testencoder onbereikbaar" } }));
  await page.route("**/api/v1/encoding/jobs", (r) => r.fulfill({ json: [herstel
    ? { ...taak, updated_at: hersteltijd, progress: 0.75 } : taak] }));
  await page.goto("/");
  const kaart = page.getByRole("region", { name: "Nu bezig" });
  const balk = kaart.getByRole("progressbar");
  const rij = kaart.locator(".list-row").filter({ hasText: "Show" }).first();
  await expect(rij).toBeVisible();
  await expect(page.getByTestId("status-hero").getByRole("link", { name: /AV1-engine/ })).toContainText("Onbekend");
  const oudeWaarde = await balk.count() ? await balk.getAttribute("aria-valuenow") : null;
  const gemarkeerd = /laatst bekend|verouderd|onbekend/i.test(await rij.innerText());
  stop = true;
  await page.clock.fastForward(30_001);
  await expect(page.getByTestId("hero-sub")).toContainText("Noodstop staat aan");
  const taakGepauzeerd = (await rij.innerText()).includes("Gepauzeerd (noodstop)");
  stop = false;
  hersteltijd = await page.evaluate(() => new Date(Date.now() + 30_001).toISOString());
  herstel = true;
  await page.clock.fastForward(30_001);
  await expect(balk).toHaveAttribute("aria-valuenow", "75");
  expect.soft(oudeWaarde === null || gemarkeerd, "Een oude bronmeting krijgt geen actuele voortgangsbalk").toBe(true);
  expect(taakGepauzeerd, "Alleen een bevestigde taakpauze heet Gepauzeerd").toBe(false);
});

test("R-HARDE-EIS veiligheidsmelding en knop na verlies van de eigen bron", async ({ page }) => {
  const kern = await (await page.request.get("/api/v1/status")).json();
  let staat: "aan" | "onbereikbaar" | "uit" = "aan";
  let mislukteSlotlezingen = 0;
  await page.route("**/api/v1/status", async (r) => staat === "onbereikbaar" ? r.abort("failed") : r.fulfill({
    json: { ...kern, time: await page.evaluate(() => new Date().toISOString()), emergency_stop: { active: staat === "aan", by: "test", since: new Date().toISOString() } } }));
  await page.route("**/api/v1/recovery/readiness", (r) => {
    if (staat === "onbereikbaar") { mislukteSlotlezingen++; return r.abort("failed"); }
    return r.fulfill({ json: { library_locked: staat === "aan" } });
  });
  await page.goto("/");
  const banner = page.getByTestId("estop-banner");
  const slot = page.getByTestId("library-lock-banner");
  await expect(banner).toContainText("Noodstop actief");
  await expect(slot).toContainText("Bibliotheek nog vergrendeld");
  staat = "onbereikbaar";
  await page.clock.fastForward(65_000);
  await expect(page.getByTestId("hero-sub")).toContainText("Laatst bekende stand");
  await expect.poll(() => mislukteSlotlezingen).toBeGreaterThan(0);
  await page.getByTestId("profile-button").click();
  const knop = page.getByTestId("estop-button");
  const pressed = await knop.getAttribute("aria-pressed");
  const bannerActief = (await banner.count()) > 0 && /Noodstop actief/.test(await banner.innerText()) && !/laatst bekend|verouderd|onbekend/i.test(await banner.innerText());
  const vrijgave = banner.getByRole("button", { name: "Noodstop opheffen" });
  const opheffen = (await vrijgave.count()) > 0 && await vrijgave.isEnabled();
  const belooftSlot = (await slot.count()) > 0 && (await slot.innerText()).includes("vervangt nog geen bestanden") && !/laatst bekend|verouderd|onbekend/i.test(await slot.innerText());
  await page.keyboard.press("Escape");
  staat = "uit";
  await page.clock.fastForward(65_000);
  await expect(banner).toHaveCount(0);
  await expect(slot).toHaveCount(0);
  expect(pressed).toBe("false");
  expect(opheffen).toBe(false);
  expect.soft(bannerActief).toBe(false);
  expect(belooftSlot).toBe(false);
});

test("R-HARDE-EIS kopbalk en zijbalk gebruiken de meettijd van hun eigen bron", async ({ page }) => {
  await legeActielijst(page);
  let herstel = false;
  const vorigeMeting = "2025-01-01T08:00:00.000Z";
  await page.route("**/api/v1/library/kpis", (r) => r.fulfill({ json: {
    generated_at: herstel ? new Date().toISOString() : vorigeMeting,
    totals: { available: herstel ? 8 : 7, total: 10, unmonitored: 0, upcoming: 0 }, instances: [],
  } }));
  await page.route("**/api/v1/health/summary", (r) => r.fulfill({ json: herstel
    ? samenvatting(new Date().toISOString(), 2) : samenvatting("2025-01-01T08:00:00Z", 4) }));
  await page.goto("/");
  const fouten = page.locator(".kpi").filter({ hasText: "Fouten bekijken" });
  await expect(fouten.locator('[data-status="stale"]')).toBeVisible();
  const kop = page.getByTestId("status-pill");
  await expect(kop).toBeVisible();
  const koptekst = (await kop.innerText()).trim();
  const bibliotheek = page.locator(".kpi").filter({ hasText: "Beschikbaar" });
  await expect(bibliotheek.locator(".kpi-value")).toHaveText("7");
  const bibliotheekOud = await bibliotheek.locator('[data-status="stale"]').count() > 0;
  const juisteBibliotheektijd = await bibliotheek.locator("time").getAttribute("datetime") === vorigeMeting;
  await page.setViewportSize({ width: 1440, height: 900 });
  const zij = page.getByTestId("sidebar-health");
  await expect(zij).toBeVisible();
  const zijtekst = (await zij.innerText()).trim();
  herstel = true;
  await page.getByTestId("status-hero").getByRole("button").first().click();
  await expect(kop).toContainText("2 bezig");
  await page.clock.fastForward(30_001);
  await expect(bibliotheek.locator(".kpi-value")).toHaveText("8");
  expect.soft(koptekst).toMatch(/verouderd|laatst bekend|onbekend/i);
  expect.soft(zijtekst).toMatch(/verouderd|laatst bekend|onbekend/i);
  expect.soft(bibliotheekOud).toBe(true);
  expect(juisteBibliotheektijd).toBe(true);
});

test("R-HARDE-EIS de actieteller herkent uitval na een lege deellijst", async ({ page }) => {
  await legeActielijst(page);
  await jsonBron(page, "seerr/settings", { api_key_set: false });
  await page.route("**/api/v1/health/summary", async route =>
    route.fulfill({ json: samenvatting(await page.evaluate(() => new Date().toISOString()), 0) }));
  let mode: "leeg" | "offline" | "hersteld" = "leeg";
  let geweigerd = 0;
  await page.route("**/api/v1/migration/runs", (r) => {
    if (mode === "offline") { geweigerd++; return r.abort("failed"); }
    return r.fulfill({ json: mode === "leeg" ? [] : [{ id: "test", name: "Testmigratie", status: "active" }] });
  });
  await page.goto("/");
  const kop = page.getByTestId("status-pill");
  const kaart = page.getByTestId("todo-card");
  await expect(kop).toContainText("Aandacht 1");
  mode = "offline";
  await page.clock.fastForward(30_001);
  await expect.poll(() => geweigerd).toBeGreaterThan(0);
  const koptekst = (await kop.innerText()).trim();
  const kaarttekst = (await kaart.innerText()).replace(/\s+/g, " ").trim();
  const paneeltekst = await page.getByTestId("hero-todo").innerText();
  mode = "hersteld";
  await page.clock.fastForward(30_001);
  await expect(kop).toContainText("Aandacht 2");
  for (const tekst of [koptekst, kaarttekst, paneeltekst]) {
    expect(tekst).toMatch(/Minstens 1 bekend/);
    expect(tekst).toMatch(/Migratie/);
  }
});

test("R-HARDE-EIS een gemelde downloadclientfout blijft zichtbaar in de totaalstatus", async ({ page }) => {
  await legeActielijst(page);
  let storing = true;
  await jsonBron(page, "encoder/health", { status: "ok" });
  const tijd = new Date().toISOString();
  const gezond = samenvatting(tijd, 0);
  const metingen = { gezond, storing: { ...gezond, errors: { total: 1, instances_unreachable: 1 },
    instances: { unreachable: [{ instance_id: null, name: "Testdownloadclient" }] } } };
  await jsonBron(page, "instances", [{ id: "testfilms", name: "Testfilms", app: "radarr", status: "healthy", last_check_at: tijd }]);
  await page.route("**/api/v1/health/summary", (r) => r.fulfill({ json: {
    ...(storing ? metingen.storing : metingen.gezond), generated_at: tijd,
  } }));
  await page.goto("/");
  const paneel = page.getByTestId("status-hero");
  const fouten = page.locator(".kpi").filter({ hasText: "Fouten bekijken" }).locator(".kpi-value");
  await expect(fouten).toHaveText("1");
  await expect(paneel.getByRole("link", { name: /Testfilms/ })).toContainText("Gezond");
  await expect(paneel.getByRole("link", { name: /AV1-engine/ })).toContainText("Gezond");
  const totaal = await paneel.getAttribute("data-status");
  storing = false;
  await paneel.getByRole("button").first().click();
  await expect(fouten).toHaveText("0");
  await expect(paneel).toHaveAttribute("data-status", "healthy");
  expect(totaal).not.toBe("healthy");
});

test("R-HARDE-EIS verwijdertekst bij de laatste gevolgde mediaserver", async ({ page }) => {
  const status = async () => (await (await page.request.get("/api/v1/mediaserver/status")).json()).servers as
    Array<{ server_id: number, label: string, followed: boolean, legacy: boolean }>;
  const { csrf_token } = await (await page.request.get("/api/v1/auth/me")).json();
  const headers = { "X-CSRF-Token": csrf_token };
  const voor = await status();
  expect(voor.filter(s => s.followed)).toHaveLength(0);
  expect(voor.every(s => s.legacy)).toBe(true);
  const antwoord = await page.request.post("/api/v1/mediaserver/config", { headers, data: {
    add: true, kind: "jellyfin", label: "Testslot", url: "http://localhost:1", api_key: crypto.randomUUID(),
  } });
  expect(antwoord.ok()).toBeTruthy();
  const gekoppeld = (await status()).filter(s => s.followed);
  expect(gekoppeld.map(s => s.label)).toEqual(["Testslot"]);
  await page.goto("/inrichten?tab=mediaserver");
  await page.getByRole("button", { name: "Verwijderen…: Testslot" }).click();
  const dialoog = page.getByRole("alertdialog");
  const claim = (await dialoog.innerText()).includes("Andere mediaservers blijven gekoppeld");
  await dialoog.getByRole("button", { name: "Server verwijderen" }).click();
  await expect.poll(async () => (await status()).filter(s => s.followed).length).toBe(0);
  expect(claim).toBe(false);
});

test("R-HARDE-EIS melding en herstelactie blijven bereikbaar op iedere Peil-breedte", async ({ page }) => {
  let mode: "bezig" | "fout" | "offline" | "hersteld" = "bezig";
  const lezingen = { fout: 0, offline: 0 };
  let antwoord!: () => void;
  const vrij = new Promise<void>((resolve) => { antwoord = resolve; });
  await page.route("**/api/v1/encoder/health", async (route) => {
    if (mode === "bezig") await vrij;
    if (mode === "offline") { lezingen.offline++; return route.abort("failed"); }
    if (mode === "fout") { lezingen.fout++; return route.fulfill({ status: 503, json: { detail: "Ongeldige testmeting" } }); }
    return route.fulfill({ json: { status: "ok" } });
  });
  await jsonBron(page, "instances", []);
  const paneel = page.getByTestId("status-hero");
  const rij = paneel.getByRole("link", { name: /AV1-engine/ });
  try {
    await page.goto("/");
    await expect(rij.locator('[data-status="unknown"]')).toBeVisible();
    await expect(paneel.getByRole("link", { name: /Niet gekoppeld/i })).toBeVisible();
  } finally {
    mode = "fout";
    antwoord();
  }
  const opnieuw = paneel.getByRole("button", { name: "Opnieuw proberen" });
  await expect(opnieuw).toBeVisible();
  for (const toestand of ["fout", "offline"] as const) {
    mode = toestand;
    const voor = lezingen[toestand];
    await opnieuw.click();
    await expect.poll(() => lezingen[toestand]).toBeGreaterThan(voor);
    for (const thema of ["light", "dark"] as const) {
      await page.getByTestId("profile-button").click();
      await page.getByRole("button", { name: thema === "light" ? "Licht" : "Donker", exact: true }).click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", thema);
      await page.keyboard.press("Escape");
      for (const breedte of [320, 360, 390, 768, 1024, 1440]) {
        await page.setViewportSize({ width: breedte, height: 900 });
        await expect(rij).toContainText("Ophalen van de meting is mislukt");
        await expect(rij.locator('[data-status="healthy"]')).toHaveCount(0);
        await expect(opnieuw).toBeVisible();
        const naam = [toestand, thema, breedte].join(" ");
        expect(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) - innerWidth), naam).toBeLessThanOrEqual(1);
        const eerder = lezingen[toestand];
        await opnieuw.focus();
        await page.keyboard.press("Enter");
        await expect.poll(() => lezingen[toestand]).toBeGreaterThan(eerder);
      }
    }
  }
  mode = "hersteld";
  await opnieuw.focus();
  await page.keyboard.press("Enter");
  await expect(rij.locator('[data-status="healthy"]')).toBeVisible();
  await page.getByTestId("profile-button").click();
  await page.getByTestId("theme-choice").getByRole("button", { name: "Systeem", exact: true }).click();
});
// Elke regel koppelt één zichtbare stand aan haar eigen leesverzoek. De browser
// verifieert dezelfde regel bij HTTP 200 (vers en oud), uitval, 403 en herstel.
type Mode = "vers" | "oud" | "uitval" | "weigering";
type Text = string | RegExp;
interface Element {
  selector: string;
  fresh: Text;
  // Bij weigering mag een rij met afgeschermde gegevens ook helemaal verdwijnen.
  denied?: "weg" | Text;
  stale?: Text | "weg";
  attribute?: [string, string, string];
}

const oud = /laatst bekend|verouderd/i;
const onbekend = /onbekend|geen toegang|geen rechten|niet toegestaan/i;
const testId = (id: string) => `[data-testid="${id}"]`;
const hero = testId("status-hero");
const busy = 'section:has(h2:text("Nu bezig"))';
const kpi = (label: string) => `.kpi:has(.kpi-label:text-is("${label}"))`;
const telling: Element[] = [
  { selector: testId("status-pill"), fresh: "Aandacht 1", denied: /Minstens 1 bekend/ },
  { selector: testId("hero-todo"), fresh: /1 ding/, denied: /Minstens 1 bekend/ },
  { selector: testId("todo-card") + " .card-title", fresh: /1/, denied: /Minstens 1 bekend/ },
  { selector: testId("sidebar-health") + " a", fresh: /1 ding/, denied: /Minstens 1 bekend/ },
  { selector: 'aside a[href="/activiteit"]', fresh: /1/, denied: /Minstens 1 bekend/ },
];

interface SourceCase {
  path: string;
  view?: "profile" | "bell" | "search";
  // Bron van de meettijd. Bij een live DB-lezing draagt HTTP Date de meettijd.
  clock: string;
  // Bij een opgeslagen meettijd blijft de HTTP-datering vers, ook bij een oude payload.
  payloadTime?: boolean;
  variant?: (path: string, at: string) => unknown;
  elements: Element[];
}
const inventory: Record<string, SourceCase> = {
  kern: { path: "status", clock: "time", payloadTime: true, view: "profile", elements: [
    { selector: ".page-header", fresh: "proefversie" },
    { selector: ".version-line", fresh: "proefversie" },
    { selector: hero + ' a[href="/systeem"]', fresh: "Gezond" },
    { selector: testId("hero-sub"), fresh: "Noodstop staat aan" },
    { selector: testId("estop-banner"), fresh: "Noodstop actief" },
    { selector: testId("estop-button"), fresh: /Noodstop/, attribute: ["aria-pressed", "true", "false"] },
  ] },
  slot: { path: "recovery/readiness", clock: "HTTP Date", elements: [
    { selector: testId("library-lock-banner"), fresh: "Bibliotheek nog vergrendeld" },
  ] },
  gezondheid: { path: "health/summary", clock: "generated_at", payloadTime: true, elements: [
    { selector: hero + ' a[href="/activiteit?tab=problemen"]', fresh: "Gezond" },
    { selector: testId("sidebar-health"), fresh: "Geen fouten gemeld" },
    { selector: testId("status-pill"), fresh: "4 bezig" },
    { selector: kpi("Fouten"), fresh: /0/ },
    { selector: busy, fresh: /4 download/, denied: /Actuele activiteit onbekend/ },
  ] },
  afzonderlijkeFout: { path: "status", clock: "Actuele fout bij wisselende kernmeting", payloadTime: true,
    variant: (path, at) => path === "health/summary" ? { ...samenvatting(at, 0), errors: { total: 1, instances_unreachable: 1 },
      instances: { unreachable: [{ instance_id: null, name: "Testdownloadclient" }] } } : undefined, elements: [
      { selector: hero + ' h2 + div [role="status"]', fresh: "Beperkt", stale: "Beperkt", denied: "Beperkt" },
      { selector: testId("sidebar-health"), fresh: "Beperkt", stale: "Beperkt", denied: "Beperkt" },
      { selector: testId("status-pill"), fresh: "Beperkt", stale: "Beperkt", denied: "Beperkt" },
    ] },
  bibliotheek: { path: "library/kpis", clock: "generated_at", payloadTime: true, elements: [
    { selector: kpi("Beschikbaar"), fresh: /7/ },
  ] },
  ontbrekend: { path: "wanted/kpis", clock: "HTTP Date of generated_at", elements: [
    { selector: kpi("Ontbrekend"), fresh: /6/ },
    { selector: testId("overview-wanted-line"), fresh: /6/ },
  ] },
  statistieken: { path: "encoder/statistieken", clock: "X-MediaGuard-Stale of HTTP Date", elements: [
    { selector: kpi("AV1-besparing"), fresh: /0/ },
  ] },
  besparingsgrafiek: { path: "encoder/statistieken", clock: "Dezelfde meting als de weekbesparing", variant: (path, at) =>
    path === "encoder/statistieken" ? { totaal: { bespaard_bytes: 2048, gemiddeld: 25, jobs: 3 },
      per_dag: [{ dag: at, bespaard_bytes: 900 }, { dag: at, bespaard_bytes: 1148 }] } : undefined, elements: [
      { selector: kpi("AV1-besparing"), fresh: /25%/ },
    ] },
  encoder: { path: "encoder/health", clock: "X-MediaGuard-Stale of HTTP Date", elements: [
    { selector: hero + ' a[href="/av1"]', fresh: "Gezond" },
  ] },
  instanties: { path: "instances", clock: "last_check_at", payloadTime: true, elements: [
    { selector: hero + ' a[href*="/instellingen/instanties"]', fresh: "Gezond" },
  ] },
  encodes: { path: "encoding/jobs", clock: "updated_at per taak", payloadTime: true, elements: [
    { selector: busy + ' .list-row:has(.list-title:text-is("Proeffilm 1"))', fresh: "50%", denied: "weg" },
    { selector: testId("queued-encodes"), fresh: /1 encode/, denied: "weg" },
    { selector: busy + ' a:has-text("en nog")', fresh: /1 encode/, denied: "weg" },
  ] },
  legeInstanties: { path: "instances", clock: "HTTP Date van de lege lijst", variant: path => path === "instances" ? [] : undefined, elements: [
    { selector: hero + ' a[href="/instellingen/instanties"]', fresh: "Niet gekoppeld" },
  ] },
  gepauzeerdeInstantie: { path: "instances", clock: "HTTP Date van de pauzestand", variant: path =>
    path === "instances" ? [{ id: "testfilms", name: "Testfilms", suspended: true, status: "healthy", last_check_at: oudeTijd }] : undefined, elements: [
    { selector: hero + ' a[href*="/instellingen/instanties"]', fresh: "Gepauzeerd" },
  ] },
  oorzaken: { path: "wanted/reasons", clock: "HTTP Date van de oudere oorzakencatalogus", variant: path =>
    path === "wanted/kpis" ? { missing: 6, reason_distribution: { test: 1 } } : undefined, elements: [
    { selector: testId("overview-wanted-line"), fresh: /jij bent nodig bij 1/ },
    { selector: testId("todo-card") + ' .list-row:has-text("Proefoorzaak")', fresh: "Proefoorzaak", denied: "weg" },
    { selector: testId("hero-todo"), fresh: /2 ding/, denied: /Minstens 1 bekend/ },
  ] },
  actieveMigratie: { path: "migration/runs", clock: "HTTP Date van de geopende migratie",
    variant: path => path === "migration/runs" ? [{ id: "proef", name: "Proefmigratie", status: "active" }] : undefined, elements: [
      { selector: 'aside a[href="/migratie"]', fresh: "Migratie", stale: "weg", denied: "weg" },
      { selector: testId("todo-card") + ' .list-row:has-text("Proefmigratie")', fresh: "Migratie afronden", denied: "weg" },
    ] },
  legeKalender: { path: "calendar", clock: "HTTP Date van de lege week", variant: path => path === "calendar" ? { items: [] } : undefined,
    elements: [{ selector: 'section:has(h2:text("Nieuwe afleveringen"))', fresh: "Geen nieuwe afleveringen deze week" }] },
  ...Object.fromEntries(["encoding/jobs", "jobs:running", "health/summary"].map(path => [`geenActiviteit-${path}`, {
    path, clock: "Alle drie bronnen van de leegmelding", payloadTime: path === "health/summary",
    variant: (pad: string, at: string) => pad === "encoding/jobs" || pad === "jobs:running" ? [] : pad === "health/summary" ? samenvatting(at, 0) : undefined,
    elements: [{ selector: busy, fresh: "Geen actieve encodes of downloads" }],
  }])),
  kerntaken: { path: "jobs:running", clock: "HTTP Date van de lijst", elements: [
    { selector: busy + ' .list-row:has(.list-main .list-sub)', fresh: "Bezig", denied: "weg" },
  ] },
  kalender: { path: "calendar", clock: "HTTP Date van de selectie", elements: [
    { selector: 'section:has(h2:text("Nieuwe afleveringen")) .list-row', fresh: "Proefaflevering", denied: "weg" },
  ] },
  opslag: { path: "storage/ledger", clock: "end (einde meetperiode)", payloadTime: true, elements: [
    { selector: testId("storage-ledger-line"), fresh: "Proefopslag" },
  ] },
  migratie: { path: "migration/runs", clock: "HTTP Date van de lijst, ook leeg", elements: telling },
  inrichting: { path: "provisioning/status", clock: "HTTP Date", elements: telling },
  acties: { path: "attention", clock: "HTTP Date", elements: telling },
  mislukteTaken: { path: "jobs:failed", clock: "HTTP Date van de lijst", elements: telling },
  seerr: { path: "seerr/settings", clock: "HTTP Date", elements: telling.map(e => ({ ...e, denied: /Minstens 0 bekend/ })) },
  hub: { path: "settings/effective:system.hub_url", clock: "HTTP Date van de instelling", elements: [
    { selector: 'aside [data-testid="hub-link"]', fresh: "Terug naar de Hub", stale: "weg", denied: "weg" },
  ] },
  zoekMedia: { path: "library", clock: "HTTP Date van de zoekopdracht", view: "search", elements: [
    { selector: testId("search-results"), fresh: "Proefserie", denied: /Bibliotheek.*Onbekend/i },
  ] },
  zoekInstellingen: { path: "settings/search", clock: "HTTP Date van de zoekopdracht", view: "search", elements: [
    { selector: testId("search-results"), fresh: "Proefinstelling", denied: /Instellingen.*Onbekend/i },
  ] },
  meldingen: { path: "notifications/count", clock: "HTTP Date", view: "bell", elements: [
    { selector: ".notif-wrap", fresh: "3" },
  ] },
  meldingenWachtend: { path: "notifications/count", clock: "HTTP Date van de wachttelling", view: "bell",
    variant: path => path === "notifications/count" ? { unread: 0, attention: 2, badge: 2 } : undefined,
    elements: [{ selector: testId("bell-waiting"), fresh: /2/, stale: "weg", denied: "weg" }] },
  oudereMeldingenteller: { path: "status", clock: "Kernmeting wanneer de telroute ontbreekt", payloadTime: true, view: "bell",
    variant: (path, at) => path === "status" ? { version: "proefversie", time: at, jobs: {}, emergency_stop: { active: false }, unread_notifications: 2 } : undefined,
    elements: [{ selector: ".notif-wrap", fresh: "2" }] },
  meldingsdetail: { path: "notifications/1", clock: "HTTP Date van de uitgeklapte melding", view: "bell",
    variant: path => path === "notifications" ? [{ id: 1, title: "Proefmelding", severity: "info", body: "Proef…", summary: "Proef…" }] : undefined,
    elements: [{ selector: ".notif-list .list-row", fresh: "Volledige proeftekst" }] },
  meldinglijst: { path: "notifications", clock: "HTTP Date, ts is de gebeurtenistijd", view: "bell", elements: [
    { selector: ".notif-list", fresh: "Proefmelding" },
  ] },
};

function sample(path: string, at: string, target: string) {
  const hasTodo = [ "migration/runs", "provisioning/status", "attention", "jobs:failed", "seerr/settings", "wanted/reasons" ].includes(target);
  switch (path) {
    case "status": return { version: "proefversie", time: at, jobs: {}, emergency_stop: { active: target === "status", since: at, by: "proef" } };
    case "recovery/readiness": return { library_locked: true };
    case "health/summary": return samenvatting(at, 4);
    case "library/kpis": return { generated_at: at, totals: { available: 7, total: 10, unmonitored: 0, upcoming: 0 }, instances: [] };
    case "wanted/reasons": return { test: { needs_user: true, label_nl: "Proefoorzaak", fix_nl: "Proefactie", href: "/ontbrekend" } };
    case "wanted/kpis": return { missing: 6, missing_aired_7d: 1, resolved_7d: 2, needs_user: { reasons: [] } };
    case "encoder/statistieken": return { totaal: { bespaard_bytes: 0, gemiddeld: 0, jobs: 0 }, per_dag: [] };
    case "encoder/health": return { status: "ok" };
    case "instances": return [{ id: "testfilms", name: "Testfilms", status: "healthy", last_check_at: at }];
    case "encoding/jobs": return Array.from({ length: 10 }, (_, i) => ({ ...taak, id: i + 1, source_path: `Proeffilm ${i + 1}.mkv`, status: i === 9 ? "queued" : "running", updated_at: at }));
    case "jobs:running": return [{ id: 100, job_type: "library_scan", status: "running" }];
    case "calendar": return { items: [{ lib_item_id: 1, instance_id: "testfilms", title: "Proefaflevering", type: "episode", season_number: 1, episode_number: 3, date: at, state: "available" }] };
    case "storage/ledger": return { end: at, period: "24h", totals: {}, summary_nl: "Proefopslag" };
    case "migration/runs": case "jobs:failed": return [];
    case "provisioning/status": return { needs: [] };
    case "seerr/settings": return { api_key_set: !hasTodo };
    case "attention": return { items: [], count: 0, errors: 0 };
    case "settings/effective:system.hub_url": return [{ key: "system.hub_url", value: "https://hub.example.test" }];
    case "library": return { items: [{ id: 1, kind: "series", title: "Proefserie", year: 2025, instance_id: "testfilms" }] };
    case "settings/search": return [{ key: "ui.theme", label_nl: "Proefinstelling", label_en: "Test setting" }];
    case "notifications/count": return { unread: 3, attention: 0, badge: 3 };
    case "notifications/1": return { id: 1, body: "Volledige proeftekst" };
    case "notifications": return [{ id: 1, title: "Proefmelding", severity: "info", body: "Proef", ts: at }];
    default: throw new Error(`Geen testmeting voor ${path}`);
  }
}

for (const [naam, bron] of Object.entries(inventory)) {
  test(`Broninventaris · ${naam} · ${bron.clock}`, async ({ page }) => {
    let stand: Mode = "vers";
    let metingen = 0;
    const paths = new Set(Object.values(inventory).map(c => c.path));
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.route("**/api/v1/**", async route => {
      const url = new URL(route.request().url());
      let path = url.pathname.slice("/api/v1/".length);
      if (path === "settings/effective") path += ":" + url.searchParams.get("keys");
      if (path === "jobs") path += ":" + url.searchParams.get("status");
      if (!paths.has(path)) return route.fallback();
      if (naam === "oudereMeldingenteller" && path === "notifications/count") return route.fulfill({ status: 404, json: { detail: "Not Found" } });
      const mode = path === bron.path ? stand : "vers";
      if (path === bron.path) metingen++;
      if (mode === "uitval") return route.abort("failed");
      if (mode === "weigering") return route.fulfill({ status: 403, json: { detail: "Geen leesrecht" } });
      const browserTime = await page.evaluate(() => Date.now());
      const at = new Date(mode === "oud" ? oudeTijd : browserTime);
      const transport = bron.payloadTime ? new Date(browserTime) : at;
      return route.fulfill({ headers: { date: transport.toUTCString() }, json: bron.variant?.(path, at.toISOString()) ?? sample(path, at.toISOString(), bron.path) });
    });
    await page.goto("/");
    if (bron.view === "search") {
      await page.getByRole("combobox").fill("Proef");
      await expect(page.getByTestId("search-results")).toContainText("Proefserie");
    } else if (bron.view) await page.getByTestId(bron.view === "profile" ? "profile-button" : "bell").click();
    if (naam === "meldingsdetail") await page.locator(".notif-list .list-row button").click();
    const profile = page.getByTestId("profile-menu");
    const tech = profile.getByRole("switch");
    const themeChoice = page.getByTestId("theme-choice").locator('button[aria-pressed="true"]');
    const localPreference = bron.view === "profile" ? { tech: await tech.getAttribute("aria-checked"), theme: await themeChoice.innerText() } : null;
    const modes = ["vers", "oud", "uitval", "weigering", "vers"] as const;
    for (const [index, mode] of modes.entries()) {
      await test.step(index === 4 ? "herstel" : mode, async () => {
        const voor = metingen;
        stand = mode;
        // Eerst de echte eerste lezing afwachten. Pas daarna de browserklok verzetten.
        if (index > 0) await page.clock.fastForward(301_000);
        await expect.poll(() => metingen).toBeGreaterThan(index === 0 ? 0 : voor);
        for (const el of bron.elements) {
          const node = page.locator(el.selector);
          const text = mode === "vers" ? el.fresh : mode === "weigering" ? el.denied ?? onbekend : el.stale ?? oud;
          if (text === "weg") { await expect(node).toHaveCount(0); continue; }
          await expect(node).toBeVisible();
          await expect(node).toContainText(text);
          if (el.attribute) await expect(node).toHaveAttribute(el.attribute[0], el.attribute[mode === "vers" ? 1 : 2]);
        }
        await expect(page.locator('aside a[href="/"][aria-current="page"]')).toHaveCount(1);
        if (localPreference) {
          await expect(page.getByTestId("profile-button")).toHaveAttribute("aria-expanded", "true");
          await expect(themeChoice).toHaveText(localPreference.theme);
          await expect(profile.getByRole("group", { name: "Taal" }).locator('button[aria-pressed="true"]')).toHaveText("Nederlands");
          await expect(tech).toHaveAttribute("aria-checked", localPreference.tech!);
        }
        if (naam === "meldingsdetail") {
          await expect(page.locator(".notif-list .list-row button")).toHaveAttribute("aria-expanded", "true");
          if (mode === "weigering") await expect(page.locator(".notif-list .list-row")).not.toContainText("Volledige proeftekst");
        }
        if (bron.view === "bell") await expect(page.getByTestId("bell")).toHaveAttribute("aria-expanded", "true");
        if (bron.view === "search") await expect(page.getByRole("combobox")).toHaveAttribute("aria-expanded", "true");
        if (["kern", "encoder", "gezondheid", "instanties", "legeInstanties", "gepauzeerdeInstantie"].includes(naam)) {
          await expect(page.locator(hero)).toHaveAttribute("data-status", mode === "vers" ? "healthy" : "unknown");
          await expect(page.locator(hero).getByRole("button").first()).toHaveText(mode === "vers" ? "Verversen" : "Opnieuw proberen");
        }
        if (naam.startsWith("geenActiviteit-") && mode !== "vers")
          await expect(page.locator(busy)).not.toContainText("Geen actieve encodes of downloads");
        if (naam === "kern") {
          const button = page.getByTestId("estop-banner").getByRole("button");
          if (mode === "vers") await expect(button).toBeEnabled();
          else await expect(button.and(page.locator(":enabled"))).toHaveCount(0);
        }
        if (naam === "slot") await expect(page.getByTestId("library-unlock")).toBeEnabled({ enabled: mode === "vers" });
        if (naam === "besparingsgrafiek")
          await expect(page.locator(kpi("AV1-besparing") + " .kpi-spark")).toHaveCount(mode === "vers" ? 1 : 0);
        if (naam === "encodes") {
          const bars = page.locator(busy).getByRole("progressbar");
          await expect(bars).toHaveCount(mode === "vers" ? 8 : 0);
          if (mode === "vers") await expect(bars.first()).toHaveAttribute("aria-valuenow", "50");
        }
        if (naam === "meldingen") {
          await expect(page.getByTestId("bell-badge")).toHaveCount(mode === "vers" ? 1 : 0);
          await expect(page.getByTestId("notif-panel").getByRole("button").first()).toBeEnabled({ enabled: mode === "vers" });
          if (mode !== "vers") await expect(page.getByTestId("bell")).toHaveAccessibleName(mode === "weigering" ? onbekend : oud);
        }
      });
    }
  });
}

for (const actie of ["balk", "profiel", "bibliotheek"] as const) {
  test(`Veiligheidsactie ${actie} verifieert opnieuw vóór het schrijven`, async ({ page }) => {
    let verificatie: "vers" | "oud" | "weigering" | "uitval" | "uit" = "vers";
    let gelezen = 0;
    let geschreven = 0;
    const slot = actie === "bibliotheek";
    const pad = slot ? "recovery/readiness" : "status";
    const kern = await (await page.request.get("/api/v1/status")).json();
    await page.route("**/api/v1/" + pad, async route => {
      gelezen++;
      if (verificatie === "weigering") return route.fulfill({ status: 403, json: { detail: "Geen leesrecht" } });
      if (verificatie === "uitval") return route.abort("failed");
      const at = verificatie === "oud" ? oudeTijd : await page.evaluate(() => new Date().toISOString());
      return route.fulfill({ json: slot ? { library_locked: verificatie !== "uit", generated_at: at }
        : { ...kern, time: at, emergency_stop: { active: verificatie !== "uit" } } });
    });
    await page.route("**/api/v1/" + (slot ? "recovery/library-write" : "system/emergency-stop"), route => {
      expect(route.request().method()).toBe("POST");
      expect(route.request().postDataJSON()).toMatchObject(slot ? { confirm: true } : { active: false });
      geschreven++;
      return route.fulfill({ json: {} });
    });
    for (const situatie of ["oud", "weigering", "uitval", "uit", "vers"] as const) {
      verificatie = "vers";
      await page.goto("/");
      if (slot) await page.getByTestId("library-unlock").click();
      if (actie === "profiel") {
        await page.getByTestId("profile-button").click();
        await expect(page.getByTestId("estop-button")).toHaveAttribute("aria-pressed", "true");
        await page.getByTestId("estop-button").click();
      }
      const knop = actie === "balk" ? page.getByTestId("estop-banner").getByRole("button", { name: "Noodstop opheffen" })
        : slot ? page.getByRole("alertdialog").getByRole("button", { name: "Bibliotheek vrijgeven" }) : page.getByTestId("estop-confirm");
      await expect(knop).toBeEnabled();
      verificatie = situatie;
      const voor = gelezen;
      await knop.click();
      await expect.poll(() => gelezen).toBeGreaterThan(voor);
      if (situatie === "vers") await expect.poll(() => geschreven).toBe(1);
      else {
        if (actie === "balk") await expect(page.getByText("De actuele stand kon niet worden bevestigd.")).toBeVisible();
        else if (situatie !== "uit" || !slot) await expect(knop).toBeDisabled();
        else await expect(knop).toHaveCount(0);
        expect(geschreven).toBe(0);
      }
    }
  });
}

for (const taal of ["nl-NL", "en-US"] as const) {
  test(`Verwijderdialoog bij één en twee gevolgde servers · ${taal}`, async ({ page }) => {
    const basis = await (await page.request.get("/api/v1/mediaserver/status")).json();
    const voorbeeld = { ...basis.servers[0], server_id: 42, label: "Testslot", kind: "jellyfin", enabled: true,
      followed: true, key_set: true, legacy: false, path_map: [] };
    const me = await (await page.request.get("/api/v1/auth/me")).json();
    await page.route("**/api/v1/auth/me", r => r.fulfill({ json: { ...me, locale: taal } }));
    for (const aantal of [1, 2]) {
      const servers = aantal === 1 ? [voorbeeld] : [voorbeeld, { ...voorbeeld, server_id: 43, label: "Testzaal" }];
      await page.route("**/api/v1/mediaserver/status", r => r.fulfill({ json: { ...basis, servers } }));
      await page.goto("/inrichten?tab=mediaserver");
      await page.getByTestId("ms-server-42").getByRole("button", { name: taal === "nl-NL" ? /Verwijderen…/ : /Remove…/ }).click();
      const dialoog = page.getByRole("alertdialog");
      await expect(dialoog).toContainText(taal === "nl-NL" ? "alleen de koppeling met Testslot" : "only the connection to Testslot");
      await expect(dialoog).not.toContainText(/andere mediaservers|other media servers/i);
      await expect(dialoog.getByRole("button", { name: taal === "nl-NL" ? "Annuleren" : "Cancel" })).toBeFocused();
    }
  });
}

test("R-HARDE-EIS kopbalk bij gelijktijdige veroudering en weigering", async ({ page }) => {
  await legeActielijst(page);
  await jsonBron(page, "seerr/settings", { api_key_set: false });
  await page.route("**/api/v1/health/summary", async route =>
    route.fulfill({ json: samenvatting(await page.evaluate(() => new Date().toISOString()), 0) }));
  let geweigerd = false;
  for (const [pad, inhoud] of [["migration/runs", []], ["provisioning/status", { needs: [] }],
    ["notifications/count", { unread: 2, badge: 2 }]] as const) {
    await page.route("**/api/v1/" + pad, route => geweigerd
      ? route.fulfill({ status: 403, json: { detail: "Geen testrecht" } })
      : route.fulfill({ json: inhoud, headers: { date: new Date(oudeTijd).toUTCString() } }));
  }
  await page.goto("/");
  const kop = page.getByTestId("status-pill");
  const bel = page.getByTestId("bell");
  const kaart = page.getByTestId("todo-card");
  const balk = page.getByRole("banner");
  for (const toestand of ["oud", "geweigerd"] as const) {
    if (toestand === "geweigerd") {
      geweigerd = true;
      await page.clock.fastForward(30_001);
    }
    const label = toestand === "oud" ? /Laatst bekend \d{2}:\d{2}/i : /Onbekend/i;
    await expect(bel).toHaveAccessibleName(label);
    await page.setViewportSize({ width: 320, height: 900 });
    expect.soft(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) - innerWidth), "Meldingen en bronstatus passen samen op 320 px").toBeLessThanOrEqual(1);
    await page.setViewportSize({ width: 768, height: 900 });
    await expect(kop).toContainText("Minstens 1 bekend · 2 bronnen");
    await expect(kop).toContainText(label);
    await expect(kop).toHaveAttribute("title", /Migratie.*Inrichten/);
    await expect(kop).toHaveAccessibleDescription(/Migratie.*Inrichten/);
    for (const bron of ["Migratie", "Inrichten"]) await expect(kaart).toContainText(bron);
    await expect(page.getByTestId("bell-reading")).toContainText(label);
    for (const breedte of [320, 360, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width: breedte, height: 900 });
      const hoogte = await balk.evaluate(el => el.getBoundingClientRect().height);
      expect(hoogte, `kophoogte tijdens ${toestand} op ${breedte} px`).toBeLessThanOrEqual(88);
      expect(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) - innerWidth)).toBeLessThanOrEqual(1);
      await bel.focus();
      await page.keyboard.press("Enter");
      await expect(page.getByTestId("notif-panel")).toBeVisible();
      await page.keyboard.press("Escape");
    }
    // Op de eerste desktopbreedte delen het zoekveld en de standen de ruimte, ook met ruimere letterspatiëring.
    await page.setViewportSize({ width: 1024, height: 900 });
    await balk.evaluate(el => { el.style.letterSpacing = "0.12em"; });
    expect(await balk.evaluate(el => el.getBoundingClientRect().height), `kophoogte met letterspatiëring tijdens ${toestand} op 1024 px`).toBeLessThanOrEqual(88);
    expect(await balk.getByRole("combobox").evaluate(el => el.getBoundingClientRect().width)).toBeGreaterThanOrEqual(44);
    await balk.evaluate(el => { el.style.removeProperty("letter-spacing"); });
    await page.setViewportSize({ width: 768, height: 900 });
  }
});
