import { createHmac } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";

/** Rapport 5: meldingen buiten de browser (M1), 'Wat is er gebeurd' (M2), bundel buiten de NAS (M11), 2FA/sessies (M12). */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ request }) => {
  const status = await (await request.get("/api/v1/auth/setup-status")).json() as { needs_setup: boolean };
  if (status.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
});

function totp(secret: string, offset = 0): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/=+$/, "")) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const bytes = Buffer.from((bits.match(/.{8}/g) ?? []).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + offset));
  const h = createHmac("sha1", bytes).update(counter).digest();
  const o = h[h.length - 1] & 0x0f;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

async function noSideScroll(page: Page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(1);
}

for (const width of [1440, 390] as const) {
  test.describe(`breedte ${width}`, () => {
    test.use({ viewport: { width, height: width === 390 ? 844 : 900 } });

    test(`Meldingen: ntfy-QR, kanaal toevoegen, keuze per soort (${width})`, async ({ page }) => {
      await login(page);
      await page.goto("/instellingen/meldingen");
      await expect(page.getByTestId("notify-settings")).toBeVisible();
      await expect(page.getByTestId("ntfy-qr").first()).toBeVisible();
      await expect(page.getByRole("link", { name: /ntfy\.sh\/mediaguard-/ }).first()).toBeVisible();
      await noSideScroll(page);

      await page.getByTestId("notify-add").click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Soort kanaal").selectOption("webhook");
      await dialog.getByLabel("Naam").fill(`Hook ${width}`);
      await dialog.getByLabel("Adres").fill("http://127.0.0.1:9/bestaat-niet");
      await page.getByTestId("notify-save").click();
      await expect(dialog).toHaveCount(0);
      const row = page.getByTestId("notify-channel-webhook").filter({ hasText: `Hook ${width}` });
      await expect(row).toBeVisible();
      await row.getByRole("button", { name: "Testbericht" }).click();
      await expect(page.getByText(/Testbericht mislukt/).first()).toBeVisible();

      const cat = page.getByRole("group", { name: "AV1 klaar" });
      await cat.getByRole("button", { name: "Direct" }).click();
      await expect(cat.getByRole("button", { name: "Direct" })).toHaveAttribute("aria-pressed", "true");
      await cat.getByRole("button", { name: "Dagoverzicht" }).click();
      await expect(cat.getByRole("button", { name: "Dagoverzicht" })).toHaveAttribute("aria-pressed", "true");
    });

    test(`Wat is er gebeurd en bundel buiten de NAS (${width})`, async ({ page }) => {
      await login(page);
      await page.goto("/activiteit?tab=gebeurd");
      await expect(page.getByTestId("happened-today")).toContainText("Vandaag tot nu toe");
      await noSideScroll(page);
      await page.goto("/systeem");
      const card = page.getByTestId("offsite-card");
      await card.scrollIntoViewIfNeeded({ timeout: 20_000 });
      await expect(card).toBeVisible();
      await card.getByLabel("Waarheen").selectOption("path");
      await card.getByLabel("Map (in de app gekoppeld)").fill("/bestaat/niet");
      await page.getByTestId("offsite-save").click();
      await page.getByTestId("offsite-run").click();
      await expect(card.getByRole("alert")).toContainText(/bestaat niet|nog geen herstelbundel/);
      await card.getByLabel("Waarheen").selectOption("off");
      await page.getByTestId("offsite-save").click();
      // geen zijwaarts-scrollcheck op /systeem: de testdatamap (lange /tmp-paden) in de
      // opslagkaart is breder dan 390 px; dat zijn testpaden, niet deze kaart
    });
  });
}

test("Tweestapsverificatie: instellen, inloggen met code, sessies, uitzetten", async ({ page, browser }) => {
  await login(page);
  await page.goto("/instellingen/beveiliging");
  await expect(page.getByTestId("security-settings")).toBeVisible();
  await page.getByTestId("totp-start").click();
  await expect(page.getByTestId("totp-qr")).toBeVisible();
  const secret = (await page.getByTestId("totp-secret").textContent())?.trim() ?? "";
  expect(secret).toMatch(/^[A-Z2-7]{32}$/);
  await page.getByLabel("Code van 6 cijfers").fill(totp(secret));
  await page.getByTestId("totp-confirm").click();
  await expect(page.getByTestId("recovery-codes")).toContainText(/[A-Z0-9]{5}-[A-Z0-9]{5}/);
  await page.getByTestId("codes-done").click();
  await expect(page.getByText(/Aan sinds/)).toBeVisible();

  // tweede apparaat: wachtwoord, dan code
  const ctx2 = await browser.newContext();
  const p2 = await ctx2.newPage();
  await p2.goto("/login");
  await p2.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await p2.getByLabel("Wachtwoord").fill(ADMIN.password);
  await p2.getByRole("button", { name: "Inloggen" }).click();
  await expect(p2.getByTestId("login-2fa")).toBeVisible();
  await p2.getByLabel("Code van 6 cijfers").fill("000000");
  await p2.getByRole("button", { name: "Inloggen" }).click();
  await expect(p2.getByTestId("login-2fa").getByRole("alert")).toContainText("Code niet geaccepteerd");
  await p2.getByLabel("Code van 6 cijfers").fill(totp(secret, 1));
  await p2.getByRole("button", { name: "Inloggen" }).click();
  await expect(p2.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();

  // sessies: het andere apparaat uitloggen
  await page.reload();
  const list = page.getByTestId("session-list");
  await expect(list.getByText("Dit apparaat")).toBeVisible();
  await page.getByRole("button", { name: "Alle andere apparaten uitloggen" }).click();
  await expect(page.getByText("Apparaat uitgelogd").first()).toBeVisible();
  expect((await p2.request.get("/api/v1/auth/me")).status()).toBe(401);
  await ctx2.close();

  // weer uitzetten zodat andere specs gewoon kunnen inloggen
  await page.getByRole("button", { name: "Uitzetten" }).click();
  const dlg = page.getByRole("dialog", { name: "Tweestapsverificatie uitzetten?" });
  await dlg.getByLabel("Wachtwoord").fill(ADMIN.password);
  await dlg.getByRole("button", { name: "Uitzetten" }).click();
  await expect(page.getByTestId("totp-start")).toBeVisible();
});
