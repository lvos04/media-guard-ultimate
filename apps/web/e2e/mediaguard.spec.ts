import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * Runs serially against a real, freshly started core (see playwright.config.ts).
 * Order matters: the first test performs the one-time setup.
 */
test.describe.configure({ mode: "default" });

import { ADMIN } from "./fixtures/admin";

test.afterEach(async ({ page }) => {
  // Een lopende route.fetch moet haar antwoord nog kunnen lezen vóór de testcontext sluit.
  await page.unrouteAll({ behavior: "wait" });
});

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth);
  expect(overflow, "page must not scroll horizontally").toBeLessThanOrEqual(1);
}

async function tabTo(page: Page, target: Locator) {
  await expect(target).toBeVisible();
  for (let i = 0; i < 60; i++) {
    if (await target.evaluate((element) => document.activeElement === element)) return;
    await page.keyboard.press("Tab");
  }
  await expect(target).toBeFocused();
}

async function contrastViolations(page: Page) {
  const result = await new AxeBuilder({ page: page as unknown as ConstructorParameters<typeof AxeBuilder>[0]["page"] }).withRules(["color-contrast"]).analyze();
  return result.violations.flatMap((v) => v.nodes.map((n) => `${n.target.join(" ")} — ${n.failureSummary ?? ""}`));
}

test("eerste installatie: beheerder aanmaken, taal kiezen, naar overzicht", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/setup$/);
  await expect(page.getByRole("heading", { name: "Welkom bij MediaGuard" })).toBeVisible();
  // client-side validation in Dutch
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord", { exact: true }).fill("kort");
  await page.getByLabel("Herhaal wachtwoord").fill("anders");
  await page.getByRole("button", { name: "Account aanmaken en doorgaan" }).click();
  await expect(page.getByText("Wachtwoord moet minimaal 12 tekens hebben")).toBeVisible();
  await expect(page.getByText("Wachtwoorden komen niet overeen")).toBeVisible();
  await page.getByLabel("Wachtwoord", { exact: true }).fill(ADMIN.password);
  await page.getByLabel("Herhaal wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Account aanmaken en doorgaan" }).click();
  await expect(page.getByRole("heading", { name: "Taal en uiterlijk" })).toBeVisible();
  await page.getByRole("button", { name: "Opslaan en verder" }).click();
  await expect(page.getByRole("heading", { name: "Klaar om te beginnen" })).toBeVisible();
  await page.getByRole("link", { name: "Later — naar het overzicht" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
  // Dutch UI strings present
  // Peil: 'Wacht op jou' heet nu 'Wat actie nodig heeft' (PEIL_MIGRATIE §6.7)
  for (const text of ["Beschikbaar", "Ontbrekend", "Fouten", "AV1-besparing", "Wat actie nodig heeft", "Nu bezig"]) {
    await expect(page.locator("main").getByText(text).first()).toBeVisible();
  }
  // setup is closed afterwards
  await page.goto("/setup");
  await expect(page.getByText("Installatie is al voltooid.")).toBeVisible();
});

test("inloggen en uitloggen", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill("fout-wachtwoord");
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.locator(".inline-state--danger")).toContainText("Onjuiste gebruikersnaam of wachtwoord");
  await login(page);
  await page.getByRole("button", { name: "Profiel" }).click();
  await page.getByRole("button", { name: "Uitloggen" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/instellingen");
  await expect(page).toHaveURL(/\/login\?next=%2Finstellingen/);
});

test("instelling wijzigen blijft bewaard na herladen", async ({ page }) => {
  await login(page);
  await page.goto("/instellingen?view=all");
  await page.getByTestId("settings-search").fill("jobs.max_concurrency");
  const field = page.locator('[data-setting="jobs.max_concurrency"]');
  await expect(field).toBeVisible();
  await field.getByRole("textbox").fill("6");
  await page.getByTestId("settings-save").click();
  await expect(page.getByText("1 instelling opgeslagen")).toBeVisible();
  await page.reload();
  await page.getByTestId("settings-search").fill("jobs.max_concurrency");
  await expect(page.locator('[data-setting="jobs.max_concurrency"]').getByRole("textbox")).toHaveValue("6");
  await expect(page.locator('[data-setting="jobs.max_concurrency"]')).toContainText("Gezet op Globaal");
});

