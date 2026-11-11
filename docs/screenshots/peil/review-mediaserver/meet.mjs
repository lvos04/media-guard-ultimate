// Peil-review MediaGuard — ronde "meerdere mediaservers": onafhankelijke meting van het tabblad
// Koppelingen → Mediaservers & ondertitels (lijst, koppelstatus per server, formulieren, dialogen, kijker, en-US)
// tegen Peil 1.1.1. Hoort bij docs/ontwerp/PEIL_REVIEW.md (ronde 2); alleen-lezen hulpscript, hoort niet bij de app.
//
// De meetfuncties `meet` (contrast met opacity/alfa/arcering, UI-randen, overloop, doelen, groen, statusvorm) en
// `focusInfo` (focusring en bedekking) komen LETTERLIJK uit ../review/review.mjs, zodat beide rondes met dezelfde maat
// meten. Deze ronde voegt toe: overloop binnen een kaart of dialoog, afgekapte tekst, labels zonder veld, reden naast
// elke Onbekend/Verouderd, voorbeeldwaarden in placeholders, geknepen tekst (per letter gebroken), uitgeschakeld zonder
// zichtbare reden, zichtbare knoptekst in de toegankelijke naam, dialoogfocus (start, Escape, terug naar de opener) en een
// Tab-doorloop door het tabblad zelf.
//
// Toestanden van de serverlijst: zes servers (echt, uit hulp/vul.py: gezond, storing/zonder contact, ontkoppeld, zonder
// sleutel, verouderd), en daarnaast nagebootst in de browser (GET /mediaserver/status ingekort of aangepast, de rest is
// het echte antwoord van de core; in review.json staat dan "nagebootst"): één server, twee servers en een ontkoppelde
// server 1. Rollen: beheerder, operator, kijker en een beheerder met en-US.
//
// Gebruik (app gestart met hulp/start.sh en gevuld met hulp/vul.py):
//   MG_PEIL_BASE_URL=http://127.0.0.1:18400 MG_PEIL_CORE_URL=http://127.0.0.1:18390 MG_PEIL_PASSWORD=… \
//   PEIL_PLAYWRIGHT=…/playwright/index.mjs PEIL_AXE=…/axe-core/axe.min.js PEIL_UIT=/map/buiten/de/repo \
//   node docs/screenshots/peil/review-mediaserver/meet.mjs [schermen|toetsenbord|dialogen|alles]
// Het wachtwoord komt alleen uit de omgeving en wordt nergens weggeschreven. PEIL_UIT is verplicht en moet buiten de
// repo liggen: de afdrukken en review.json bevatten de meetuitvoer en horen niet in deze openbare repo (V-023).
// Exitcode 1 als een meting een fout gaf of als er minder metingen zijn dan de matrix voorschrijft (V-005).
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.MG_PEIL_BASE_URL ?? "http://127.0.0.1:18400";
const PASSWORD = process.env.MG_PEIL_PASSWORD ?? "";
const WHAT = process.argv[2] ?? "alles";
const ONLY = process.env.PEIL_ALLEEN ? new RegExp(process.env.PEIL_ALLEEN) : null;
const PAR = Number(process.env.PEIL_PARALLEL ?? 3);
if (!PASSWORD) throw new Error("MG_PEIL_PASSWORD is verplicht");
if (!process.env.PEIL_AXE) throw new Error("PEIL_AXE is verplicht (pad naar axe.min.js)");
if (!process.env.PEIL_UIT) throw new Error("PEIL_UIT is verplicht: een map buiten de repo");
// de repo-root ligt vier mappen boven deze map (docs/screenshots/peil/review-mediaserver)
const REPO = path.resolve(HERE, "..", "..", "..", "..");
const OUT = path.resolve(process.env.PEIL_UIT);
if (OUT === REPO || OUT.startsWith(REPO + path.sep)) throw new Error(`PEIL_UIT ligt in de repo (${path.relative(REPO, OUT) || "."}): kies een map erbuiten`);
const AXE_SRC = readFileSync(process.env.PEIL_AXE, "utf8");
const { chromium, request } = await import(process.env.PEIL_PLAYWRIGHT ?? "playwright");

// dezelfde maat als de eerste reviewronde
const REVIEW = readFileSync(path.join(HERE, "..", "review", "review.mjs"), "utf8");
const cut = (from, to) => { const i = REVIEW.indexOf(from); const j = REVIEW.indexOf(to, i); if (i < 0 || j < 0) throw new Error(`review.mjs: ${from} niet gevonden`); return REVIEW.slice(i, j).trim(); };
const MEET = `(${cut("function meet(opts) {", "async function axe(page)")})({})`;
const FOCUS = `(${cut("function focusInfo() {", "async function tabWalk(")})()`;

