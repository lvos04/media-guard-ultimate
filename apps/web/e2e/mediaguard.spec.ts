import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Runs serially against a real, freshly started core (see playwright.config.ts).
 * Order matters: the first test performs the one-time setup.
 */
test.describe.configure({ mode: "default" });

const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "page must not scroll horizontally").toBeLessThanOrEqual(1);
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

  test("360 px: kernpagina's passen", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 });
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

  test("verwijderen: preview, getypte bevestiging, extra waarschuwing zonder geslaagde controle", async ({ page }) => {
    const { seedQuarantine } = await import("./seed");
    const [verified, unchecked] = seedQuarantine();
    await login(page);
    await page.goto("/quarantaine");
    await expect(page.getByTestId(`q-item-${verified}`)).toContainText("Vrijgavecontrole: geslaagd");
    await expect(page.getByTestId(`q-item-${unchecked}`)).toContainText("nog niet gecontroleerd");

    // bulk preview → only the verified item is deletable unless forced
    await page.getByTestId(`q-item-${verified}`).getByRole("checkbox").check();
    await page.getByTestId(`q-item-${unchecked}`).getByRole("checkbox").check();
    await page.getByTestId("bulk-delete").click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("1 originelen definitief verwijderen?");
    await expect(dialog).toContainText("4 GB");
    await expect(dialog.getByTestId("force-unverified")).toBeVisible();
    await dialog.getByRole("button", { name: "Annuleren" }).click();

    // single delete of the unchecked item → 409 → explicit second warning → force
    await page.getByTestId(`q-item-${unchecked}`).getByTestId("row-menu").click();
    await page.getByTestId(`delete-${unchecked}`).click();
    let confirm = page.getByRole("alertdialog");
    const confirmButton = confirm.getByRole("button", { name: "Definitief verwijderen" });
    await expect(confirmButton).toBeDisabled();
    await confirm.getByLabel(/Typ VERWIJDER/).fill("VERWIJDER");
    await confirmButton.click();
    confirm = page.getByRole("alertdialog");
    await expect(confirm).toContainText("Vrijgavecontrole niet geslaagd.");
    await expect(confirm.getByRole("button", { name: "Toch verwijderen zonder controle" })).toBeDisabled();
    await confirm.getByLabel(/Typ VERWIJDER/).fill("VERWIJDER");
    await confirm.getByRole("button", { name: "Toch verwijderen zonder controle" }).click();
    await expect(page.getByText(/Verwijderen ingepland/)).toBeVisible();

    // purge preview
    await page.getByTestId("purge-button").click();
    await expect(page.getByRole("alertdialog")).toContainText("verlopen originelen opruimen?");
    await page.getByRole("alertdialog").getByRole("button", { name: "Annuleren" }).click();
  });
});