test("ongeldige instelling toont fout en behoudt oude waarde", async ({ page }) => {
  await login(page);
  await page.goto("/instellingen?view=all");
  await page.getByTestId("settings-search").fill("jobs.max_concurrency");
  const field = page.locator('[data-setting="jobs.max_concurrency"]');
  await field.getByRole("textbox").fill("99");
  await page.getByTestId("settings-save").click();
  await expect(field.getByRole("alert")).toContainText("maximaal 32");
  await expect(field.getByRole("textbox")).toHaveAttribute("aria-invalid", "true");
  await page.reload();
  await page.getByTestId("settings-search").fill("jobs.max_concurrency");
  await expect(page.locator('[data-setting="jobs.max_concurrency"]').getByRole("textbox")).toHaveValue("6");
});

test("herstel standaard zet waarde terug", async ({ page }) => {
  await login(page);
  await page.goto("/instellingen?view=all");
  await page.getByTestId("settings-search").fill("jobs.max_concurrency");
  const field = page.locator('[data-setting="jobs.max_concurrency"]');
  await field.getByRole("button", { name: "Herstel standaard" }).click();
  await expect(page.getByText("Standaard hersteld")).toBeVisible();
  await expect(field.getByRole("textbox")).toHaveValue("4");
});

test("noodstop aan en uit", async ({ page }) => {
  await login(page);
  // noodstop staat in het profielmenu (niet meer altijd als rode knop in de kopbalk)
  await page.getByTestId("profile-button").click();
  await page.getByTestId("estop-button").click();
  await expect(page.getByTestId("profile-menu")).toHaveCount(0);
  await expect(page.getByRole("alertdialog", { name: "Noodstop activeren?" })).toBeVisible();
  await page.getByLabel("Reden (komt in de auditlog)").fill("E2E-test");
  await page.getByTestId("estop-confirm").click();
  await expect(page.getByTestId("estop-banner")).toContainText("Noodstop actief");
  await expect(page.getByTestId("estop-banner")).toContainText("E2E-test");
  await page.reload();
  await expect(page.getByTestId("estop-banner")).toBeVisible();
  await page.getByTestId("estop-banner").getByRole("button", { name: "Noodstop opheffen" }).click();
  await expect(page.getByTestId("estop-banner")).toHaveCount(0);
  // audited
  await page.goto("/instellingen/audit");
  await expect(page.getByText("Noodstop gewijzigd").first()).toBeVisible();
});