const ROUTE = "/inrichten?tab=mediaserver";
const THEMES = [["licht", "light"], ["donker", "dark"]];
const WIDTHS = [320, 360, 390, 768, 1024, 1440];
const dev = (w) => ({ viewport: { width: w, height: w < 640 ? 800 : w < 1024 ? 1024 : 900 }, isMobile: w < 768, hasTouch: w < 1024 });
const json = (status, body) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function storageState(username) {
  const ctx = await request.newContext({ baseURL: BASE });
  const r = await ctx.post("/api/v1/auth/login", { data: { username, password: PASSWORD } });
  if (r.status() !== 200) throw new Error(`inloggen ${username} mislukt: HTTP ${r.status()}`);
  const state = await ctx.storageState();
  await ctx.dispose();
  return state;
}

async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  const end = Date.now() + 15_000;
  while (Date.now() < end && (await page.locator(".skeleton").count()) > 0) await page.waitForTimeout(250);
  await page.getByTestId("mediaserver-tab").waitFor({ timeout: 15_000 }).catch(() => undefined);
  await page.waitForTimeout(400);
}

// ---------------------------------------------------------------- extra meting van deze ronde (in de pagina)
function extra() {
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width >= 1 && r.height >= 1 && cs.visibility !== "hidden" && cs.display !== "none" && !el.closest(".sr-only,[hidden]"); };
  const txt = (el) => (el.textContent || "").trim().replace(/\s+/g, " ");
  const desc = (el) => `${el.tagName.toLowerCase()}${el.getAttribute("data-testid") ? `[${el.getAttribute("data-testid")}]` : ""} "${(el.getAttribute("aria-label") || txt(el)).slice(0, 60)}"`;
  const scope = document.querySelector("[role=alertdialog],[role=dialog]") ?? document.querySelector('[data-testid="mediaserver-tab"]') ?? document.body;
  const res = { buiten_kaart: [], afgekapt: [], labels_zonder_veld: [], velden_zonder_naam: [], placeholders: [], servers: [], onbekend_zonder_reden: [], live: [], knoppen: [], uitgeschakeld_zonder_reden: [], naam_zonder_zichtbare_tekst: [], geknepen: [], dialoog: null };

  // tekst die uit zijn kaart, rij of dialoog steekt, of die wordt afgesneden (overflow hidden/clip)
  for (const el of scope.querySelectorAll("*")) {
    if (!vis(el) || el instanceof SVGElement) continue;
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own && !["INPUT", "BUTTON"].includes(el.tagName)) continue;
    const box = el.closest(".card,[role=alertdialog],[role=dialog]");
    const r = el.getBoundingClientRect();
    if (box) { const b = box.getBoundingClientRect(); if (r.right > b.right + 1 || r.left < b.left - 1) res.buiten_kaart.push(`${desc(el)} ${Math.round(r.left)}–${Math.round(r.right)} buiten ${Math.round(b.left)}–${Math.round(b.right)}`); }
    const cs = getComputedStyle(el);
    if (el.tagName !== "INPUT" && el.scrollWidth > el.clientWidth + 1 && ["hidden", "clip"].includes(cs.overflowX)) res.afgekapt.push(`${desc(el)} ${el.scrollWidth}>${el.clientWidth}`);
    // geknepen: tekst van zes tekens of meer in een blok smaller dan 64 px dat over meer dan twee regels breekt
    // (zo breekt een regel per letter; zelfde gedachte als ../review/knijp.mjs)
    const eigen = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
    const lh = parseFloat(cs.lineHeight) || 20;
    if (eigen.length >= 6 && cs.display !== "inline" && r.width < 64 && r.height > lh * 2.5) res.geknepen.push(`${desc(el)} ${Math.round(r.width)}×${Math.round(r.height)}`);
  }
  // labels en velden
  for (const l of scope.querySelectorAll("label[for]")) {
    if (!vis(l)) continue;
    const target = document.getElementById(l.getAttribute("for"));
    if (!target) res.labels_zonder_veld.push(`${desc(l)} for="${l.getAttribute("for")}"`);
    else if (!target.matches("input,select,textarea,button,meter,progress,output")) res.labels_zonder_veld.push(`${desc(l)} for="${l.getAttribute("for")}" wijst naar <${target.tagName.toLowerCase()}>`);
  }
  for (const f of scope.querySelectorAll("input:not([type=hidden]),select,textarea")) {
    if (!vis(f)) continue;
    const named = f.getAttribute("aria-label") || f.getAttribute("aria-labelledby") || (f.id && document.querySelector(`label[for="${CSS.escape(f.id)}"]`)) || f.closest("label");
    if (!named) res.velden_zonder_naam.push(desc(f));
    if (f.getAttribute("placeholder")) res.placeholders.push(`${f.id || f.name || f.tagName}: "${f.getAttribute("placeholder")}"`);
  }
  // per server: status (vorm + icoon + woord) en de reden ernaast
  for (const row of document.querySelectorAll('[data-testid^="ms-server-"]')) {
    const id = row.getAttribute("data-testid").slice(10);
    const pills = [...row.querySelectorAll(".chip,.stale-mark,[data-status]")].filter(vis).map((c) => {
      const svg = c.querySelector("svg"); const cs = getComputedStyle(c);
      return { woord: txt(c), status: c.getAttribute("data-status"), klasse: [...c.classList].filter((x) => x.startsWith("chip--") || x === "stale-mark" || x === "arcering").join(" "),
        icoon: svg ? ([...svg.classList].find((x) => x.startsWith("lucide-") && x !== "lucide") ?? "svg") : null, rand: `${cs.borderTopStyle} ${cs.borderTopWidth}`, kleur: cs.color, vlak: cs.backgroundColor, arcering: cs.backgroundImage !== "none" };
    });
    const why = row.querySelector(`[data-testid="ms-why-${id}"]`);
    res.servers.push({ id, naam: txt(row.querySelector("h3,.list-title") ?? row).slice(0, 60), pillen: pills, reden: why ? txt(why) : null });
    for (const p of pills) if (/unknown|stale/.test(`${p.status} ${p.klasse}`) && !(why && txt(why).length > 8)) res.onbekend_zonder_reden.push(`server ${id}: ${p.woord}`);
  }
  for (const el of scope.querySelectorAll("[role=status],[role=alert],[aria-live]")) if (vis(el) && txt(el)) res.live.push(`${el.getAttribute("role") ?? "aria-live=" + el.getAttribute("aria-live")}: ${txt(el).slice(0, 120)}`);
  for (const b of scope.querySelectorAll("button,[role=button],a.btn")) {
    if (!vis(b)) continue;
    const r = b.getBoundingClientRect();
    res.knoppen.push(`${(b.getAttribute("aria-label") || txt(b)).slice(0, 60)}${b.disabled ? " (uit)" : ""} ${Math.round(r.width)}×${Math.round(r.height)}`);
    // WCAG 2.5.3: de zichtbare tekst van een knop staat in zijn toegankelijke naam
    const zichtbaar = txt(b).toLowerCase(); const naam = (b.getAttribute("aria-label") || "").toLowerCase();
    if (naam && zichtbaar && !naam.includes(zichtbaar)) res.naam_zonder_zichtbare_tekst.push(`"${txt(b)}" ↔ aria-label "${b.getAttribute("aria-label")}"`);
  }
  // uitgeschakeld = zichtbaar met een reden: aria-describedby naar een zichtbare tekst (of een title)
  for (const c of scope.querySelectorAll("button:disabled,input:disabled,select:disabled,textarea:disabled")) {
    if (!vis(c)) continue;
    const ids = (c.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    const reden = ids.map((id) => document.getElementById(id)).some((el) => el && vis(el) && txt(el).length > 8);
    if (!reden && !c.getAttribute("title")) res.uitgeschakeld_zonder_reden.push(`${c.tagName.toLowerCase()} ${(c.getAttribute("aria-label") || c.id || txt(c)).slice(0, 60)}`);
  }
  const dlg = document.querySelector("[role=alertdialog],[role=dialog]");
  if (dlg) {
    const cs = getComputedStyle(dlg); const a = document.activeElement;
    const titel = dlg.querySelector("h1,h2,h3,.dialog-title");
    res.dialoog = { rol: dlg.getAttribute("role"), modal: dlg.getAttribute("aria-modal"), naam: titel ? txt(titel) : null, beschreven: Boolean(dlg.getAttribute("aria-describedby")),
      gevaar: dlg.classList.contains("dialog--danger"), rand: cs.borderTopColor, focus: a && dlg.contains(a) ? (a.getAttribute("aria-label") || txt(a)).slice(0, 50) : "(buiten de dialoog)",
      knoppen: [...dlg.querySelectorAll("button")].map((b) => `${txt(b)} [${[...b.classList].filter((x) => x.startsWith("btn--") || /danger|primary|secondary|ghost/.test(x)).join(" ")}]`),
      past: dlg.getBoundingClientRect().right <= innerWidth + 1 && dlg.getBoundingClientRect().bottom <= innerHeight + 1, schuift: dlg.scrollHeight > dlg.clientHeight + 1 };
  }
  for (const k of ["buiten_kaart", "afgekapt", "geknepen"]) res[k] = [...new Set(res[k])].slice(0, 15);
  res.uitgeschakeld_zonder_reden = [...new Set(res.uitgeschakeld_zonder_reden)];
  return res;
}

