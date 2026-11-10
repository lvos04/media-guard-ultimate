// Peil-review MediaGuard (stap 5) — onafhankelijke controle van de omgezette web-UI tegen Peil 1.1.0.
// Eigen meting, los van de scripts van stap 2/3 (docs/screenshots/peil/voor|na): axe met ALLE impactniveaus,
// contrast gemeten in de gerenderde pagina (met opacity, alfa en arcering), UI-randen ≥ 3:1, overloop op 320 px,
// doelen ≥ 44 px op elke breedte (alleen links in lopende tekst uitgezonderd), groen alleen bij gezondheid,
// onbekend/verouderd nooit groen en altijd icoon + woord + vorm, en een Tab-doorloop met ringcontrast en bedekking.
//
// Alleen-lezen hulpscript bij docs/ontwerp/PEIL_REVIEW.md; hoort niet bij de app en wordt niet gebouwd. Het doet wat
// een gebruiker ook doet (inloggen, pagina's openen). Toestanden die de testdata niet heeft (core onbereikbaar,
// verouderd, AV1-engine actief) worden nagebootst door API-antwoorden in de browser te onderscheppen.
//
// Gebruik (app gestart zoals in docs/screenshots/peil/voor/README.md):
//   MG_PEIL_BASE_URL=http://127.0.0.1:18200 MG_PEIL_PASSWORD=… PEIL_PLAYWRIGHT=…/playwright/index.mjs \
//   PEIL_AXE=…/axe-core/axe.min.js PEIL_UIT=/map/buiten/de/repo node docs/screenshots/peil/review/review.mjs \
//   [audit|afdrukken|toetsenbord|toestanden|toevoegingen|alles]
// Het wachtwoord komt alleen uit de omgeving en wordt nergens weggeschreven.
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.MG_PEIL_BASE_URL ?? "http://127.0.0.1:18200";
const PASSWORD = process.env.MG_PEIL_PASSWORD ?? "";
const AXE_SRC = readFileSync(process.env.PEIL_AXE ?? "", "utf8");
const OUT = process.env.PEIL_UIT ?? path.join(HERE, "uit");
const WHAT = process.argv[2] ?? "alles";
const ONLY = process.env.PEIL_ALLEEN ? new RegExp(process.env.PEIL_ALLEEN) : null;
const PAR = Number(process.env.PEIL_PARALLEL ?? 3);
if (!PASSWORD) throw new Error("MG_PEIL_PASSWORD is verplicht");
const { chromium, request } = await import(process.env.PEIL_PLAYWRIGHT ?? "playwright");

const THEMES = [["licht", "light"], ["donker", "dark"]];
const AUDIT_WIDTHS = [320, 360, 390, 768, 1024, 1440];
const SHOT_WIDTHS = [360, 390, 768, 1440];
const dev = (w) => ({ viewport: { width: w, height: w < 640 ? 800 : w < 1024 ? 1024 : 900 }, isMobile: w < 768, hasTouch: w < 1024 });

// alle routes met een eigen pagina; dynamische routes (@…) worden bij de start opgezocht
const ROUTES = [
  ["overzicht", "/"], ["activiteit", "/activiteit"], ["bibliotheek", "/bibliotheek"], ["bibliotheek-titel", "@titel"],
  ["bibliotheek-film", "@film"], ["aflevering", "@aflevering"], ["ontbrekend", "/ontbrekend"], ["kalender", "/kalender"],
  ["ruimte", "/av1"], ["ruimte-wachtrij", "/av1/wachtrij"], ["ruimte-kwaliteit", "/av1/kwaliteit"], ["terugzetten", "/quarantaine"],
  ["aanvragen", "/aanvragen"], ["instellingen", "/instellingen"], ["av1-instellingen", "/av1/instellingen"],
  ["koppelingen", "/inrichten"], ["kwaliteit", "/kwaliteit"], ["migratie", "/migratie"], ["dolby-vision", "/dolby-vision"],
  ["release-lab", "/release-lab"], ["anime", "/anime"], ["ai-adviseur", "/ai"], ["systeem", "/systeem"],
  ["hoe-werkt-het", "/hoe-werkt-het"], ["instanties", "/instellingen/instanties"], ["gebruikers", "/instellingen/gebruikers"],
  ["geheimen", "/instellingen/geheimen"], ["api-sleutels", "/instellingen/api-sleutels"], ["meldingen", "/instellingen/meldingen"],
  ["beveiliging", "/instellingen/beveiliging"], ["niet-gevonden", "/bestaat-niet"], ["inloggen", "/login", { anonymous: true }],
];
// hoofdschermen voor de afdrukken (navigatie + de belangrijkste onderschermen)
const HOOFD = ["overzicht", "activiteit", "bibliotheek", "bibliotheek-titel", "ontbrekend", "kalender", "ruimte", "terugzetten",
  "aanvragen", "instellingen", "koppelingen", "systeem", "inloggen"];

const json = (status, body) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const DOWN = json(502, { detail: "Upstream service unreachable", code: "upstream_unreachable" });
const notAuth = (url) => !/\/api\/v1\/auth\//.test(url);

// nagebootste AV1-engine (zelfde vorm als e2e/studio-av1.spec.ts; fictieve titels)
const ago = (ms) => new Date(Date.now() - ms).toISOString();
const SYS = { cpu_percent: 41, cpu_temp: null, geheugen: { totaal_bytes: 16e9, gebruikt_bytes: 6e9, beschikbaar_bytes: 10e9, percent: 37 },
  gpu: { video_percent: 55, render_percent: 20, freq_mhz: null, power_w: null, package_power_w: null, platform_power_w: null, platform_power_beschikbaar: false, beschikbaar: true },
  schijf_vrij_gb: 900, schijf_vrij_bytes: 900e9, schijf_gereserveerd_bytes: 0, schijf_beschikbaar_na_reservering: 900e9, cache_status: "voldoende",
  cache_waarschuwing_gb: 250, cache_pauze_gb: 150, effectieve_cache_waarschuwing_gb: 250, effectieve_cache_pauze_gb: 150, opslagfout: null,
  opslagmodus: "aparte_schijf", systeemschijf_reserve_gb: 150 };