test("toetsenbordnavigatie: skip-link, / zoeken, g s, Esc", async ({ page }) => {
  await login(page);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Naar inhoud" })).toBeFocused();
  await page.locator("body").click({ position: { x: 700, y: 400 } });
  await page.keyboard.press("/");
  await expect(page.locator("[data-global-search]")).toBeFocused();
  await page.keyboard.press("Escape");
  await page.locator("body").click({ position: { x: 700, y: 400 } });
  await page.keyboard.press("g");
  await page.keyboard.press("s");
  await expect(page).toHaveURL(/\/instellingen$/);
  await page.keyboard.press("?");
  await expect(page.getByRole("dialog", { name: "Sneltoetsen" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Sneltoetsen" })).toHaveCount(0);
});

test("768 px: alle acties van het Overzicht zijn bereikbaar met zichtbare toetsenbordfocus", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 900 });
  await login(page);
  await page.goto("/");
  await expect(page.getByTestId("todo-card")).toBeVisible();
  await expect(page.getByTestId("storage-ledger-line")).toBeVisible();
  // Tel pas als de asynchrone actierijen en de ingestelde instanties in de DOM staan.
  await expect(page.getByTestId("status-pill")).toHaveText(/^Aandacht \d+$/);
  const instances = await (await page.request.get("/api/v1/instances")).json() as Array<{ name: string; archived?: boolean }>;
  for (const instance of instances.filter(row => !row.archived)) {
    await expect(page.getByTestId("status-hero").getByRole("link").filter({ hasText: instance.name }).first()).toBeVisible();
  }
  const main = page.getByRole("main");
  const controls = main.locator('a[href], button, summary, [role="button"]');
  const expected = await controls.evaluateAll((elements) => elements.flatMap((el, index) =>
    el instanceof HTMLElement && el.getClientRects().length && !el.matches(":disabled") ? [index] : []));
  expect(expected.length).toBeGreaterThan(6);
  const visited = new Set<number>();
  for (let i = 0; i < 60 && visited.size < expected.length; i++) {
    await page.keyboard.press("Tab");
    const focus = await controls.evaluateAll((elements) => {
      const el = document.activeElement;
      if (!(el instanceof HTMLElement) || !elements.includes(el)) return null;
      const css = getComputedStyle(el);
      const box = el.getBoundingClientRect();
      const hit = document.elementFromPoint(box.left + box.width / 2, box.top + Math.min(12, box.height / 2));
      return { index: elements.indexOf(el), name: el.textContent?.trim(),
        ring: el.matches(":focus-visible") && css.outlineStyle !== "none"
          && parseFloat(css.outlineWidth) >= 3 && css.outlineColor !== "rgba(0, 0, 0, 0)",
        uncovered: Boolean(hit && el.contains(hit)) };
    });
    if (!focus) continue;
    expect(focus.ring, focus.name).toBe(true);
    expect(focus.uncovered, focus.name).toBe(true);
    visited.add(focus.index);
  }
  expect([...visited].sort((a, b) => a - b)).toEqual(expected);
  await noHorizontalScroll(page);
  const navigation = page.getByRole("navigation", { name: "Mobiele navigatie" });
  for (const [name, route] of [["Activiteit", "/activiteit"], ["Bibliotheek", "/bibliotheek"], ["Ruimte", "/av1"],
    ["Aanvragen", "/aanvragen"], ["Overzicht", "/"]]) {
    await tabTo(page, navigation.getByRole("link", { name, exact: true }));
    await page.keyboard.press("Enter");
    await expect.poll(() => new URL(page.url()).pathname).toBe(route);
    await expect(main.getByRole("heading", { level: 1 })).toBeVisible();
    await noHorizontalScroll(page);
  }
});

