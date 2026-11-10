import { expect, test, type Page } from "@playwright/test";

/** 'Wacht op jou': verborgen wachttoestanden op Overzicht (Nog te doen) en Activiteit → Problemen. */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };

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

const act = (label: string, path: string, extra: Record<string, unknown> = {}) => ({ label_nl: label, method: "post", path, body: {}, confirm_nl: null, role: "operator", primary: true, ...extra });
const attention = {
  count: 4, errors: 1,
  items: [
    { key: "encoding.conflict", severity: "error", title_nl: "1 vervanging(en) met een conflict", detail_nl: "Er is niets verwijderd; kies per bestand.", href: "/quarantaine", count: 1, actions: [],
      rows: [{ id: 7, title: "Show S01E01.mkv", detail: "Bron gewijzigd", actions: [act("Origineel houden", "/encoding/jobs/7/cancel", { confirm_nl: "Het AV1-resultaat wordt weggegooid.", primary: false })] }] },
    { key: "encoding.awaiting_activation", severity: "warning", title_nl: "3 omgezet(te) bestand(en) wacht(en) op activatie", detail_nl: "Gecontroleerd en klaar.", href: "/av1", count: 3, actions: [],
      rows: [{ id: 1, title: "Film.mkv", detail: "", actions: [act("Activeren", "/encoding/jobs/1/activate")] }] },
    { key: "instance.suspended.radarr-films", severity: "warning", title_nl: "Radarr Films staat op pauze", detail_nl: "MediaGuard zoekt niets.", href: "/instellingen/instanties", count: 1, actions: [act("Hervatten", "/instances/radarr-films/resume")], rows: [] },
    // zoals core het stuurt (security/api.py): rijen zonder 'actions' — liet de Problemen-tab crashen
    { key: "security.twofa_off", severity: "info", title_nl: "Tweestapsverificatie staat uit", detail_nl: "Het blijft je keuze.", href: "/instellingen/beveiliging", count: 1, actions: [],
      rows: [{ id: "1", title: "beheerder", detail: "Beheerder zonder tweestapsverificatie" }] },
  ],
};

test("Wacht op jou: zichtbaar in Nog te doen en Problemen, knoppen roepen de juiste actie aan", async ({ page }) => {
  await page.setViewportSize({ width: 412, height: 900 });
  await page.route("**/api/v1/attention", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(attention) }));
  const posted: string[] = [];
  await page.route("**/api/v1/instances/radarr-films/resume", (route) => { posted.push(route.request().url()); return route.fulfill({ status: 200, contentType: "application/json", body: "{}" }); });
  await login(page);
  const todo = page.getByTestId("todo-card");
  await expect(todo.getByText("3 omgezet(te) bestand(en) wacht(en) op activatie")).toBeVisible();
  await expect(todo.getByText("1 vervanging(en) met een conflict")).toBeVisible(); // fouten bovenaan

  await page.goto("/activiteit?tab=problemen");
  const list = page.getByTestId("attention-list");
  await expect(list.getByText("1 vervanging(en) met een conflict")).toBeVisible();
  await expect(list.getByRole("button", { name: "Activeren" })).toBeVisible();
  await expect(list.getByText("Beheerder zonder tweestapsverificatie")).toBeVisible();
  await list.getByRole("button", { name: "Hervatten" }).click();
  await expect.poll(() => posted.length).toBe(1);
  // risicovolle actie vraagt eerst bevestiging
  await list.getByRole("button", { name: "Origineel houden" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
