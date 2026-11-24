import { execFileSync } from "node:child_process";
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { ADMIN } from "./fixtures/admin";

// S-P3-600: echte web-app en kern, uitsluitend de eigen e2e-database.
// Het voorvoegsel werk- houdt de eenmalige installatie in mediaguard.spec.ts als eerste.
const SCOPE = "emergency_stop:on";
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const ROLE_ERROR = "Rechten (scopes) kunnen alleen op een sleutel met rol kijker. Kies rol kijker of laat de rechten leeg.";
const UNKNOWN_ERROR = `Onbekend recht (scope) 'wanted:search'. Toegestaan: ${SCOPE}.`;
const NAMES = ["review-hub", "review-operator", "review-lezer", "review-onbekend"];

async function csrf(page: Page) {
  const me = await (await page.request.get("/api/v1/auth/me")).json() as { csrf_token: string };
  return { "X-CSRF-Token": me.csrf_token };
}

test.beforeAll(async ({ request }) => {
  const status = await (await request.get("/api/v1/auth/setup-status")).json() as { needs_setup: boolean };
  if (status.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
});

type Theme = "light" | "dark" | "system" | null;
let originalTheme: Theme;
let originalLocale: string;
let suiteTheme: Theme;
let suiteLocale: string;

async function preferences(page: Page) {
  return await (await page.request.get("/api/v1/auth/me")).json() as {
    preferences: { theme?: Exclude<Theme, null> }; locale: string;
  };
}

test.beforeAll(async ({ request }) => {
  expect((await request.post("/api/v1/auth/login", { data: ADMIN })).ok()).toBeTruthy();
  const me = await (await request.get("/api/v1/auth/me")).json();
  suiteTheme = me.preferences.theme ?? null;
  suiteLocale = me.locale;
});

test.beforeEach(async ({ page }) => {
  expect((await page.request.post("/api/v1/auth/login", { data: ADMIN })).ok()).toBeTruthy();
  const me = await preferences(page);
  originalTheme = me.preferences.theme ?? null;
  originalLocale = me.locale;
});

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
  // Verlaat de app voordat de voorkeur wordt hersteld: een late browserwrite
  // mag de teruggezette serverkeuze niet opnieuw overschrijven.
  await page.goto("/login");
  await page.evaluate(() => localStorage.removeItem("mg.theme"));
  const headers = await csrf(page);
  expect((await page.request.put("/api/v1/auth/me/preferences", { headers, data: { theme: originalTheme } })).ok()).toBeTruthy();
  expect((await page.request.post("/api/v1/system/emergency-stop", { headers, data: { active: false, reason: "ontwerpreview opruimen" } })).ok()).toBeTruthy();
  expect((await page.request.put("/api/v1/auth/me/locale", { headers, data: { locale: originalLocale } })).ok()).toBeTruthy();
  const keys = await (await page.request.get("/api/v1/auth/api-keys")).json() as { items: { id: number; name: string; revoked_at: string | null }[] };
  const own = keys.items.filter((key) => NAMES.includes(key.name));
  for (const key of own) {
    expect((await page.request.post(`/api/v1/auth/api-keys/${key.id}/revoke`, { headers })).ok()).toBeTruthy();
  }
  const revoked = await (await page.request.get("/api/v1/auth/api-keys")).json() as typeof keys;
  expect(revoked.items.filter((key) => NAMES.includes(key.name) && !key.revoked_at), "alle eigen sleutels ingetrokken").toEqual([]);
  expect((await preferences(page)).preferences.theme ?? null, "serverthema teruggezet").toBe(originalTheme);
  expect(await page.evaluate(() => localStorage.getItem("mg.theme")), "opslagthema gewist").toBeNull();
  // Alleen de vier zelfgemaakte fixtures opruimen; sleutels uit andere tests blijven staan.
  execFileSync(process.env.MG_E2E_PYTHON as string, ["-I", "-c",
    "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.executemany('DELETE FROM api_keys WHERE name=?', [(n,) for n in sys.argv[2:]]); c.commit()",
    path.join(process.env.MG_E2E_DATA_DIR as string, "mediaguard.db"), ...NAMES]);
});

