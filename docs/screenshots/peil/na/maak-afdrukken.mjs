// Peil-migratie MediaGuard — schermafdrukken en meting NA de omzetting naar Peil ("na"), tegen een draaiende app.
// Zelfde script, schermen, breedtes, nabootsingen en metingen als docs/screenshots/peil/voor/ (vergelijkbaar), plus de
// groep "hub": "Terug naar de Hub" met een fictief testadres (.example, RFC 2606) dat daarna weer leeg wordt gezet.
//
// Alleen-lezen hulpscript voor docs/ontwerp/PEIL_MIGRATIE.md; het hoort niet bij de app en wordt niet gebouwd.
// Het verandert niets aan de app behalve wat een gebruiker ook doet (inloggen, pagina's en dialogen openen en
// annuleren). Uitzondering: de groep "noodstop" zet de noodstop via de API aan en daarna weer uit.
// Toestanden die de testdata niet vanzelf heeft (laden, fout, offline, verouderd, eerste start, AV1-engine actief)
// worden nagebootst door API-antwoorden in de browser te onderscheppen; in metingen.json staat per afdruk welke
// onderschepping is gebruikt ("nagebootst").
//
// Gebruik (app gestart zoals beschreven in README.md):
//   MG_PEIL_BASE_URL=http://127.0.0.1:18200 MG_PEIL_PASSWORD=… \
//   PEIL_PLAYWRIGHT=/pad/naar/node_modules/playwright/index.mjs PEIL_AXE=/pad/naar/axe-core/axe.min.js \
//   node docs/screenshots/peil/na/maak-afdrukken.mjs [schermen|tabs|toestanden|noodstop|hub|alles]
// Het wachtwoord komt alleen uit de omgeving en wordt nergens weggeschreven.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.MG_PEIL_BASE_URL ?? "http://127.0.0.1:18200";
const PASSWORD = process.env.MG_PEIL_PASSWORD ?? "";
const AXE = process.env.PEIL_AXE ?? "";
const WHAT = process.argv[2] ?? "alles";
const ONLY = process.env.PEIL_ALLEEN ? new RegExp(process.env.PEIL_ALLEEN) : null;
if (!PASSWORD) throw new Error("MG_PEIL_PASSWORD is verplicht");
const { chromium, request } = await import(process.env.PEIL_PLAYWRIGHT ?? "playwright");

const THEMES = [["licht", "light"], ["donker", "dark"]];
const ALL_WIDTHS = [360, 390, 768, 1440];
const PAIR_WIDTHS = [390, 1440];
const DEVICE = {
  360: { viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true },
  390: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
  768: { viewport: { width: 768, height: 1024 }, isMobile: false, hasTouch: true },
  1440: { viewport: { width: 1440, height: 900 }, isMobile: false, hasTouch: false },
};

// alle routes met een eigen pagina (geen omleidingen); dynamische routes worden bij de start opgezocht
const SCREENS = [
  ["overzicht", "/"], ["activiteit", "/activiteit"], ["bibliotheek", "/bibliotheek"], ["bibliotheek-titel", "@titel"],
  ["bibliotheek-film", "@film"], ["aflevering", "@aflevering"], ["ontbrekend", "/ontbrekend"], ["kalender", "/kalender"],
  ["ruimte", "/av1"], ["ruimte-wachtrij", "/av1/wachtrij"], ["ruimte-kwaliteit", "/av1/kwaliteit"], ["terugzetten", "/quarantaine"],
  ["aanvragen", "/aanvragen"], ["instellingen", "/instellingen"], ["av1-instellingen", "/av1/instellingen"],
  ["koppelingen", "/inrichten"], ["kwaliteit", "/kwaliteit"], ["migratie", "/migratie"], ["dolby-vision", "/dolby-vision"],
  ["release-lab", "/release-lab"], ["anime", "/anime"], ["ai-adviseur", "/ai"], ["systeem", "/systeem"],
  ["hoe-werkt-het", "/hoe-werkt-het"], ["instanties", "/instellingen/instanties"], ["gebruikers", "/instellingen/gebruikers"],
  ["geheimen", "/instellingen/geheimen"], ["api-sleutels", "/instellingen/api-sleutels"], ["meldingen", "/instellingen/meldingen"],
  ["beveiliging", "/instellingen/beveiliging"],
];
const TABS = [
  ...["problemen", "downloads", "gebeurd", "geschiedenis"].map((t) => [`activiteit-${t}`, `/activiteit?tab=${t}`, {}]),
  ...["downloads", "indexers", "paden", "media", "seerr", "mediaserver", "hernoemen"].map((t) => [`koppelingen-${t}`, `/inrichten?tab=${t}`, {}]),
  ...["optimaliseren", "instellingen"].map((t) => [`kwaliteit-${t}`, `/kwaliteit?tab=${t}`, {}]),
  ["migratie-av1", "/migratie?tab=av1", {}],
  ...[1, 2, 3, 4].map((i) => [`bibliotheek-titel-tab${i + 1}`, "@titel", { tab: i }]),
  ...[1, 2, 3, 4, 5, 6].map((i) => [`aflevering-tab${i + 1}`, "@aflevering", { tab: i }]),
];