const TICK = () => JSON.stringify({ type: "tick", tijd: new Date().toISOString().slice(0, 10), systeem: SYS, wachtrij: { wachtend: 5, bezig: 2, klaar_vandaag: 2, fouten: 1 },
  workers: [
    { baan: "gpu", slot: 1, aantal: 1, job_id: 7, status: "bezig", fase: "encode", fase_gestart_op: ago(6e5), gestart_op: ago(6e5), bestand: "/data/media/movies/Zomerlicht (2021)/Zomerlicht.mkv", encoder: "av1_qsv", percent: 55, fps: 96, speed: 4, eta_seconden: 900, bitrate_kbps: 9000, grootte_bron: 40e9, grootte_nu: 8e9, gepauzeerd: false },
    { baan: "cpu", slot: 1, aantal: 1, job_id: 9, status: "bezig", fase: "encode", fase_gestart_op: ago(6e5), gestart_op: ago(6e5), bestand: "/data/media/tv/Bergpas/Season 01/Bergpas - S01E01.mkv", encoder: "libsvtav1", percent: 20, fps: 30, speed: 1.2, eta_seconden: 3000, grootte_bron: 3e9, grootte_nu: 5e8, gepauzeerd: true },
  ] });
const DAGEN = Array.from({ length: 14 }, (_, i) => new Date(Date.now() - (13 - i) * 864e5).toISOString().slice(0, 10));
async function mockEngine(page) {
  await page.route("**/api/v1/encoder/**", async (route) => {
    const p = new URL(route.request().url()).pathname.replace("/api/v1/encoder", "");
    const ok = (body) => route.fulfill(json(200, body));
    if (p === "/statistieken") return ok({ totaal: { bespaard_bytes: 2.5e12, gemiddeld: 41.5, jobs: 120 }, per_dag: DAGEN.filter((_, i) => i !== 9).map((dag, i) => ({ dag, bespaard_bytes: (40 + ((i * 37) % 60)) * 1e9, jobs: 2 + (i % 4) })), doorvoer_bytes_per_dag_7d: 1.1e11, resterend_bytes: 4e12, prognose_klaar: "2026-12-20" });
    if (p === "/systeem/diagnose") return ok({ dependencies: { tools: { dovi_tool: true }, ffmpeg_version: "8.0.1", libsvtav1: true, av1_qsv: true, dolbyvision: true, hdr10plus: true, render_node: { pad: "/dev/dri/renderD128", aanwezig: true, leesbaar: true, schrijfbaar: true }, qsv_gereed: true } });
    // zelfde vorm als de engine (lib/ws.tsx Metric); minuten 40–44 zonder meting (gat)
    if (p === "/systeem/metingen") return ok(Array.from({ length: 60 }, (_, i) => ({ tijd: ago((59 - i) * 60_000), cpu: i >= 40 && i < 45 ? null : 35 + ((i * 7) % 25), gpuVideo: i >= 40 && i < 45 ? null : 50 + ((i * 3) % 20), gpuRender: 20, gpuPower: null, packagePower: null, platformPower: null, memoryPercent: i >= 40 && i < 45 ? null : 37 + (i % 5), memoryUsedBytes: 6e9, memoryCachePercent: 12 })));
    if (p === "/instellingen") return ok({ meld_browser_fouten: false, meld_browser_batches: false });
    if (p === "/jobs") return ok({ items: [
      { id: 7, status: "bezig", baan: "gpu", encoder: "av1_qsv", pad: "/data/media/movies/Zomerlicht (2021)/Zomerlicht.mkv", bron_grootte: 40e9, percent: 55, reden: "bitrate boven drempel", gestart_op: ago(6e5), fase: "encode" },
      { id: 10, status: "mislukt", baan: "cpu", encoder: "libsvtav1", pad: "/data/media/tv/Stadshaven/Season 01/Stadshaven - S01E04.mkv", bron_grootte: 2e9, percent: 0, reden: "VMAF te laag", fout: "Kwaliteit te laag: VMAF 81,5 onder 93,0 — origineel blijft staan", gestart_op: ago(6e5) },
      { id: 11, status: "wachtend", baan: "gpu", encoder: "av1_qsv", pad: "/data/media/anime/Sterrenveld/Season 01/Sterrenveld - 01.mkv", bron_grootte: 1.4e9, percent: 0, reden: "codec h264" },
    ], totaal: 3, pagina: 1, per_pagina: 250 });
    return ok({});
  });
  await page.routeWebSocket("**/api/v1/encoder/ws", (ws) => { ws.send(TICK()); });
}

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
  await page.waitForTimeout(500);
}