/** Contrast en reflow van de werkelijk gerenderde tekst, inclusief overgeërfde achtergronden. */
async function geometry(root: Locator) {
  return root.evaluate(async (scope) => {
    // Ook een rolwissel kan de spoor- en knopkleur nog in dezelfde renderbeurt wijzigen.
    for (;;) {
      void scope.getBoundingClientRect();
      const moving = scope.getAnimations({ subtree: true }).filter((a) => a.playState === "running" && a.effect?.getTiming().iterations !== Infinity);
      if (!moving.length) break;
      // Een mediawissel kan een oude transitie annuleren. Meet de nieuwe stijl
      // pas als ook de vervangende transities zijn uitgetekend.
      await Promise.allSettled(moving.map((animation) => animation.finished));
    }
    const rgb = (s: string) => (s.match(/[\d.]+/g) ?? []).map(Number);
    const lum = (c: number[]) => c.slice(0, 3).map((v) => v / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
      .reduce((v, c, i) => v + c * [.2126, .7152, .0722][i], 0);
    const ratio = (a: number[], b: number[]) => (Math.max(lum(a), lum(b)) + .05) / (Math.min(lum(a), lum(b)) + .05);
    const background = (el: Element | null): number[] => {
      if (!el) return [255, 255, 255];
      const c = rgb(getComputedStyle(el).backgroundColor); const a = c[3] ?? 1;
      return a === 1 ? c : background(el.parentElement).map((v, i) => c[i] * a + v * (1 - a));
    };
    const small: string[] = []; const clipped: string[] = []; const targets: string[] = [];
    const contrast: number[] = [];
    for (const el of [scope, ...scope.querySelectorAll<HTMLElement>("*")]) {
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      if (!r.width || !r.height || el.closest(".sr-only,[hidden]")) continue;
      if (el.matches("button,input,select,textarea,a[href]") && (r.width < 43.5 || r.height < 43.5)) targets.push(el.id || el.tagName);
      if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent?.trim())) continue;
      if (parseFloat(s.fontSize) < 13.95) small.push(el.tagName);
      if (el.scrollWidth > el.clientWidth + 1 || (s.textOverflow === "ellipsis" && el.scrollWidth > el.clientWidth)) clipped.push(el.textContent?.trim().slice(0, 60) ?? "");
      contrast.push(ratio(rgb(s.color), background(el)));
    }
    const switches = [...scope.querySelectorAll('[role="switch"]')].map((el) => {
      const track = el.firstElementChild!; const thumb = track.firstElementChild!;
      const ts = getComputedStyle(track); const bs = getComputedStyle(thumb);
      return { border: ratio(rgb(ts.borderTopColor), background(el)), thumb: Math.max(ratio(rgb(bs.backgroundColor), background(track)),
        bs.borderTopStyle !== "none" && parseFloat(bs.borderTopWidth) > 0 ? ratio(rgb(bs.borderTopColor), background(track)) : 0) };
    });
    return { small, clipped, targets, minContrast: Math.min(...contrast), switches,
      overflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - innerWidth };
  });
}

async function measure(page: Page, root: Locator, state: string, axeScope: string) {
  const g = await geometry(root);
  expect(g.small, `${state}: tekst ≥ 14 px`).toEqual([]);
  expect(g.clipped, `${state}: volledige tekst`).toEqual([]);
  expect(g.targets, `${state}: doelen ≥ 44 px`).toEqual([]);
  expect(g.overflow, `${state}: paginaoverloop`).toBeLessThanOrEqual(0);
  expect(g.minContrast, `${state}: tekstcontrast`).toBeGreaterThanOrEqual(4.5);
  for (const sw of g.switches) {
    expect(sw.border, `${state}: spoorrandcontrast`).toBeGreaterThanOrEqual(3);
    expect(sw.thumb, `${state}: knopcontrast`).toBeGreaterThanOrEqual(3);
  }
  const a = await new AxeBuilder({ page: page as unknown as ConstructorParameters<typeof AxeBuilder>[0]["page"] }).include(axeScope).withTags(TAGS).analyze();
  expect(a.violations.map((v) => v.id), `${state}: axe ${JSON.stringify(a.violations)}`).toEqual([]);
  const out = process.env.MG_REVIEW_DIR;
  if (out) {
    mkdirSync(out, { recursive: true });
    writeFileSync(path.join(out, `${state}.json`), JSON.stringify({ ...g, axeScope, axe: a.violations }, null, 2));
    // De eenmalige sleutel wordt nooit als afbeelding opgeslagen.
    await page.screenshot({ path: path.join(out, `${state}.png`), fullPage: true, mask: [page.getByTestId("apikey-value")] });
  }
}