for (const theme of ["light", "dark"] as const) {
  test(`768 px: Systeemstatus toont bronuitval, veroudering en herstel · ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 900 });
    await page.emulateMedia({ colorScheme: theme });
    await page.clock.install();
    await login(page);
    const routes = ["status", "attention", "provisioning/status", "migration/runs", "seerr/settings",
      "jobs?status=failed&limit=20", "jobs?status=running&limit=5", "wanted/kpis", "health/summary",
      "library/kpis", "notifications/count", "recovery/readiness"];
    let meettijd = await page.evaluate(() => Date.now());
    let mode: "fresh" | "stale" | "offline" = "fresh";
    // Lees de lokale testkern vooraf. De gecontroleerde browserklok dateert elk volgend antwoord.
    for (const pad of routes) {
      const url = new URL("/api/v1/" + pad, page.url());
      const response = await page.request.get(url.href);
      expect(response.ok(), url.pathname).toBeTruthy();
      const data = await response.json();
      await page.route(link => link.origin === url.origin && link.pathname === url.pathname &&
        link.searchParams.get("status") === url.searchParams.get("status"), route => {
        const tijd = new Date(meettijd);
        const json = pad === "status" ? { ...data, time: tijd.toISOString() }
          : data && typeof data === "object" && "generated_at" in data ? { ...data, generated_at: tijd.toISOString() } : data;
        return route.fulfill({ json, headers: { date: tijd.toUTCString() } });
      });
    }
    const volgendeMeting = () => {
      meettijd += 60_000;
      return page.clock.fastForward(60_000);
    };
    await page.route("**/api/v1/instances", (route) => route.fulfill({ json: [{
      id: "testfilms", name: "Testfilms", app: "radarr", status: "healthy", last_check_at: "2025-01-01T00:00:00Z",
    }] }));
    await page.route("**/api/v1/encoder/health", (route) => route.fulfill(mode === "offline"
      ? { status: 503, json: { detail: "Testbron reageert niet" } }
      : { json: { status: "ok" }, headers: mode === "stale" ? { "X-MediaGuard-Stale": "90" } : {} }));
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await page.addStyleTag({ content: 'body { font-family: "DejaVu Sans", sans-serif !important; }' });
    const panel = page.getByRole("region", { name: "Systeemstatus" });
    const encoder = panel.getByRole("link", { name: /AV1-engine/ });
    const core = panel.getByRole("link", { name: /MediaGuard-core/ });
    const instance = panel.getByRole("link", { name: /Testfilms/ });
    const healthy = encoder.locator('[data-status="healthy"]');
    const stale = encoder.locator('[data-status="stale"]');
    await expect(healthy).toHaveText("Gezond");
    const healthyColor = await healthy.evaluate((element) => getComputedStyle(element).color);
    await expect(core.locator('[data-status="healthy"]')).toBeVisible();
    await expect(instance.locator('[data-status="stale"]')).toHaveText("Verouderd");
    mode = "stale";
    await volgendeMeting();
    await expect(stale).toHaveText("Verouderd");
    await expect(stale).toHaveCSS("border-top-style", "dashed");
    await expect(stale).not.toHaveCSS("color", healthyColor);
    await expect(healthy).toHaveCount(0);
    await expect(encoder).toContainText("Laatst bekend");
    await expect(core.locator('[data-status="healthy"]')).toBeVisible();
    const todo = page.getByTestId("todo-card");
    await expect(todo).toContainText("Je hebt de code nodig om een versleutelde MediaGuard-herstelbundel te openen.");
    await expect(todo).not.toContainText("alles na een schijfcrash terug kan");
    await noHorizontalScroll(page);
    if (process.env.MG_PEIL_680_SHOTS) {
      await page.screenshot({ path: `${process.env.MG_PEIL_680_SHOTS}/overzicht-768-${theme}.png`, animations: "disabled" });
    }
    mode = "offline";
    await volgendeMeting();
    await expect(stale).toBeVisible();
    await expect(healthy).toHaveCount(0);
    mode = "fresh";
    await tabTo(page, panel.getByRole("button", { name: "Opnieuw proberen" }));
    await page.keyboard.press("Enter");
    await expect(healthy).toBeVisible();
    await page.addStyleTag({ content: `main * {
      line-height: 1.5 !important;
      letter-spacing: 0.12em !important;
      word-spacing: 0.16em !important;
    }` });
    for (const width of [768, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await noHorizontalScroll(page);
      const overflow = await panel.getByRole("link").evaluateAll((rows) =>
        Math.max(...rows.map((row) => row.scrollWidth - row.clientWidth)));
      expect(overflow, `statusrijen met vergrote tekstafstand op ${width} px`).toBeLessThanOrEqual(1);
    }
  });
}

test("768 px: Overzicht ververst de eigen bronnen en toont geen onbevestigde voortgang", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 900 });
  await page.clock.install();
  const measuredAt = () => page.evaluate(() => new Date().toISOString());
  await page.route("**/api/v1/status", async route => {
    const reply = await route.fetch();
    return route.fulfill({ json: { ...await reply.json(), time: await measuredAt() } });
  });
  let available = 7;
  let wanted: number | null = null;
  let progress: number | null = null;
  let jobsOffline = false;
  await page.route("**/api/v1/library/kpis", async (route) => route.fulfill({ json: { generated_at: await measuredAt(), totals: {
    available, total: 42, missing: 24, unmonitored: 0, upcoming: 0,
  } } }));
  await page.route("**/api/v1/wanted/kpis", (route) => route.fulfill(wanted === null
    ? { status: 503, json: { detail: "Testbron reageert niet" } }
    : { json: { missing: wanted, missing_aired_7d: 0, resolved_7d: 0 } }));
  await page.route("**/api/v1/encoding/jobs", async (route) => route.fulfill(jobsOffline
    ? { status: 503, json: { detail: "Testbron reageert niet" } }
    : { json: [{ id: 680, status: "running", updated_at: await measuredAt(), source_path: "Testfilm.mkv", progress }] }));
  await login(page);
  const main = page.getByRole("main");
  const library = main.locator(".kpi").filter({ hasText: "Beschikbaar" });
  const missing = main.locator(".kpi").filter({ hasText: "Ontbrekend" });
  const activity = main.getByRole("region", { name: "Nu bezig" });
  await expect(library.locator(".kpi-value")).toHaveText("7");
  await expect(missing.locator('[data-status="unknown"]')).toBeVisible();
  await expect(missing.locator(".kpi-value")).toHaveCount(0);
  await expect(activity.getByText("Voortgang onbekend")).toBeVisible();
  await expect(activity.getByRole("progressbar")).toHaveCount(0);
  available = 8;
  wanted = 16;
  progress = 0.42;
  await page.clock.fastForward(30_001);
  await expect(library.locator(".kpi-value")).toHaveText("8");
  await expect(missing.locator(".kpi-value")).toHaveText("16");
  await expect(activity.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
  wanted = null;
  jobsOffline = true;
  await page.clock.fastForward(30_001);
  await expect(missing.locator('[data-status="stale"]')).toBeVisible();
  await expect(missing.locator(".kpi-value")).toHaveText("16");
  await expect(library.locator('[data-status="stale"]')).toHaveCount(0);
  await expect(activity.getByText("Voortgang onbekend")).toBeVisible();
  await expect(activity.getByRole("progressbar")).toHaveCount(0);
  await noHorizontalScroll(page);
});

test("thema: standaard Systeem, wisselen (Donker → Licht) blijft bewaard, ook server-side per gebruiker", async ({ page }) => {
  // Peil (ADR-0016 punt 5): standaard Systeem; het script zet altijd het opgeloste thema
  await page.emulateMedia({ colorScheme: "dark" });
  await login(page);
  await expect(page.locator("html")).toHaveAttribute("data-theme-choice", "system");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByTestId("profile-button").click();
  await page.getByTestId("theme-choice").getByRole("button", { name: "Donker" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.keyboard.press("Escape");
  const saved = page.waitForResponse((r) => r.url().includes("/api/v1/auth/me/preferences") && r.request().method() === "PUT");
  await page.getByTestId("profile-button").click();
  await page.getByTestId("theme-choice").getByRole("button", { name: "Licht" }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("profile-menu")).toHaveCount(0);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect((await saved).ok()).toBeTruthy();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  // stored per user on the server: survives clearing this browser's cache
  await page.evaluate(() => window.localStorage.removeItem("mg.theme"));
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Profiel" }).click();
  await page.getByRole("button", { name: "Donker" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("contrast (axe color-contrast) donker en licht", async ({ page }) => {
  await login(page);
  for (const theme of ["dark", "light"] as const) {
    // per-user preference via the profile menu (stored server-side; wins over localStorage)
    await page.getByRole("button", { name: "Profiel" }).click();
    await page.getByRole("button", { name: theme === "dark" ? "Donker" : "Licht", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await page.getByRole("button", { name: "Profiel" }).click();
    for (const path of ["/", "/instellingen", "/ontbrekend", "/instellingen/instanties", "/migratie", "/anime", "/inrichten"]) {
      await page.goto(path);
      await page.waitForTimeout(800);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      expect(await contrastViolations(page), `${theme} ${path}`).toEqual([]);
    }
  }
  await page.getByRole("button", { name: "Profiel" }).click();
  await page.getByRole("button", { name: "Donker", exact: true }).click();
});

test("pagina's laden met eerlijke staten (geen nepdata)", async ({ page }) => {
  await login(page);
  for (const path of ["/bibliotheek", "/ontbrekend", "/kalender", "/release-lab", "/av1", "/dolby-vision", "/audio", "/anime", "/migratie", "/inrichten", "/downloads", "/seerr", "/kwaliteit", "/jobs", "/quarantaine", "/instellingen/instanties", "/instellingen/gebruikers", "/instellingen/geheimen", "/ai", "/systeem"]) {
    await page.goto(path);
    await expect(page.locator("h1").first()).toBeVisible();
    await expect(page.locator(".state--error")).toHaveCount(0);
  }
});

test.describe("mobiel", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

  test("bottom nav, profielmenu en geen horizontale scroll", async ({ page }) => {
    await login(page);
    const bottom = page.getByRole("navigation", { name: "Mobiele navigatie" });
    await expect(bottom).toBeVisible();
    await expect(page.locator(".sidebar")).toBeHidden();
    await noHorizontalScroll(page);
    // onderste balk: de vijf onderdelen; Instellingen via het profielmenu
    await expect(bottom.getByRole("link")).toHaveText(["Overzicht", "Activiteit", "Bibliotheek", "Ruimte", "Aanvragen"]);
    await bottom.getByRole("link", { name: "Activiteit" }).click();
    await expect(page).toHaveURL(/\/activiteit$/);
    await noHorizontalScroll(page);
    await page.getByTestId("profile-button").click();
    await page.getByRole("menuitem", { name: "Instellingen" }).click();
    await expect(page).toHaveURL(/\/instellingen$/);
    await noHorizontalScroll(page);
  });

  for (const width of [360, 768]) test(`${width} px: kernpagina's passen`, async ({ page }) => {
    await page.setViewportSize({ width, height: 780 });
    await login(page);
    for (const path of ["/", "/bibliotheek", "/ontbrekend", "/instellingen", "/instellingen/instanties", "/jobs", "/dolby-vision", "/ai", "/systeem"]) {
      await page.goto(path);
      await page.waitForTimeout(500);
      await noHorizontalScroll(page);
    }
  });
});

