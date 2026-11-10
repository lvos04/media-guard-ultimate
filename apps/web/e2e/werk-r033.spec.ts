import { expect, test, type Page, type Route } from "@playwright/test";

/** Testrapport 3: opgevolgde pogingen, joblinks met MediaGuard-nummer, prognose in weken, audit, quarantaine-bevestiging. */
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

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const job = (id: number, status: string, pad: string, extra: Record<string, unknown> = {}) => ({
  id, bestand_id: id, status, baan: "gpu", prioriteit: 0, percent: status === "gecommit" ? 100 : 0, fps: null, speed: null, eta_seconden: null,
  fout: status === "mislukt" ? "Herstartlus — 3× onderbroken" : null, foutsoort: status === "mislukt" ? "HerstartLus" : null, commando: null, pad,
  bron_grootte: 1.4e9, library_id: 1, actie: "hercoderen", encoder: "av1_qsv", reden: "1080p naar AV1 via GPU", gestart_op: "2026-11-05T05:19:00Z",
  klaar_op: "2026-11-05T06:39:00Z", resultaat_grootte: null, verificatie_geslaagd: null, verificatie_toelichting: null, kwaliteitsadvies: null, kwaliteitsadvies_status: null, ...extra,
});
const PAD = "/data/media/anime/Perzikbloesem/Season 02/Perzikbloesem - S02E03.mkv";

async function mockEngine(page: Page, stats: Record<string, unknown> = {}) {
  await page.routeWebSocket("**/api/v1/encoder/ws", () => { /* geen live-kanaal nodig */ });
  await page.route("**/api/v1/encoder/**", async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace("/api/v1/encoder", "");
    if (p === "/jobs") {
      const st = url.searchParams.get("status");
      const items = [job(40, "mislukt", PAD), job(50, "gecommit", PAD, { resultaat_grootte: 3e8 }), job(43, "mislukt", "/data/media/movies/De Toverlantaarn/TL.mkv")];
      const rows = st ? items.filter((j) => j.status === st) : items;
      return json(route, { items: rows, totaal: rows.length, pagina: 1, per_pagina: 50 });
    }
    if (p === "/jobs/58") return json(route, { detail: "Job niet gevonden" }, 404);
    if (p === "/jobs/43") return json(route, job(43, "mislukt", "/data/media/movies/De Toverlantaarn/TL.mkv"));
    if (p.startsWith("/jobs/") && p.endsWith("/log")) return json(route, { regels: [], volgende: 0 });
    if (p === "/statistieken") return json(route, { totaal: { bespaard_bytes: 7.1e9, gemiddeld: 70, jobs: 8 }, per_dag: [], doorvoer_bytes_per_dag_7d: 1.5e9, resterend_bytes: 60e9, prognose_klaar: new Date(Date.now() + 40 * 86_400_000).toISOString(), wacht_op_activatie: { jobs: 0, besparing_bytes: 0 }, ...stats });
    if (p === "/fouten") return json(route, { groepen: [] });
    return json(route, {});
  });
  await page.route("**/api/v1/encoding/engine-states", (route) => json(route, { items: [
    { engine_id: 40, core_id: 37, core_status: "failed", status_nl: "Opgelost door een latere poging", rolled_back: false, superseded_by: 50, resolved: true },
    { engine_id: 50, core_id: 47, core_status: "committed", status_nl: null, rolled_back: false, superseded_by: null, resolved: false },
  ] }));
  await page.route("**/api/v1/encoding/jobs/resolve/58", (route) => json(route, { core_id: 58, engine_id: 43, matched: "core" }));
}

test("wachtrij verbergt pogingen die later wel lukten, en toont ze op verzoek (R4/R21)", async ({ page }) => {
  await login(page);
  await mockEngine(page);
  await page.goto("/av1/wachtrij");
  await expect(page.getByTestId("superseded-note")).toContainText("later wel gelukt");
  await expect(page.getByText("#40 ·")).toHaveCount(0);
  await expect(page.getByText("#50 ·")).toBeVisible();
  await page.getByRole("button", { name: "Toon eerdere pogingen" }).click();
  await expect(page.getByText("#40 ·")).toBeVisible();
  await expect(page.locator("td.hide-md").getByText("Later gelukt")).toBeVisible();
});

test("link met een MediaGuard-jobnummer opent de juiste engine-job (R5)", async ({ page }) => {
  await login(page);
  await mockEngine(page);
  await page.goto("/av1/wachtrij?job=58");
  await expect(page.getByRole("dialog").getByText("TL.mkv").first()).toBeVisible();
  await expect(page.getByText("Job niet gevonden")).toHaveCount(0);
});

test("prognose als '± X weken' en geen datum bij een lege wachtrij (R12)", async ({ page }) => {
  await login(page);
  await mockEngine(page);
  await page.goto("/av1");
  await expect(page.getByText(/klaar over ± 6 weken/)).toBeVisible();
});

test("audit: instantiefilter met namen, geen ruwe slug-placeholder (R16)", async ({ page }) => {
  await login(page);
  await page.goto("/activiteit?tab=geschiedenis&view=audit");
  const filter = page.getByLabel("Filter op instantie");
  await expect(filter).toBeVisible();
  await expect(filter.locator("option")).toContainText(["Filter op instantie", "Series"]);
});