async function focusVisible(locator: Locator) {
  await expect(locator).toBeFocused();
  // transition-colors bevat ook outline-color. Forceer stijlberekening en wacht op de
  // werkelijk lopende animaties, ook bij reduced motion (0,01 ms ≠ al getekend).
  // Geen vaste wachttijd, herkansing of lagere contrastgrens.
  await locator.evaluate(async (el) => {
    for (;;) {
      void getComputedStyle(el).outlineColor;
      const moving = el.getAnimations().filter((a) => a.playState === "running" && a.effect?.getTiming().iterations !== Infinity);
      if (!moving.length) break;
      await Promise.allSettled(moving.map((animation) => animation.finished));
    }
  });
  const f = await locator.evaluate((el) => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    const top = Math.max(0, r.top); const bottom = Math.min(innerHeight, r.bottom);
    const point = document.elementFromPoint(r.x + r.width / 2, (top + bottom) / 2);
    const rgb = (c: string) => (c.match(/[\d.]+/g) ?? []).map(Number);
    const lum = (c: number[]) => c.map((v) => v / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
      .reduce((v, c, i) => v + c * [.2126, .7152, .0722][i], 0);
    const background = (node: Element | null): number[] => {
      if (!node) return [255, 255, 255];
      const c = rgb(getComputedStyle(node).backgroundColor); const alpha = c[3] ?? 1;
      return alpha === 1 ? c.slice(0, 3) : background(node.parentElement).map((v, i) => c[i] * alpha + v * (1 - alpha));
    };
    const ratio = (a: number[], b: number[]) => (Math.max(lum(a), lum(b)) + .05) / (Math.min(lum(a), lum(b)) + .05);
    // Peil C40: buitenring naast het omringende vlak, binnenhalo naast de knop
    // en de twee lagen naast elkaar. Meet de gerenderde kleuren, niet alleen tokens.
    const surrounding = background(el.parentElement);
    const rawOutline = rgb(s.outlineColor); const alpha = rawOutline[3] ?? 1;
    const outline = rawOutline.slice(0, 3).map((v, i) => v * alpha + surrounding[i] * (1 - alpha));
    const haloColor = s.boxShadow.match(/rgba?\([^)]+\)/)?.[0];
    const halo = haloColor ? rgb(haloColor).slice(0, 3) : null;
    const outerContrast = ratio(outline, surrounding);
    return { visible: top < bottom && !!point && (el === point || el.contains(point)),
      ring: s.outlineStyle === "solid" && parseFloat(s.outlineWidth) >= 3,
      outerContrast, haloToButton: halo ? ratio(halo, background(el)) : null,
      ringToHalo: halo ? ratio(outline, halo) : null,
      outline: s.outlineColor, shadow: s.boxShadow, offset: s.outlineOffset,
      background: surrounding, element: el.id || el.textContent?.trim() };
  });
  expect(f.visible).toBeTruthy(); expect(f.ring).toBeTruthy();
  // De volledige 3 px buitenring moet zelfstandig ≥ 3:1 halen; de halo
  // vervangt die eis niet. In forced colors laat de browser box-shadow weg.
  expect(f.outerContrast, JSON.stringify(f)).toBeGreaterThanOrEqual(3);
  if (f.ringToHalo !== null) expect(f.ringToHalo, JSON.stringify(f)).toBeGreaterThanOrEqual(3);
  const out = process.env.MG_REVIEW_DIR;
  if (out) {
    mkdirSync(out, { recursive: true });
    appendFileSync(path.join(out, "focus.jsonl"), JSON.stringify({ test: test.info().title, ...f }) + "\n");
    if (await locator.getAttribute("data-testid") === "apikey-create") {
      await locator.page().screenshot({ path: path.join(out, `${test.info().title}-focus-aanmaken.png`), fullPage: true });
    }
  }
}

