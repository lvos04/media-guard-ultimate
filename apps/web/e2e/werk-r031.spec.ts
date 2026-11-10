import { expect, test, type Page } from "@playwright/test";

/** Laatste open punten: één plek per functie, techniek tonen, ontbrekend-redenen, mapnaam-typfouten. */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };
const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

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

test("oude adressen sturen door naar de ene plek (optimaliseren, audit)", async ({ page }) => {
  await login(page);
  await page.goto("/migratie?tab=optimize&instance=sonarr-series");
  await expect(page).toHaveURL(/\/kwaliteit\?.*tab=optimaliseren/);
  await expect(page).toHaveURL(/instance=sonarr-series/);
  await expect(page.getByRole("tab", { name: "Bibliotheek optimaliseren" })).toHaveAttribute("aria-selected", "true");
  await page.goto("/inrichten?tab=optimaliseren");
  await expect(page).toHaveURL(/\/kwaliteit\?tab=instellingen/);
  await page.goto("/instellingen/audit");
  await expect(page).toHaveURL(/\/activiteit\?tab=geschiedenis&view=audit/);
  // 'Gevorderd' is de sectie Gereedschap geworden; Audit staat bij Beveiliging & toegang
  await page.goto("/instellingen/gevorderd");
  await expect(page).toHaveURL(/\/instellingen#gereedschap$/);
  await expect(page.getByTestId("section-gereedschap")).not.toContainText("Audit");
});

test("Dolby Vision: hoofdschakelaar in Instellingen, hier alleen uitzonderingen per titel", async ({ page }) => {
  await login(page);
  await page.goto("/dolby-vision");
  const scopes = page.getByRole("group", { name: /niveau|scope/i }).first();
  await expect(scopes.getByRole("button")).toHaveCount(3);
  await expect(page.getByRole("link", { name: "Wijzigen in Instellingen" })).toBeVisible();
});

test("technische details: schakelaar in het profiel klapt ze overal uit en blijft bewaard", async ({ page }) => {
  await login(page);
  await page.goto("/systeem");
  const first = page.locator("details.tech-details").first();
  await expect(first).toBeAttached();
  await expect(first).not.toHaveAttribute("open", "");
  await page.getByTestId("profile-button").click();
  await page.getByTestId("tech-toggle").getByRole("switch").click();
  await expect(first).toHaveAttribute("open", "");
  await page.reload();
  await expect(page.locator("details.tech-details").first()).toHaveAttribute("open", "");
  await page.getByTestId("profile-button").click();
  await page.getByTestId("tech-toggle").getByRole("switch").click();
  await expect(page.locator("details.tech-details").first()).not.toHaveAttribute("open", "");
});

test("ontbrekend: redenen gesplitst, 'vraagt jou iets' in Wacht op jou, nog niet verschenen apart", async ({ page }) => {
  const block = {
    missing: 12, cutoff: 40, unavailable: 0, ignored: 0, missing_aired_7d: 2, resolved_7d: 1, missing_aired_30d: 3, resolved_30d: 4,
    reason_distribution: { "root-folder-missing": 3, "searched-no-grab": 9 },
    reason_distribution_cutoff: { "rejected-quality": 40 },
    reason_distribution_detail: { missing: { "root-folder-missing": { total: 3, episodes: 2, movies: 1 }, "searched-no-grab": { total: 9, episodes: 9, movies: 0 } }, cutoff: { "rejected-quality": { total: 40, episodes: 40, movies: 0 } } },
    undiagnosed: { missing: 0, cutoff: 0, automatic: true }, upcoming: { total: 5, movies: 2, episodes: 3 },
    needs_user: { total: 3, reasons: [{ code: "root-folder-missing", reason: "missing", label_nl: "Hoofdmap ontbreekt", fix_nl: "Controleer de hoofdmap.", href: "/instellingen?tab=opslag", total: 3, episodes: 2, movies: 1 }] },
    automatic_total: 49,
    backfill_30d: { searches: 0, completed: 0, grabbed: 0, errors: 0, success_rate: null }, import_failed: 0,
  };
  await page.route("**/api/v1/wanted/kpis**", (r) => r.fulfill(json({ ...block, per_instance: {} })));
  await page.route("**/api/v1/wanted/reasons", (r) => r.fulfill(json({
    "root-folder-missing": { label_nl: "Hoofdmap ontbreekt", fix_nl: "Controleer de hoofdmap.", needs_user: true, href: "/instellingen?tab=opslag" },
    "searched-no-grab": { label_nl: "Gezocht zonder resultaat", fix_nl: "MediaGuard zoekt later opnieuw.", needs_user: false },
    "rejected-quality": { label_nl: "Afgewezen op kwaliteit", fix_nl: "—", needs_user: false },
  })));
  // andere meldingen uit gedeelde testdata weglaten, zodat de lijst (max 5) deze rij toont
  await page.route("**/api/v1/attention**", (r) => r.fulfill(json({ count: 0, errors: 0, items: [] })));
  await page.route("**/api/v1/jobs?**", (r) => r.fulfill(json([])));
  await login(page);
  await expect(page.getByTestId("overview-wanted-line")).toContainText("jij bent nodig bij 1 oorzaak");
  await expect(page.getByTestId("overview-wanted-line")).toContainText("5 nog niet verschenen");
  await expect(page.getByText("Ontbrekend: Hoofdmap ontbreekt")).toBeVisible();
  // de knop in 'Wacht op jou' gaat naar de plek waar je het oplost (Systeem → Opslag)
  await page.getByTestId("todo-card").locator(".list-row", { hasText: "Hoofdmap ontbreekt" }).getByRole("link").click();
  await expect(page).toHaveURL(/\/systeem#opslag/);
  await page.goto("/ontbrekend");
  await expect(page.getByText("2 afl. · 1 film", { exact: true })).toBeVisible();
  await expect(page.getByText("Vraagt jou iets")).toBeVisible();
  await expect(page.getByTestId("wanted-notes")).toContainText("nog niet verschenen");
  await page.getByRole("button", { name: "Onder kwaliteit" }).click();
  await expect(page).toHaveURL(/reason=cutoff/);
  await expect(page.getByText("Afgewezen op kwaliteit")).toBeVisible();
});

test("mapnaam-typfout: tonen, bevestigen en de taak volgen; oudere core zonder endpoint toont niets", async ({ page }) => {
  let posted: unknown = null;
  let calls = 0;
  await page.route("**/api/v1/storage/typos", (r) => r.fulfill(json([
    { instance_id: "radarr-animefilms", instance_name: "Radarr Anime Films", root_folder_id: 2, path: "/data/media/Anime Fims", suggested_path: "/data/media/Anime Films", items: 42, free_space_ok: true, blocked_reason_nl: null },
    { instance_id: "sonarr-anime", instance_name: "Sonarr Anime", root_folder_id: 3, path: "/data/media/Anmie", suggested_path: "/data/media/Anime", items: 7, free_space_ok: true, blocked_reason_nl: "Nog in proefmodus: eerst overschakelen." },
  ])));
  await page.route("**/api/v1/storage/typos/radarr-animefilms/2/fix", (r) => {
    calls += 1;
    posted = r.request().postDataJSON();
    // eerste poging: te weinig ruimte (409) → melding in het venster, tweede lukt
    return calls === 1 ? r.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "Te weinig vrije ruimte op de doelschijf." }) }) : r.fulfill(json({ job_id: 999999 }));
  });
  await login(page);
  await page.goto("/systeem");
  await expect(page.getByTestId("folder-typos")).toContainText("Anime Fims");
  // de geblokkeerde rij vóór het openen van de dialoog controleren: een modale dialoog (Radix, Peil fase 2) maakt de
  // rest van de pagina onbereikbaar voor hulptechnologie, dus rollen buiten de dialoog zijn dan niet te vinden
  await expect(page.getByRole("button", { name: "Map hernoemen naar Anime", exact: true })).toBeDisabled();
  await expect(page.getByTestId("folder-typos")).toContainText("Nog in proefmodus");
  await page.getByRole("button", { name: "Map hernoemen naar Anime Films" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("Er wordt niets verwijderd");
  await page.getByRole("alertdialog").getByRole("button", { name: "Map hernoemen naar Anime Films" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("Te weinig vrije ruimte op de doelschijf.");
  await page.getByRole("alertdialog").getByRole("button", { name: "Map hernoemen naar Anime Films" }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect.poll(() => posted).toEqual({ confirm: true, target_path: "/data/media/Anime Films" });
  await page.unroute("**/api/v1/storage/typos");
  await page.route("**/api/v1/storage/typos", (r) => r.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) }));
  await page.reload();
  await expect(page.getByTestId("storage-overview")).toBeVisible();
  await expect(page.getByTestId("folder-typos")).toHaveCount(0);
});
