import { expect, test, type Page } from "@playwright/test";
import { ADMIN } from "./fixtures/admin";

/** Instellingen openen eenvoudig: Systeem & prestaties bovenaan, Geavanceerd per sectie,
 *  zoeken gaat naar 'Alle instellingen', geen horizontale scroll op 412 px. */

test.beforeAll(async ({ request }) => {
  const status = await (await request.get("/api/v1/auth/setup-status")).json() as { needs_setup: boolean };
  if (status.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
});

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

test("Pooltoken blijft gebonden aan het Hub-adres uit het open formulier", async ({ page }) => {
  await login(page);
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  const key = "system.pool_hub_url";
  const secret = "hub:poolstatus";
  const kiesAdres = async (value?: string) => {
    const change = value ? { key, value } : { key, reset: true };
    const res = await page.request.patch("/api/v1/settings/scoped", {
      data: { changes: [change] }, headers: { "X-CSRF-Token": me.csrf_token },
    });
    expect(res.status()).toBe(200);
  };
  const dialog = page.getByRole("dialog");
  const open = async (origin: string) => {
    await page.getByRole("button", { name: "Geheim instellen" }).click();
    await expect(dialog.getByText(/Gebruik hub:poolstatus/)).toBeVisible();
    await dialog.getByLabel("Naam", { exact: true }).fill(secret);
    await expect(dialog.getByText(origin, { exact: false })).toBeVisible();
    await expect(dialog.getByLabel("Waarde")).toHaveValue("");
  };
  const opslaan = async (status: number) => {
    await dialog.getByLabel("Waarde").fill("proefwaarde");
    const antwoord = page.waitForResponse((r) =>
      r.request().method() === "PUT" && decodeURIComponent(r.url()).endsWith("/secrets/" + secret));
    await dialog.getByRole("button", { name: "Opslaan" }).click();
    expect((await antwoord).status()).toBe(status);
  };
  const eerste = "https://hub.example.test";
  const tweede = "https://andere-hub.example.test";
  try {
    await kiesAdres(eerste);
    await page.goto("/instellingen/geheimen");
    await open(eerste);
    await kiesAdres(tweede);
    await opslaan(409);
    await expect(page.getByText(/Het gekozen Hub-adres is gewijzigd/)).toBeVisible();
    await open(tweede);
    await opslaan(200);
    await expect(page.getByRole("row").filter({ hasText: secret })).toBeVisible();
  } finally {
    await kiesAdres();
  }
});

test("Instellingen: eenvoudige weergave met Systeem & prestaties", async ({ page }) => {
  await page.setViewportSize({ width: 412, height: 900 });
  await page.route("**/api/v1/encoding/resources", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    desired: { cpu_percent: 75, cpu_workers: 1, gpu_workers: 1 }, effective: { cores: 16, threads_per_encode: 12, threads_total: 12 }, encoder_reachable: true, parallel_encodes: 2 }) }));
  await login(page);
  // Systeem & prestaties staat onder de tab Systeem (M15)
  await page.goto("/systeem");
  const perf = page.getByTestId("section-prestaties");
  await expect(perf).toBeVisible();
  await expect(perf.getByText("Maximaal CPU-gebruik voor AV1")).toBeVisible();
  await expect(perf.getByText("Encodes tegelijk op de processor (CPU)")).toBeVisible();
  await expect(perf.getByText(/12 van de 16 processorkernen/)).toBeVisible();
  // jargon en vervallen instellingen niet in de eenvoudige weergave
  await expect(page.getByText("Canary", { exact: false })).toHaveCount(0);
  await expect(page.getByText("Max. gelijktijdige AV1-encodes (CPU/GPU)")).toHaveCount(0);
  await expect(page.getByTestId("section-originelen")).toHaveCount(0);
  // CPU-percentage aanpassen en opslaan
  const input = perf.getByLabel("Maximaal CPU-gebruik voor AV1");
  await input.fill("60");
  await page.getByTestId("settings-save").click();
  await expect(page.getByTestId("settings-save")).toHaveCount(0);
  const eff = await (await page.request.get("/api/v1/settings/effective?keys=resources.av1_cpu_percent")).json() as Array<{ value: number }>;
  expect(eff[0].value).toBe(60);
  // geavanceerd per sectie (Instellingen → Algemeen)
  await page.goto("/instellingen");
  await expect(page.getByTestId("section-prestaties")).toHaveCount(0);
  await page.getByTestId("advanced-originelen").click();
  await expect(page.getByTestId("section-originelen").getByText("Minimale vrije ruimte (GB)")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  // zoeken filtert de pagina zelf (ook geavanceerde instellingen); 'Per serie of instantie' → alle
  await page.getByTestId("settings-search").fill("Minimale vrije ruimte");
  await expect(page.getByTestId("section-originelen").getByText("Minimale vrije ruimte (GB)")).toBeVisible();
  await expect(page.getByTestId("section-av1")).toHaveCount(0);
  await page.getByTestId("settings-search").fill("");
  await page.getByTestId("settings-scope-open").click();
  await expect(page).toHaveURL(/view=all/);
  await expect(page.getByTestId("settings-add-exception")).toBeVisible();
});
