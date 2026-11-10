// Peil-review MediaGuard (stap 5): zoekt "geknepen" tekst — elementen waarvan de tekst per letter breekt (smal en hoog),
// per route en breedte (zo is het knijpen van tabellen op 1024–1279 px gevonden). Alleen-lezen hulpscript.
// Gebruik: MG_PEIL_BASE_URL=… MG_PEIL_PASSWORD=… PEIL_PLAYWRIGHT=…/playwright/index.mjs node knijp.mjs "/,/bibliotheek,…" "768,1024,1100,1280"
const { chromium, request } = await import(process.env.PEIL_PLAYWRIGHT);
const BASE = process.env.MG_PEIL_BASE_URL ?? "http://127.0.0.1:18200";
const rc = await request.newContext({ baseURL: BASE });
await rc.post("/api/v1/auth/login", { data: { username: "beheerder", password: process.env.MG_PEIL_PASSWORD } });
const state = await rc.storageState();
const routes = process.argv[2].split(",");
const widths = process.argv[3].split(",").map(Number);
const b = await chromium.launch();
for (const w of widths) for (const route of routes) {
  const ctx = await b.newContext({ baseURL: BASE, viewport: { width: w, height: 900 }, storageState: state, locale: "nl-NL" });
  const p = await ctx.newPage();
  await p.goto(route); await p.waitForLoadState("networkidle").catch(() => {}); await p.waitForTimeout(700);
  const res = await p.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll("main *")) {
      const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
      if (own.length < 6) continue;
      const r = el.getBoundingClientRect(); const lh = parseFloat(getComputedStyle(el).lineHeight) || 20;
      const longest = Math.max(...own.split(/\s+/).map((x) => x.length));
      if (r.width > 0 && r.height > lh * 2.5 && r.width < longest * 6) out.push(`${el.tagName.toLowerCase()}.${(el.className || "").toString().split(" ")[0]} "${own.slice(0, 30)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
    return [...new Set(out)].slice(0, 6);
  });
  if (res.length) console.log(w, route, "\n  " + res.join("\n  "));
  await ctx.close();
}
await b.close();