async function axe(page) {
  await page.addScriptTag({ content: AXE_SRC });
  return page.evaluate(async () => {
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }, resultTypes: ["violations", "incomplete"] });
    return {
      overtredingen: r.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, voorbeelden: v.nodes.slice(0, 3).map((n) => `${n.target.join(" ")} — ${(n.failureSummary || "").split("\n").slice(1, 2).join(" ").slice(0, 160)}`) })),
      onvolledig: r.incomplete.map((v) => ({ id: v.id, n: v.nodes.length })),
    };
  });
}

async function newPage(browser, states, theme, w, user = "beheerder") {
  const ctx = await browser.newContext({ ...dev(w), baseURL: BASE, deviceScaleFactor: 1, locale: user === "engels" ? "en-US" : "nl-NL", timezoneId: "Europe/Amsterdam",
    colorScheme: theme[1], storageState: states[user], reducedMotion: "reduce" });
  await ctx.addInitScript((t) => { try { localStorage.setItem("mg.theme", t); } catch { /* geblokkeerd */ } }, theme[1]);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return { ctx, page, errors };
}

// ---------------------------------------------------------------- nagebootste "alles gevuld"-toestand
// De echte testdata heeft geen afspeeltests, acties, kijkers of gevonden servers (er draait geen encoder en er wordt
// niet op het netwerk gezocht). Deze toestand vult GET /mediaserver/status in de browser aan met die onderdelen en
// met lange namen en adressen; de rest van het antwoord is het echte antwoord van de core. Sim-getest.
const ago = (ms) => new Date(Date.now() - ms).toISOString();
// Past alleen het antwoord van GET /mediaserver/status in de browser aan; de rest van het antwoord is echt.
async function mockStatus(page, change) {
  await page.route("**/api/v1/mediaserver/status", async (route) => {
    const real = await route.fetch();
    const s = await real.json();
    change(s);
    await route.fulfill(json(200, s));
  });
}
// Eén server: alleen server 1 (de bestaande koppeling), zoals bij wie alleen Emby heeft.
const alleenServer1 = (s) => { s.servers = (s.servers ?? []).filter((x) => x.server_id === 1); };
// Twee servers: server 1 (Emby) en server 2 (Jelly Leon), beide echt gemeten.
const tweeServers = (s) => { s.servers = (s.servers ?? []).filter((x) => x.server_id <= 2); };
// Server 1 ontkoppeld: zoals de core hem dan geeft (enabled/followed/connected uit, adres en sleutel bewaard).
const server1Ontkoppeld = (s) => {
  s.servers = (s.servers ?? []).filter((x) => x.server_id <= 2);
  const een = s.servers.find((x) => x.server_id === 1);
  if (een) Object.assign(een, { enabled: false, followed: false, connected: false, hold: { active: false } });
};
async function mockGevuld(page) {
  await page.route("**/api/v1/mediaserver/status", async (route) => {
    const real = await route.fetch();
    const s = await real.json();
    const srv = s.servers ?? [];
    if (srv[0]) { srv[0].path_map = [["/data/media/films-en-documentaires-in-4k-hdr", "/mnt/bibliotheek/films-en-documentaires-in-4k-hdr"], ["/data/media/tv", "/mnt/bibliotheek/series"]]; srv[0].results = { open: 1, ok: 12, transcode: 3, failed: 1, not_found: 1, nl_visible: 9, nl_missing: 4 }; srv[0].hold = { active: true }; }
    if (srv[1]) { srv[1].label = "Jelly Leon in de woonkamer op de nieuwe mediaserver"; srv[1].url = "http://een-heel-lange-hostnaam-voor-de-mediaserver.thuisnetwerk.example:8096/jellyfin"; srv[1].results = { open: 0, ok: 10, transcode: 2, failed: 0, not_found: 0, nl_visible: 7, nl_missing: 1 }; }
    s.servers = srv;
    s.hold = { active: true, reason: "er kijkt iemand via Emby (Zomerlicht)" };
    s.sessions = { watching: [{ user: "kijker", title: "Zomerlicht (2021)", paused: false }, { user: "gast", title: "Bergpas — S01E03 — De oversteek bij de noordelijke pas", paused: true }] };
    s.discovery = { servers: [{ kind: "jellyfin", url: "http://mediaserver.example:8096", name: "Zolder", label: "Jellyfin" }, { kind: "plex", url: "http://mediaserver.example:32400", name: "Plex", label: "Plex" }], bazarr: [{ url: "http://mediaserver.example:6767" }] };
    s.tests = [
      { path: "/data/media/movies/Zomerlicht (2021)/Zomerlicht (2021) - 2160p HDR10 AV1.mkv", result: "ok", detail_nl: "speelt direct af", done_at: ago(9e5), servers: [
        { server_id: 1, result: "ok", detail_nl: null, nl_subtitles: "visible", label: "Emby" }, { server_id: 2, result: "transcode", detail_nl: null, nl_subtitles: "missing", label: "Jelly Leon" }] },
      { path: "/data/media/tv/Bergpas/Season 01/Bergpas - S01E01.mkv", result: "failed", detail_nl: "afspelen mislukt", done_at: ago(36e5), servers: [
        { server_id: 1, result: "failed", detail_nl: null, nl_subtitles: null, label: "Emby" }, { server_id: 2, result: "ok", detail_nl: null, nl_subtitles: "visible", label: "Jelly Leon" }] },
      { path: "/data/media/tv/Stadshaven/Season 01/Stadshaven - S01E04.mkv", result: "transcode", detail_nl: "via omzetten", done_at: ago(72e5) },
      { path: "/data/media/anime/Sterrenveld/Season 01/Sterrenveld - 01.mkv", result: "not_found", detail_nl: "niet gevonden", done_at: ago(9e6) },
    ];
    s.actions = [
      { ts: ago(8e5), kind: "refresh", ok: 1, detail_nl: "Emby: Zomerlicht (2021) ververst na AV1-vervanging", server_id: 1 },
      { ts: ago(85e4), kind: "refresh", ok: 0, detail_nl: "Jellyfin woonkamer: verversen mislukt (niet bereikbaar)", server_id: 3 },
    ];
    s.subtitles = { ...s.subtitles, missing: 4 };
    await route.fulfill(json(200, s));
  });
}