const json = (status, body) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const ERR500 = json(500, { detail: "Interne fout in MediaGuard-core", code: "internal_error", correlation_id: "3f9c2a7d41b8" });
const DOWN = json(502, { detail: "Upstream service unreachable", code: "upstream_unreachable" }); // zelfde vorm als server.mjs bij een onbereikbare core
const notAuth = (url) => !/\/api\/v1\/auth\//.test(url);

// nep-AV1-engine (zelfde vorm als e2e/studio-av1.spec.ts; fictieve titels)
const now = () => new Date(Date.now() - 600_000).toISOString();
const SYSTEEM = {
  cpu_percent: 41, cpu_temp: null, geheugen: { totaal_bytes: 16e9, gebruikt_bytes: 6e9, beschikbaar_bytes: 10e9, percent: 37 },
  gpu: { video_percent: 55, render_percent: 20, freq_mhz: null, power_w: null, package_power_w: null, platform_power_w: null, platform_power_beschikbaar: false, beschikbaar: true },
  schijf_vrij_gb: 900, schijf_vrij_bytes: 900e9, schijf_gereserveerd_bytes: 0, schijf_beschikbaar_na_reservering: 900e9,
  cache_status: "voldoende", cache_waarschuwing_gb: 250, cache_pauze_gb: 150, effectieve_cache_waarschuwing_gb: 250, effectieve_cache_pauze_gb: 150,
  opslagfout: null, opslagmodus: "aparte_schijf", systeemschijf_reserve_gb: 150,
};
const TICK = () => JSON.stringify({
  type: "tick", tijd: new Date().toISOString().slice(0, 10), systeem: SYSTEEM, wachtrij: { wachtend: 5, bezig: 3, klaar_vandaag: 2, fouten: 1 },
  workers: [
    { baan: "gpu", slot: 1, aantal: 2, job_id: 7, status: "bezig", fase: "encode", fase_gestart_op: now(), gestart_op: now(), bestand: "/data/media/movies/Zomerlicht (2021)/Zomerlicht.mkv", encoder: "av1_qsv", percent: 55, fps: 96, speed: 4, eta_seconden: 900, bitrate_kbps: 9000, grootte_bron: 40e9, grootte_nu: 8e9, gepauzeerd: false },
    { baan: "gpu", slot: 2, aantal: 2, job_id: 8, status: "bezig", fase: "encode", fase_gestart_op: now(), gestart_op: now(), bestand: "/data/media/movies/Polderwind (2021)/Polderwind.mkv", encoder: "av1_qsv", percent: 12, fps: 90, speed: 3.6, eta_seconden: 2400, grootte_bron: 30e9, grootte_nu: 2e9, gepauzeerd: false },
    { baan: "cpu", slot: 1, aantal: 1, job_id: 9, status: "verifieren", fase: "verifieren", fase_gestart_op: now(), gestart_op: now(), bestand: "/data/media/tv/Bergpas/Season 01/Bergpas - S01E01.mkv", encoder: "libsvtav1", percent: null, fps: 30, speed: 1.2, eta_seconden: 3000, grootte_bron: 3e9, grootte_nu: 0, gepauzeerd: false },
  ],
});
const DAGEN = Array.from({ length: 14 }, (_, i) => new Date(Date.now() - (13 - i) * 864e5).toISOString().slice(0, 10));
async function mockEngine(page) {
  await page.route("**/api/v1/encoder/**", async (route) => {
    const p = new URL(route.request().url()).pathname.replace("/api/v1/encoder", "");
    const ok = (body) => route.fulfill(json(200, body));
    if (p === "/statistieken") return ok({ totaal: { bespaard_bytes: 2.5e12, gemiddeld: 41.5, jobs: 120 }, per_dag: DAGEN.filter((_, i) => i !== 9).map((dag, i) => ({ dag, bespaard_bytes: (40 + ((i * 37) % 60)) * 1e9, jobs: 2 + (i % 4) })), doorvoer_bytes_per_dag_7d: 1.1e11, resterend_bytes: 4e12, prognose_klaar: "2026-12-20" });
    if (p === "/systeem/diagnose") return ok({ dependencies: { tools: { dovi_tool: true }, ffmpeg_version: "8.0.1", libsvtav1: true, av1_qsv: true, dolbyvision: true, hdr10plus: true, render_node: { pad: "/dev/dri/renderD128", aanwezig: true, leesbaar: true, schrijfbaar: true }, qsv_gereed: true } });
    if (p === "/systeem/metingen") return ok(Array.from({ length: 60 }, (_, i) => ({ tijd: new Date(Date.now() - (59 - i) * 60_000).toISOString(), cpu_percent: 35 + ((i * 7) % 25), gpu: { video_percent: 50 + ((i * 3) % 20) }, geheugen: { percent: 37 } })));
    if (p === "/instellingen") return ok({ meld_browser_fouten: false, meld_browser_batches: false });
    if (p === "/jobs") return ok({ items: [
      { id: 7, status: "bezig", baan: "gpu", encoder: "av1_qsv", pad: "/data/media/movies/Zomerlicht (2021)/Zomerlicht.mkv", bron_grootte: 40e9, percent: 55, reden: "bitrate boven drempel", gestart_op: now(), fase: "encode" },
      { id: 10, status: "mislukt", baan: "cpu", encoder: "libsvtav1", pad: "/data/media/tv/Stadshaven/Season 01/Stadshaven - S01E04.mkv", bron_grootte: 2e9, percent: 0, reden: "VMAF te laag", fout: "Kwaliteit te laag: VMAF 81,5 onder 93,0 — origineel blijft staan", gestart_op: now() },
      { id: 11, status: "wachtend", baan: "gpu", encoder: "av1_qsv", pad: "/data/media/anime/Sterrenveld/Season 01/Sterrenveld - 01.mkv", bron_grootte: 1.4e9, percent: 0, reden: "codec h264" },
    ], totaal: 3, pagina: 1, per_pagina: 250 });
    return ok({});
  });
  await page.routeWebSocket("**/api/v1/encoder/ws", (ws) => { ws.send(TICK()); });
}