for (const theme of ["light", "dark"] as const) for (const width of [320, 360, 390, 768, 1024, 1440]) {
  test(`ontwerpreview rechten ${theme} ${width}`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.goto("/login");
    await page.evaluate((t) => localStorage.setItem("mg.theme", t), theme);
    const themeHeaders = await csrf(page);
    expect((await page.request.put("/api/v1/auth/me/preferences", { headers: themeHeaders, data: { theme } })).ok()).toBeTruthy();
    await page.goto("/instellingen/api-sleutels");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const prefix = `${theme}-${width}`;
    const open = async () => {
      await page.getByTestId("apikey-new").click();
      return page.getByRole("dialog", { name: "Nieuwe sleutel" });
    };
    let dialog = await open();
    await expect(dialog.getByRole("button", { name: "Operator", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator("#apikey-scope-estop")).toHaveCount(0);
    await measure(page, dialog, `${prefix}-operator`, '[role="dialog"]');
    await dialog.getByLabel("Naam", { exact: true }).fill("review-operator");
    await dialog.getByRole("button", { name: "Kijker", exact: true }).click();
    const sw = dialog.getByRole("switch", { name: "Noodstop activeren", exact: true });
    await expect(sw).toHaveAttribute("aria-checked", "false");
    await expect(sw).toHaveAccessibleDescription(/nooit opheffen.*emergency_stop:on/);
    await expect(dialog.getByRole("group", { name: "Rechten (scopes)" })).toHaveAccessibleDescription(/Alles uit/);
    await measure(page, dialog, `${prefix}-kijker-uit`, '[role="dialog"]');

    // Volledige Tab-doorloop, inclusief focusval, ring en focus terug na Esc.
    await dialog.locator("#apikey-name").focus();
    for (const target of [dialog.getByRole("button", { name: "Operator", exact: true }), dialog.getByRole("button", { name: "Kijker", exact: true }), sw]) {
      await page.keyboard.press("Tab"); await focusVisible(target);
    }
    await page.keyboard.press("Space"); await expect(sw).toHaveAttribute("aria-checked", "true");
    await page.keyboard.press("Enter"); await expect(sw).toHaveAttribute("aria-checked", "false");
    // Datum heeft in Chromium meerdere native segmenten; loop door tot Annuleren.
    await page.keyboard.press("Tab"); await focusVisible(dialog.locator("#apikey-expires"));
    for (let i = 0; i < 8 && !(await dialog.getByRole("button", { name: "Annuleren", exact: true }).evaluate((e) => e === document.activeElement)); i++) await page.keyboard.press("Tab");
    await focusVisible(dialog.getByRole("button", { name: "Annuleren", exact: true }));
    await page.keyboard.press("Tab"); await focusVisible(page.getByTestId("apikey-create"));
    await page.keyboard.press("Tab"); await focusVisible(dialog.locator("#apikey-name"));
    await page.keyboard.press("Escape"); await expect(dialog).toHaveCount(0); await focusVisible(page.getByTestId("apikey-new"));

    dialog = await open();
    await dialog.getByLabel("Naam", { exact: true }).fill("review-operator");
    await dialog.getByRole("button", { name: "Kijker", exact: true }).click();
    await sw.click();
    await dialog.getByRole("button", { name: "Operator", exact: true }).click();
    await dialog.getByRole("button", { name: "Kijker", exact: true }).click();
    await expect(sw).toHaveAttribute("aria-checked", "false");
    await dialog.getByRole("button", { name: "Operator", exact: true }).click();
    const sent = page.waitForRequest((r) => r.url().endsWith("/api/v1/auth/api-keys") && r.method() === "POST");
    await page.getByTestId("apikey-create").click();
    expect((await sent).postDataJSON()).toEqual({ name: "review-operator", role: "operator" });
    await page.getByTestId("apikey-done").click();
    await expect(page.getByTestId("apikey-scopes-review-operator")).toHaveCount(0);

    dialog = await open();
    await dialog.getByLabel("Naam", { exact: true }).fill("review-hub");
    await dialog.getByRole("button", { name: "Kijker", exact: true }).click();
    await dialog.locator('label[for="apikey-scope-estop"]').click();
    await expect(sw).toHaveAttribute("aria-checked", "true");
    await measure(page, dialog, `${prefix}-kijker-aan`, '[role="dialog"]');
    if (width === 320) {
      const letter = await page.addStyleTag({ content: '[role="dialog"], [role="dialog"] * { font-family: "DejaVu Sans", sans-serif !important; }' });
      await measure(page, dialog, `${prefix}-dejavu`, '[role="dialog"]');
      const spacing = await page.addStyleTag({ content: '[role="dialog"] * { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; } [role="dialog"] p { margin-bottom: 2em !important; }' });
      await measure(page, dialog, `${prefix}-tekstafstand`, '[role="dialog"]');
      await spacing.evaluate((el) => el.parentNode?.removeChild(el)); await letter.evaluate((el) => el.parentNode?.removeChild(el));
    }

    // Punt 9: het spoor én de knop zelf zijn zichtbaar; alleen een aria-attribuut bewijst dat niet.
    // Axe op de schakelaar zelf; de hele dialoog krijgt axe in beide gewone thema's.
    // In forced colors gebruikt axe voor tekst -webkit-text-fill-color (de auteurskleur),
    // terwijl Chromium de tekst met de systeemkleur schildert. De DOM-contrastmeting
    // hieronder blijft daarom óók op alle werkelijk gerenderde dialoogtekst staan.
    await page.emulateMedia({ forcedColors: "active" });
    const thumbStyle = () => sw.locator("span > span").evaluate((el) => {
      const s = getComputedStyle(el); const track = getComputedStyle(el.parentElement!);
      return { left: el.getBoundingClientRect().left - el.parentElement!.getBoundingClientRect().left,
        thumb: s.backgroundColor, track: track.backgroundColor, border: s.borderTopStyle, borderWidth: parseFloat(s.borderTopWidth), borderColor: s.borderTopColor };
    });
    await measure(page, dialog, `${prefix}-forced-aan`, '[role="switch"]');
    const on = await thumbStyle();
    expect(on.thumb !== on.track || (on.border !== "none" && on.borderWidth > 0 && on.borderColor !== on.track), `de knop is zichtbaar tegen het spoor in geforceerde kleuren: ${JSON.stringify(on)}`).toBeTruthy();
    await dialog.getByRole("button", { name: "Kijker", exact: true }).focus();
    await page.keyboard.press("Tab"); await focusVisible(sw);

    await page.keyboard.press("Space");
    await measure(page, dialog, `${prefix}-forced-uit`, '[role="switch"]');
    const off = await thumbStyle();
    expect(off.thumb !== off.track || (off.border !== "none" && off.borderWidth > 0 && off.borderColor !== off.track)).toBeTruthy();
    expect(on.left - off.left).toBeGreaterThan(10);

    await page.keyboard.press("Space");
    await page.emulateMedia({ forcedColors: "none" });

    // Echte 422-antwoorden: verander alleen de uitgaande testbody, nooit de response.
    for (const [body, message, state] of [
      [{ name: "review-hub", role: "operator", scopes: [SCOPE] }, ROLE_ERROR, "fout-rol"],
      [{ name: "review-hub", role: "viewer", scopes: ["wanted:search"] }, UNKNOWN_ERROR, "fout-onbekend"],
    ] as const) {
      await page.route("**/api/v1/auth/api-keys", (route) => route.request().method() === "POST" ? route.continue({ postData: JSON.stringify(body) }) : route.continue());
      const response = page.waitForResponse((r) => r.url().endsWith("/api/v1/auth/api-keys") && r.request().method() === "POST");
      await page.getByTestId("apikey-create").click();
      const r = await response; expect(r.status()).toBe(422); expect(await r.json()).toEqual({ detail: message });
      await expect(dialog.getByRole("alert")).toHaveText(message);
      await expect(dialog.getByLabel("Naam", { exact: true })).toHaveValue("review-hub");
      await expect(sw).toHaveAttribute("aria-checked", "true");
      await measure(page, dialog, `${prefix}-${state}`, '[role="dialog"]');
      await page.unroute("**/api/v1/auth/api-keys");
    }
    await page.getByTestId("apikey-create").click();
    await expect(page.getByTestId("apikey-value")).toBeVisible();
    const key = await page.getByTestId("apikey-value").inputValue();
    // Geen schermafdruk van dit venster: de sleutel is een geheim, ook in de testopstelling.
    await page.getByTestId("apikey-done").click();
    const headers = await csrf(page);
    for (const name of ["review-lezer", "review-onbekend"]) expect((await page.request.post("/api/v1/auth/api-keys", { headers, data: { name, role: "viewer" } })).ok()).toBeTruthy();
    execFileSync(process.env.MG_E2E_PYTHON as string, ["-I", "-c",
      "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('UPDATE api_keys SET scopes=? WHERE name=?', ('[\"wanted:search\"]', 'review-onbekend')); c.commit()",
      path.join(process.env.MG_E2E_DATA_DIR as string, "mediaguard.db")]);
    await page.reload();
    await expect(page.getByTestId("apikey-scopes-review-hub")).toContainText("Noodstop activeren");
    const row = page.getByTestId("apikey-row-review-hub");
    await expect(row).toContainText("Kijker"); await expect(row).toContainText("nooit opheffen");
    await expect(page.getByTestId("apikey-scopes-review-lezer")).toHaveCount(0);
    await expect(page.getByTestId("apikey-scopes-review-onbekend")).toContainText("Onbekend recht: wanted:search");
    await measure(page, page.getByTestId("apikey-table"), `${prefix}-lijst`, '[data-testid="apikey-table"]');
    await page.emulateMedia({ forcedColors: "active" });
    await expect(page.locator('[data-testid="apikey-scopes-review-onbekend"] [data-status="unknown"]')).toHaveCSS("border-top-style", "dashed");
    await page.emulateMedia({ forcedColors: "none" });
    await row.getByRole("button", { name: "Intrekken", exact: true }).click();
    const danger = page.getByRole("alertdialog");
    await expect(danger).toContainText("Rechten (scopes)");
    await expect(danger.getByTestId("apikey-revoke-scopes")).toContainText("Noodstop activeren");
    await measure(page, danger, `${prefix}-intrekken`, '[role="alertdialog"]');
    await page.keyboard.press("Escape");

    // De herhaalde noodstop levert de nieuwe echte logboekregel, in beide talen zonder afkappen.
    expect((await page.request.post("/api/v1/system/emergency-stop", { headers, data: { active: true, reason: "ontwerpreview" } })).ok()).toBeTruthy();
    const reason = `ontwerpreview herhaling ${prefix}`;
    expect((await page.request.post("/api/v1/system/emergency-stop", { headers: { "X-Api-Key": key, Cookie: "" }, data: { active: true, reason } })).ok()).toBeTruthy();
    for (const locale of ["nl-NL", "en-US"]) {
      expect((await page.request.put("/api/v1/auth/me/locale", { headers, data: { locale } })).ok()).toBeTruthy();
      await page.goto("/activiteit?tab=geschiedenis&view=audit");
      await page.getByRole("textbox", { name: locale === "nl-NL" ? /^Filter op actie/ : /^Filter by action/ }).fill("system.emergency_stop_repeated");
      const event = page.locator(".audit-table tr").filter({ hasText: reason }).locator('.tag[title="system.emergency_stop_repeated"]');
      await expect(event).toHaveCount(1);
      await expect(event).toHaveText(locale === "nl-NL" ? "Noodstop stond al aan: opnieuw gevraagd, niets gewijzigd" : "Emergency stop already active: requested again, nothing changed");
      await measure(page, event.locator("xpath=ancestor::tr"), `${prefix}-log-${locale}`, ".audit-table");
    }
  });
}

// Draait ook bij een gerichte uitvoering van één matrixgeval met deze nacontrole.
test("ontwerpreview laat thema, taal, sleutels en noodstop teruggezet achter", async ({ page }) => {
  const me = await preferences(page);
  expect(me.preferences.theme ?? null).toBe(suiteTheme);
  expect(me.locale).toBe(suiteLocale);
  const keys = await (await page.request.get("/api/v1/auth/api-keys")).json() as { items: { name: string }[] };
  expect(keys.items.filter((key) => NAMES.includes(key.name))).toEqual([]);
  const statusResponse = await page.request.get("/api/v1/status");
  expect(statusResponse.ok()).toBeTruthy();
  const status = await statusResponse.json();
  expect(status.emergency_stop.active).toBe(false);
});
