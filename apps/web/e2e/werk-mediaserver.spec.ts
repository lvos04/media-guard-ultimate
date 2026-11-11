import { expect, test, type Page } from "@playwright/test";

/**
 * Mediaservers (Emby en Jelly Leon/Jellyfin naast elkaar; Δ-MG-DUAL, ADR-0022) en ondertitels: alles in te stellen in
 * de app (rapport 5, M3/M8).
 */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };
// Schermafdrukken voor ontwerpwerk: alleen met MG_SHOTS_DIR=<map> (zie design-shots.spec.ts).
const SHOTS = process.env.MG_SHOTS_DIR;

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

interface Srv { server_id: number; label: string; kind: string; enabled: boolean; followed: boolean; key_set: boolean; legacy: boolean; connected: boolean }
async function servers(page: Page): Promise<Srv[]> {
  return ((await (await page.request.get("/api/v1/mediaserver/status")).json()) as { servers: Srv[] }).servers;
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
    await expect(tab.getByRole("button", { name: "Verbinding testen" })).toBeVisible();
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
  test(`mediaservers naast elkaar: toevoegen, losmaken, weer koppelen en verwijderen (${width})`, async ({ page }) => {
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
    // een adres waar niets luistert: opslaan lukt, de verbindingstest niet, en dat is ook wat de pagina dan zegt
    await form.locator("#ms-url-new").fill("http://127.0.0.1:9");
    await form.locator("#ms-key-new").fill("e2e-sleutel-jellyfin");
    await tab.getByTestId("ms-add-save").click();
    const tweede = tab.getByTestId("ms-server-2");
    await expect(tweede).toBeVisible();
    await expect(tweede).toContainText("Jelly Leon");
    await expect(tweede).toContainText("http://127.0.0.1:9");
    await expect(tweede).toContainText("Authorization: MediaBrowser");
    await expect(tweede.locator(".chip").first()).toContainText("Gekoppeld");
    // er is geen geslaagde meting: dus nooit "Gezond", wel de reden
    await expect(tweede.locator(".chip")).not.toContainText(["Gezond"]);
    await expect(tweede.locator(".chip").nth(1)).toContainText(/Storing|Onbekend/);
    await expect(tab.getByTestId("ms-why-2")).toContainText(/Geen contact|Nog niet gemeten/);
    const na = await servers(page);
    expect(na.map((x) => [x.server_id, x.kind, x.label, x.enabled, x.key_set, x.legacy])).toEqual([
      [1, voor[0].kind, voor[0].label, true, false, true], [2, "jellyfin", "Jelly Leon", true, true, false],
    ]);
    expect(na[1].connected).toBe(false);
    // met twee servers gelden de schakelaars voor elke gekoppelde server
    await expect(tab).toContainText("Wat MediaGuard met je mediaservers doet");
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/mediaservers-twee-${width}.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    for (const extra of await tab.locator(".chip").evaluateAll((els) => els.map((el) => el.scrollWidth - el.clientWidth))) expect(extra).toBeLessThanOrEqual(1);

    // losmaken vraagt eerst wat er gebeurt; adres en sleutel blijven bewaard
    await tweede.getByRole("button", { name: "Jelly Leon losmaken" }).click();
    const dialoog = page.getByRole("alertdialog");
    await expect(dialoog).toContainText("Adres en sleutel blijven bewaard");
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/mediaservers-losmaken-${width}.png` });
    await dialoog.getByRole("button", { name: "Jelly Leon losmaken" }).click();
    await expect(dialoog).toHaveCount(0);
    await expect(tweede.locator(".chip")).toContainText("Ontkoppeld");
    await expect(tab.getByTestId("ms-why-2")).toContainText("Adres en sleutel zijn bewaard");
    expect((await servers(page))[1]).toMatchObject({ enabled: false, followed: false, key_set: true });
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/mediaservers-ontkoppeld-${width}.png`, fullPage: true });

    // weer koppelen is één handeling
    await tweede.getByRole("button", { name: "Jelly Leon weer koppelen" }).click();
    await expect(tweede.locator(".chip").first()).toContainText("Gekoppeld");
    expect((await servers(page))[1]).toMatchObject({ enabled: true, key_set: true });

    // verwijderen haalt alleen deze server uit MediaGuard; server 1 blijft staan
    await tweede.getByRole("button", { name: "Jelly Leon verwijderen" }).click();
    await expect(dialoog).toContainText("Andere mediaservers blijven gekoppeld");
    await dialoog.getByRole("button", { name: "Jelly Leon verwijderen" }).click();
    await expect(tweede).toHaveCount(0);
    await expect(eerste).toBeVisible();
    expect((await servers(page)).map((x) => x.server_id)).toEqual([1]);
  });
}

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
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/mediaservers-en-1440.png`, fullPage: true });
  } finally {
    // de taal hoort bij de gebruiker: zet hem terug, de andere specs draaien in het Nederlands
    expect((await taal("nl-NL")).ok()).toBeTruthy();
  }
});