// toestanden: [slug, route, breedtes, opties]
const STATES = [
  ["inloggen", "/login", PAIR_WIDTHS, { anonymous: true }],
  ["eerste-start", "/setup", PAIR_WIDTHS, { anonymous: true, nagebootst: "GET /auth/setup-status → needs_setup", routes: [["**/api/v1/auth/setup-status", json(200, { needs_setup: true })]] }],
  ["niet-gevonden", "/bestaat-niet", PAIR_WIDTHS, {}],
  ["laden", "/", PAIR_WIDTHS, { nagebootst: "alle /api/v1-verzoeken behalve /auth 20 s vertraagd", delay: true, noSettle: true }],
  ["fout", "/bibliotheek", PAIR_WIDTHS, { nagebootst: "GET /library* → 500 (foutvorm detail/code/correlation_id)", routes: [["**/api/v1/library**", ERR500]] }],
  ["offline", "/", PAIR_WIDTHS, { nagebootst: "alle /api/v1-verzoeken behalve /auth → 502 upstream_unreachable (zoals server.mjs bij een onbereikbare core)", down: true, noSettle: true }],
  ["verouderd", "/", PAIR_WIDTHS, { nagebootst: "eerst echte data, daarna alle /api/v1-verzoeken behalve /auth → 502 (verversen mislukt)", stale: true, staleWait: 70_000 }],
  ["verouderd-bibliotheek", "/bibliotheek", PAIR_WIDTHS, { nagebootst: "eerst echte data, daarna /api/v1 → 502", stale: true, staleWait: 70_000 }],
  ["kijker-overzicht", "/", PAIR_WIDTHS, { user: "kijker" }],
  ["kijker-terugzetten", "/quarantaine", PAIR_WIDTHS, { user: "kijker" }],
  ["en-us-overzicht", "/", PAIR_WIDTHS, { user: "engels" }],
  ["en-us-activiteit", "/activiteit?tab=problemen", PAIR_WIDTHS, { user: "engels" }],
  ["en-us-instellingen", "/instellingen", PAIR_WIDTHS, { user: "engels" }],
  ["ruimte-engine-actief", "/av1", PAIR_WIDTHS, { nagebootst: "AV1-engine (/api/v1/encoder/** + WebSocket) zoals e2e/studio-av1.spec.ts", engine: true }],
  ["ruimte-wachtrij-engine-actief", "/av1/wachtrij", PAIR_WIDTHS, { nagebootst: "AV1-engine zoals e2e/studio-av1.spec.ts", engine: true }],
  ["zoeken", "/", PAIR_WIDTHS, { action: "search", viewportOnly: true }],
  ["profielmenu", "/", PAIR_WIDTHS, { action: "profile", viewportOnly: true }],
  ["meldingen-paneel", "/", PAIR_WIDTHS, { action: "bell", viewportOnly: true }],
  ["sneltoetsen", "/", [1440], { action: "help", viewportOnly: true }],
  ["noodstop-dialoog", "/", PAIR_WIDTHS, { action: "estop", viewportOnly: true }],
  ["vrijgeven-dialoog", "/", PAIR_WIDTHS, { action: "unlock", viewportOnly: true }],
  ["terugzet-dialoog", "/quarantaine", PAIR_WIDTHS, { action: "restore", viewportOnly: true }],
  ["verwijder-dialoog", "/quarantaine", PAIR_WIDTHS, { action: "delete", viewportOnly: true }],
];
const ESTOP = [["noodstop-overzicht", "/"], ["noodstop-ruimte", "/av1"], ["noodstop-aanvragen", "/aanvragen"]];