// ---------------------------------------------------------------- toestanden
// [slug, gebruiker, handeling, opties]; een handeling doet wat een gebruiker doet (klikken, typen), maar bevestigt
// nooit een dialoog en slaat niets op.
const rij = (page, id) => page.getByTestId(`ms-server-${id}`);
const STATES = [
  ["lijst", "beheerder", null, { shot: true }],
  ["toevoegen", "beheerder", async (page) => { await page.getByTestId("ms-add-open").click(); await page.getByTestId("ms-add-form").waitFor(); await page.getByTestId("ms-add-form").scrollIntoViewIfNeeded(); }, { shot: true }],
  ["wijzigen", "beheerder", async (page) => { await rij(page, 2).getByRole("button", { name: /wijzigen/i }).click(); await page.locator("#ms-url-2").waitFor(); }, { shot: true }],
  ["dialoog-ontkoppelen", "beheerder", async (page) => { await rij(page, 2).getByRole("button", { name: /^ontkoppelen…/i }).click(); await page.getByRole("alertdialog").waitFor(); }, { shot: true, viewportOnly: true }],
  ["dialoog-verwijderen", "beheerder", async (page) => { await rij(page, 3).getByRole("button", { name: /^verwijderen…/i }).click(); await page.getByRole("alertdialog").waitFor(); }, { shot: true, viewportOnly: true }],
  ["test-geslaagd", "beheerder", async (page) => { await rij(page, 2).getByRole("button", { name: /^verbinding testen/i }).click(); await page.locator('[data-testid="ms-result-2"] .inline-state').waitFor(); await page.waitForTimeout(500); }, { shot: true, widths: [390, 1440] }],
  ["test-mislukt", "beheerder", async (page) => { await rij(page, 3).getByRole("button", { name: /^verbinding testen/i }).click(); await page.locator('[data-testid="ms-result-3"] .inline-state').waitFor(); await page.waitForTimeout(500); }, { shot: true, widths: [390, 1440] }],
  ["gevuld", "beheerder", null, { shot: true, mock: true, nagebootst: "GET /mediaserver/status aangevuld met afspeeltests, acties, kijkers, gevonden servers en lange namen (rest = echt antwoord van de core)" }],
  ["gevuld-toevoegen", "beheerder", async (page) => { await page.getByTestId("ms-add-open").click(); await page.getByTestId("ms-add-form").waitFor(); await page.getByTestId("ms-add-form").scrollIntoViewIfNeeded(); }, { shot: true, mock: true, widths: [320, 390, 1440], nagebootst: "als gevuld; formulier met 'Gevonden op je netwerk'" }],
  ["een-server", "beheerder", null, { shot: true, mock: alleenServer1, nagebootst: "GET /mediaserver/status ingekort tot server 1 (rest = echt antwoord van de core)" }],
  ["twee-servers", "beheerder", null, { shot: true, mock: tweeServers, nagebootst: "GET /mediaserver/status ingekort tot server 1 en 2 (rest = echt antwoord van de core)" }],
  ["server1-ontkoppeld", "beheerder", null, { shot: true, mock: server1Ontkoppeld, nagebootst: "GET /mediaserver/status: server 1 als ontkoppeld (enabled/followed/connected uit), server 2 echt" }],
  ["kijker", "kijker", null, { shot: true, widths: [390, 1440] }],
  ["operator", "operator", null, { shot: true, widths: [390, 1440] }],
  ["operator-test-mislukt", "operator", async (page) => { await rij(page, 3).getByRole("button", { name: /^verbinding testen/i }).click(); await page.locator('[data-testid="ms-result-3"] .inline-state').waitFor(); await page.waitForTimeout(500); }, { shot: true, widths: [390, 1440] }],
  ["en-us-lijst", "engels", null, { shot: true }],
  ["en-us-een-server", "engels", null, { shot: true, mock: alleenServer1, widths: [390, 1440], nagebootst: "als een-server" }],
  ["en-us-server1-ontkoppeld", "engels", null, { shot: true, mock: server1Ontkoppeld, widths: [390, 1440], nagebootst: "als server1-ontkoppeld" }],
  ["en-us-toevoegen", "engels", async (page) => { await page.getByTestId("ms-add-open").click(); await page.getByTestId("ms-add-form").waitFor(); await page.getByTestId("ms-add-form").scrollIntoViewIfNeeded(); }, { shot: true, widths: [320, 390, 1440] }],
  ["en-us-dialoog-ontkoppelen", "engels", async (page) => { await rij(page, 2).getByRole("button", { name: /^unlink…/i }).click(); await page.getByRole("alertdialog").waitFor(); }, { shot: true, viewportOnly: true, widths: [320, 390, 1440] }],
  ["en-us-gevuld", "engels", null, { shot: true, mock: true, widths: [320, 390, 1440], nagebootst: "als gevuld; teksten die de core aanlevert (acties, redenen) blijven Nederlands" }],
];

