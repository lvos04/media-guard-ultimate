import { expect, test, type Page } from "@playwright/test";

import { seedLinkWorld, seedQuarantine } from "./seed";

/**
 * Linkcrawler: elke interne link die de UI toont (menu, pagina's, meldingen, 'Wacht op jou',
 * Activiteit) moet naar een bestaande pagina leiden zonder foutmelding, en een link naar een
 * serie/film moet ook bij díe serie/film uitkomen.
 */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };
const START = [
  "/", "/activiteit", "/activiteit?tab=problemen", "/activiteit?tab=geschiedenis", "/bibliotheek",
  "/ontbrekend", "/kalender", "/av1", "/av1/wachtrij", "/av1/kwaliteit", "/av1/instellingen",
  "/quarantaine", "/instellingen", "/instellingen/instanties", "/instellingen/gevorderd",
  "/inrichten", "/systeem", "/migratie", "/migratie?tab=optimize", "/kwaliteit", "/anime", "/ai",
  "/dolby-vision", "/release-lab", "/kwaliteit?tab=optimaliseren", "/kwaliteit?tab=instellingen",
  "/instellingen/audit", "/inrichten?tab=optimaliseren", "/activiteit?tab=geschiedenis&view=audit", "/instellingen?tab=opslag",
];

let titles: { series: string; movie: string };

test.beforeAll(async ({ request }) => {
  const status = await (await request.get("/api/v1/auth/setup-status")).json() as { needs_setup: boolean };
  if (status.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
  const [qid] = seedQuarantine();
  titles = seedLinkWorld(qid);
});

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(ADMIN.username);
  await page.getByLabel("Wachtwoord").fill(ADMIN.password);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page.getByRole("heading", { name: "Overzicht", level: 1 })).toBeVisible();
}

async function settle(page: Page) {
  await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => undefined);
}

async function internalLinks(page: Page): Promise<string[]> {
  return page.evaluate(() => [...document.querySelectorAll("a[href]")]
    .map((a) => a.getAttribute("href") ?? "")
    .filter((h) => h.startsWith("/") && !h.startsWith("/api/") && !h.startsWith("/_next/")));
}

/** Leidt de link naar een echte pagina zonder fout? Geeft een foutomschrijving of null. */
async function problem(page: Page, href: string): Promise<string | null> {
  const resp = await page.goto(href, { waitUntil: "domcontentloaded" });
  await settle(page);
  if (resp && resp.status() >= 400) return `HTTP ${resp.status()}`;
  // echte 404: onze not-found-pagina of die van Next; niet elk getal '404' in de inhoud (een
  // sha256 in de audit gaf een vals alarm)
  if (await page.getByTestId("not-found").count()) return "404-pagina";
  const text = await page.locator("body").innerText();
  if (/This page could not be found/.test(text)) return "404-pagina";
  const bad = await page.locator('[data-state="error"], [data-state="not_found"]').count();
  if (bad) return `foutstatus op de pagina (${(await page.locator('[data-state="error"], [data-state="not_found"]').first().innerText()).slice(0, 120)})`;
  return null;
}

test("alle interne links werken en leiden naar het juiste item", async ({ page }) => {
  test.setTimeout(240_000);
  await login(page);
  const found = new Map<string, string>(START.map((s) => [s, "startlijst"])); // href -> waar gevonden
  for (const start of START) {
    await page.goto(start);
    await settle(page);
    for (const href of await internalLinks(page)) if (!found.has(href)) found.set(href, start);
  }
  // meldingen (klokje)
  await page.goto("/");
  await settle(page);
  const notes = await (await page.request.get("/api/v1/notifications")).json() as Array<{ link?: string | null }> | { items?: Array<{ link?: string | null }> };
  for (const n of Array.isArray(notes) ? notes : notes.items ?? []) if (n.link) found.set(n.link, "melding");
  // 'Wacht op jou' en Activiteit (links uit de API)
  const att = await (await page.request.get("/api/v1/attention")).json() as { items: Array<{ key: string; href?: string | null; rows?: Array<{ href?: string | null }> }> };
  expect(att.items.some((i) => i.key === "optimization.review")).toBeFalsy(); // geen handmatige taak meer
  for (const i of att.items) {
    if (i.href) found.set(i.href, `wacht op jou: ${i.key}`);
    for (const r of i.rows ?? []) if (r.href) found.set(r.href, `wacht op jou-rij: ${i.key}`);
  }
  const act = await (await page.request.get("/api/v1/activity")).json() as Record<string, unknown>;
  const walk = (v: unknown) => {
    if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") {
      const o = v as Record<string, unknown>;
      if (typeof o.link === "string" && o.link.startsWith("/")) found.set(o.link, "activiteit");
      Object.values(o).forEach(walk);
    }
  };
  walk(act);

  const failures: string[] = [];
  for (const [href, where] of found) {
    const p = await problem(page, href);
    if (p) failures.push(`${href} (gevonden op ${where}): ${p}`);
    if (/^\/bibliotheek\/[^/]+\/\d+/.test(href) && !p) {
      const body = await page.locator("main").innerText().catch(() => "");
      if (!body.includes(titles.series) && !body.includes(titles.movie)) failures.push(`${href} (${where}): toont niet de verwachte serie/film`);
    }
    // de doelpagina moet het item ook echt aanwijzen
    const q = /^\/quarantaine\?item=(\d+)/.exec(href);
    if (q && !p && await page.locator(`[data-testid="q-item-${q[1]}"][aria-current="true"]`).count() === 0) failures.push(`${href} (${where}): quarantaine-item niet gemarkeerd`);
  }
  expect(found.size).toBeGreaterThan(30);
  expect(failures, failures.join("\n")).toEqual([]);
});

test("AV1-wachtrij: 'Plannen bekijken' leidt naar het optimalisatieplan (niet naar Migratie)", async ({ page }) => {
  await login(page);
  // er is een plan zodra er een dry-run is gedaan
  const csrf = (await (await page.request.get("/api/v1/auth/me")).json() as { csrf_token: string }).csrf_token;
  await page.request.post("/api/v1/optimization/dry-run", { data: { filters: {} }, headers: { "X-CSRF-Token": csrf } });
  await page.goto("/av1/wachtrij");
  await settle(page);
  const link = page.getByRole("link", { name: "Plannen bekijken" });
  await expect(link).toHaveAttribute("href", /\/kwaliteit\?tab=optimaliseren/);
  await link.click();
  await expect(page).toHaveURL(/\/kwaliteit\?tab=optimaliseren/);
  await expect(page.getByTestId("plan-av1-items")).toBeVisible();
  await page.goto("/av1");
  await settle(page);
  await expect(page.getByTestId("av1-plan-link")).toBeVisible();
});