const results = existsSync(path.join(HERE, "metingen.json")) ? JSON.parse(readFileSync(path.join(HERE, "metingen.json"), "utf8")) : {};

async function storageState(username) {
  const ctx = await request.newContext({ baseURL: BASE });
  const r = await ctx.post("/api/v1/auth/login", { data: { username, password: PASSWORD } });
  if (r.status() !== 200) throw new Error(`inloggen ${username} mislukt: HTTP ${r.status()}`);
  const state = await ctx.storageState();
  await ctx.dispose();
  return state;
}

async function resolveDynamic(state) {
  const ctx = await request.newContext({ baseURL: BASE, storageState: state });
  const series = await (await ctx.get("/api/v1/library?type=tv")).json();
  const movies = await (await ctx.get("/api/v1/library?type=movie")).json();
  const titel = series.items.find((i) => i.title === "Bergpas") ?? series.items[0];
  const film = movies.items.find((i) => i.has_file) ?? movies.items[0];
  await ctx.dispose();
  return { "@titel": `/bibliotheek/${titel.instance_id}/${titel.id}`, "@film": `/bibliotheek/${film.instance_id}/${film.id}` };
}

async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  const end = Date.now() + 15_000;
  while (Date.now() < end && (await page.locator(".skeleton").count()) > 0) await page.waitForTimeout(250);
  await page.waitForTimeout(600);
}

