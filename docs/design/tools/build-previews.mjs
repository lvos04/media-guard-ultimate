// Builds self-contained static HTML previews from docs/design/previews/src/*.html.
// - inlines the canonical design-system CSS (apps/web/styles/mg.css)
// - embeds the self-hosted OFL fonts (Inter, JetBrains Mono) as base64
// - expands <i data-i="icon-name" class="..."></i> to inline lucide SVG (ISC)
// - wraps content in the application shell (sidebar, topbar, bottom nav)
// Usage: node docs/design/tools/build-previews.mjs   (run from repo root)
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const web = join(root, "apps", "web");
const srcDir = join(root, "docs", "design", "previews", "src");
const outDir = join(root, "docs", "design", "previews");
const iconDir = join(web, "node_modules", "lucide-react", "dist", "esm", "icons");

const css = readFileSync(join(web, "styles", "mg.css"), "utf8");
const font = (f) => readFileSync(join(web, "app", "fonts", f)).toString("base64");
const fontCss = `
@font-face { font-family: "Inter Variable"; font-style: normal; font-weight: 100 900; font-display: swap; src: url(data:font/woff2;base64,${font("InterVariable-latin.woff2")}) format("woff2"); }
@font-face { font-family: "JetBrains Mono Variable"; font-style: normal; font-weight: 100 800; font-display: swap; src: url(data:font/woff2;base64,${font("JetBrainsMonoVariable-latin.woff2")}) format("woff2"); }`;

const iconCache = new Map();
function icon(name, cls = "ico") {
  if (!iconCache.has(name)) {
    let text = readFileSync(join(iconDir, `${name}.mjs`), "utf8");
    const alias = text.match(/export \{ default \} from '\.\/([a-z0-9-]+)\.mjs'/);
    if (alias) text = readFileSync(join(iconDir, `${alias[1]}.mjs`), "utf8");
    const literal = text.slice(text.indexOf("{", text.indexOf("__iconData")), text.indexOf("};", text.indexOf("__iconData")) + 1);
    const data = Function(`return (${literal});`)();
    const inner = data.node.map(([tag, attrs]) => `<${tag} ${Object.entries(attrs).filter(([k]) => k !== "key").map(([k, v]) => `${k}="${v}"`).join(" ")}/>`).join("");
    iconCache.set(name, inner);
  }
  return `<svg class="${cls}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconCache.get(name)}</svg>`;
}
const expandIcons = (html) => html.replace(/<i data-i="([a-z0-9-]+)"(?: class="([^"]*)")?><\/i>/g, (_, n, c) => icon(n, c ? `ico ${c}` : "ico"));

export const NAV = [
  [null, [["Overzicht", "/", "layout-dashboard"]]],
  ["Media", [["Bibliotheek", "/bibliotheek", "library"], ["Ontbrekend & herstel", "/ontbrekend", "search-x", "1.284", "danger"], ["Kalender", "/kalender", "calendar-days"], ["Anime mapping", "/anime", "list-tree", "12", "warning"]]],
  ["Kwaliteit", [["Release Lab", "/release-lab", "flask-conical"], ["Kwaliteit & TRaSH", "/kwaliteit", "gauge"], ["Audio & ondertiteling", "/audio", "languages"]]],
  ["AV1 & HDR", [["AV1 Studio", "/av1", "clapperboard", "214"], ["Dolby Vision & HDR", "/dolby-vision", "aperture"]]],
  ["Operaties", [["Jobs & logs", "/jobs", "activity"], ["Quarantaine & herstel", "/quarantaine", "archive-restore"], ["Migratie & optimalisatie", "/migratie", "route"]]],
  ["Integraties", [["Downloads & indexers", "/downloads", "download"], ["Seerr", "/seerr", "inbox"], ["AI Adviseur", "/ai", "bot"]]],
  ["Systeem", [["Instellingen", "/instellingen", "settings"], ["Updates & diagnostics", "/systeem", "stethoscope"]]],
];