test.describe("quarantaine (opslag schaars)", () => {
  test("gebruiksbalk op de pagina, bewaartermijn op één plek in Instellingen", async ({ page }) => {
    await login(page);
    await page.goto("/quarantaine");
    await expect(page.getByTestId("quarantine-usage")).toContainText("Quarantaine");
    await page.getByTestId("settings-link").getByRole("link", { name: /Originelen & herstellen/ }).click();
    await expect(page).toHaveURL(/\/instellingen\?open=originelen/);
    const field = page.locator('[data-setting="quarantine.retention_days"]');
    await expect(field).toBeVisible();
    await field.getByRole("textbox").fill("10");
    await page.getByTestId("settings-save").click();
    await expect(page.getByText("1 instelling opgeslagen")).toBeVisible();
    await page.goto("/quarantaine");
    await expect(page.getByTestId("quarantine-usage")).toContainText("bewaartermijn 10 dagen");
  });

  test("quarantainemodus: alleen in Instellingen, met bevestiging; Quarantaine toont de echte stand", async ({ page }) => {
    await login(page);
    await page.goto("/quarantaine");
    await expect(page.getByTestId("quarantine-mode")).toHaveAttribute("data-mode", "retain");
    await page.goto("/instellingen?open=originelen#originelen");
    const mode = page.locator('[data-setting="quarantine.mode"]').getByRole("combobox");
    await mode.selectOption("immediate_after_check");
    // eerst een bevestiging met uitleg (H1)
    const dialog = page.getByRole("alertdialog", { name: "Originelen direct verwijderen?" });
    await expect(dialog).toContainText("niet meer terugzetten");
    await dialog.getByRole("button", { name: "Ja, direct verwijderen" }).click();
    await page.getByTestId("settings-save").click();
    await expect(page.getByText("1 instelling opgeslagen")).toBeVisible();
    await expect(page.locator('[data-setting="quarantine.retention_days"]').getByRole("textbox")).toBeDisabled();
    await expect(page.getByTestId("safety-warning")).toBeVisible();
    await page.goto("/quarantaine");
    await expect(page.getByTestId("quarantine-mode")).toHaveAttribute("data-mode", "immediate");
    // standaard staat het vangnet aan (48 uur): dan nooit 'terugzetten is niet mogelijk' (rapport 6, N8)
    await expect(page.getByTestId("quarantine-mode")).toContainText("vangnet");
    await expect(page.getByTestId("quarantine-mode")).not.toContainText("terugzetten is niet mogelijk");
    // terug naar de veilige standaard via de waarschuwing
    await page.goto("/instellingen");
    await page.getByTestId("safety-warning").getByRole("button", { name: "Veilige standaard herstellen" }).click();
    await expect(page.getByText("Veilige standaard hersteld")).toBeVisible();
    await page.goto("/instellingen?open=originelen#originelen");
    await expect(page.locator('[data-setting="quarantine.retention_days"]').getByRole("textbox")).toBeEnabled();
  });

  test("de enige kopie in quarantaine blijft bewaard bij een lege verwijderselectie", async ({ page }) => {
    const { seedQuarantine } = await import("./seed");
    const [, enigeKopie] = seedQuarantine();
    await login(page);
    await page.goto("/quarantaine");
    await page.getByTestId(`q-item-${enigeKopie}`).getByRole("checkbox").check();
    const bevestigingen: boolean[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/quarantine/bulk-delete") {
        bevestigingen.push(request.postDataJSON().confirm);
      }
    });
    await page.getByTestId("bulk-delete").click();
    const uitleg = page.getByRole("dialog", { name: "Geen bestanden om te verwijderen" });
    await expect(uitleg).toContainText("enige kopie");
    await expect(uitleg.getByRole("textbox")).toHaveCount(0);
    await expect(uitleg.getByRole("button")).toHaveCount(1);
    await uitleg.screenshot({ path: test.info().outputPath("beschermd-origineel.png") });
    await uitleg.getByRole("button", { name: "Sluiten" }).click();
    await expect(uitleg).toHaveCount(0);
    await expect(page.getByTestId(`q-item-${enigeKopie}`)).toBeVisible();
    expect(bevestigingen).toEqual([false]);
  });

  test("verwijderen: preview, getypte bevestiging, extra waarschuwing zonder geslaagde controle", async ({ page }) => {
    const { seedQuarantine } = await import("./seed");
    const [verified, unchecked] = seedQuarantine();
    await login(page);
    await page.goto("/quarantaine");
    await expect(page.getByTestId(`q-item-${verified}`)).toContainText("Vrijgavecontrole: geslaagd");
    await expect(page.getByTestId(`q-item-${unchecked}`)).toContainText("nog niet gecontroleerd");

    // bulk preview → only the verified item is deletable; the unchecked original is the only
    // copy and stays, also when forced (HARDE EIS, S-P3-676 R-HARDE-EIS-6)
    await page.getByTestId(`q-item-${verified}`).getByRole("checkbox").check();
    await page.getByTestId(`q-item-${unchecked}`).getByRole("checkbox").check();
    await page.getByTestId("bulk-delete").click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("1 originelen definitief verwijderen?");
    await expect(dialog).toContainText("4 GB");
    await expect(dialog).toContainText("Blijft bewaard");
    await expect(dialog).toContainText("1 originelen: vrijgavecontrole niet geslaagd, dit is de enige kopie");
    await expect(dialog.getByTestId("force-unverified")).toHaveCount(0);
    await dialog.getByRole("button", { name: "Annuleren" }).click();

    // single delete of the unchecked item → 409 → kept, no forced second step
    await page.getByTestId(`q-item-${unchecked}`).getByTestId("row-menu").click();
    await page.getByTestId(`delete-${unchecked}`).click();
    let confirm = page.getByRole("alertdialog");
    const confirmButton = confirm.getByRole("button", { name: "Definitief verwijderen" });
    await expect(confirmButton).toBeDisabled();
    await confirm.getByLabel(/Typ VERWIJDER/).fill("VERWIJDER");
    await confirmButton.click();
    confirm = page.getByRole("alertdialog");
    await expect(confirm).toContainText("Origineel blijft bewaard");
    await expect(confirm.getByRole("button", { name: "Toch verwijderen zonder controle" })).toHaveCount(0);
    await confirm.getByRole("button", { name: "Annuleren" }).click();
    await expect(page.getByTestId(`q-item-${unchecked}`)).toBeVisible();

    // purge preview
    await page.getByTestId("purge-button").click();
    await expect(page.getByRole("alertdialog")).toContainText("verlopen originelen opruimen?");
    await page.getByRole("alertdialog").getByRole("button", { name: "Annuleren" }).click();
  });
});