/** Nulmeting in de pagina: doelen < 44 px, tekst < 14 px, overloop, toon-gebruik, inline styles, koppen. */
function meetInPagina(apparaatBreedte) {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" && Number(cs.opacity) > 0;
  };
  const describe = (el) => {
    const name = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("title") || "").trim().replace(/\s+/g, " ").slice(0, 40);
    const cls = typeof el.className === "string" ? el.className.split(/\s+/).filter(Boolean).slice(0, 2).join(".") : "";
    return `${el.tagName.toLowerCase()}${cls ? "." + cls : ""} "${name}"`;
  };
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=switch], [role=option], [tabindex]:not([tabindex="-1"])';
  const klein = [];
  let doelen = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el) || el.closest(".sr-only") || el.classList.contains("skip-link") || el.tagName === "MAIN") continue;
    const cs = getComputedStyle(el);
    if (el.tagName === "A" && cs.display === "inline") continue; // link in lopende tekst: uitzondering WCAG 2.5.8
    let target = el;
    if ((el.type === "checkbox" || el.type === "radio") && el.closest("label")) target = el.closest("label");
    const r = target.getBoundingClientRect();
    doelen++;
    if (r.width < 43.5 || r.height < 43.5) klein.push(`${describe(el)} ${Math.round(r.width)}×${Math.round(r.height)}`);
  }
  const hist = {};
  let kleineTekst = 0;
  let minPx = Infinity;
  const kleineVoorbeelden = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.textContent.trim()) continue;
    const el = node.parentElement;
    if (!el || seen.has(el) || !vis(el) || el.closest(".sr-only, script, style")) continue;
    seen.add(el);
    const px = parseFloat(getComputedStyle(el).fontSize);
    if (px < 13.95) {
      kleineTekst++;
      const k = String(Math.round(px * 10) / 10);
      hist[k] = (hist[k] ?? 0) + 1;
      minPx = Math.min(minPx, px);
      if (kleineVoorbeelden.length < 6) kleineVoorbeelden.push(`${describe(el)} ${px}px`);
    }
  }
  const count = (s) => document.querySelectorAll(s).length;
  const fonts = new Set();
  for (const el of [document.body, document.querySelector("h1"), document.querySelector(".mono, code")].filter(Boolean)) fonts.add(getComputedStyle(el).fontFamily.split(",")[0].trim());
  return {
    overloop_px: document.documentElement.scrollWidth - window.innerWidth,
    layout_breedte_px: Math.max(document.documentElement.scrollWidth, window.innerWidth),
    overloop_apparaat_px: Math.max(document.documentElement.scrollWidth, window.innerWidth) - apparaatBreedte,
    pagina_hoogte: document.documentElement.scrollHeight,
    h1: count("h1"),
    lettertypes: [...fonts],
    thema: document.documentElement.dataset.theme ?? null,
    doelen,
    doelen_kleiner_dan_44: klein.length,
    voorbeelden_doelen: klein.slice(0, 6),
    tekst_kleiner_dan_14: kleineTekst,
    kleinste_tekst_px: Number.isFinite(minPx) ? minPx : null,
    tekst_histogram_px: hist,
    voorbeelden_tekst: kleineVoorbeelden,
    inline_styles: count("[style]"),
    tonen: {
      chip_success: count(".chip--success"), chip_warning: count(".chip--warning"), chip_danger: count(".chip--danger"),
      chip_info: count(".chip--info"), chip_accent: count(".chip--accent"),
      chip_neutraal: count(".chip:not([class*='chip--success']):not([class*='chip--warning']):not([class*='chip--danger']):not([class*='chip--info']):not([class*='chip--accent'])"),
      kpi_success: count(".kpi--success"), kpi_warning: count(".kpi--warning"), kpi_danger: count(".kpi--danger"),
      dot_success: count(".dot--success"), t_success: count(".t-success"), stale_mark: count(".stale-mark"),
      pauze_icoon: count("svg.lucide-pause, svg.lucide-circle-pause"), cirkel_check: count("svg.lucide-circle-check, svg.lucide-circle-check-big"),
    },
    grafieken: { mg_chart: count(".mg-chart"), stackbar: count(".stackbar"), heat_cell: count(".heat-cell"), progress: count("[role=progressbar]"), role_img: count("[role=img]") },
    toestanden: [...document.querySelectorAll("[data-state]")].map((e) => e.getAttribute("data-state")),
  };
}

async function axe(page) {
  if (!AXE) return null;
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }, resultTypes: ["violations"] });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, voorbeeld: v.nodes[0]?.target?.join(" ") ?? null }));
  });
}