function shell(active, body, opts) {
  const nav = NAV.map(([group, items]) => `${group ? `<div class="nav-group">${group}</div>` : ""}${items.map(([label, href, ic, count, tone]) =>
    `<a class="nav-item" href="#"${href === active ? ' aria-current="page"' : ""}>${icon(ic)}<span class="nav-label">${label}</span>${count ? `<span class="nav-count${tone ? ` nav-count--${tone}` : ""}">${count}</span>` : ""}</a>`).join("")}`).join("");
  const bottom = [["Overzicht", "/", "layout-dashboard"], ["Bibliotheek", "/bibliotheek", "library"], ["Ontbrekend", "/ontbrekend", "search-x", "1.284"], ["AV1", "/av1", "clapperboard"], ["Meer", "#more", "menu"]]
    .map(([l, h, ic, c]) => `<a href="#"${h === active || (h === "#more" && opts.moreActive) ? ' aria-current="page"' : ""}>${icon(ic)}${c ? `<span class="nav-count nav-count--danger">${c}</span>` : ""}<span>${l}</span></a>`).join("");
  return `
<a class="skip-link" href="#main">Naar inhoud</a>
<div class="app">
  <aside class="sidebar" aria-label="Hoofdnavigatie">
    <div class="brand"><span class="brand-mark">${icon("shield-check")}</span><span><div class="brand-name">MediaGuard</div><div class="brand-sub">Media Operations</div></span></div>
    <nav class="nav">${nav}</nav>
    <div class="sidebar-footer">
      <div class="health-line"><span class="dot dot--success"></span><span><strong>Alle services gezond</strong></span></div>
      <div class="health-line"><span class="dot dot--warning"></span><span>radarr-animefilms · degraded</span></div>
      <div class="version-line"><span>v0.4.0 · core 8100</span><span>nl-NL</span></div>
    </div>
  </aside>
  <div class="main">
    <header class="topbar">
      <button class="icon-btn mobile-only" aria-label="Menu openen">${icon("menu")}</button>
      <label class="topbar-search">${icon("search")}<span class="sr-only">Zoeken</span><input placeholder="Zoek serie, film, aflevering, job of instelling…" /><span class="kbd">/</span></label>
      <div class="topbar-spacer"></div>
      <div class="topbar-cluster">
        <a class="top-pill" href="#">${icon("loader-circle", "ico-sm")}<strong>7</strong> actief</a>
        <a class="top-pill top-pill--wide" href="#">${icon("hard-drive", "ico-sm")}Opslag <strong>71%</strong> · 18,4 TB vrij</a>
        <button class="icon-btn" aria-label="Meldingen, 3 ongelezen">${icon("bell")}<span class="dot-badge">3</span></button>
        <button class="estop" aria-pressed="${opts.estop ? "true" : "false"}">${icon("octagon-alert")}<span class="estop-label">${opts.estop ? "Noodstop actief" : "Noodstop"}</span></button>
        <span class="avatar" title="Profiel">JV</span>
      </div>
    </header>
    ${opts.estop ? `<div class="banner banner--danger" role="alert">${icon("octagon-alert")}<span><strong>Noodstop actief</strong> sinds 14:02 door jan — nieuwe grabs, encodes en herstelmutaties zijn geblokkeerd. Lopende jobs stoppen bij hun volgende controlepunt.</span><span class="banner-actions"><button class="btn btn--sm">Noodstop opheffen</button></span></div>` : ""}
    <main id="main" class="content">${body}</main>
  </div>
</div>
<nav class="bottomnav" aria-label="Mobiele navigatie">${bottom}</nav>`;
}

mkdirSync(outDir, { recursive: true });
const files = readdirSync(srcDir).filter((f) => f.endsWith(".html")).sort();
for (const file of files) {
  const src = readFileSync(join(srcDir, file), "utf8");
  const meta = Object.fromEntries([...src.matchAll(/^<!--\s*(\w+):\s*(.*?)\s*-->/gm)].map((m) => [m[1], m[2]]));
  const body = src.replace(/^<!--.*?-->\n?/gm, "");
  const content = meta.bare === "1" ? body : shell(meta.active ?? "/", body, { estop: meta.estop === "1", moreActive: meta.more === "1" });
  const html = `<!doctype html>
<html lang="nl" data-theme="${meta.theme ?? "dark"}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${meta.title ?? file} · MediaGuard preview</title>
<style>${fontCss}
${css}
/* preview-only */
.preview-note { position: fixed; right: 12px; top: 70px; z-index: 99; padding: 3px 9px; border-radius: 999px; background: var(--mg-raised); border: 1px solid var(--mg-border); color: var(--mg-text-3); font-size: 10.5px; font-weight: 600; pointer-events: none; }
@media (max-width: 860px) { .preview-note { top: auto; bottom: 74px; right: 8px; } }
</style>
</head>
<body>
${expandIcons(content)}
<div class="preview-note">Ontwerppreview · voorbeeldinhoud</div>
</body>
</html>
`;
  writeFileSync(join(outDir, file), html);
  console.log("built", file, `${Math.round(html.length / 1024)} KB`);
}