const results = {};
async function pool(jobs, n, fn) { let i = 0; await Promise.all(Array.from({ length: n }, async () => { while (i < jobs.length) { const j = jobs[i++]; await fn(j); } })); }

async function audit(browser, states, slug, user, action, theme, w, opts) {
  const key = `schermen/${slug}-${theme[0]}-${w}`;
  if (ONLY && !ONLY.test(key)) return;
  const { ctx, page, errors } = await newPage(browser, states, theme, w, user);
  try {
    if (opts.mock === true) await mockGevuld(page);
    else if (opts.mock) await mockStatus(page, opts.mock);
    await page.goto(ROUTE, { waitUntil: "domcontentloaded" }); await settle(page);
    if (action) await action(page, w);
    await page.waitForTimeout(300);
    const m = await page.evaluate(MEET);
    const x = await page.evaluate(extra);
    const a = await axe(page);
    if (opts.shot) await page.screenshot({ path: path.join(OUT, "afdrukken", `${slug}-${theme[0]}-${w}.png`), fullPage: !opts.viewportOnly, animations: "disabled" });
    results[key] = { toestand: slug, gebruiker: user, thema: theme[0], breedte: w, nagebootst: opts.nagebootst ?? null, ...m, extra: x, axe: a, js_fouten: errors };
    const nv = a.overtredingen.reduce((s, v) => s + v.n, 0);
    console.log(`ok   ${key}  axe=${nv} contrast=${m.tekst_te_laag_n} randen=${m.randen_te_laag_n} doelen<44=${m.doelen_klein_n} overloop=${m.overloop} buitenkaart=${x.buiten_kaart.length} afgekapt=${x.afgekapt.length} geknepen=${x.geknepen.length} labels=${x.labels_zonder_veld.length} groen=${m.groen_n}/${m.andere_groen_n} vorm=${m.status_vorm.fouten.length} zonderreden=${x.onbekend_zonder_reden.length}`);
  } catch (e) {
    results[key] = { toestand: slug, thema: theme[0], breedte: w, fout: String(e).slice(0, 300) };
    console.log(`FOUT ${key}: ${String(e).split("\n")[0]}`);
  } finally { await ctx.close(); }
}

