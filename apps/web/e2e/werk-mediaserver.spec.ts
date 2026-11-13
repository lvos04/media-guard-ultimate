import { expect, test, type Page } from "@playwright/test";

/**
 * Mediaservers (Emby en Jelly Leon/Jellyfin naast elkaar; Δ-MG-DUAL, ADR-0022) en ondertitels: alles in te stellen in
 * de app (rapport 5, M3/M8).
 */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };
// Een kijker en een operator: zij zien de reden waarom iets uit staat (RolMelding), nooit het beheerformulier.
const KIJKER = { username: "e2e-ms-kijker", password: `${ADMIN.password}-kijker`, role: "viewer" };
const OPERATOR = { username: "e2e-ms-operator", password: `${ADMIN.password}-operator`, role: "operator" };
// Schermafdrukken voor ontwerpwerk: alleen met MG_SHOTS_DIR=<map> (zie design-shots.spec.ts).
const SHOTS = process.env.MG_SHOTS_DIR;

async function login(page: Page, who: { username: string; password: string } = ADMIN) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(who.username);
  await page.getByLabel("Wachtwoord").fill(who.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

interface Srv { server_id: number; label: string; kind: string; url: string; enabled: boolean; followed: boolean; key_set: boolean; legacy: boolean; connected: boolean }
interface Status { servers: Srv[]; setting_kind: string; setting_url: string }
async function status(page: Page): Promise<Status> {
  return (await (await page.request.get("/api/v1/mediaserver/status")).json()) as Status;
}
async function servers(page: Page): Promise<Srv[]> {
  return (await status(page)).servers;
}
async function csrf(page: Page): Promise<Record<string, string>> {
  const me = await (await page.request.get("/api/v1/auth/me")).json() as { csrf_token: string };
  return { "X-CSRF-Token": me.csrf_token };
}

test.beforeAll(async ({ request }) => {
  const status = await (await request.get("/api/v1/auth/setup-status")).json() as { needs_setup: boolean };
  if (status.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
});

for (const width of [1440, 390] as const) {
  test(`mediaserver-tab: Emby-sleutel en schakelaars in de app (${width})`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await login(page);
    await page.goto("/inrichten?tab=mediaserver");
    const tab = page.getByTestId("mediaserver-tab");
    await expect(tab).toBeVisible();
    await expect(tab).toContainText("Mediaserver");
    // waar de sleutel te maken is, staat erbij
    await expect(tab).toContainText("Geavanceerd → API-sleutels");
    await expect(tab.getByLabel("API-sleutel", { exact: true })).toBeVisible();
    // een beheerder mag alles: geen RolMelding
    await expect(tab.getByTestId("ms-role-note")).toHaveCount(0);
    await expect(tab.getByRole("button", { name: /^Verbinding testen/ })).toBeVisible();
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

for (const width of [1440, 390] as const) {
  test(`mediaservers naast elkaar: toevoegen, ontkoppelen, weer koppelen en verwijderen (${width})`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await login(page);
    await page.goto("/inrichten?tab=mediaserver");
    const tab = page.getByTestId("mediaserver-tab");
    const eerste = tab.getByTestId("ms-server-1");
    await expect(eerste).toBeVisible();
    // server 1 is de koppeling die er altijd al was; zonder adres is hij niet gekoppeld, en dat staat er met de reden bij
    await expect(eerste.locator(".chip")).toContainText("Niet gekoppeld");
    await expect(tab.getByTestId("ms-why-1")).toContainText("Nog geen adres");
    const voor = await servers(page);
    expect(voor.map((x) => x.server_id)).toEqual([1]);

    // een tweede server erbij: de eerste blijft zoals hij is
    await tab.getByTestId("ms-add-open").click();
    const form = tab.getByTestId("ms-add-form");
    await expect(form).toContainText("blijven precies zo gekoppeld");
    await expect(form).toContainText("Jellyfin of Jelly Leon: Dashboard → API-sleutels");
    await expect(form.getByRole("button", { name: "Jellyfin (Jelly Leon)" })).toHaveAttribute("aria-pressed", "true");
    await form.locator("#ms-name-new").fill("Jelly Leon");
    // het adres is verplicht en dat staat erbij; zonder adres staat "Toevoegen en testen" uit, met de reden eronder
    await expect(form.locator('label[for="ms-url-new"]')).toContainText("(verplicht)");
    const opslaan = tab.getByTestId("ms-add-save");
    await expect(opslaan).toBeDisabled();
    await expect(opslaan).toHaveAttribute("aria-describedby", "ms-add-need-url");
    await expect(form.locator("#ms-add-need-url")).toHaveText("Vul eerst het adres in; daarna kun je toevoegen en testen.");
    // een adres waar niets luistert: opslaan lukt, de verbindingstest niet, en dat is ook wat de pagina dan zegt
    await form.locator("#ms-url-new").fill("http://127.0.0.1:9");
    await expect(opslaan).toBeEnabled();
    await expect(opslaan).not.toHaveAttribute("aria-describedby", /.+/);
    await expect(form.locator("#ms-add-need-url")).toHaveCount(0);
    const sleutel = "e2e-sleutel-jellyfin";
    await form.locator("#ms-key-new").fill(sleutel);
    await expect(form.locator("#ms-key-new")).toHaveValue(sleutel);
    await opslaan.click();
    // het id van de nieuwe server komt van de core; een id wordt nooit opnieuw uitgedeeld (S-P2-600, R-TECH-3), dus
    // de tweede run van deze test in dezelfde core krijgt een hoger nummer dan 2
    await expect.poll(async () => (await servers(page)).length).toBe(2);
    const nieuw = (await servers(page))[1].server_id;
    expect(nieuw).toBeGreaterThan(1);
    const tweede = tab.getByTestId(`ms-server-${nieuw}`);
    await expect(tweede).toBeVisible();
    await expect(tweede).toContainText("Jelly Leon");
    await expect(tweede).toContainText("http://127.0.0.1:9");
    await expect(tweede).toContainText("Authorization: MediaBrowser");
    await expect(tweede.locator(".chip").first()).toContainText("Gekoppeld");
    // er is geen geslaagde meting: dus nooit "Gezond", wel de reden
    await expect(tweede.locator(".chip")).not.toContainText(["Gezond"]);
    await expect(tweede.locator(".chip").nth(1)).toContainText(/Storing|Onbekend/);
    await expect(tab.getByTestId(`ms-why-${nieuw}`)).toContainText(/Geen contact|Nog niet gemeten/);
    // TestResultaat (C-58): de mislukte test staat als gemeten falen (danger, nooit amber) in de vaste live-regio van
    // DEZE server, niet los onder de lijst
    const uitslag = tab.getByTestId(`ms-result-${nieuw}`);
    await expect(uitslag).toHaveAttribute("role", "status");
    await expect(uitslag.locator(".inline-state")).toHaveAttribute("data-tone", "danger");
    await expect(uitslag.locator(".inline-state svg")).toHaveClass(/lucide-octagon-x/);
    await expect(tab.getByTestId("ms-result-1")).toBeEmpty();
    // de sleutel staat na het opslaan nergens in de pagina: niet in het veld, niet in de HTML, niet na herladen en niet
    // in het statusantwoord van de API
    const nergens = async () => {
      expect(await page.content()).not.toContain(sleutel);
      // de HTML toont de waarde van een veld niet altijd: lees ook elk veld zelf
      for (const waarde of await page.locator("input,textarea").evaluateAll((els) => els.map((el) => (el as HTMLInputElement).value))) expect(waarde).not.toContain(sleutel);
    };
    await nergens();
    expect(await (await page.request.get("/api/v1/mediaserver/status")).text()).not.toContain(sleutel);
    await page.reload();
    await expect(tweede).toContainText("http://127.0.0.1:9");
    await nergens();
    const na = await servers(page);
    expect(na.map((x) => [x.server_id, x.kind, x.label, x.enabled, x.key_set, x.legacy])).toEqual([
      [1, voor[0].kind, voor[0].label, true, false, true], [nieuw, "jellyfin", "Jelly Leon", true, true, false],
    ]);
    expect(na[1].connected).toBe(false);
    // met twee servers gelden de schakelaars voor elke gekoppelde server
    await expect(tab).toContainText("Wat MediaGuard met je mediaservers doet");
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/mediaservers-twee-${width}.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    for (const extra of await tab.locator(".chip").evaluateAll((els) => els.map((el) => el.scrollWidth - el.clientWidth))) expect(extra).toBeLessThanOrEqual(1);

    // de knoppen zijn kort; de servernaam staat in hun toegankelijke naam (Peil C-41a)
    await expect(tweede.getByRole("button", { name: "Ontkoppelen…: Jelly Leon" })).toHaveText("Ontkoppelen…");
    // ontkoppelen vraagt eerst wat er gebeurt; adres en sleutel blijven bewaard. Het is omkeerbaar: geen gevaardialoog.
    await tweede.getByRole("button", { name: "Ontkoppelen…: Jelly Leon" }).click();
    const dialoog = page.getByRole("alertdialog");
    await expect(dialoog).toContainText("Jelly Leon ontkoppelen?");
    await expect(dialoog).toContainText("Adres en sleutel blijven bewaard");
    await expect(dialoog).not.toHaveClass(/dialog--danger/);
    await expect(dialoog.getByRole("button", { name: "Annuleren" })).toBeFocused();
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/mediaservers-ontkoppelen-${width}.png` });
    await dialoog.getByRole("button", { name: "Server ontkoppelen" }).click();
    await expect(dialoog).toHaveCount(0);
    await expect(tweede.locator(".chip")).toContainText("Ontkoppeld");
    await expect(tab.getByTestId(`ms-why-${nieuw}`)).toContainText("Adres en sleutel zijn bewaard");
    expect((await servers(page))[1]).toMatchObject({ enabled: false, followed: false, key_set: true });
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/mediaservers-ontkoppeld-${width}.png`, fullPage: true });

    // weer koppelen is één handeling
    await tweede.getByRole("button", { name: "Weer koppelen: Jelly Leon" }).click();
    await expect(tweede.locator(".chip").first()).toContainText("Gekoppeld");
    expect((await servers(page))[1]).toMatchObject({ enabled: true, key_set: true });

    // verwijderen haalt alleen deze server uit MediaGuard; server 1 blijft staan
    await tweede.getByRole("button", { name: "Verwijderen…: Jelly Leon" }).click();
    await expect(dialoog).toContainText("Jelly Leon verwijderen?");
    await expect(dialoog).toContainText("Andere mediaservers blijven gekoppeld");
    await expect(dialoog).toHaveClass(/dialog--danger/);
    await dialoog.getByRole("button", { name: "Server verwijderen" }).click();
    await expect(tweede).toHaveCount(0);
    await expect(eerste).toBeVisible();
    expect((await servers(page)).map((x) => x.server_id)).toEqual([1]);
  });
}

for (const width of [1440, 390] as const) {
  test(`een ontkoppelde server 1 blijft zichtbaar en is met één handeling weer gekoppeld (${width})`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await login(page);
    const voor = await status(page);
    expect(voor.servers.map((x) => [x.server_id, x.legacy, x.key_set])).toEqual([[1, true, false]]);
    try {
      await page.goto("/inrichten?tab=mediaserver");
      const tab = page.getByTestId("mediaserver-tab");
      const eerste = tab.getByTestId("ms-server-1");
      // server 1 heeft nog geen sleutel: voor een beheerder staat het formulier open
      // een adres dat de core weigert, is een fout van het verzoek en geen meting: de fout staat als melding
      // (role=alert) bij deze server en de live-regio van het TestResultaat blijft leeg (nooit "mislukt" als storing)
      await eerste.locator("#ms-url-1").fill("http://127.0.0.1 9");
      await eerste.getByRole("button", { name: /^Opslaan en testen/ }).click();
      await expect(eerste.getByRole("alert")).toContainText("Ongeldig adres");
      await expect(tab.getByTestId("ms-result-1")).toBeEmpty();
      expect((await servers(page))[0]).toMatchObject({ server_id: 1, key_set: false, url: voor.servers[0].url });
      await eerste.locator("#ms-url-1").fill("http://127.0.0.1:9");
      await eerste.locator("#ms-key-1").fill("e2e-sleutel-server-1");
      await eerste.getByRole("button", { name: /^Opslaan en testen/ }).click();
      await expect(eerste.locator(".chip").first()).toContainText("Gekoppeld");
      await expect(eerste.locator(".chip")).not.toContainText(["Gezond"]);
      const label = (await servers(page))[0].label;

      await eerste.getByRole("button", { name: `Ontkoppelen…: ${label}` }).click();
      const dialoog = page.getByRole("alertdialog");
      await expect(dialoog).toContainText(`${label} ontkoppelen?`);
      await expect(dialoog).not.toHaveClass(/dialog--danger/);
      await dialoog.getByRole("button", { name: "Server ontkoppelen" }).click();
      await expect(dialoog).toHaveCount(0);
      // ontkoppeld: de rij blijft staan, met woord, reden en de weg terug
      await expect(eerste).toBeVisible();
      await expect(eerste.locator(".chip")).toHaveText(["Ontkoppeld"]);
      await expect(eerste.locator(".chip")).not.toContainText(["Gezond"]);
      await expect(tab.getByTestId("ms-why-1")).toContainText("Adres en sleutel zijn bewaard");
      // ontkoppelen is een keuze, geen meting: geen "mislukt"-uitslag in de live-regio van de server
      await expect(tab.getByTestId("ms-result-1")).toBeEmpty();
      // de core noemt een ontkoppelde server 1 zonder soort anders (apps/core/mediaguard/mediaserver/servers.py,
      // functie detached_legacy); de knop draagt de naam van nu
      const naamNu = (await servers(page))[0].label;
      await expect(eerste.getByRole("button", { name: `Weer koppelen: ${naamNu}` })).toBeVisible();
      // server 1 is de bestaande koppeling: verwijderen kan niet, alleen ontkoppelen
      await expect(eerste.getByRole("button", { name: /^Verwijderen/ })).toHaveCount(0);
      expect((await servers(page))[0]).toMatchObject({ server_id: 1, legacy: true, enabled: false, followed: false, key_set: true });
      // ook na herladen staat hij er nog
      await page.reload();
      await expect(eerste.locator(".chip")).toHaveText(["Ontkoppeld"]);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/mediaservers-server1-ontkoppeld-${width}.png`, fullPage: true });

      await eerste.getByRole("button", { name: `Weer koppelen: ${naamNu}` }).click();
      await expect(eerste.locator(".chip").first()).toContainText("Gekoppeld");
      expect((await servers(page))[0]).toMatchObject({ server_id: 1, enabled: true, key_set: true });
    } finally {
      // terug naar de begintoestand van deze run: de andere tests verwachten server 1 zonder adres en sleutel
      const r = await page.request.post("/api/v1/mediaserver/config", {
        data: { kind: voor.setting_kind, url: voor.setting_url, clear_key: true }, headers: await csrf(page),
      });
      expect(r.ok()).toBeTruthy();
      expect((await servers(page))[0]).toMatchObject({ server_id: 1, enabled: voor.servers[0].enabled, key_set: false, url: voor.servers[0].url });
    }
  });
}

test("ondertitels: wat ontbreekt is een neutraal label, een mislukte test is gemeten falen in de vaste live-regio, een geweigerd verzoek een melding", async ({ page }) => {
  // de e2e-core kent geen titels: het echte statusantwoord, alleen met een telling van ontbrekende ondertitels erin
  await page.route("**/api/v1/mediaserver/status", async (route) => {
    const r = await route.fetch();
    const body = await r.json() as { subtitles: { missing: number } };
    body.subtitles.missing = 3;
    await route.fulfill({ response: r, json: body });
  });
  await login(page);
  const voor = await status(page) as unknown as { subtitles: { bazarr_url: string; bazarr_key_set: boolean; source: string | null } };
  // zonder Bazarr-sleutel en zonder OpenSubtitles-account mislukt de test altijd; dat is de begintoestand van de e2e
  expect(voor.subtitles).toMatchObject({ bazarr_key_set: false, source: null });
  await page.goto("/inrichten?tab=mediaserver");
  const kaart = page.locator("section.card", { has: page.getByTestId("subtitles-card") });
  // een telling is een feit, geen gezondheid: sterk-neutraal label, zonder statusvorm, nooit amber of groen
  const telling = kaart.locator(".card-header .chip");
  await expect(telling).toHaveText("NL-ondertitel ontbreekt bij 3 titels");
  await expect(telling).toHaveClass(/(^| )chip--strong( |$)/);
  expect(await telling.getAttribute("data-status")).toBeNull();
  await expect(telling.locator("svg")).toHaveCount(0);
  // TestResultaat (C-58): de vaste live-regio staat er al (leeg) en krijgt de uitslag als gemeten falen (danger)
  const uitslag = kaart.getByTestId("subs-result");
  await expect(uitslag).toHaveAttribute("role", "status");
  await expect(uitslag).toBeEmpty();
  await kaart.getByRole("button", { name: "Opslaan en testen" }).click();
  await expect(uitslag.locator(".inline-state")).toHaveAttribute("data-tone", "danger");
  await expect(uitslag.locator(".inline-state svg")).toHaveClass(/lucide-octagon-x/);
  await expect(uitslag).toContainText("Nog geen Bazarr-sleutel of OpenSubtitles-account.");
  await expect(kaart.getByRole("alert")).toHaveCount(0);
  // een verzoek dat de core weigert, is geen meting: de fout staat als melding (role=alert) onder de knoppen, er komt
  // geen "mislukt" in de live-regio en de oudere uitslag gaat weg (R-TECH-6; de weigering is nagebootst, er wordt niets
  // opgeslagen)
  const weiger = "**/api/v1/mediaserver/subtitles/config";
  await page.route(weiger, (route) => route.fulfill({ status: 422, json: { detail: "Ongeldig adres: geen spaties en geen gebruikersnaam in het adres." } }));
  await kaart.getByRole("button", { name: "Opslaan en testen" }).click();
  await expect(kaart.getByRole("alert")).toContainText("Ongeldig adres");
  await expect(kaart.getByRole("alert")).toHaveAttribute("data-tone", "danger");
  await expect(uitslag).toBeEmpty();
  await page.unroute(weiger);
  // een nieuw verzoek wist de oude fout en geeft weer een echte uitslag
  await kaart.getByRole("button", { name: "Opslaan en testen" }).click();
  await expect(kaart.getByRole("alert")).toHaveCount(0);
  await expect(uitslag).toContainText("Nog geen Bazarr-sleutel of OpenSubtitles-account.");
  // opslaan zonder wijziging laat de instellingen zoals ze waren
  expect((await status(page) as unknown as typeof voor).subtitles).toMatchObject(voor.subtitles);
});

test("390 px: uitslag en melding van de ondertitelkaart staan na de klik helemaal in beeld, niet onder de ondernavigatie", async ({ page }) => {
  // PEIL_REVIEW §11.5 punt 2: de ondertitelkaart is de laatste kaart; wie tot onderaan scrolt en "Opslaan en testen"
  // kiest, kreeg de uitslag of melding half achter de ondernavigatie. Die komt nu bij verschijnen in beeld
  // (scrollIntoView, block "nearest"; de scroll-padding van Peil houdt kop en ondernavigatie vrij).
  await page.setViewportSize({ width: 390, height: 800 });
  await login(page);
  await page.goto("/inrichten?tab=mediaserver");
  const kaart = page.locator("section.card", { has: page.getByTestId("subtitles-card") });
  const knop = kaart.getByRole("button", { name: "Opslaan en testen" });
  await expect(knop).toBeVisible();
  // helemaal in beeld = binnen het venster, onder de kopbalk en boven de ondernavigatie
  const inBeeld = (loc: ReturnType<Page["locator"]>) => loc.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const kop = document.querySelector("header.topbar")!.getBoundingClientRect().bottom;
    const nav = document.querySelector("nav.bottomnav")!.getBoundingClientRect().top;
    return r.height > 0 && r.top >= kop - 0.5 && r.bottom <= Math.min(nav, window.innerHeight) + 0.5;
  });
  const naarOnder = () => page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  // uitslag (vaste live-regio): de e2e-core heeft geen Bazarr-sleutel, dus een gemeten "mislukt"
  await naarOnder();
  await expect(knop).toBeInViewport();
  await knop.click();
  const uitslag = kaart.getByTestId("subs-result").locator(".inline-state");
  await expect(uitslag).toContainText("Nog geen Bazarr-sleutel of OpenSubtitles-account.");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/ondertitels-uitslag-390.png` });
  await expect.poll(() => inBeeld(uitslag)).toBe(true);
  // melding (role=alert) bij een geweigerd verzoek (nagebootst; er wordt niets opgeslagen), op een verse pagina: zonder
  // eerdere uitslag is er onder de knoppen nog geen ruimte die de melding kan innemen
  await page.route("**/api/v1/mediaserver/subtitles/config", (route) => route.fulfill({ status: 422, json: { detail: "Ongeldig adres: geen spaties en geen gebruikersnaam in het adres." } }));
  await page.goto("/inrichten?tab=mediaserver");
  await expect(knop).toBeVisible();
  await naarOnder();
  await expect(knop).toBeInViewport();
  await knop.click();
  const melding = kaart.getByRole("alert");
  await expect(melding).toContainText("Ongeldig adres");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/ondertitels-melding-390.png` });
  await expect.poll(() => inBeeld(melding)).toBe(true);
  // geen zijwaarts scrollen
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
});

test("een mislukt verzoek na een geslaagde test: de oude uitslag gaat weg, de fout is een melding", async ({ page }) => {
  // R-TECH-6: eerst een geslaagde "Verbinding testen", daarna faalt hetzelfde verzoek (500). De live-regio van het
  // TestResultaat toont dan geen oude "geslaagd" meer naast de fout, en ook geen "mislukt": de fout staat als role=alert.
  // Beide antwoorden zijn nagebootst; de core en server 1 blijven ongemoeid.
  let keer = 0;
  await page.route("**/api/v1/mediaserver/test*", (route) => {
    keer += 1;
    return keer === 1
      ? route.fulfill({ status: 200, json: { ok: true, message_nl: "Verbonden met Emby 4.10.2.0 (nagebootst)" } })
      : route.fulfill({ status: 500, json: { detail: "Interne fout bij het testen (nagebootst)." } });
  });
  await login(page);
  await page.goto("/inrichten?tab=mediaserver");
  const tab = page.getByTestId("mediaserver-tab");
  const eerste = tab.getByTestId("ms-server-1");
  const uitslag = tab.getByTestId("ms-result-1");
  await eerste.getByRole("button", { name: /^Verbinding testen/ }).click();
  await expect(uitslag).toContainText("Verbonden met Emby 4.10.2.0 (nagebootst)");
  await expect(eerste.getByRole("alert")).toHaveCount(0);
  await eerste.getByRole("button", { name: /^Verbinding testen/ }).click();
  await expect(eerste.getByRole("alert")).toContainText("Interne fout bij het testen (nagebootst).");
  await expect(uitslag).toBeEmpty();
  expect(keer).toBe(2);
});

test("kijker en operator: de reden staat erbij, het beheerformulier niet", async ({ browser, page }) => {
  await login(page);
  for (const who of [KIJKER, OPERATOR]) {
    const r = await page.request.post("/api/v1/auth/users", { data: who, headers: await csrf(page) });
    expect([200, 409]).toContain(r.status());
  }
  for (const who of [KIJKER, OPERATOR]) {
    const ctx = await browser.newContext();
    const p = await ctx.newPage();
    try {
      await login(p, who);
      await p.goto("/inrichten?tab=mediaserver");
      const tab = p.getByTestId("mediaserver-tab");
      // RolMelding (C-49): zegt wie het wel mag; voor de operator ook wat hij wel kan
      const melding = tab.getByTestId("ms-role-note");
      await expect(melding).toHaveAttribute("id", "ms-role-note");
      await expect(melding).toHaveText(who === KIJKER
        ? "Je kijkt als kijker: je kunt hier niets wijzigen."
        : "Alleen een beheerder kan deze instellingen wijzigen. Testen en zoeken kan wel.");
      // server 1 heeft nog geen sleutel; een beheerder ziet dan het formulier, wie geen beheerder is niet
      const eerste = tab.getByTestId("ms-server-1");
      await expect(eerste).toBeVisible();
      await expect(eerste.locator("input")).toHaveCount(0);
      await expect(tab.getByTestId("ms-add-open")).toHaveCount(0);
      await expect(tab.getByTestId("ms-add-form")).toHaveCount(0);
      // elke schakelaar en keuze die uit staat, wijst naar de RolMelding als reden
      for (const name of ["Verversen na overdracht", "Afspeeltest", "Recent bekeken niet omzetten", "Automatisch zoeken"]) {
        const sw = tab.getByRole("switch", { name });
        await expect(sw).toBeDisabled();
        await expect(sw).toHaveAttribute("aria-describedby", "ms-role-note");
      }
      const keuzes = tab.getByRole("group", { name: "AV1 pauzeren tijdens kijken" }).getByRole("button");
      await expect(keuzes).toHaveCount(3);
      for (const knop of await keuzes.all()) {
        await expect(knop).toBeDisabled();
        await expect(knop).toHaveAttribute("aria-describedby", "ms-role-note");
      }
      await expect(tab.locator("#subs-bazarr-url")).toBeDisabled();
      await expect(tab.locator("#subs-bazarr-url")).toHaveAttribute("aria-describedby", /(^| )ms-role-note( |$)/);
      await expect(tab.locator("#subs-bazarr-key")).toHaveAttribute("aria-describedby", "subs-bazarr-key-hint ms-role-note");
      const test = eerste.getByRole("button", { name: /^Verbinding testen/ });
      if (who === KIJKER) {
        await expect(test).toHaveCount(0);
        await expect(tab.getByRole("button", { name: "Opnieuw zoeken" })).toHaveCount(0);
      } else {
        // testen mag de operator wel; de uitslag staat in de live-regio van server 1 (zonder adres: gemeten falen)
        await test.click();
        await expect(tab.getByTestId("ms-result-1").locator(".inline-state")).toHaveAttribute("data-tone", "danger");
        await expect(tab.getByRole("button", { name: "Opnieuw zoeken" })).toBeVisible();
      }
      // een rol zonder rechten ziet nooit de knoppen die gegevens wijzigen
      for (const naam of [/^Wijzigen/, /^Ontkoppelen/, /^Verwijderen/, /^Weer koppelen/, /^Opslaan/]) await expect(tab.getByRole("button", { name: naam })).toHaveCount(0);
    } finally {
      await ctx.close();
    }
  }
});

test("mediaservers in het Engels: dezelfde lijst, Engelse woorden", async ({ page }) => {
  await login(page);
  const me = await (await page.request.get("/api/v1/auth/me")).json() as { csrf_token: string };
  const taal = (locale: string) => page.request.put("/api/v1/auth/me/locale", { data: { locale }, headers: { "X-CSRF-Token": me.csrf_token } });
  expect((await taal("en-US")).ok()).toBeTruthy();
  try {
    await page.goto("/inrichten?tab=mediaserver");
    const tab = page.getByTestId("mediaserver-tab");
    await expect(tab).toContainText("Media servers");
    await expect(tab.getByTestId("ms-server-1").locator(".chip")).toContainText("Not linked");
    await expect(tab.getByTestId("ms-why-1")).toContainText("No address yet");
    await expect(tab.getByTestId("ms-add-open")).toContainText("Add media server");
    await expect(tab).toContainText("Advanced → API Keys");
    // het formulier voor een extra server: verplicht adres en de reden bij de knop die uit staat, ook in het Engels
    await tab.getByTestId("ms-add-open").click();
    const form = tab.getByTestId("ms-add-form");
    await expect(form.locator('label[for="ms-url-new"]')).toContainText("(required)");
    await expect(tab.getByTestId("ms-add-save")).toBeDisabled();
    await expect(form.locator("#ms-add-need-url")).toHaveText("Enter the address first; then you can add and test.");
    await expect(form).not.toContainText("nas.lan");
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/mediaservers-en-1440.png`, fullPage: true });
  } finally {
    // de taal hoort bij de gebruiker: zet hem terug, de andere specs draaien in het Nederlands
    expect((await taal("nl-NL")).ok()).toBeTruthy();
  }
});