// ---------------------------------------------------------------- meting in de pagina
function meet(opts) {
  const W = document.documentElement.clientWidth;
  const cv = document.createElement("canvas"); cv.width = cv.height = 1;
  const cx = cv.getContext("2d", { willReadFrequently: true });
  const cache = new Map();
  const rgba = (c) => {
    if (cache.has(c)) return cache.get(c);
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = "rgba(0,0,0,0)"; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data; const v = [d[0], d[1], d[2], d[3] / 255]; cache.set(c, v); return v;
  };
  const blend = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hex = (c) => "#" + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  const oklch = (c) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    const [r, g, b] = [f(c[0]), f(c[1]), f(c[2])];
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b), m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b), s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
    return { L, C: Math.hypot(A, B), H: ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360 };
  };
  const vis = (el) => {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || cs.visibility === "hidden" || cs.display === "none") return false;
    for (let e = el; e; e = e.parentElement) { const s = getComputedStyle(e); if (Number(s.opacity) === 0) return false; if (s.clipPath && s.clipPath.startsWith("inset(50%")) return false; if (s.clip && s.clip.startsWith("rect(0")) return false; }
    return !el.closest(".sr-only,[hidden]");
  };
  const desc = (el) => {
    const t = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("title") || "").trim().replace(/\s+/g, " ").slice(0, 50);
    const id = el.getAttribute("data-testid");
    const cls = typeof el.className === "string" ? el.className.split(/\s+/).filter((c) => c && !c.includes(":") && !c.includes("[")).slice(0, 2).join(".") : "";
    return `${el.tagName.toLowerCase()}${id ? `[${id}]` : ""}${cls ? "." + cls : ""} "${t}"`;
  };
  // achtergrond achter een element: alle lagen van buiten naar binnen; arcering = extra kandidaat (worst case)
  const bgCache = new Map();
  const backgrounds = (el) => {
    if (bgCache.has(el)) return bgCache.get(el);
    let cands;
    if (!el.parentElement) cands = [[255, 255, 255, 1]];
    else cands = backgrounds(el.parentElement);
    const cs = getComputedStyle(el);
    const c = rgba(cs.backgroundColor);
    if (c[3] > 0) cands = cands.map((b) => blend(c, b));
    const fills = (cs.backgroundSize || "auto").split(",").every((x) => /^\s*auto( auto)?\s*$|100%/.test(x));
    if (cs.backgroundImage && cs.backgroundImage.includes("gradient") && fills) {
      const stops = cs.backgroundImage.match(/(rgba?\([^)]*\)|oklch\([^)]*\)|color\([^)]*\)|#[0-9a-f]{3,8})/gi) ?? [];
      const extra = [];
      for (const s of stops) { const g = rgba(s); if (g[3] > 0) for (const b of cands) extra.push(blend(g, b)); }
      cands = cands.concat(extra);
    }
    const uniq = []; for (const x of cands) if (!uniq.some((u) => u.every((v, i) => Math.abs(v - x[i]) < 1))) uniq.push(x);
    bgCache.set(el, uniq); return uniq;
  };
  const svgBg = (el) => {
    const g = el.closest("g"); const r = g?.querySelector("rect");
    const base = backgrounds(el.closest("svg").parentElement);
    if (!r) return base;
    const f = rgba(getComputedStyle(r).fill); return f[3] > 0 ? base.map((b) => blend(f, b)) : base;
  };
  const opacityChain = (el) => { let o = 1; for (let e = el; e; e = e.parentElement) o *= Number(getComputedStyle(e).opacity); return o; };
  const disabled = (el) => !!el.closest(":disabled,[aria-disabled=true],[data-disabled]");

  const res = { breedte: W, overloop: Math.max(0, document.documentElement.scrollWidth - W), uitstekers: [], tekst_te_laag: [], tekst_klein: [], tekst_n: 0, laagste_tekst: null,
    uitgeschakeld_laag: [], randen_te_laag: [], doelen_n: 0, doelen_klein: [], doelen_in_zin: [], links_zonder_streep: [], grafiekslot: [], groen: [], andere_groen: [], status_vorm: [], ellips: [], h1: 0, kopsprongen: [], dubbele_ids: [] };

  // overloop: elementen die buiten het venster steken en niet in een eigen scrollvak staan
  if (res.overloop > 0) {
    for (const el of document.body.querySelectorAll("*")) {
      const r = el.getBoundingClientRect(); if (r.right <= W + 1 || r.width === 0) continue;
      let inScroll = false; for (let e = el.parentElement; e; e = e.parentElement) { const ox = getComputedStyle(e).overflowX; if (ox === "auto" || ox === "scroll" || ox === "hidden" || ox === "clip") { inScroll = e !== document.documentElement && e !== document.body; break; } }
      if (!inScroll && vis(el)) res.uitstekers.push(`${desc(el)} right=${Math.round(r.right)}`);
      if (res.uitstekers.length >= 6) break;
    }
  }

  // tekstcontrast + tekstmaat
  let laagste = 99;
  for (const el of document.body.querySelectorAll("*")) {
    if (["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"].includes(el.tagName)) continue;
    if (el instanceof SVGElement && !["text", "tspan"].includes(el.tagName)) continue;
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 0);
    const isField = (el.tagName === "INPUT" && !["checkbox", "radio", "range", "hidden", "file", "color"].includes(el.type) && el.value) || (el.tagName === "SELECT") || (el.tagName === "TEXTAREA" && el.value);
    if (!own && !isField) continue;
    if (!vis(el)) continue;
    const cs = getComputedStyle(el);
    const size = parseFloat(cs.fontSize); const weight = Number(cs.fontWeight) || 400;
    res.tekst_n++;
    if (size < 13.95) res.tekst_klein.push(`${desc(el)} ${size}px`);
    const fg = [...rgba(el instanceof SVGElement ? cs.fill : cs.color)]; fg[3] *= opacityChain(el) * (el instanceof SVGElement ? Number(cs.fillOpacity || 1) : 1);
    const bgs = el instanceof SVGElement ? svgBg(el) : backgrounds(el);
    let min = 99, worstBg = null;
    for (const b of bgs) { const r = ratio(blend(fg, b), b); if (r < min) { min = r; worstBg = b; } }
    const groot = size >= 24 || (size >= 18.66 && weight >= 700);
    const eis = groot ? 3 : 4.5;
    if (el.closest(".poster")) { res.poster_tekst = (res.poster_tekst ?? 0) + 1; continue; } // tekst over poster-art (zusterlaag): handmatig gemeten
    if (disabled(el)) { if (min < 4.5) res.uitgeschakeld_laag.push(`${desc(el)} ${min.toFixed(2)}:1`); continue; }
    if (min < laagste) { laagste = min; res.laagste_tekst = `${min.toFixed(2)}:1 ${desc(el)} ${hex(blend(fg, worstBg))} op ${hex(worstBg)}`; }
    if (min < eis) res.tekst_te_laag.push(`${min.toFixed(2)}:1 (eis ${eis}) ${desc(el)} ${hex(blend(fg, worstBg))} op ${hex(worstBg)}`);
    if (cs.textOverflow === "ellipsis" && el.scrollWidth > el.clientWidth + 1) res.ellips.push(desc(el));
  }
  // placeholders
  for (const el of document.querySelectorAll("input[placeholder],textarea[placeholder]")) {
    if (!vis(el) || el.value) continue;
    const pc = rgba(getComputedStyle(el, "::placeholder").color);
    const b = backgrounds(el)[0]; const r = ratio(blend(pc, b), b);
    if (r < 4.5 && !disabled(el)) res.tekst_te_laag.push(`${r.toFixed(2)}:1 placeholder ${desc(el)}`);
  }

  // UI-randen (WCAG 1.4.11): velden en selectievakjes/keuzerondjes met eigen rand; schakelaars
  for (const el of document.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select,textarea,[role=switch],[role=checkbox]:not(input),[role=radio]:not(input)")) {
    if (!vis(el) || disabled(el)) continue;
    const vorm = el.matches("[role=switch]") && el.firstElementChild ? el.firstElementChild : el;
    const cs = getComputedStyle(vorm);
    const parentBg = backgrounds(el.parentElement);
    const own = rgba(cs.backgroundColor);
    const bw = parseFloat(cs.borderTopWidth) || 0;
    let border = bw > 0 && cs.borderTopStyle !== "none" ? rgba(cs.borderTopColor) : null;
    if ((!border || border[3] === 0) && el.parentElement && el.parentElement.querySelectorAll("input,select,textarea").length === 1) {
      const ps = getComputedStyle(el.parentElement); // veld zonder eigen rand in een omhulling met rand (zoekveld kopbalk)
      if ((parseFloat(ps.borderTopWidth) || 0) > 0 && ps.borderTopStyle !== "none") border = rgba(ps.borderTopColor);
    }
    let best = 0;
    for (const pb of parentBg) {
      const r1 = border && border[3] > 0 ? ratio(blend(border, pb), pb) : 0;
      const r2 = own[3] > 0 ? ratio(blend(own, pb), pb) : 0;
      const r = Math.max(r1, r2); best = best === 0 ? r : Math.min(best, r);
    }
    if (best < 3) res.randen_te_laag.push(`${best.toFixed(2)}:1 ${desc(el)}`);
  }

  // aanraakdoelen ≥ 44 px (Peil §6); uitzondering: link in lopende tekst
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=switch], [role=checkbox], [role=radio], [role=option], [role=menuitem], [tabindex]:not([tabindex="-1"])';
  for (const el of document.querySelectorAll(sel)) {
    if (el.tagName === "MAIN" || el.closest(".skip-link") || el.classList.contains("skip-link")) continue;
    let target = el;
    if (el.type === "checkbox" || el.type === "radio") {
      const lab = el.closest("label") ?? (el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null);
      if (lab) target = lab;
      else if (!vis(el)) continue;
    } else if (!vis(el)) continue;
    if (target !== el && !vis(target)) continue;
    if (el.closest("[inert]")) continue;
    const cs = getComputedStyle(el);
    if (cs.display === "inline" || (cs.display.startsWith("inline") && el.matches("a,.term-btn"))) {
      // in een zin: het dichtstbijzijnde blok-element heeft eigen tekst naast dit doel (WCAG 2.5.8-uitzondering, Peil §6)
      let p = el.parentElement; while (p && getComputedStyle(p).display === "inline" && p.parentElement) p = p.parentElement;
      const own = [...p.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").replace(/[·•|,]/g, "").trim();
      if (own.length >= 3) { res.doelen_in_zin.push(desc(el)); continue; }
    }
    if (["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName) && (parseFloat(cs.borderTopWidth) || 0) === 0 && el.parentElement?.querySelectorAll("input,select,textarea").length === 1
      && el.parentElement.tagName === "LABEL") target = el.parentElement;
    if (el.getAttribute("tabindex") === "0" && (el.matches("[role=img],figure *,[data-chart-plot],svg") || el.closest("figure"))) { res.doelen_n++; continue; } // plotvak van een grafiek
    const r = target.getBoundingClientRect();
    res.doelen_n++;
    if (r.width < 43.5 || r.height < 43.5) res.doelen_klein.push(`${desc(el)} ${Math.round(r.width)}×${Math.round(r.height)}`);
  }

  for (const a of document.querySelectorAll("a[href]")) {
    if (!vis(a) || a.closest("nav,aside,[role=tablist],[role=menu],header,.bottomnav,.sidebar")) continue;
    const cs = getComputedStyle(a);
    const bw = parseFloat(cs.borderTopWidth) || 0; const bg = rgba(cs.backgroundColor);
    if ((bw > 0 && rgba(cs.borderTopColor)[3] > 0) || bg[3] > 0) continue; // knop- of kaartvorm
    if (a.querySelector("div,p,h2,h3,h4,img,.list-main,.kpi-value") || a.matches(".list-row,.kpi,.media-card,.nav-item,.chip,.btn,.tab,[role=tab],[class*=btn]")) continue;
    if (!cs.textDecorationLine.includes("underline")) res.links_zonder_streep.push(`${desc(a)} kleur ${hex(rgba(cs.color))}`);
  }

  // groen: alleen bij gemeten gezondheid (Peil §4.1 regel 1)
  const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  const HEALTHY = { light: ["#0f6b33", "#dff2e5", "#0a4a22"], dark: ["#6fd394", "#0f2b1b", "#a6ebbf"] }[theme];
  for (const el of document.body.querySelectorAll("*")) {
    if (!vis(el)) continue;
    const cs = getComputedStyle(el);
    const props = [["color", cs.color], ["background", cs.backgroundColor], ["border", parseFloat(cs.borderTopWidth) > 0 ? cs.borderTopColor : null],
      ["border-left", parseFloat(cs.borderLeftWidth) > 0 ? cs.borderLeftColor : null], ["fill", el instanceof SVGElement ? cs.fill : null], ["stroke", el instanceof SVGElement ? cs.stroke : null]];
    for (const [prop, val] of props) {
      if (!val || val === "none") continue;
      const c = rgba(val); if (c[3] === 0) continue;
      if (prop === "color" && !(el instanceof SVGElement) && ![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
      const h = hex(c);
      const ctx = (el.closest(".chip,[data-status],.status-hero,.state,.kpi,[role=status],li,tr,.card") ?? el);
      const tekst = (ctx.textContent || "").trim().replace(/\s+/g, " ").slice(0, 70);
      if (HEALTHY.includes(h)) { res.groen.push(`${prop} ${h} ${desc(el).slice(0, 60)} | context: "${tekst}"`); break; }
      if (["#00a672", "#199e70"].includes(h)) { res.grafiekslot.push(`${prop} ${h} ${desc(el).slice(0, 50)}`); break; }
      const o = oklch(c);
      if (o.H > 115 && o.H < 175 && o.C > 0.06 && !el.closest("figure,svg[role=img],.chart,.heat-cell,.legend-swatch,[data-chart]")) { res.andere_groen.push(`${prop} ${h} ${desc(el).slice(0, 60)} | "${tekst}"`); break; }
    }
  }

  // statusvorm: onbekend/verouderd/storing/beperkt/gepauzeerd = icoon + woord + vorm, onbekend nooit pauze-icoon, nooit groen
  for (const el of document.querySelectorAll('.chip--unknown,.chip--unlinked,.chip--warning,.chip--danger,.chip--paused,.chip--healthy,[data-status],.stale-mark')) {
    if (!vis(el)) continue;
    const cs = getComputedStyle(el);
    const status = el.getAttribute("data-status") ?? [...el.classList].find((c) => c.startsWith("chip--"))?.slice(6) ?? "stale";
    const svg = el.querySelector("svg"); const icon = svg ? ([...svg.classList].find((c) => c.startsWith("lucide-") && c !== "lucide") ?? "svg") : null;
    const woord = (el.textContent || "").trim().replace(/\s+/g, " ");
    const fouten = [];
    if (!icon) fouten.push("geen icoon");
    if (!woord) fouten.push("geen woord");
    if (/unknown|unlinked|stale/.test(status)) {
      if (!el.matches("[data-status]:not(.chip):not(.stale-mark)") && cs.borderTopStyle !== "dashed") fouten.push(`rand ${cs.borderTopStyle}, niet gestippeld`);
      if (icon && /pause/.test(icon)) fouten.push("pauze-icoon bij onbekend");
      const c = hex(rgba(cs.color)); if (HEALTHY.includes(c)) fouten.push("groene tekst");
    }
    res.status_vorm.push({ status, icon, woord: woord.slice(0, 60), fouten });
  }

  // koppen en id's
  res.h1 = [...document.querySelectorAll("h1")].filter(vis).length;
  let prev = 0;
  for (const h of document.querySelectorAll("h1,h2,h3,h4,h5,h6")) { if (!vis(h)) continue; const n = Number(h.tagName[1]); if (prev && n > prev + 1) res.kopsprongen.push(`h${prev}→h${n} "${h.textContent.trim().slice(0, 40)}"`); prev = n; }
  const ids = {}; for (const el of document.querySelectorAll("[id]")) ids[el.id] = (ids[el.id] ?? 0) + 1;
  res.dubbele_ids = Object.entries(ids).filter(([, n]) => n > 1).map(([id, n]) => `${id}×${n}`);
  // beperk de lijsten (volledige telling blijft)
  for (const k of ["doelen_in_zin", "links_zonder_streep", "grafiekslot", "tekst_te_laag", "tekst_klein", "uitgeschakeld_laag", "randen_te_laag", "doelen_klein", "groen", "andere_groen", "ellips"]) { res[`${k}_n`] = res[k].length; res[k] = [...new Set(res[k])].slice(0, 25); }
  const sv = res.status_vorm; res.status_vorm = { n: sv.length, fouten: sv.filter((s) => s.fouten.length).slice(0, 20), soorten: [...new Set(sv.map((s) => `${s.status}:${s.icon}:${s.woord.slice(0, 30)}`))].slice(0, 40) };
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

async function newPage(browser, states, theme, w, opts = {}) {
  const ctx = await browser.newContext({ ...dev(w), baseURL: BASE, deviceScaleFactor: 1, locale: opts.user === "engels" ? "en-US" : "nl-NL", timezoneId: "Europe/Amsterdam",
    colorScheme: theme[1], storageState: opts.anonymous ? undefined : states[opts.user ?? "beheerder"], reducedMotion: "reduce" });
  await ctx.addInitScript((t) => { try { localStorage.setItem("mg.theme", t); } catch { /* geblokkeerd */ } }, theme[1]);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return { ctx, page, errors };
}

const results = {};
async function pool(jobs, n, fn) {
  let i = 0;
  await Promise.all(Array.from({ length: n }, async () => { while (i < jobs.length) { const j = jobs[i++]; await fn(j); } }));
}

async function audit(browser, states, dyn, group, slug, route, theme, w, opts = {}) {
  const key = `${group}/${slug}-${theme[0]}-${w}`;
  if (ONLY && !ONLY.test(key)) return;
  const { ctx, page, errors } = await newPage(browser, states, theme, w, opts);
  try {
    for (const [pattern, reply] of opts.routes ?? []) await page.route(pattern, (r) => r.fulfill(reply));
    if (opts.down) await page.route("**/api/v1/**", (r) => (notAuth(r.request().url()) ? r.fulfill(DOWN) : r.continue()));
    if (opts.engine) await mockEngine(page);
    await page.goto(dyn[route] ?? route, { waitUntil: "domcontentloaded" });
    if (opts.noSettle) await page.waitForTimeout(4_000); else await settle(page);
    if (opts.stale) { await page.route("**/api/v1/**", (r) => (notAuth(r.request().url()) ? r.fulfill(DOWN) : r.continue())); await page.waitForTimeout(opts.stale); }
    if (opts.action) await opts.action(page, w);
    const m = await page.evaluate(meet, {});
    const a = await axe(page);
    if (opts.shot) {
      const file = path.join(OUT, opts.shotDir ?? "afdrukken", `${slug}-${theme[0]}-${w}.png`);
      await page.screenshot({ path: file, fullPage: !opts.viewportOnly, animations: "disabled" });
    }
    results[key] = { route: dyn[route] ?? route, thema: theme[0], breedte: w, nagebootst: opts.nagebootst ?? null, ...m, axe: a, js_fouten: errors };
    const nv = a.overtredingen.reduce((s, v) => s + v.n, 0);
    console.log(`ok   ${key}  axe=${nv} contrast=${m.tekst_te_laag_n} randen=${m.randen_te_laag_n} doelen<44=${m.doelen_klein_n} links=${m.links_zonder_streep_n} overloop=${m.overloop} groen=${m.groen_n}/${m.andere_groen_n} vorm=${m.status_vorm.fouten.length}`);
  } catch (e) {
    results[key] = { route, thema: theme[0], breedte: w, fout: String(e).slice(0, 300) };
    console.log(`FOUT ${key}: ${String(e).split("\n")[0]}`);
  } finally { await ctx.close(); }
}

// ---------------------------------------------------------------- Tab-doorloop
function focusInfo() {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const cv = document.createElement("canvas"); cv.width = cv.height = 1; const cx = cv.getContext("2d", { willReadFrequently: true });
  const rgba = (c) => { cx.clearRect(0, 0, 1, 1); cx.fillStyle = "rgba(0,0,0,0)"; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1); const d = cx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const blend = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const bgOf = (e) => { const chain = []; for (let p = e; p; p = p.parentElement) chain.push(p); let c = [255, 255, 255, 1]; for (const p of chain.reverse()) { const b = rgba(getComputedStyle(p).backgroundColor); if (b[3] > 0) c = blend(b, c); } return c; };
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const name = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("title") || el.getAttribute("placeholder") || "").trim().replace(/\s+/g, " ").slice(0, 40);
  const ow = parseFloat(cs.outlineWidth) || 0;
  const hasOutline = cs.outlineStyle !== "none" && ow > 0;
  const shadow = cs.boxShadow && cs.boxShadow !== "none";
  const behind = bgOf(el.parentElement ?? el);
  const ring = hasOutline ? ratio(blend(rgba(cs.outlineColor), behind), behind) : 0;
  // bedekt door een vaste/sticky balk? meet 5 punten binnen het element
  const pts = [[0.5, 0.5], [0.1, 0.15], [0.9, 0.15], [0.1, 0.85], [0.9, 0.85]].map(([fx, fy]) => [r.left + r.width * fx, r.top + r.height * fy]);
  let bedekt = 0, buiten = 0;
  for (const [x, y] of pts) {
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) { buiten++; continue; }
    const hit = document.elementFromPoint(x, y);
    if (!hit || hit === el || el.contains(hit) || hit.contains(el)) continue;
    let fixed = false; for (let p = hit; p; p = p.parentElement) { const pos = getComputedStyle(p).position; if (pos === "fixed" || pos === "sticky") { fixed = !p.contains(el); break; } }
    if (fixed) bedekt++;
  }
  return { el: `${el.tagName.toLowerCase()}${el.getAttribute("data-testid") ? `[${el.getAttribute("data-testid")}]` : ""} "${name}"`, outline: hasOutline ? `${cs.outlineStyle} ${ow}px` : "geen", ring: Number(ring.toFixed(2)), halo: shadow,
    bedekt, buiten, grootte: `${Math.round(r.width)}×${Math.round(r.height)}` };
}

async function tabWalk(browser, states, dyn, slug, route, theme, w) {
  const key = `toetsenbord/${slug}-${theme[0]}-${w}`;
  if (ONLY && !ONLY.test(key)) return;
  const { ctx, page } = await newPage(browser, states, theme, w);
  try {
    await page.goto(dyn[route] ?? route, { waitUntil: "domcontentloaded" }); await settle(page);
    const stops = []; const seen = new Set();
    for (let i = 0; i < 70; i++) {
      await page.keyboard.press("Tab");
      await page.waitForTimeout(60);
      const f = await page.evaluate(focusInfo);
      if (!f) { stops.push({ el: "(body)" }); continue; }
      const sig = `${f.el}|${i > 3 ? "" : i}`;
      if (seen.has(f.el) && stops.length > 8 && stops[0].el === f.el) break; // rond
      seen.add(sig); stops.push(f);
    }
    const eerste = stops[0]?.el ?? null;
    const zonderRing = stops.filter((s) => s.outline === "geen" && s.el !== "(body)");
    const zwak = stops.filter((s) => s.outline !== "geen" && s.ring < 3);
    const bedekt = stops.filter((s) => s.bedekt > 0);
    const geheel = stops.filter((s) => s.bedekt >= 5 - (s.buiten ?? 0) && s.bedekt > 0);
    results[key] = { route, thema: theme[0], breedte: w, stops: stops.length, eerste, zonder_ring: zonderRing.slice(0, 10), ring_onder_3: zwak.slice(0, 10), deels_bedekt: bedekt.slice(0, 10), geheel_bedekt: geheel.slice(0, 10), laagste_ring: Math.min(...stops.filter((s) => s.ring).map((s) => s.ring)) };
    console.log(`ok   ${key} stops=${stops.length} eerste=${eerste} zonderRing=${zonderRing.length} ring<3=${zwak.length} bedekt=${bedekt.length}/${geheel.length}`);
  } catch (e) {
    results[key] = { fout: String(e).slice(0, 300) }; console.log(`FOUT ${key}: ${String(e).split("\n")[0]}`);
  } finally { await ctx.close(); }
}

// ---------------------------------------------------------------- start
const browser = await chromium.launch();
const states = { beheerder: await storageState("beheerder"), kijker: await storageState("kijker"), engels: await storageState("engels") };
const dyn = {};
{
  const ctx = await request.newContext({ baseURL: BASE, storageState: states.beheerder });
  const series = await (await ctx.get("/api/v1/library?type=tv")).json();
  const movies = await (await ctx.get("/api/v1/library?type=movie")).json();
  const titel = series.items.find((i) => i.title === "Bergpas") ?? series.items[0];
  const film = movies.items.find((i) => i.has_file) ?? movies.items[0];
  await ctx.dispose();
  dyn["@titel"] = `/bibliotheek/${titel.instance_id}/${titel.id}`; dyn["@film"] = `/bibliotheek/${film.instance_id}/${film.id}`;
  const { ctx: c2, page } = await newPage(browser, states, THEMES[0], 1440);
  await page.goto(dyn["@titel"]); await settle(page);
  dyn["@aflevering"] = await page.locator('a[href*="/aflevering/"]').first().getAttribute("href");
  await c2.close();
}
console.log("dynamisch:", dyn);
mkdirSync(path.join(OUT, "afdrukken"), { recursive: true }); mkdirSync(path.join(OUT, "toestanden"), { recursive: true });
const resFile = path.join(OUT, "review.json");
if (existsSync(resFile)) Object.assign(results, JSON.parse(readFileSync(resFile, "utf8")));
const save = () => writeFileSync(resFile, JSON.stringify(Object.fromEntries(Object.entries(results).sort(([a], [b]) => a.localeCompare(b))), null, 1));

if (WHAT === "audit" || WHAT === "afdrukken" || WHAT === "alles") {
  const jobs = [];
  for (const theme of THEMES) for (const w of WHAT === "afdrukken" ? SHOT_WIDTHS : AUDIT_WIDTHS) for (const [slug, route, o] of ROUTES) {
    const shot = HOOFD.includes(slug) && SHOT_WIDTHS.includes(w);
    if (WHAT === "afdrukken" && !shot) continue;
    jobs.push([slug, route, theme, w, { ...(o ?? {}), shot }]);
  }
  await pool(jobs, PAR, ([slug, route, theme, w, o]) => audit(browser, states, dyn, "schermen", slug, route, theme, w, o));
  save();
}
if (WHAT === "toetsenbord" || WHAT === "alles") {
  const jobs = [];
  for (const slug of ["overzicht", "activiteit", "bibliotheek", "bibliotheek-titel", "ruimte", "terugzetten", "aanvragen", "instellingen", "koppelingen"])
    for (const theme of THEMES) for (const w of [390, 1440]) jobs.push([slug, ROUTES.find((r) => r[0] === slug)[1], theme, w]);
  await pool(jobs, PAR, ([slug, route, theme, w]) => tabWalk(browser, states, dyn, slug, route, theme, w));
  save();
}
if (WHAT === "toestanden" || WHAT === "alles") {
  // onbekend en verouderd: nooit groen, altijd icoon + woord + vorm
  const ST = [
    ["core-weg-overzicht", "/", { down: true, noSettle: true, nagebootst: "alle /api/v1 behalve /auth → 502 upstream_unreachable" }],
    ["core-weg-ruimte", "/av1", { down: true, noSettle: true, nagebootst: "alle /api/v1 behalve /auth → 502" }],
    ["core-weg-activiteit", "/activiteit", { down: true, noSettle: true, nagebootst: "alle /api/v1 behalve /auth → 502" }],
    ["verouderd-bibliotheek", "/bibliotheek", { stale: 70_000, nagebootst: "eerst echte data, daarna /api/v1 → 502 (verversen mislukt)" }],
    ["verouderd-activiteit", "/activiteit", { stale: 70_000, nagebootst: "eerst echte data, daarna /api/v1 → 502" }],
    ["engine-actief-ruimte", "/av1", { engine: true, nagebootst: "AV1-engine zoals e2e/studio-av1.spec.ts (één CPU-worker gepauzeerd)" }],
    ["engine-actief-overzicht", "/", { engine: true, nagebootst: "AV1-engine zoals e2e/studio-av1.spec.ts" }],
    ["engine-actief-systeem", "/systeem", { engine: true, nagebootst: "AV1-engine (metingen 60 min)" }],
    ["en-us-overzicht", "/", { user: "engels" }], ["en-us-ruimte-engine", "/av1", { user: "engels", engine: true, nagebootst: "AV1-engine" }],
    ["kijker-overzicht", "/", { user: "kijker" }],
  ];
  const jobs = [];
  for (const [slug, route, o] of ST) for (const theme of THEMES) for (const w of [390, 1440]) jobs.push([slug, route, theme, w, { ...o, shot: true, shotDir: "toestanden" }]);
  await pool(jobs, PAR, ([slug, route, theme, w, o]) => audit(browser, states, dyn, "toestanden", slug, route, theme, w, o));
  save();
}
if (WHAT === "toevoegingen" || WHAT === "alles") {
  const out = {};
  const setHub = async (value) => {
    const ctx = await request.newContext({ baseURL: BASE, storageState: states.beheerder });
    const me = await (await ctx.get("/api/v1/auth/me")).json();
    const r = await ctx.patch("/api/v1/settings/scoped", { data: { changes: [{ scope_type: "global", scope_id: "", key: "system.hub_url", value }], reason: "Peil-review (testomgeving)" }, headers: { "X-CSRF-Token": me.csrf_token ?? "" } });
    await ctx.dispose();
    return r.status();
  };
  const hubLinks = async (theme, w) => {
    const { ctx, page } = await newPage(browser, states, theme, w);
    await page.goto("/"); await settle(page);
    const zijbalk = await page.locator('aside a[href^="http"], nav a[href^="http"]').evaluateAll((els) => els.filter((e) => e.getBoundingClientRect().width > 0).map((e) => ({ tekst: e.textContent.trim(), href: e.getAttribute("href"), h: Math.round(e.getBoundingClientRect().height) })));
    let menu = [];
    const prof = page.getByRole("button", { name: /profiel|account|beheerder/i }).first();
    if (await prof.count()) { await prof.click(); await page.waitForTimeout(300); menu = await page.locator('[role=menu] a[href^="http"], [role=dialog] a[href^="http"], .popover a[href^="http"]').evaluateAll((els) => els.map((e) => ({ tekst: e.textContent.trim(), href: e.getAttribute("href"), h: Math.round(e.getBoundingClientRect().height) }))); }
    const shot = path.join(OUT, "toestanden", `hub-link-${theme[0]}-${w}.png`); await page.screenshot({ path: shot });
    await ctx.close();
    return { zijbalk, menu };
  };
  out.hub_leeg = await hubLinks(THEMES[0], 1440);
  out.hub_ongeldig_status = await setHub("javascript:alert(1)");
  out.hub_geldig_status = await setHub("http://hub.example:30080");
  try {
    out.hub_geldig_1440_licht = await hubLinks(THEMES[0], 1440);
    out.hub_geldig_1440_donker = await hubLinks(THEMES[1], 1440);
    out.hub_geldig_390_licht = await hubLinks(THEMES[0], 390);
  } finally { out.hub_terug_leeg_status = await setHub(""); }
  out.hub_weer_leeg = await hubLinks(THEMES[0], 1440);
  // grafieken (nagebootste engine): tabelweergave, toetsenbord en "Geen meting" — Besparing per dag (dag 10 ontbreekt)
  // en Belasting (minuten 40–44 zonder meting)
  for (const theme of THEMES) {
    const { ctx, page } = await newPage(browser, states, theme, 1440);
    await mockEngine(page);
    await page.goto("/av1"); await settle(page);
    const r = {};
    for (const [naam, stappen] of [["besparing-per-dag", 9], ["belasting", 42]]) {
      const fig = page.getByTestId(`grafiek-${naam}`);
      if (!(await fig.count())) { r[naam] = "niet gevonden"; continue; }
      const plot = fig.locator('[tabindex="0"][role="img"]').first();
      const live = fig.getByTestId(`grafiek-${naam}-lezing`);
      await plot.focus(); await page.keyboard.press("Home"); await page.waitForTimeout(100);
      const eerste = (await live.textContent())?.trim();
      for (let i = 0; i < stappen; i++) await page.keyboard.press("ArrowRight");
      await page.waitForTimeout(150);
      const gat = (await live.textContent())?.trim();
      await page.keyboard.press("Escape"); await page.waitForTimeout(100);
      const naEsc = (await live.textContent())?.trim();
      const twin = fig.getByTestId(`grafiek-${naam}-tabel`);
      let tabel = null;
      if (await twin.count()) {
        await twin.getByText(/Toon als tabel/).click(); await page.waitForTimeout(250);
        tabel = await twin.locator("table").first().evaluate((t) => ({ caption: t.caption?.textContent?.trim() ?? null,
          koppen: [...t.querySelectorAll("thead th")].map((th) => `${th.textContent.trim()}[${th.getAttribute("scope")}]`),
          rijen: t.querySelectorAll("tbody tr").length, geen_meting: [...t.querySelectorAll("td")].filter((td) => /Geen meting/.test(td.textContent)).length }));
      }
      r[naam] = { plot_label: await plot.getAttribute("aria-label"), eerste, na_stappen: gat, na_escape: naEsc, tabel, gat_vakken: await fig.locator("rect[data-gat]").count() };
      await fig.screenshot({ path: path.join(OUT, "toestanden", `grafiek-${naam}-tabel-${theme[0]}-1440.png`) });
    }
    r.axe = await axe(page);
    out[`grafiek_${theme[0]}`] = r;
    await ctx.close();
  }
  results.toevoegingen = out;
  console.log(JSON.stringify(out, null, 1).slice(0, 4000));
  save();
}
await browser.close();
save();
console.log(`klaar: ${resFile}`);