// Tab-doorloop door het tabblad: elke stop heeft een zichtbare ring (≥ 3:1) en is niet bedekt door een vaste balk
async function tabWalk(browser, states, slug, user, action, theme, w) {
  const key = `toetsenbord/${slug}-${theme[0]}-${w}`;
  if (ONLY && !ONLY.test(key)) return;
  const { ctx, page } = await newPage(browser, states, theme, w, user);
  try {
    await page.goto(ROUTE, { waitUntil: "domcontentloaded" }); await settle(page);
    if (action) await action(page, w);
    // begin vlak vóór het tabblad: focus op het eerste focusbare element erin, dan één stap terug
    await page.evaluate(() => { const first = document.querySelector('[data-testid="mediaserver-tab"]')?.querySelector("button,a[href],input,[tabindex]"); first?.focus(); });
    await page.keyboard.press("Shift+Tab");
    const stops = [];
    for (let i = 0; i < 90; i++) {
      await page.keyboard.press("Tab"); await page.waitForTimeout(50);
      const inTab = await page.evaluate(() => Boolean(document.activeElement?.closest('[data-testid="mediaserver-tab"]')));
      if (!inTab) { if (stops.length) break; continue; }
      stops.push(await page.evaluate(FOCUS));
    }
    const ok = stops.filter(Boolean);
    results[key] = { toestand: slug, thema: theme[0], breedte: w, stops: ok.length, volgorde: ok.map((s) => s.el), zonder_ring: ok.filter((s) => s.outline === "geen").map((s) => s.el), ring_onder_3: ok.filter((s) => s.outline !== "geen" && s.ring < 3).map((s) => `${s.el} ${s.ring}`),
      bedekt: ok.filter((s) => s.bedekt > 0).map((s) => `${s.el} ${s.bedekt}/5`), laagste_ring: Math.min(...ok.filter((s) => s.ring).map((s) => s.ring)) };
    console.log(`ok   ${key} stops=${ok.length} zonderRing=${results[key].zonder_ring.length} ring<3=${results[key].ring_onder_3.length} bedekt=${results[key].bedekt.length} laagste=${results[key].laagste_ring}`);
  } catch (e) { results[key] = { fout: String(e).slice(0, 300) }; console.log(`FOUT ${key}: ${String(e).split("\n")[0]}`); } finally { await ctx.close(); }
}