async function act(page, action, width) {
  if (action === "search") {
    if (width < 861) await page.getByTestId("mobile-search").click();
    const box = page.locator(width < 861 ? ".search-sheet [data-global-search]" : "[data-global-search]").first();
    await box.waitFor({ timeout: 10_000 });
    await box.fill("Berg");
    await page.waitForTimeout(1500);
  } else if (action === "profile") {
    await page.getByTestId("profile-button").click();
    await page.getByTestId("profile-menu").waitFor();
  } else if (action === "bell") {
    await page.getByTestId("bell").click();
    await page.getByTestId("notif-panel").waitFor();
  } else if (action === "help") {
    await page.locator("body").click({ position: { x: 700, y: 5 } }).catch(() => undefined);
    await page.keyboard.press("?");
    await page.getByRole("dialog").last().waitFor();
  } else if (action === "estop") {
    await page.getByTestId("profile-button").click();
    await page.getByTestId("estop-button").click();
    await page.getByTestId("estop-confirm").waitFor();
  } else if (action === "unlock") {
    await page.getByTestId("library-unlock").first().click();
    await page.getByRole("alertdialog").or(page.getByRole("dialog")).first().waitFor();
  } else if (action === "restore") {
    await page.getByRole("button", { name: /Terugzetten/ }).last().click();
    await page.getByRole("alertdialog").or(page.getByRole("dialog")).first().waitFor();
  } else if (action === "delete") {
    // via het rij-menu: op de telefoon is er geen selectiekolom (hide-mobile), dus geen bulk
    await page.getByTestId("row-menu").first().click();
    await page.locator('[role=menu] [data-testid^="delete-"]').first().click();
    await page.getByRole("alertdialog").or(page.getByRole("dialog")).first().waitFor();
  }
  await page.waitForTimeout(500);
}

async function shoot(browser, states, dyn, group, slug, route, theme, width, opts = {}) {
  const key = `${group}/${slug}-${theme[0]}-${width}`;
  if (ONLY && !ONLY.test(key)) return;
  const ctx = await browser.newContext({
    ...DEVICE[width], baseURL: BASE, deviceScaleFactor: 1, locale: opts.user === "engels" ? "en-US" : "nl-NL", timezoneId: "Europe/Amsterdam",
    colorScheme: theme[1], storageState: opts.anonymous ? undefined : states[opts.user ?? "beheerder"],
  });
  await ctx.addInitScript((t) => { try { localStorage.setItem("mg.theme", t); } catch { /* geblokkeerd */ } }, theme[1]);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  for (const [pattern, reply] of opts.routes ?? []) await page.route(pattern, (r) => r.fulfill(reply));
  if (opts.delay) await page.route("**/api/v1/**", async (r) => { if (notAuth(r.request().url())) await new Promise((res) => setTimeout(res, 20_000)); await r.continue().catch(() => undefined); });
  if (opts.down) await page.route("**/api/v1/**", (r) => (notAuth(r.request().url()) ? r.fulfill(DOWN) : r.continue()));
  if (opts.engine) await mockEngine(page);
  const target = dyn[route] ?? route;
  const file = path.join(HERE, group, `${slug}-${theme[0]}-${width}.png`);
  try {
    await page.goto(target, { waitUntil: "domcontentloaded" });
    if (opts.noSettle) await page.waitForTimeout(3_000); else await settle(page);
    if (opts.tab !== undefined) {
      const tabs = page.getByRole("tab");
      await tabs.nth(opts.tab).click();
      await settle(page);
    }
    if (opts.stale) {
      await page.route("**/api/v1/**", (r) => (notAuth(r.request().url()) ? r.fulfill(DOWN) : r.continue()));
      await page.waitForTimeout(opts.staleWait ?? 35_000);
    }
    if (opts.action) await act(page, opts.action, width);
    const meting = await page.evaluate(meetInPagina, width);
    const axeResult = await axe(page);
    await page.screenshot({ path: file, fullPage: !opts.viewportOnly, animations: "disabled" });
    results[key] = { route: target, thema: theme[0], breedte: width, gebruiker: opts.anonymous ? null : (opts.user ?? "beheerder"), nagebootst: opts.nagebootst ?? null, afdruk: opts.viewportOnly ? "viewport" : "volledige pagina", ...meting, axe: axeResult, js_fouten: errors };
    console.log(`ok   ${key}`);
  } catch (e) {
    results[key] = { route: target, thema: theme[0], breedte: width, fout: String(e).slice(0, 300) };
    console.log(`FOUT ${key}: ${String(e).split("\n")[0]}`);
  } finally {
    await ctx.close();
  }
}