// Dialogen met het toetsenbord: openen met Enter, focus start op Annuleren, Tab blijft binnen de dialoog, Escape sluit
// en de focus keert terug naar de knop waarmee je opende. Bevestigt niets.
async function dialogWalk(browser, states, slug, user, naam, id, theme, w) {
  const key = `dialogen/${slug}-${theme[0]}-${w}`;
  if (ONLY && !ONLY.test(key)) return;
  const { ctx, page } = await newPage(browser, states, theme, w, user);
  try {
    await page.goto(ROUTE, { waitUntil: "domcontentloaded" }); await settle(page);
    const opener = rij(page, id).getByRole("button", { name: naam });
    await opener.focus(); await page.keyboard.press("Enter");
    const dlg = page.getByRole("alertdialog"); await dlg.waitFor(); await page.waitForTimeout(250);
    const start = await page.evaluate(FOCUS);
    const info = (await page.evaluate(extra)).dialoog;
    const ring = [start];
    for (let i = 0; i < 4; i++) { await page.keyboard.press("Tab"); await page.waitForTimeout(50); ring.push(await page.evaluate(FOCUS)); }
    const binnen = await page.evaluate(() => Boolean(document.activeElement?.closest("[role=alertdialog]")));
    await page.keyboard.press("Escape"); await page.waitForTimeout(350);
    const dicht = (await dlg.count()) === 0;
    const terug = await page.evaluate(() => (document.activeElement?.textContent || "").trim().replace(/\s+/g, " ").slice(0, 50));
    results[key] = { toestand: slug, thema: theme[0], breedte: w, ...info, focus_start: start?.el ?? null, ring_start: start?.ring ?? null, tab_volgorde: ring.map((s) => s?.el ?? "(geen)"), tab_blijft_binnen: binnen, escape_sluit: dicht, focus_terug_op: terug };
    console.log(`ok   ${key} rol=${info?.rol} gevaar=${info?.gevaar} start=${start?.el} binnen=${binnen} esc=${dicht} terug="${terug}"`);
  } catch (e) { results[key] = { fout: String(e).slice(0, 300) }; console.log(`FOUT ${key}: ${String(e).split("\n")[0]}`); } finally { await ctx.close(); }
}

// ---------------------------------------------------------------- start
const browser = await chromium.launch();
const states = Object.fromEntries(await Promise.all(["beheerder", "operator", "kijker", "engels"].map(async (u) => [u, await storageState(u)])));
mkdirSync(path.join(OUT, "afdrukken"), { recursive: true });
// De core telt een geslaagd contact 10 minuten mee (daarna: verouderd). Zonder achtergrondtaken in de testomgeving
// meet niemand opnieuw, dus dit script drukt zelf elke 4 minuten op "Verbinding testen" voor de servers die gezond
// horen te zijn (MG_PEIL_VERS, standaard 1 en 2) — nooit voor de server die verouderd moet blijven.
const VERS = (process.env.MG_PEIL_VERS ?? "1,2").split(",").map(Number).filter(Boolean);
async function houdVers() {
  const ctx = await request.newContext({ baseURL: BASE, storageState: states.beheerder });
  try {
    const me = await (await ctx.get("/api/v1/auth/me")).json();
    for (const id of VERS) await ctx.post(id === 1 ? "/api/v1/mediaserver/test" : `/api/v1/mediaserver/test?server_id=${id}`, { headers: { "X-CSRF-Token": me.csrf_token ?? "" } });
  } finally { await ctx.dispose(); }
}
await houdVers();
const versTimer = setInterval(() => void houdVers().catch((e) => console.log(`vers houden mislukt: ${e}`)), 240_000);
const resFile = path.join(OUT, "review.json");
if (existsSync(resFile)) Object.assign(results, JSON.parse(readFileSync(resFile, "utf8")));
const save = () => writeFileSync(resFile, JSON.stringify(Object.fromEntries(Object.entries(results).sort(([a], [b]) => a.localeCompare(b))), null, 1));

// V-005: elke meting die deze run hoort te doen, wordt geteld; ontbreekt er een of gaf er een een fout, dan faalt het script
const verwacht = [];
// een oude uitkomst uit review.json telt niet mee: wat deze run hoort te meten, begint leeg
const wanted = (key) => { if (!ONLY || ONLY.test(key)) { verwacht.push(key); delete results[key]; } };
if (WHAT === "schermen" || WHAT === "alles") {
  const jobs = [];
  for (const [slug, user, action, opts] of STATES) for (const theme of THEMES) for (const w of opts.widths ?? WIDTHS) { jobs.push([slug, user, action, theme, w, opts]); wanted(`schermen/${slug}-${theme[0]}-${w}`); }
  await pool(jobs, PAR, ([slug, user, action, theme, w, opts]) => audit(browser, states, slug, user, action, theme, w, opts));
  save();
}
if (WHAT === "toetsenbord" || WHAT === "alles") {
  const open = STATES.find((s) => s[0] === "toevoegen")[2];
  const jobs = [];
  for (const theme of THEMES) for (const w of [390, 1440]) {
    jobs.push(["lijst", "beheerder", null, theme, w]); jobs.push(["toevoegen", "beheerder", open, theme, w]);
    wanted(`toetsenbord/lijst-${theme[0]}-${w}`); wanted(`toetsenbord/toevoegen-${theme[0]}-${w}`);
  }
  await pool(jobs, PAR, ([slug, user, action, theme, w]) => tabWalk(browser, states, slug, user, action, theme, w));
  save();
}
if (WHAT === "dialogen" || WHAT === "alles") {
  const jobs = [];
  for (const theme of THEMES) for (const w of [390, 1440]) {
    jobs.push(["ontkoppelen", "beheerder", /^ontkoppelen…/i, 2, theme, w]);
    jobs.push(["verwijderen", "beheerder", /^verwijderen…/i, 3, theme, w]);
    wanted(`dialogen/ontkoppelen-${theme[0]}-${w}`); wanted(`dialogen/verwijderen-${theme[0]}-${w}`);
  }
  await pool(jobs, 2, ([slug, user, naam, id, theme, w]) => dialogWalk(browser, states, slug, user, naam, id, theme, w));
  save();
}
clearInterval(versTimer);
await browser.close();
save();
const ontbreekt = verwacht.filter((k) => !results[k]);
const fouten = verwacht.filter((k) => results[k]?.fout);
console.log(`klaar: ${resFile} — ${verwacht.length - ontbreekt.length - fouten.length} van ${verwacht.length} metingen zonder fout`);
if (!verwacht.length || ontbreekt.length || fouten.length) {
  for (const k of [...ontbreekt, ...fouten]) console.log(`ONVOLLEDIG ${k}${results[k]?.fout ? `: ${results[k].fout.split("\n")[0]}` : ": niet gemeten"}`);
  process.exitCode = 1;
}