async function estop(state, active) {
  const ctx = await request.newContext({ baseURL: BASE, storageState: state });
  const me = await (await ctx.get("/api/v1/auth/me")).json();
  const r = await ctx.post("/api/v1/system/emergency-stop", { data: { active, reason: active ? "Nulmeting Peil (testomgeving)" : "" }, headers: { "X-CSRF-Token": me.csrf_token ?? "" } });
  await ctx.dispose();
  if (r.status() !== 200) throw new Error(`noodstop ${active}: HTTP ${r.status()}`);
}

const browser = await chromium.launch();
const states = { beheerder: await storageState("beheerder"), kijker: await storageState("kijker"), engels: await storageState("engels") };
const dyn = await resolveDynamic(states.beheerder);
{ // aflevering: eerste afleveringslink op de titelpagina
  const ctx = await browser.newContext({ baseURL: BASE, storageState: states.beheerder });
  const page = await ctx.newPage();
  await page.goto(dyn["@titel"]); await settle(page);
  dyn["@aflevering"] = await page.locator('a[href*="/aflevering/"]').first().getAttribute("href");
  await ctx.close();
}
console.log("dynamisch:", dyn);
for (const group of ["schermen", "tabs", "toestanden"]) mkdirSync(path.join(HERE, group), { recursive: true });
if (WHAT === "schermen" || WHAT === "alles") {
  for (const theme of THEMES) for (const w of ALL_WIDTHS) for (const [slug, route] of SCREENS) await shoot(browser, states, dyn, "schermen", slug, route, theme, w);
}
if (WHAT === "tabs" || WHAT === "alles") {
  for (const theme of THEMES) for (const w of PAIR_WIDTHS) for (const [slug, route, opts] of TABS) await shoot(browser, states, dyn, "tabs", slug, route, theme, w, opts);
}
if (WHAT === "toestanden" || WHAT === "alles") {
  for (const [slug, route, widths, opts] of STATES) for (const theme of THEMES) for (const w of widths) await shoot(browser, states, dyn, "toestanden", slug, route, theme, w, opts);
}
if (WHAT === "noodstop" || WHAT === "alles") {
  await estop(states.beheerder, true);
  try {
    for (const [slug, route] of ESTOP) for (const theme of THEMES) for (const w of PAIR_WIDTHS) await shoot(browser, states, dyn, "toestanden", slug, route, theme, w, { nagebootst: null, echt: "noodstop via POST /system/emergency-stop aan en daarna weer uit" });
  } finally {
    await estop(states.beheerder, false);
  }
}
if (WHAT === "hub" || WHAT === "alles") {
  // toegestane toevoeging O-11: alleen een geldig system.hub_url geeft de link (fictief testadres, geen NAS-gegeven)
  const setHub = async (value) => {
    const ctx = await request.newContext({ baseURL: BASE, storageState: states.beheerder });
    const me = await (await ctx.get("/api/v1/auth/me")).json();
    const r = await ctx.patch("/api/v1/settings/scoped", { data: { changes: [{ scope_type: "global", scope_id: "", key: "system.hub_url", value }], reason: "Peil-afdrukken (testomgeving)" }, headers: { "X-CSRF-Token": me.csrf_token ?? "" } });
    await ctx.dispose();
    if (r.status() !== 200) throw new Error(`hub_url: HTTP ${r.status()}`);
  };
  await setHub("http://hub.example:30080");
  try {
    for (const theme of THEMES) {
      await shoot(browser, states, dyn, "toestanden", "hub-link-overzicht", "/", theme, 1440, { nagebootst: null, echt: "system.hub_url = http://hub.example:30080 (fictief testadres), daarna weer leeg", viewportOnly: true });
      await shoot(browser, states, dyn, "toestanden", "hub-link-profielmenu", "/", theme, 390, { nagebootst: null, echt: "system.hub_url = http://hub.example:30080 (fictief testadres), daarna weer leeg", action: "profile", viewportOnly: true });
    }
  } finally {
    await setHub("");
  }
}
await browser.close();
const sorted = Object.fromEntries(Object.entries(results).sort(([a], [b]) => a.localeCompare(b)));
writeFileSync(path.join(HERE, "metingen.json"), `${JSON.stringify(sorted, null, 1)}\n`);
console.log(`metingen: ${Object.keys(sorted).length} afdrukken`);
